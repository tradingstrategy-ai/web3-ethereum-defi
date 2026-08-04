"""IPOR Fusion vault tests."""

import os
from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault_protocol.ipor.deposit_redeem import IPOR_ACCOUNT_IS_LOCKED_SELECTOR, IPOR_FAILED_INNER_CALL_SELECTOR, IPOR_WITHDRAW_MANAGER_INVALID_SHARES_TO_RELEASE_SELECTOR, IPORDepositManager
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable
from eth_defi.vault.fee import FeeData, VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

#: Bitcoin Dollar USDC vault on Ethereum.
#:
#: https://app.ipor.io/fusion/ethereum/0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8
IPOR_BDUSD_ETHEREUM = "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8"

#: BL USDC WSR Loop, a vault whose deposit selector is restricted by IPOR's
#: AccessManager for the report's simulated wallet.
IPOR_RESTRICTED_ETHEREUM = "0x95b2ed8f821570f85fd0e3e6e7088c6296587088"

#: Exact IPOR PlasmaVault from trade-executor PR #1602's status-0 redemption.
TAU_INFINIFI_POINTSMAX_ETHEREUM = "0xb0f56bb0bf13ee05fef8cd2d8df5ffdfcac7a74f"

#: Evidence block recorded for the PR #1602 simulation experiment.
TAU_INFINIFI_POINTSMAX_EVIDENCE_BLOCK = 25_670_641

#: Shares minted by a 1,001 USDC deposit at the evidence block.
TAU_INFINIFI_POINTSMAX_RAW_SHARES = 97_201_868_258

#: Shares minted by a 1 USDC deposit at ``BASE_MIDNIGHT_BLOCK``.
AUTOPILOT_USDC_MORPHO_BASE_RAW_SHARES = 95_285_062

#: Simulated wallet from trade-executor's unsupported-vault report.
REPORT_CALLER = "0xa2b04c6a053ab2efbc699f5dd0f0957742a41629"

#: This fee has been set to 0 on-chain as of 2026-05-22.
IPOR_BDUSD_DEPOSIT_FEE = 0.0


@pytest.fixture(scope="module")
def autopilot_base_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the fixed Base fork with the USDC whale unlocked for Autopilot."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_BASE,
        BASE_MIDNIGHT_BLOCK,
        unlocked_addresses=[USDC_WHALE[8453]],
    )


@pytest.fixture(scope="module")
def autopilot_base_web3(autopilot_base_fork: AnvilLaunch) -> Web3:
    """Connect to the shared Base Autopilot fork."""
    return create_multi_provider_web3(autopilot_base_fork.json_rpc_url)


