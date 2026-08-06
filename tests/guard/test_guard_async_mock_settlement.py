"""Exercise asynchronous protocol mock settlement drivers through GuardV0.

The local mocks provide only the privileged/operator boundary that a mainnet
fork cannot safely reproduce. Request and claim calls remain manager-generated
and go through ``SimpleVaultV0`` and GuardV0 exactly as production calls do.
"""

from collections.abc import Iterator
from decimal import Decimal

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
from eth_defi.erc_4626.vault_protocol.ember.deposit_redeem import EmberDepositManager, EmberRedemptionRequest, EmberRedemptionTicket
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager, GainsRedemptionRequest, GainsRedemptionTicket, OstiumRedemptionRequest, OstiumV15DepositManager
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault, OstiumVault, OstiumVersion
from eth_defi.erc_4626.vault_protocol.plutus.deposit_redeem import PlutusAsyncDepositManager, PlutusRedemptionRequest, PlutusRedemptionTicket
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusVault
from eth_defi.erc_4626.vault_protocol.yieldnest.deposit_redeem import YieldNestDepositManager
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YieldNestVault
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation, VaultFlowUnavailable

RAW_AMOUNT = 100_000_000


@pytest.fixture(scope="module")
def anvil() -> Iterator[AnvilLaunch]:
    """Start one isolated local Anvil backend for protocol-shaped mocks.

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
    """Connect to the local mock backend.

    :param anvil:
        Running local Anvil process.
    :return:
        Connected Web3 client.
    """
    return Web3(HTTPProvider(anvil.json_rpc_url))


@pytest.fixture(autouse=True)
def _snapshot(anvil: AnvilLaunch) -> Iterator[None]:
    """Restore local chain state after every lifecycle test.

    :param anvil:
        Running local Anvil process.
    :return:
        Snapshot/revert context.
    """
    yield from evm_snapshot_revert(anvil)


def _deploy_guarded_mock(web3: Web3, mock_abi: str) -> tuple[Contract, Contract, Contract, HexAddress]:
    """Deploy one asset, protocol mock and GuardV0-controlled SimpleVault.

    :param web3:
        Local Anvil client.
    :param mock_abi:
        Compiled Guard mock ABI name.
    :return:
        Asset, protocol mock, SimpleVault and its asset-manager address.
    """
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    asset = deploy_contract(web3, "guard/GuardMockERC20.json", deployer, "Mock USD", "mUSD", 6)
    mock = deploy_contract(web3, f"guard/{mock_abi}.json", deployer, asset.address)
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    assert_transaction_success_with_explanation(web3, simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer}))
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    assert_transaction_success_with_explanation(web3, guard.functions.whitelistERC4626(mock.address, "Allow async protocol mock").transact({"from": deployer}))
    return asset, mock, simple_vault, asset_manager


def _guarded(web3: Web3, simple_vault: Contract, asset_manager: HexAddress, func: ContractFunction) -> HexBytes:
    """Execute one manager-generated call through SimpleVaultV0 and GuardV0.

    :param web3:
        Local Anvil client.
    :param simple_vault:
        Guarded protocol caller.
    :param asset_manager:
        Authorised SimpleVault manager.
    :param func:
        Bound protocol call.
    :return:
        Mined transaction hash.
    """
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return HexBytes(tx_hash)


def _bind_vault(web3: Web3, vault_type: type, protocol_abi: str, mock: Contract, asset: Contract):
    """Create a protocol vault adapter backed by a local Guard mock.

    :param web3:
        Local Anvil client.
    :param vault_type:
        Concrete adapter class required by the manager.
    :param protocol_abi:
        Production ABI used for event parsing and manager calls.
    :param mock:
        Deployed protocol-shaped test mock.
    :param asset:
        Deployed mock denomination ERC-20.
    :return:
        Concrete vault adapter with narrow production ABI at the mock address.
    """
    vault = vault_type(
        web3,
        VaultSpec(chain_id=web3.eth.chain_id, vault_address=HexAddress(mock.address)),
        features={ERC4626Feature.erc_7540_like},
        default_block_identifier="latest",
    )
    vault.__dict__["vault_contract"] = get_deployed_contract(web3, protocol_abi, mock.address)
    vault.__dict__["denomination_token"] = TokenDetails(asset, name="Mock USD", symbol="mUSD", decimals=6)
    vault.__dict__["share_token"] = TokenDetails(mock, name="Mock shares", symbol="mSHARE", decimals=6)
    return vault


def _seed_shares(web3: Web3, asset: Contract, mock: Contract, simple_vault: Contract, asset_manager: HexAddress) -> None:
    """Mint one-to-one mock shares through the guarded ERC-4626 deposit path.

    :param web3:
        Local Anvil client.
    :param asset:
        Mock denomination ERC-20.
    :param mock:
        Protocol mock exposing standard deposit.
    :param simple_vault:
        Guarded owner.
    :param asset_manager:
        Authorised SimpleVault manager.
    """
    assert_transaction_success_with_explanation(web3, asset.functions.mint(simple_vault.address, RAW_AMOUNT).transact({"from": web3.eth.accounts[0]}))
    _guarded(web3, simple_vault, asset_manager, asset.functions.approve(mock.address, RAW_AMOUNT))
    _guarded(web3, simple_vault, asset_manager, mock.functions.deposit(RAW_AMOUNT, simple_vault.address))


def test_yieldnest_mock_ignore_liquidity_and_guarded_redeem(web3: Web3) -> None:
    """Use the explicit local override to test YieldNest redemption via GuardV0.

    The mock begins with ``maxRedeem == 0``, matching the no-buffer condition
    observed on the reference fork. The option is deliberately required before
    the manager can construct and execute the otherwise standard ERC-4626
    redemption through GuardV0.

    :param web3:
        Isolated local Anvil client.
    """
    asset, mock, simple_vault, asset_manager = _deploy_guarded_mock(web3, "MockYieldNestVault")
    vault = YieldNestVault(
        web3,
        VaultSpec(chain_id=web3.eth.chain_id, vault_address=HexAddress(mock.address)),
        features={ERC4626Feature.yieldnest_like},
        default_block_identifier="latest",
    )
    vault.__dict__["vault_contract"] = get_deployed_contract(web3, "guard/MockYieldNestVault.json", mock.address)
    vault.__dict__["denomination_token"] = TokenDetails(asset, name="Mock USD", symbol="mUSD", decimals=6)
    vault.__dict__["share_token"] = TokenDetails(mock, name="Mock YieldNest shares", symbol="mynRWAx", decimals=6)
    manager = YieldNestDepositManager(vault)
    _seed_shares(web3, asset, mock, simple_vault, asset_manager)

    assert manager.can_create_redemption_request(simple_vault.address) is False
    with pytest.raises(VaultFlowUnavailable, match="exceeds buffer capacity"):
        manager.create_redemption_request(owner=simple_vault.address, raw_shares=RAW_AMOUNT)
    with pytest.raises(UnsupportedVaultSimulation, match="no local mock settlement driver"):
        manager.force_settle(None, mock=mock)

    settlement = manager.force_settle(None, mock=mock, ignore_liquidity=True)
    override_events = mock.events.LiquidityOverrideSet().process_receipt(
        web3.eth.get_transaction_receipt(settlement.transaction_hashes[0]),
        errors=EventLogErrorFlags.Discard,
    )
    assert settlement.liquidity_constraints_ignored is True
    assert len(override_events) == 1 and override_events[0]["args"]["ignoreLiquidity"] is True
    assert manager.can_create_redemption_request(simple_vault.address) is True

    request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=RAW_AMOUNT)
    redeem_hash = _guarded(web3, simple_vault, asset_manager, request.funcs[0])
    withdraws = mock.events.Withdraw().process_receipt(
        web3.eth.get_transaction_receipt(redeem_hash),
        errors=EventLogErrorFlags.Discard,
    )
    assert len(withdraws) == 1
    assert withdraws[0]["args"]["assets"] == RAW_AMOUNT
    assert withdraws[0]["args"]["shares"] == RAW_AMOUNT
    assert asset.functions.balanceOf(simple_vault.address).call() == RAW_AMOUNT


def test_plutus_mock_force_settle_and_guarded_claim(web3: Web3) -> None:
    """Fulfil Plutus locally, parse its event and claim through GuardV0.

    :param web3:
        Local Anvil client.
    """
    asset, mock, simple_vault, asset_manager = _deploy_guarded_mock(web3, "MockPlutusVault")
    vault = _bind_vault(web3, PlutusVault, "plutus/HedgeVaultV2.json", mock, asset)
    manager = PlutusAsyncDepositManager(vault)
    _seed_shares(web3, asset, mock, simple_vault, asset_manager)

    request = PlutusRedemptionRequest(vault, simple_vault.address, simple_vault.address, Decimal(100), RAW_AMOUNT, [vault.vault_contract.functions.requestRedeem(RAW_AMOUNT, simple_vault.address, simple_vault.address)])
    ticket = request.parse_redeem_transaction([_guarded(web3, simple_vault, asset_manager, request.funcs[0])])
    assert isinstance(ticket, PlutusRedemptionTicket)
    settlement = manager.force_settle(ticket, mock=mock)
    events = vault.vault_contract.events.RedeemFulfilled().process_receipt(web3.eth.get_transaction_receipt(settlement.transaction_hashes[0]), errors=EventLogErrorFlags.Discard)
    assert settlement.status_after is AsyncVaultRequestStatus.claimable
    assert len(events) == 1 and events[0]["args"]["shares"] == RAW_AMOUNT and events[0]["args"]["lockedAssets"] == RAW_AMOUNT
    claim_hash = _guarded(web3, simple_vault, asset_manager, manager.finish_redemption(ticket))
    withdraws = mock.events.Withdraw().process_receipt(web3.eth.get_transaction_receipt(claim_hash), errors=EventLogErrorFlags.Discard)
    assert len(withdraws) == 1 and withdraws[0]["args"]["assets"] == RAW_AMOUNT
    assert asset.functions.balanceOf(simple_vault.address).call() == RAW_AMOUNT


def test_ember_mock_force_settle_direct_terminal_payout(web3: Web3) -> None:
    """Process Ember locally and parse its direct-pay terminal event.

    :param web3:
        Local Anvil client.
    """
    asset, mock, simple_vault, asset_manager = _deploy_guarded_mock(web3, "MockEmberVault")
    vault = _bind_vault(web3, EmberVault, "ember/EmberVault.json", mock, asset)
    manager = EmberDepositManager(vault)
    _seed_shares(web3, asset, mock, simple_vault, asset_manager)

    request = EmberRedemptionRequest(vault, simple_vault.address, simple_vault.address, Decimal(100), RAW_AMOUNT, [vault.vault_contract.functions.redeemShares(RAW_AMOUNT, simple_vault.address)])
    ticket = request.parse_redeem_transaction([_guarded(web3, simple_vault, asset_manager, request.funcs[0])])
    assert isinstance(ticket, EmberRedemptionTicket)
    settlement = manager.force_settle(ticket, mock=mock)
    events = vault.vault_contract.events.RequestProcessed().process_receipt(web3.eth.get_transaction_receipt(settlement.transaction_hashes[0]), errors=EventLogErrorFlags.Discard)
    assert settlement.status_after is AsyncVaultRequestStatus.none
    assert len(events) == 1 and events[0]["args"]["withdrawAmount"] == RAW_AMOUNT and events[0]["args"]["receiver"] == simple_vault.address
    assert asset.functions.balanceOf(simple_vault.address).call() == RAW_AMOUNT
    assert manager.finish_redemption(ticket) is None


def test_gains_mock_force_settle_and_guarded_claim(web3: Web3) -> None:
    """Advance the Gains mock epoch, parse it and claim through GuardV0.

    :param web3:
        Local Anvil client.
    """
    asset, mock, simple_vault, asset_manager = _deploy_guarded_mock(web3, "MockGainsV1Vault")
    vault = _bind_vault(web3, GainsVault, "gains/GToken.json", mock, asset)
    manager = GainsDepositManager(vault)
    _seed_shares(web3, asset, mock, simple_vault, asset_manager)

    request = GainsRedemptionRequest(vault, simple_vault.address, simple_vault.address, Decimal(100), RAW_AMOUNT, [vault.vault_contract.functions.makeWithdrawRequest(RAW_AMOUNT, simple_vault.address)])
    ticket = request.parse_redeem_transaction([_guarded(web3, simple_vault, asset_manager, request.funcs[0])])
    assert isinstance(ticket, GainsRedemptionTicket)
    settlement = manager.force_settle(ticket, mock=mock)
    events = mock.events.EpochAdvanced().process_receipt(web3.eth.get_transaction_receipt(settlement.transaction_hashes[0]), errors=EventLogErrorFlags.Discard)
    assert settlement.status_after is AsyncVaultRequestStatus.claimable
    assert len(events) == 1 and events[0]["args"]["newEpoch"] == ticket.unlock_epoch
    claim_hash = _guarded(web3, simple_vault, asset_manager, manager.finish_redemption(ticket))
    withdraws = mock.events.Withdraw().process_receipt(web3.eth.get_transaction_receipt(claim_hash), errors=EventLogErrorFlags.Discard)
    assert len(withdraws) == 1 and withdraws[0]["args"]["assets"] == RAW_AMOUNT
    assert asset.functions.balanceOf(simple_vault.address).call() == RAW_AMOUNT


def test_ostium_v15_mock_force_settle_and_guarded_claim(web3: Web3) -> None:
    """Settle Ostium V1.5 locally, parse its terminal event and claim through GuardV0.

    :param web3:
        Local Anvil client.
    """
    asset, mock, simple_vault, asset_manager = _deploy_guarded_mock(web3, "MockOstiumV15Vault")
    vault = _bind_vault(web3, OstiumVault, "gains/OstiumVaultV1_5.json", mock, asset)
    vault.__dict__["version"] = OstiumVersion.v1_5
    manager = OstiumV15DepositManager(vault)
    _seed_shares(web3, asset, mock, simple_vault, asset_manager)

    request = OstiumRedemptionRequest(vault, simple_vault.address, simple_vault.address, Decimal(100), RAW_AMOUNT, [vault.vault_contract.functions.requestWithdraw(RAW_AMOUNT)])
    ticket = request.parse_redeem_transaction([_guarded(web3, simple_vault, asset_manager, request.funcs[0])])
    settlement = manager.force_settle(ticket, mock=mock)
    events = vault.vault_contract.events.AsyncDepositWithdrawExecuted().process_receipt(web3.eth.get_transaction_receipt(settlement.transaction_hashes[0]), errors=EventLogErrorFlags.Discard)
    assert settlement.status_after is AsyncVaultRequestStatus.claimable
    assert len(events) == 1 and events[0]["args"]["totalSharesToWithdraw"] == RAW_AMOUNT
    claim_hash = _guarded(web3, simple_vault, asset_manager, manager.finish_redemption(ticket))
    claims = vault.vault_contract.events.WithdrawClaimedV2().process_receipt(web3.eth.get_transaction_receipt(claim_hash), errors=EventLogErrorFlags.Discard)
    assert len(claims) == 1 and claims[0]["args"]["assets"] == RAW_AMOUNT
    assert asset.functions.balanceOf(simple_vault.address).call() == RAW_AMOUNT
