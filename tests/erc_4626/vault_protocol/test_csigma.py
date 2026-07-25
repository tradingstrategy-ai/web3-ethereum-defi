"""Test cSigma Finance vault metadata."""

import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.csigma.deposit_redeem import CSIGMA_WITHDRAWAL_PENDING_SELECTOR, CsigmaDepositManager
from eth_defi.erc_4626.vault_protocol.csigma.vault import CSIGMA_V2_POOL_ADDRESS, CsigmaVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, fund_erc20_on_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

CSIGMA_USD_VAULT = "0xd5d097f278a735d0a3c609deee71234cac14b47e"

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EXPECTED_V2_DEPOSITED_RAW_SHARES = 94_348_140
EXPECTED_SUPQPV_DEPOSITED_RAW_SHARES = 94_445_037

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
    assert vault.can_check_redeem() is False


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

    # Fees are not yet known for cSigma
    assert vault.get_management_fee("latest") == 0
    assert vault.get_performance_fee("latest") == 0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://edge.csigma.finance/"

    # The V2 pool's owner-specific capacity views cannot be validated through a
    # zero-address probe, so generic ERC-4626 capability checks remain disabled.
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_csigma_v2_pool_deposit_and_redeem_lifecycle(web3: Web3) -> None:
    """Complete one immediate cSigma deposit and redemption lifecycle.

    The selected fork exposes immediate liquidity for this representative V2
    pool. The same manager uses ``force_settle(None)`` for both synchronous
    operations.
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
    assert manager.can_create_redemption_request(owner) is True
    redemption_ticket = manager.create_redemption_request(owner=owner, raw_shares=raw_shares).broadcast(from_=owner)
    redemption_analysis = manager.analyse_redemption(redemption_ticket.tx_hash, redemption_ticket)
    assert redemption_analysis.share_count == pytest.approx(Decimal("94.34814"))
    assert redemption_analysis.denomination_amount == pytest.approx(Decimal("99.999999"))
    assert vault.share_token.fetch_raw_balance_of(owner) == 0
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
    """Shared fixed Ethereum midnight-block fork for the current-state cSigma USD checks."""
    return create_multi_provider_web3(anvil_fork_pool.get_launch(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK).json_rpc_url)


@flaky.flaky
@pytest.mark.xdist_group("fork:ethereum:midnight")
def test_csigma_usd_redemption_capacity_preflight(midnight_web3: Web3) -> None:
    """cSigma USD refuses an over-capacity redemption with a typed WithdrawalPending error.

    cSigma USD (csUSD) is a reserve-limited synchronous pool. A redemption
    beyond the immediate ``maxRedeem`` capacity is queued off-chain and reverts
    ``WithdrawalPending`` onchain; the manager must refuse it before broadcast
    with a typed :class:`VaultFlowUnavailable` carrying the decoded error. This
    is verified at the current-state midnight block (the pool was not deployed
    at the 21.9M lifecycle block, and its deposits are ``Pausable``-paused now,
    so only the read-only capacity preflight is exercised here).
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
    assert error.error_selector == CSIGMA_WITHDRAWAL_PENDING_SELECTOR
