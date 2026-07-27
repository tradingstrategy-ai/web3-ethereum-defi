"""Exercise Accountable's local mock redemption settlement through GuardV0."""

from collections.abc import Iterator

import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3
from web3._utils.events import EventLogErrorFlags  # noqa: PLC2701
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem import AccountableDepositManager, AccountableRedemptionTicket
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

RAW_AMOUNT = 100_000_000


class StaticCall:
    """Return a deterministic local preflight value from a Web3-like call."""

    def __init__(self, value: int):
        """Store the value returned by :meth:`call`.

        :param value:
            Raw integer returned by the mocked view.
        """
        self.value = value

    def call(self) -> int:
        """Return the configured raw value.

        :return:
            Configured raw integer.
        """
        return self.value


class AccountableMockFunctions:
    """Add Accountable's minimum view to the deployed generic ERC-7540 mock."""

    def __init__(self, functions):
        """Wrap the Accountable ABI function namespace.

        :param functions:
            Bound Accountable ABI functions at the mock address.
        """
        self.functions = functions

    def MIN_AMOUNT_WEI(self) -> StaticCall:  # noqa: N802, PLR6301
        """Return the smallest non-zero mock redemption amount.

        The production Accountable adapter must preflight this view. The generic
        mock intentionally omits protocol-specific configuration, so the local
        test supplies only the static minimum and delegates lifecycle calls to
        the deployed contract.

        :return:
            One raw share.
        """
        return StaticCall(1)

    def __getattr__(self, name: str):
        """Delegate all real lifecycle calls to the Accountable ABI binding.

        :param name:
            Requested Web3 contract-function name.
        :return:
            Bound function factory from the deployed mock.
        """
        return getattr(self.functions, name)


class AccountableMockContract:
    """Expose an Accountable ABI at the generic ERC-7540 mock address."""

    def __init__(self, contract: Contract):
        """Wrap the contract's functions without changing its event decoder.

        :param contract:
            Accountable ABI bound to ``MockERC7540Vault``.
        """
        self.functions = AccountableMockFunctions(contract.functions)
        self.events = contract.events


@pytest.fixture(scope="module")
def anvil() -> Iterator[AnvilLaunch]:
    """Launch an isolated local Anvil backend for the mock lifecycle.

    :return:
        Running local Anvil process.
    """
    launch = launch_anvil()
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil: AnvilLaunch) -> Web3:
    """Connect Web3 to the isolated local Anvil backend.

    :param anvil:
        Running local Anvil process.
    :return:
        Local Web3 connection.
    """
    return Web3(HTTPProvider(anvil.json_rpc_url))


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil: AnvilLaunch) -> Iterator[None]:
    """Restore the local backend after each Accountable mock test.

    :param anvil:
        Running local Anvil process.
    :return:
        Snapshot-revert context.
    """
    yield from evm_snapshot_revert(anvil)


@pytest.fixture
def guarded_accountable_mock(web3: Web3) -> tuple[AccountableVault, AccountableDepositManager, Contract, Contract, HexAddress]:
    """Deploy a GuardV0-controlled Accountable-shaped generic ERC-7540 mock.

    :param web3:
        Local Anvil connection.
    :return:
        Accountable adapter and manager, deployed mock, SimpleVaultV0 and asset-manager address.
    """
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    asset = deploy_contract(web3, "guard/GuardMockERC20.json", deployer, "Mock USD", "mUSD", 6)
    mock = deploy_contract(web3, "guard/MockERC7540Vault.json", deployer, asset.address)
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    assert_transaction_success_with_explanation(web3, simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer}))

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    assert_transaction_success_with_explanation(
        web3,
        guard.functions.whitelistERC4626(mock.address, "Allow Accountable mock").transact({"from": deployer}),
    )

    accountable_vault = AccountableVault(
        web3,
        VaultSpec(chain_id=web3.eth.chain_id, vault_address=HexAddress(mock.address)),
        features={ERC4626Feature.accountable_like},
        default_block_identifier="latest",
    )
    accountable_contract = get_deployed_contract(web3, "accountable/AccountableAsyncRedeemVault.json", mock.address)
    accountable_vault.__dict__["vault_contract"] = AccountableMockContract(accountable_contract)
    accountable_vault.__dict__["denomination_token"] = TokenDetails(
        asset,
        name="Mock USD",
        symbol="mUSD",
        decimals=6,
    )
    accountable_vault.__dict__["share_token"] = TokenDetails(
        get_deployed_contract(web3, "guard/MockERC7540Vault.json", mock.address),
        name="Mock Accountable shares",
        symbol="mASH",
        decimals=6,
    )
    manager = accountable_vault.get_deposit_manager()
    assert isinstance(manager, AccountableDepositManager)
    return accountable_vault, manager, mock, simple_vault, asset_manager


