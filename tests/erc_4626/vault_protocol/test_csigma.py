"""Test cSigma Finance vault metadata."""

import os
from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import flaky
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.csigma.deposit_redeem import CSIGMA_WITHDRAWAL_PENDING_SELECTOR, CsigmaDepositManager
from eth_defi.erc_4626.vault_protocol.csigma.vault import CSIGMA_V2_POOL_ADDRESS, SECONDS_PER_DAY, CsigmaVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, fund_erc20_on_anvil, make_anvil_custom_rpc_request
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

CSIGMA_USD_VAULT = "0xd5d097f278a735d0a3c609deee71234cac14b47e"

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EXPECTED_V2_DEPOSITED_RAW_SHARES = 94_348_140
EXPECTED_SUPQPV_DEPOSITED_RAW_SHARES = 94_445_037
EXPECTED_CSUPERIOR_QUEUE_DUE_RAW_SHARES = 41_603_916_251
CSIGMA_PAUSE_FORK_BLOCK = 25_598_869
EXPECTED_CSIGMA_PAUSE_START = 0
EXPECTED_CSIGMA_PAUSE_DURATION = 1_800

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        fork_block_number=21_900_000,
        unlocked_addresses=[USDC_WHALE[1]],
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_csigma(
    web3: Web3,
):
    """Read cSigma Finance vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xd5d097f278a735d0a3c609deee71234cac14b47e",
    )

    assert isinstance(vault, CsigmaVault)
    assert vault.get_protocol_name() == "cSigma Finance"
    assert vault.features == {ERC4626Feature.csigma_like}
    assert vault.is_whitelisted_deposit() is False

    # Fees are not yet known for cSigma
    assert vault.get_management_fee("latest") == 0
    assert vault.get_performance_fee("latest") == 0
    assert vault.has_custom_fees() is False
    # cSigma USD is a reserve-limited synchronous pool: it is in the synchronous
    # address set, so it advertises the capacity-aware manager and capability.
    assert isinstance(vault.get_deposit_manager(), CsigmaDepositManager)
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "synchronous",
    }

    # Check vault link
    assert vault.get_link() == "https://edge.csigma.finance/"

    # cSigma doesn't implement standard maxDeposit/maxRedeem (returns empty data)
    # so we cannot use address(0) checks for this vault
    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


def test_csigma_paused_deposit_is_a_guard_validation_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat cSigma's global Pausable state as a closed-deposit trigger.

    The ABI loader is mocked because this unit test isolates closure semantics;
    the production ABI binding is covered by the fixed-fork pause-window test.

    1. Return a cSigma binding whose global ``paused()`` state is true.
    2. Fetch the protocol-specific deposit closure reason.
    3. Assert the vault reports a closed deposit without reading the daily window.
    """
    # 1. Return a cSigma binding whose global paused() state is true.
    paused_contract = MagicMock()
    paused_contract.functions.paused().call.return_value = True
    web3 = SimpleNamespace()
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.csigma.vault.get_deployed_contract", lambda *_args: paused_contract)
    vault = object.__new__(CsigmaVault)
    vault.web3 = web3
    vault.spec = VaultSpec(chain_id=1, vault_address=CSIGMA_USD_VAULT)

    # 2. Fetch the protocol-specific deposit closure reason.
    assert vault.can_check_deposit() is False

    # 3. Assert the vault reports a closed deposit without reading the daily window.
    assert vault.fetch_deposit_closed_reason() == "cSigma deposits paused by governance"


@pytest.fixture(scope="module")
def csigma_midnight_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the production-rerun fork at cSigma's pause boundary."""
    return anvil_fork_pool.get_launch(JSON_RPC_ETHEREUM, CSIGMA_PAUSE_FORK_BLOCK)


@pytest.fixture(scope="module")
def csigma_midnight_web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the cSigma production-rerun fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, CSIGMA_PAUSE_FORK_BLOCK)