@pytest.fixture
def autopilot_base_snapshot(autopilot_base_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the mutating Autopilot fork after every liquidity test."""
    yield from evm_snapshot_revert(autopilot_base_fork)


@pytest.fixture(scope="module")
def tau_infinifi_ethereum_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the historical TAU InfiniFi fork with the Ethereum USDC whale unlocked."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_ETHEREUM,
        TAU_INFINIFI_POINTSMAX_EVIDENCE_BLOCK,
        unlocked_addresses=[USDC_WHALE[1]],
    )


@pytest.fixture(scope="module")
def tau_infinifi_ethereum_web3(tau_infinifi_ethereum_fork: AnvilLaunch) -> Web3:
    """Connect to the shared historical TAU InfiniFi fork."""
    return create_multi_provider_web3(tau_infinifi_ethereum_fork.json_rpc_url)


@pytest.fixture
def tau_infinifi_ethereum_snapshot(tau_infinifi_ethereum_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the mutating TAU InfiniFi fork after every redemption test."""
    yield from evm_snapshot_revert(tau_infinifi_ethereum_fork)


def test_internalised_fee_mode_preserves_explicit_deposit_fee():
    """Explicit deposit fees are investor-paid even if other fees are internalised.

    1. Create a FeeData with internalised_minting mode and a non-zero deposit fee
    2. Call get_net_fees() to compute investor-visible fees
    3. Verify management and performance are zeroed (internalised) but deposit fee survives
    """
    # 1. Create FeeData with a non-zero deposit fee (hardcoded, not tied to current on-chain state)
    deposit_fee = 0.008
    fee_data = FeeData(
        fee_mode=VaultFeeMode.internalised_minting,
        management=0.005,
        performance=0.0,
        deposit=deposit_fee,
        withdraw=0.0,
    )

    # 2. Compute investor-visible fees
    net_fees = fee_data.get_net_fees()

    # 3. Verify deposit fee survives while management/performance are zeroed
    assert net_fees.management == 0
    assert net_fees.performance == 0
    assert net_fees.deposit == deposit_fee
    assert net_fees.withdraw == 0.0


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Create an Ethereum connection."""
    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run this test")

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture(scope="module")
def vault(web3: Web3) -> IPORVault:
    """Create the IPOR bdUSD vault instance."""
    vault = IPORVault(
        web3,
        VaultSpec(
            chain_id=1,
            vault_address=IPOR_BDUSD_ETHEREUM,
        ),
        features={ERC4626Feature.ipor_like},
    )
    return vault


def test_ipor_vault_description(vault: IPORVault):
    """Fetch vault description from IPOR's offchain customisation API.

    1. Read the description property which fetches from the customisation API
    2. Verify the Bitcoin Dollar USDC vault has a non-empty description
    3. Verify the prospectus link is appended as a markdown link
    """
    # 1. Read the description property
    description = vault.description

    # 2. Verify the description is present and contains expected content
    assert description is not None, "Bitcoin Dollar USDC vault should have a description in IPOR's customisation API"
    assert "Bitcoin Dollar" in description

    # 3. Verify the prospectus markdown link is appended
    assert "[View prospectus](" in description


def test_ipor_onboarding_fee(vault: IPORVault):
    """Read IPOR onboarding fee as an explicit deposit fee."""
    fee_data = vault.get_fee_data()

    assert fee_data.fee_mode == VaultFeeMode.internalised_minting
    assert fee_data.management == pytest.approx(0.005)
    assert fee_data.performance == pytest.approx(0.0)
    assert fee_data.deposit == pytest.approx(IPOR_BDUSD_DEPOSIT_FEE)
    assert fee_data.withdraw == pytest.approx(0.0)
    assert fee_data.get_net_fees().deposit == pytest.approx(IPOR_BDUSD_DEPOSIT_FEE)


def test_ipor_preview_deposit_is_net_of_onboarding_fee(vault: IPORVault):
    """IPOR previewDeposit() returns shares net of the onboarding fee.

    1. Convert 1,000 denomination tokens to raw amount
    2. Compare convertToShares (gross) with previewDeposit (net)
    3. Verify implied fee matches the on-chain onboarding fee
    """
    # 1. Convert 1,000 denomination tokens to raw amount
    raw_assets = vault.denomination_token.convert_to_raw(Decimal(1_000))

    # 2. Compare convertToShares (gross) with previewDeposit (net)
    gross_shares = vault.vault_contract.functions.convertToShares(raw_assets).call()
    net_shares = vault.vault_contract.functions.previewDeposit(raw_assets).call()

    assert gross_shares > 0
    assert net_shares > 0
    assert net_shares <= gross_shares

    # 3. Verify implied fee matches the on-chain onboarding fee
    implied_fee = (gross_shares - net_shares) / gross_shares
    assert implied_fee == pytest.approx(IPOR_BDUSD_DEPOSIT_FEE, abs=0.001)


def test_ipor_deposit_permission_and_restricted_caller_preflight(web3: Web3):
    """Map IPOR AccessManager policy and reject its known private caller.

    The test deliberately uses a raw amount of one: admission must fail before
    the common manager checks the caller's token balance or allowance.
    """
    public_vault = IPORVault(web3, VaultSpec(chain_id=1, vault_address=IPOR_BDUSD_ETHEREUM))
    restricted_vault = IPORVault(web3, VaultSpec(chain_id=1, vault_address=IPOR_RESTRICTED_ETHEREUM))

    assert public_vault.is_whitelisted_deposit() is False
    assert public_vault.is_account_whitelisted(REPORT_CALLER) is True
    assert restricted_vault.is_whitelisted_deposit() is True
    assert restricted_vault.is_account_whitelisted(REPORT_CALLER) is False

    manager = restricted_vault.get_deposit_manager()
    assert isinstance(manager, IPORDepositManager)
    assert manager.can_create_deposit_request(REPORT_CALLER) is False

    with pytest.raises(VaultFlowUnavailable, match="does not allow immediate") as exc_info:
        manager.create_deposit_request(REPORT_CALLER, raw_amount=1)

    assert exc_info.value.function_selector == restricted_vault.get_deposit_function_selector()
    assert exc_info.value.access_delay == 0


def test_ipor_without_access_manager_uses_generic_manager() -> None:
    """An unreadable AccessManager does not disable standard ERC-4626 flows."""
    vault = object.__new__(IPORVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=IPOR_BDUSD_ETHEREUM)
    vault.__dict__["access_manager"] = None

    manager = vault.get_deposit_manager()

    assert isinstance(manager, ERC4626DepositManager)
    assert not isinstance(manager, IPORDepositManager)
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "synchronous",
    }


def test_liquidity_preflight_observes_partial_redemption_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    """IPOR accepts the simulated capacity and refuses exactly one share above it.

    The pinned Base state has zero immediate capacity after a fresh deposit, so
    this state-level boundary test models a market fuse that serves 61 of 100
    shares. It proves the binary-search figure is not a constant while the
    fork test below covers the reported live failure.
    """
    owner = "0x0000000000000000000000000000000000000001"
    expected_capacity = 61
    balance_of = MagicMock()
    balance_of.call.return_value = 100
    vault = SimpleNamespace(
        chain_id=1,
        address="0x0000000000000000000000000000000000000001",
        vault_contract=SimpleNamespace(functions=SimpleNamespace(balanceOf=lambda _owner: balance_of)),
        get_redeem_function_selector=lambda: b"\x00\x00\x00\x00",
    )
    manager = object.__new__(IPORDepositManager)
    manager.vault = vault
    monkeypatch.setattr(manager, "_assert_immediate_access", lambda *_args: None)
    monkeypatch.setattr(
        manager,
        "fetch_redeem_simulation",
        lambda _owner, raw_shares: (raw_shares <= expected_capacity, None if raw_shares <= expected_capacity else IPOR_FAILED_INNER_CALL_SELECTOR),
    )
    monkeypatch.setattr(
        ERC4626DepositManager,
        "create_redemption_request",
        lambda _manager, **kwargs: kwargs,
    )

    available_raw_shares = manager.fetch_redeemable_raw_shares(owner)

    assert available_raw_shares == expected_capacity
    assert manager.create_redemption_request(owner, raw_shares=available_raw_shares)["raw_shares"] == available_raw_shares
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner, raw_shares=available_raw_shares + 1)

    error = exc_info.value
    assert error.preflight_result == "redemption_capacity_limited"
    assert error.requested_raw_amount == expected_capacity + 1
    assert error.available_raw_amount == expected_capacity
    assert error.error_selector == IPOR_FAILED_INNER_CALL_SELECTOR


@pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run this test")
@pytest.mark.xdist_group("fork:base:midnight")
def test_autopilot_usdc_morpho_refuses_unserviceable_redemption_before_broadcast(
    autopilot_base_web3: Web3,
    autopilot_base_snapshot: None,
) -> None:
    """Autopilot turns its market-liquidity revert into a typed preflight refusal.

    1. Deposit Base USDC into the exact Autopilot USDC Morpho deployment.
    2. Attempt to construct redemption of every minted share at the pinned block.
    3. Verify the PlasmaVault liquidity simulation refuses without broadcasting redeem.
    """
    # 1. Deposit Base USDC into the exact Autopilot USDC Morpho deployment.
    assert autopilot_base_snapshot is None
    vault = IPORVault(
        autopilot_base_web3,
        VaultSpec(chain_id=8453, vault_address="0xd6701905c59ee618dc36dc747506bce0a4ac760a"),
        features={ERC4626Feature.ipor_like},
    )
    manager = vault.get_deposit_manager()
    assert isinstance(manager, IPORDepositManager)
    owner = autopilot_base_web3.eth.accounts[0]
    deposit_amount = Decimal(1)
    usdc = vault.denomination_token
    funding_hash = usdc.transfer(owner, deposit_amount).transact({"from": USDC_WHALE[8453]})
    assert_transaction_success_with_explanation(autopilot_base_web3, funding_hash)
    approval_hash = usdc.approve(vault.address, deposit_amount).transact({"from": owner})
    assert_transaction_success_with_explanation(autopilot_base_web3, approval_hash)
    manager.create_deposit_request(owner=owner, amount=deposit_amount).broadcast(from_=owner)
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares == AUTOPILOT_USDC_MORPHO_BASE_RAW_SHARES

    # 2. Attempt to construct redemption of every minted share at the pinned block.
    block_before_refusal = autopilot_base_web3.eth.block_number
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=raw_shares)

    # 3. Verify the PlasmaVault liquidity simulation refuses without broadcasting redeem.
    error = exc_info.value
    assert error.preflight_result == "redemption_window_closed"
    assert error.decoded_error == "AccountIsLocked"
    assert error.error_selector == IPOR_ACCOUNT_IS_LOCKED_SELECTOR
    assert error.direction == "redeem"
    assert error.phase == "preflight"
    assert error.requested_raw_amount == raw_shares
    assert error.available_raw_amount == 0
    assert autopilot_base_web3.eth.block_number == block_before_refusal


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
@pytest.mark.xdist_group("fork:ethereum:tau-infinifi-pointsmax")
def test_tau_infinifi_pointsmax_refuses_locked_redemption_before_broadcast(
    tau_infinifi_ethereum_web3: Web3,
    tau_infinifi_ethereum_snapshot: None,
) -> None:
    """TAU InfiniFi exposes its account redemption lock before redeem broadcast.

    1. Deposit Ethereum USDC into the reported TAU InfiniFi Pointsmax vault at its evidence block.
    2. Construct a full redemption for the minted shares.
    3. Verify the account lock becomes a typed window preflight without broadcasting redeem.
    """
    # 1. Deposit Ethereum USDC into the reported TAU InfiniFi Pointsmax vault at its evidence block.
    assert tau_infinifi_ethereum_snapshot is None
    vault = IPORVault(
        tau_infinifi_ethereum_web3,
        VaultSpec(chain_id=1, vault_address=TAU_INFINIFI_POINTSMAX_ETHEREUM),
        features={ERC4626Feature.ipor_like},
    )
    manager = vault.get_deposit_manager()
    assert isinstance(manager, IPORDepositManager)
    owner = tau_infinifi_ethereum_web3.eth.accounts[0]
    deposit_amount = Decimal(1_001)
    usdc = vault.denomination_token
    funding_hash = usdc.transfer(owner, deposit_amount).transact({"from": USDC_WHALE[1]})
    assert_transaction_success_with_explanation(tau_infinifi_ethereum_web3, funding_hash)
    approval_hash = usdc.approve(vault.address, deposit_amount).transact({"from": owner})
    assert_transaction_success_with_explanation(tau_infinifi_ethereum_web3, approval_hash)
    manager.create_deposit_request(owner=owner, amount=deposit_amount).broadcast(from_=owner)
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares == TAU_INFINIFI_POINTSMAX_RAW_SHARES

    # 2. Construct a full redemption for the minted shares.
    block_before_refusal = tau_infinifi_ethereum_web3.eth.block_number
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=raw_shares)

    # 3. Verify the account lock becomes a typed window preflight without broadcasting redeem.
    error = exc_info.value
    assert error.preflight_result == "redemption_window_closed"
    assert error.decoded_error == "AccountIsLocked"
    assert error.error_selector == IPOR_ACCOUNT_IS_LOCKED_SELECTOR
    assert error.requested_raw_amount == raw_shares
    assert error.available_raw_amount == 0
    assert tau_infinifi_ethereum_web3.eth.block_number == block_before_refusal


def test_redemption_capacity_preflight_decodes_withdrawal_manager_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose the PlasmaVault withdrawal-manager error when the RPC provides it.

    1. Prepare an IPOR manager whose full redemption can only release a subset.
    2. Return the reported withdrawal-manager error from the exact redemption simulation.
    3. Verify the typed capacity result keeps the decoded custom-error selector.
    """
    # 1. Prepare an IPOR manager whose full redemption can only release a subset.
    owner = "0x0000000000000000000000000000000000000001"
    vault = SimpleNamespace(
        chain_id=1,
        address="0x0000000000000000000000000000000000000001",
        vault_contract=SimpleNamespace(functions=SimpleNamespace(balanceOf=lambda _owner: SimpleNamespace(call=lambda: 100))),
        get_redeem_function_selector=lambda: b"\x00\x00\x00\x00",
    )
    manager = object.__new__(IPORDepositManager)
    manager.vault = vault
    monkeypatch.setattr(manager, "_assert_immediate_access", lambda *_args: None)
    monkeypatch.setattr(manager, "fetch_redeemable_raw_shares", lambda _owner: 60)

    # 2. Return the reported withdrawal-manager error from the exact redemption simulation.
    monkeypatch.setattr(
        manager,
        "fetch_redeem_simulation",
        lambda _owner, _raw_shares: (False, IPOR_WITHDRAW_MANAGER_INVALID_SHARES_TO_RELEASE_SELECTOR + b"\x00" * 96),
    )

    # 3. Verify the typed capacity result keeps the decoded custom-error selector.
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner, raw_shares=100)

    error = exc_info.value
    assert error.preflight_result == "redemption_capacity_limited"
    assert error.decoded_error == "WithdrawManagerInvalidSharesToRelease"
    assert error.error_selector == IPOR_WITHDRAW_MANAGER_INVALID_SHARES_TO_RELEASE_SELECTOR
