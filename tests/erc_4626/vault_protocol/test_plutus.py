"""Scan Euler vault metadata"""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from collections.abc import Iterator

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.plutus.deposit_redeem import PlutusAsyncDepositManager, PlutusRedemptionTicket
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusHistoricalReader, PlutusVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import REDEMPTION_CLOSED_BY_ADMIN, VaultTechnicalRisk
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

PLUTUS_HEDGE_VAULT = "0x58BfC95a864e18E8F3041D2FCD3418f48393fE6A"


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    """Read gmUSDC vault at a specific block"""
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=392_313_989)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_plutus(
    web3: Web3,
    tmp_path: Path,
):
    """Read Plutus vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x58BfC95a864e18E8F3041D2FCD3418f48393fE6A",
    )

    assert isinstance(vault, PlutusVault)

    assert vault.get_risk() == VaultTechnicalRisk.severe
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.12
    assert vault.has_custom_fees() is False
    assert vault.get_protocol_name() == "Plutus"

    # The Hedge deployment has been upgraded to the async-redemption contract.
    assert vault.is_async_redemption_deployment() is True
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "asynchronous",
    }
    manager = vault.get_deposit_manager()
    assert isinstance(manager, PlutusAsyncDepositManager)
    estimated_deposit = manager.estimate_deposit(web3.eth.accounts[0], Decimal("1"))
    expected_estimate = vault.share_token.convert_to_decimals(vault.vault_contract.functions.convertToShares(vault.denomination_token.convert_to_raw(Decimal("1"))).call())
    assert estimated_deposit == expected_estimate
    settlement = manager.force_settle(None)
    assert settlement.settlement_required is False
    assert settlement.transaction_hashes == ()

    # Verify Plutus-specific historical reader is returned
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, PlutusHistoricalReader)

    # Read vault state at the fork block using the historical reader
    block_number = web3.eth.block_number
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.timezone.utc).replace(tzinfo=None)

    calls = list(reader.construct_multicalls())
    call_results = [c.call_as_result(web3=web3, block_identifier=block_number) for c in calls]
    vault_read = reader.process_result(block_number, timestamp, call_results)

    assert vault_read.block_number == block_number
    assert vault_read.share_price == Decimal("1.158908")
    assert vault_read.total_assets == Decimal("178220.029349")
    assert vault_read.total_supply == Decimal("153782.593144")
    assert vault_read.max_deposit == Decimal("847420.85868")
    assert vault_read.max_redeem == Decimal("0")

    # Plutus derives deposit/redemption state from maxDeposit/maxRedeem
    # At block 392_313_989: maxDeposit > 0 so deposits open, maxRedeem == 0 so redemptions closed
    assert vault_read.deposits_open is True
    assert vault_read.redemption_open is False
    # Plutus does not track trading state
    assert vault_read.trading is None

    # Verify export round-trip
    exported = vault_read.export()
    assert exported["deposits_open"] == "true"
    assert exported["redemption_open"] == "false"
    assert exported["trading"] == ""

    # Test deposit/redemption status methods
    deposit_reason = vault.fetch_deposit_closed_reason()
    redemption_reason = vault.fetch_redemption_closed_reason()
    deposit_next = vault.fetch_deposit_next_open()
    redemption_next = vault.fetch_redemption_next_open()

    # At block 392_313_989: deposits open (maxDeposit > 0), redemptions closed (maxRedeem == 0)
    assert deposit_reason is None  # Deposits open
    assert redemption_reason.startswith(REDEMPTION_CLOSED_BY_ADMIN)  # Includes diagnostic info

    # Plutus has no timing info (manually controlled)
    assert deposit_next is None
    assert redemption_next is None

    # Check maxDeposit and maxRedeem with address(0)
    # Plutus uses these as global availability checks
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit > 0  # Deposits are open
    assert max_redeem == 0  # Redemptions are closed
    assert vault.can_check_redeem() is True


@pytest.fixture(scope="module")
def plutus_midnight_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the canonical fixed Arbitrum midnight-block fork, USDC whale unlocked."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_ARBITRUM,
        ARBITRUM_MIDNIGHT_BLOCK,
        unlocked_addresses=[USDC_WHALE[42161]],
    )


@pytest.fixture(scope="module")
def midnight_web3(plutus_midnight_fork: AnvilLaunch) -> Web3:
    """Connect to the shared deterministic Plutus midnight-block fork."""
    return create_multi_provider_web3(plutus_midnight_fork.json_rpc_url)


@pytest.fixture
def plutus_snapshot(plutus_midnight_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the shared fork after a mutating deposit/redeem test."""
    yield from evm_snapshot_revert(plutus_midnight_fork)


@flaky.flaky
@pytest.mark.xdist_group("fork:arbitrum:midnight")
def test_plutus_async_redemption_lifecycle(midnight_web3: Web3, plutus_snapshot: None) -> None:
    """Deposit synchronously, then request an asynchronous redemption on the Hedge vault.

    Validates the ERC-7540-style flow: ``requestRedeem`` produces a pending
    ticket, the request is not yet claimable, and forced settlement is refused
    with a precise reason because operator fulfilment is role-gated.
    """
    vault = create_vault_instance_autodetect(midnight_web3, vault_address=PLUTUS_HEDGE_VAULT)
    assert isinstance(vault, PlutusVault)
    assert vault.is_async_redemption_deployment() is True

    manager = vault.get_deposit_manager()
    assert isinstance(manager, PlutusAsyncDepositManager)

    owner = midnight_web3.eth.accounts[0]
    usdc = fetch_erc20_details(midnight_web3, USDC_NATIVE_TOKEN[42161])
    amount = Decimal(100)

    funding_hash = usdc.contract.functions.transfer(owner, usdc.convert_to_raw(amount)).transact({"from": USDC_WHALE[42161]})
    assert_transaction_success_with_explanation(midnight_web3, funding_hash)
    approve_hash = usdc.approve(vault.address, amount).transact({"from": owner})
    assert_transaction_success_with_explanation(midnight_web3, approve_hash)

    # Synchronous deposit.
    manager.create_deposit_request(owner=owner, amount=amount).broadcast(from_=owner)
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0

    # Asynchronous redemption request.
    request = manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    assert len(request.funcs) == 1
    ticket = request.broadcast(from_=owner)
    assert isinstance(ticket, PlutusRedemptionTicket)
    assert ticket.raw_shares == raw_shares
    assert ticket.request_id >= 0

    # Request is pending, not yet claimable, and restart-safe.
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.pending
    assert manager.can_finish_redeem(ticket) is False
    assert manager.reconstruct_redemption_ticket(manager.serialize_redemption_ticket(ticket)) == ticket

    # Operator fulfilment is role-gated; forced settlement is refused precisely.
    assert manager.force_settle(None).settlement_required is False
    with pytest.raises(UnsupportedVaultSimulation, match="role-gated"):
        manager.force_settle(ticket)