@pytest.fixture
def csigma_midnight_snapshot(csigma_midnight_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the pause-boundary fork after the closure test."""
    yield from evm_snapshot_revert(csigma_midnight_fork)


@pytest.mark.xdist_group("fork:ethereum:midnight")
def test_csigma_daily_pause_window_is_detected_before_deposit(
    csigma_midnight_web3: Web3,
    csigma_midnight_snapshot: None,
) -> None:
    """Detect cSigma's daily pause window independently of paused().

    1. Advance the fixed fork across the protocol's pause boundary.
    2. Pin the deployed pause configuration and current second.
    3. Read closure through the protocol-specific pause-window state.
    4. Assert request construction returns a typed closed-deposit refusal.
    """
    del csigma_midnight_snapshot

    # 1. Advance the fixed fork across the protocol's pause boundary.
    make_anvil_custom_rpc_request(csigma_midnight_web3, "evm_increaseTime", [1])
    make_anvil_custom_rpc_request(csigma_midnight_web3, "evm_mine", [])
    for vault_address in [
        CSIGMA_USD_VAULT,
        "0x438982ea288763370946625fd76c2508ee1fb229",
    ]:
        vault = create_vault_instance_autodetect(csigma_midnight_web3, vault_address)
        assert isinstance(vault, CsigmaVault)

        # 2. Pin the deployed pause configuration and current second.
        assert vault.vault_contract.functions.paused().call() is False
        assert vault.vault_contract.functions.pauseStartTime().call() == EXPECTED_CSIGMA_PAUSE_START
        assert vault.vault_contract.functions.pauseDuration().call() == EXPECTED_CSIGMA_PAUSE_DURATION
        current_second = int(csigma_midnight_web3.eth.get_block("latest")["timestamp"]) % SECONDS_PER_DAY
        assert EXPECTED_CSIGMA_PAUSE_START <= current_second <= EXPECTED_CSIGMA_PAUSE_START + EXPECTED_CSIGMA_PAUSE_DURATION

        # 3. Read closure through the protocol-specific pause-window state.
        assert vault.fetch_deposit_closed_reason() == "cSigma deposits paused during daily window"

        # 4. Refuse request construction before any approval or deposit transaction.
        manager = vault.get_deposit_manager()
        with pytest.raises(VaultFlowUnavailable) as exc_info:
            manager.create_deposit_request(
                owner=csigma_midnight_web3.eth.accounts[0],
                raw_amount=1_000_000,
                check_enough_token=False,
            )
        assert exc_info.value.preflight_result == "deposit_closed"
        assert exc_info.value.available_raw_amount == 0


@flaky.flaky
def test_csigma_v2_pool(
    web3: Web3,
):
    """Read cSigma Finance CsigmaV2Pool vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=CSIGMA_V2_POOL_ADDRESS,
    )

    assert isinstance(vault, CsigmaVault)
    assert vault.get_protocol_name() == "cSigma Finance"
    assert vault.features == {ERC4626Feature.csigma_like}
    assert vault.is_whitelisted_deposit() is False

    # Fees are not yet known for cSigma
    assert vault.get_management_fee("latest") == 0
    assert vault.get_performance_fee("latest") == 0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://edge.csigma.finance/"

    # The V2 pool's owner-specific capacity views cannot be validated through a
    # zero-address probe, so generic ERC-4626 capability checks remain disabled.
    assert vault.can_check_redeem() is False
    assert "WithdrawalPending" in {item["name"] for item in vault.vault_contract.abi if item["type"] == "error"}