def _perform_guarded_call(web3: Web3, simple_vault: Contract, asset_manager: HexAddress, func: ContractFunction) -> HexBytes:
    """Execute one manager-generated call through SimpleVaultV0 and GuardV0.

    :param web3:
        Local Anvil connection.
    :param simple_vault:
        Guarded caller holding the mock assets and shares.
    :param asset_manager:
        Allowed manager that submits ``performCall``.
    :param func:
        Manager-generated protocol call.
    :return:
        Successful guarded transaction hash.
    """
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return HexBytes(tx_hash)


def test_guarded_accountable_mock_redemption_settlement(
    web3: Web3,
    guarded_accountable_mock: tuple[AccountableVault, AccountableDepositManager, Contract, Contract, HexAddress],
) -> None:
    """Settle and claim one Accountable redemption through the local generic mock.

    :param web3:
        Local Anvil connection.
    :param guarded_accountable_mock:
        Accountable manager plus guarded mock deployment.
    """
    vault, manager, mock, simple_vault, asset_manager = guarded_accountable_mock
    asset = vault.denomination_token
    assert asset is not None

    assert_transaction_success_with_explanation(web3, asset.contract.functions.mint(simple_vault.address, RAW_AMOUNT).transact({"from": web3.eth.accounts[0]}))
    _perform_guarded_call(web3, simple_vault, asset_manager, asset.contract.functions.approve(mock.address, RAW_AMOUNT))
    _perform_guarded_call(web3, simple_vault, asset_manager, mock.functions.deposit(RAW_AMOUNT, simple_vault.address))
    assert vault.share_token.fetch_raw_balance_of(simple_vault.address) == RAW_AMOUNT

    request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=RAW_AMOUNT)
    assert len(request.funcs) == 1
    request_hash = _perform_guarded_call(web3, simple_vault, asset_manager, request.funcs[0])
    ticket = request.parse_redeem_transaction([request_hash])
    assert isinstance(ticket, AccountableRedemptionTicket)
    assert ticket.request_id == 1
    assert ticket.raw_shares == RAW_AMOUNT
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.pending

    request_events = vault.vault_contract.events.RedeemRequest().process_receipt(
        web3.eth.get_transaction_receipt(request_hash),
        errors=EventLogErrorFlags.Discard,
    )
    assert len(request_events) == 1
    assert request_events[0]["args"]["assets"] == RAW_AMOUNT

    settlement = manager.force_settle(ticket, mock=mock)
    assert settlement.settlement_required is True
    assert settlement.status_before is AsyncVaultRequestStatus.pending
    assert settlement.status_after is AsyncVaultRequestStatus.claimable
    assert len(settlement.transaction_hashes) == 1
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.claimable

    # Decode with the Accountable ABI: the shared mock preserves the deployed
    # event's indexed topic order (controller, then request id) and amounts.
    settlement_events = vault.vault_contract.events.RedeemClaimable().process_receipt(
        web3.eth.get_transaction_receipt(settlement.transaction_hashes[0]),
        errors=EventLogErrorFlags.Discard,
    )
    assert len(settlement_events) == 1
    assert settlement_events[0]["args"]["requestId"] == ticket.request_id
    assert settlement_events[0]["args"]["controller"] == simple_vault.address
    assert settlement_events[0]["args"]["assets"] == RAW_AMOUNT
    assert settlement_events[0]["args"]["shares"] == RAW_AMOUNT

    claim = manager.finish_redemption(ticket)
    claim_hash = _perform_guarded_call(web3, simple_vault, asset_manager, claim)
    claim_events = mock.events.Withdraw().process_receipt(
        web3.eth.get_transaction_receipt(claim_hash),
        errors=EventLogErrorFlags.Discard,
    )
    assert len(claim_events) == 1
    assert claim_events[0]["args"]["assets"] == RAW_AMOUNT
    assert claim_events[0]["args"]["shares"] == RAW_AMOUNT
    assert asset.fetch_raw_balance_of(simple_vault.address) == RAW_AMOUNT
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.none
