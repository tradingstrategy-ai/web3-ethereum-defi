"""Exercise Plutus Hedge ERC-7540 request validation through GuardV0."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.plutus.deposit_redeem import PlutusAsyncDepositManager, PlutusRedemptionTicket
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusVault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

#: Plutus Hedge's reviewed asynchronous ERC-7540 redemption deployment.
PLUTUS_HEDGE_VAULT: HexAddress = "0x58BfC95a864e18E8F3041D2FCD3418f48393fE6A"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def plutus_anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared Arbitrum fork for ERC-7540 guard tests."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared Arbitrum midnight fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(plutus_anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Revert every guard configuration and deployment before the next test."""
    yield from evm_snapshot_revert(plutus_anvil_fork)


@pytest.fixture
def plutus_vault(web3: Web3) -> PlutusVault:
    """Open Plutus Hedge through its reviewed asynchronous adapter."""
    vault = create_vault_instance_autodetect(web3, PLUTUS_HEDGE_VAULT)
    assert isinstance(vault, PlutusVault)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, plutus_vault: PlutusVault) -> tuple[Contract, HexAddress]:
    """Deploy SimpleVaultV0 and configure Plutus' ERC-7540 target."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = guard.functions.whitelistERC4626(plutus_vault.address, "Allow Plutus ERC-7540").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, asset_manager


def _perform_guarded_call(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
) -> HexBytes:
    """Execute one Plutus manager call through SimpleVaultV0 and GuardV0.

    The helper retains the production call path so every lifecycle phase is
    checked by GuardV0 before the SimpleVault forwards it.

    :param web3:
        Arbitrum fork connection.
    :param simple_vault:
        Guarded contract that owns the Plutus position.
    :param asset_manager:
        Authorised SimpleVault transaction sender.
    :param func:
        Bound Plutus or token function to forward.
    :return:
        Successful guarded transaction hash.
    """
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager, "gas": 2_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return HexBytes(tx_hash)


def test_guarded_plutus_fulfilment_and_claim_lifecycle(
    web3: Web3,
    plutus_vault: PlutusVault,
    guarded_simple_vault: tuple[Contract, HexAddress],
) -> None:
    """Prove Plutus fulfilment and claim when a guarded contract owns shares.

    1. Fund SimpleVaultV0 and deposit into Plutus through GuardV0.
    2. Request redemption and fulfil it with the discovered active operator.
    3. Claim through GuardV0 and verify the denomination-token payout.
    """
    simple_vault, asset_manager = guarded_simple_vault
    manager = plutus_vault.get_deposit_manager()
    assert isinstance(manager, PlutusAsyncDepositManager)
    amount = Decimal(100)
    raw_amount = plutus_vault.denomination_token.convert_to_raw(amount)

    # 1. Fund SimpleVaultV0 and deposit into Plutus through GuardV0.
    fund_erc20_on_anvil(web3, plutus_vault.denomination_token.address, simple_vault.address, raw_amount)
    approval = plutus_vault.denomination_token.contract.functions.approve(plutus_vault.address, raw_amount)
    _perform_guarded_call(web3, simple_vault, asset_manager, approval)
    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    _perform_guarded_call(web3, simple_vault, asset_manager, deposit_request.funcs[0])
    raw_shares = plutus_vault.share_token.fetch_raw_balance_of(simple_vault.address)

    # 2. Request redemption and fulfil it with the discovered active operator.
    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    request_hash = _perform_guarded_call(web3, simple_vault, asset_manager, redemption_request.funcs[0])
    ticket = redemption_request.parse_redeem_transaction([request_hash])
    assert isinstance(ticket, PlutusRedemptionTicket)
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.pending
    settlement = manager.force_settle(ticket)
    assert settlement.status_after is AsyncVaultRequestStatus.claimable

    # 3. Claim through GuardV0 and verify the denomination-token payout.
    balance_before = plutus_vault.denomination_token.fetch_raw_balance_of(simple_vault.address)
    claim_hash = _perform_guarded_call(web3, simple_vault, asset_manager, manager.finish_redemption(ticket))
    assert web3.eth.get_transaction_receipt(claim_hash)["status"] == 1
    balance_after = plutus_vault.denomination_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares == 79_007_315
    assert balance_before == 0
    assert balance_after == 99_999_999
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.none


@pytest.mark.parametrize(
    "case",
    [
        ("outsider", "vault", "Receiver not whitelisted"),
        ("vault", "outsider", "Owner not whitelisted"),
    ],
)
def test_guarded_plutus_rejects_unwhitelisted_erc7540_addresses(
    web3: Web3,
    plutus_vault: PlutusVault,
    guarded_simple_vault: tuple[Contract, HexAddress],
    case: tuple[str, str, str],
) -> None:
    """GuardV0 rejects every sensitive ERC-7540 request address mutation."""
    simple_vault, asset_manager = guarded_simple_vault
    controller, owner, expected_error = case
    outsider = HexAddress(web3.eth.accounts[3])
    controller_address = outsider if controller == "outsider" else simple_vault.address
    owner_address = outsider if owner == "outsider" else simple_vault.address
    target, call_data = encode_simple_vault_transaction(plutus_vault.vault_contract.functions.requestRedeem(1, controller_address, owner_address))
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager, "gas": 1_000_000})
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)