@flaky.flaky
def test_csigma_v2_pool_deposit_then_refuses_redemption_when_queue_state_is_unavailable(web3: Web3) -> None:
    """Fail closed when the historical fork cannot read cSuperior's queue state.

    The fork predates the current withdrawal-manager deployment, so its
    authoritative queue state cannot be read. A deposit remains synchronous,
    but the manager must not guess that a full or partial redemption is safe:
    it returns the typed zero-capacity refusal before building ``redeem()``.
    """
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=CSIGMA_V2_POOL_ADDRESS,
    )
    assert isinstance(vault, CsigmaVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, CsigmaDepositManager)
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "synchronous",
    }

    owner = web3.eth.accounts[0]
    deposit_amount = Decimal(100)
    usdc = vault.denomination_token
    funding_hash = usdc.transfer(owner, deposit_amount).transact({"from": USDC_WHALE[1]})
    assert_transaction_success_with_explanation(web3, funding_hash)
    approval_hash = usdc.approve(vault.address, deposit_amount).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)

    assert manager.can_create_deposit_request(owner) is True
    deposit_ticket = manager.create_deposit_request(owner=owner, amount=deposit_amount).broadcast(from_=owner)
    deposit_analysis = manager.analyse_deposit(deposit_ticket.tx_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == deposit_amount
    assert deposit_analysis.share_count == pytest.approx(Decimal("94.34814"))
    assert manager.force_settle(None).settlement_required is False

    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares == EXPECTED_V2_DEPOSITED_RAW_SHARES
    assert manager.can_create_redemption_request(owner) is False
    block_before_refusal = web3.eth.block_number
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    error = exc_info.value
    assert error.available_raw_amount == 0
    assert error.decoded_error == "WithdrawalPending"
    assert error.error_selector == CSIGMA_WITHDRAWAL_PENDING_SELECTOR
    assert error.preflight_result == "redemption_capacity_limited"
    assert web3.eth.block_number == block_before_refusal
    assert vault.share_token.fetch_raw_balance_of(owner) == raw_shares
    assert manager.force_settle(None).settlement_required is False


@flaky.flaky
def test_csigma_v2_pool_rejects_deposit_above_immediate_capacity(web3: Web3) -> None:
    """Reject an amount cSigma reports as unavailable before broadcast."""
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=CSIGMA_V2_POOL_ADDRESS,
    )
    assert isinstance(vault, CsigmaVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, CsigmaDepositManager)
    owner = web3.eth.accounts[1]
    available_raw_assets = manager.fetch_depositable_raw_assets(owner)

    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_deposit_request(owner=owner, raw_amount=available_raw_assets + 1)

    error = exc_info.value
    assert error.reason == "cSigma deposit exceeds immediate asset capacity"
    assert error.requested_raw_amount == available_raw_assets + 1
    assert error.available_raw_amount == available_raw_assets


@flaky.flaky
def test_csigma_v2_pool_rejects_redemption_above_immediate_capacity(web3: Web3) -> None:
    """Reject an amount cSigma reports as unavailable before broadcast."""
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=CSIGMA_V2_POOL_ADDRESS,
    )
    assert isinstance(vault, CsigmaVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, CsigmaDepositManager)
    owner = web3.eth.accounts[1]
    available_raw_shares = manager.fetch_redeemable_raw_shares(owner)

    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=available_raw_shares + 1)

    error = exc_info.value
    assert error.reason == "cSigma redemption exceeds immediate share capacity"
    assert error.requested_raw_amount == available_raw_shares + 1
    assert error.available_raw_amount == available_raw_shares
    # The over-capacity case is exactly what reverts WithdrawalPending onchain
    # and queues the excess off-chain; the typed refusal carries that decode.
    assert error.decoded_error == "WithdrawalPending"
    assert error.preflight_result == "redemption_capacity_limited"
    assert error.error_selector == CSIGMA_WITHDRAWAL_PENDING_SELECTOR


@flaky.flaky
def test_csigma_supqpv(
    web3: Web3,
):
    """Verify the cSuperior Quality Private Credit synchronous lifecycle.

    The fixed fork proves both request-capacity failures and a complete deposit
    and redemption against the second cSigma pool advertised by the adapter.

    :param web3:
        Web3 client connected to the deterministic Ethereum fork.
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x50d59b785df23728d9948804f8ca3543237a1495",
    )

    assert isinstance(vault, CsigmaVault)
    assert vault.get_protocol_name() == "cSigma Finance"
    assert vault.features == {ERC4626Feature.csigma_like}

    # Fees are not yet known for cSigma
    assert vault.get_management_fee("latest") == 0
    assert vault.get_performance_fee("latest") == 0
    assert vault.has_custom_fees() is False
    assert isinstance(vault.get_deposit_manager(), CsigmaDepositManager)
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "synchronous",
    }
    manager = vault.get_deposit_manager()
    owner = web3.eth.accounts[2]
    available_raw_assets = manager.fetch_depositable_raw_assets(owner)
    with pytest.raises(VaultFlowUnavailable, match="deposit exceeds immediate asset capacity"):
        manager.create_deposit_request(owner=owner, raw_amount=available_raw_assets + 1)
    available_raw_shares = manager.fetch_redeemable_raw_shares(owner)
    preflight = manager.fetch_redemption_preflight(owner, available_raw_shares + 1)
    assert preflight.available is False
    assert preflight.available_raw_shares == available_raw_shares
    assert preflight.reason == "redemption_capacity_limited"

    deposit_amount = Decimal(100)
    denomination_token = vault.denomination_token
    raw_deposit_amount = denomination_token.convert_to_raw(deposit_amount)
    fund_erc20_on_anvil(web3, denomination_token.address, owner, raw_deposit_amount)
    approval_hash = denomination_token.approve(vault.address, deposit_amount).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)

    deposit_ticket = manager.create_deposit_request(owner=owner, raw_amount=raw_deposit_amount).broadcast(from_=owner)
    deposit_analysis = manager.analyse_deposit(deposit_ticket.tx_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == deposit_amount
    assert deposit_analysis.share_count == vault.share_token.convert_to_decimals(EXPECTED_SUPQPV_DEPOSITED_RAW_SHARES)

    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares == EXPECTED_SUPQPV_DEPOSITED_RAW_SHARES
    redemption_ticket = manager.create_redemption_request(owner=owner, raw_shares=raw_shares).broadcast(from_=owner)
    redemption_analysis = manager.analyse_redemption(redemption_ticket.tx_hash, redemption_ticket)
    assert redemption_analysis.share_count == vault.share_token.convert_to_decimals(EXPECTED_SUPQPV_DEPOSITED_RAW_SHARES)
    assert redemption_analysis.denomination_amount == Decimal("99.999999")
    assert vault.share_token.fetch_raw_balance_of(owner) == 0

    # Check vault link
    assert vault.get_link() == "https://edge.csigma.finance/"

    # cSigma doesn't implement standard maxDeposit/maxRedeem (returns empty data)
    assert vault.can_check_redeem() is False


@pytest.fixture(scope="module")
def midnight_web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared deterministic cSigma midnight fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


def test_csuperior_full_fill_capacity_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow a whole redemption only when the queue gate and reserve allow it.

    The live cSuperior fork has queue debt, so it correctly offers zero
    capacity. This isolated boundary check covers the complementary state: a
    zero queue debt and 75 raw shares of reserve-backed capacity accepts 75,
    but refuses 76 rather than silently offering a partial onchain fill.
    """
    owner: HexAddress = "0x1111111111111111111111111111111111111111"
    immediate_capacity = 75
    vault_contract = MagicMock()
    vault_contract.functions.maxWithdraw(owner).call.return_value = 80
    vault_contract.functions.convertToShares(80).call.return_value = immediate_capacity
    manager = CsigmaDepositManager.__new__(CsigmaDepositManager)
    manager.vault = SimpleNamespace(
        address=CSIGMA_V2_POOL_ADDRESS,
        share_token=SimpleNamespace(fetch_raw_balance_of=lambda _owner: 100),
        vault_contract=vault_contract,
    )
    monkeypatch.setattr(manager, "fetch_withdrawal_manager_due_raw_shares", lambda: 0)

    assert manager.fetch_redeemable_raw_shares(owner) == immediate_capacity
    assert manager.fetch_redemption_preflight(owner, immediate_capacity).available is True
    refused = manager.fetch_redemption_preflight(owner, immediate_capacity + 1)
    assert refused.available is False
    assert refused.available_raw_shares == immediate_capacity
    assert refused.reason == "redemption_capacity_limited"


@flaky.flaky
@pytest.mark.xdist_group("fork:ethereum:midnight")
def test_csuperior_queue_blocks_partial_user_redemption(midnight_web3: Web3) -> None:
    """Refuse cSuperior's offchain partial-fill path before a redeem broadcast.

    At the pinned block the withdrawal manager has outstanding queue debt, so
    its verified ``_withdraw`` guard rejects every user redemption even though
    the pool advertises gross idle reserve through ``maxRedeem``. Any positive
    share request must therefore be preflight-refused in full rather than
    partially filled and queued by an offchain actor.
    """
    vault = create_vault_instance_autodetect(midnight_web3, vault_address=CSIGMA_V2_POOL_ADDRESS)
    assert isinstance(vault, CsigmaVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, CsigmaDepositManager)
    assert manager.fetch_withdrawal_manager_due_raw_shares() == EXPECTED_CSUPERIOR_QUEUE_DUE_RAW_SHARES

    owner = midnight_web3.eth.accounts[4]
    requested_raw_shares = 1
    block_before_refusal = midnight_web3.eth.block_number
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=requested_raw_shares)

    error = exc_info.value
    assert error.decoded_error == "WithdrawalPending"
    assert error.error_selector == CSIGMA_WITHDRAWAL_PENDING_SELECTOR
    assert error.preflight_result == "redemption_capacity_limited"
    assert error.direction == "redeem"
    assert error.phase == "preflight"
    assert error.requested_raw_amount == requested_raw_shares
    assert error.available_raw_amount == 0
    assert midnight_web3.eth.block_number == block_before_refusal


@flaky.flaky
@pytest.mark.xdist_group("fork:ethereum:midnight")
def test_csigma_usd_redemption_capacity_preflight(midnight_web3: Web3) -> None:
    """cSigma USD refuses an over-capacity redemption with a typed WithdrawalPending error.

    cSigma USD (csUSD) is a reserve-limited synchronous pool. A redemption
    beyond the immediate ``maxRedeem`` capacity is queued off-chain and reverts
    ``WithdrawalPending`` onchain; the manager must refuse it before broadcast
    with a typed :class:`VaultFlowUnavailable` carrying the decoded error. This
    is verified at the current-state midnight block (the pool was not deployed
    at the 21.9M lifecycle block, and the fixed timestamp is inside its daily
    pause window, so only the read-only capacity preflight is exercised here).
    """
    vault = create_vault_instance_autodetect(midnight_web3, vault_address=CSIGMA_USD_VAULT)
    assert isinstance(vault, CsigmaVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, CsigmaDepositManager)
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "synchronous",
    }

    # A fresh account holds no shares, so maxRedeem is zero and any positive
    # redemption exceeds the immediate capacity.
    owner = midnight_web3.eth.accounts[3]
    available_raw_shares = manager.fetch_redeemable_raw_shares(owner)
    assert available_raw_shares == 0
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=1)
    error = exc_info.value
    assert error.reason == "cSigma redemption exceeds immediate share capacity"
    assert error.requested_raw_amount == 1
    assert error.available_raw_amount == 0
    assert error.decoded_error == "WithdrawalPending"
    assert error.preflight_result == "redemption_capacity_limited"
    assert error.error_selector == CSIGMA_WITHDRAWAL_PENDING_SELECTOR
