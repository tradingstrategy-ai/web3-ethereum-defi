"""D2 Finance vault tests"""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.d2.vault import D2DepositManager, D2HistoricalReader, D2Vault, Epoch
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_FUNDING_PHASE,
    REDEMPTION_CLOSED_FUNDS_CUSTODIED,
)
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:392313989"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only D2 fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, 392_313_989)


@flaky.flaky
def test_d2(
    web3: Web3,
    tmp_path: Path,
):
    """Read D2 vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x75288264FDFEA8ce68e6D852696aB1cE2f3E5004",
    )

    assert isinstance(vault, D2Vault)
    assert vault.get_protocol_name() == "D2 Finance"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.has_custom_fees() is False
    assert vault.is_whitelisted_deposit() is True

    manager = vault.get_deposit_manager()
    assert isinstance(manager, D2DepositManager)
    with pytest.raises(VaultFlowUnavailable, match=DEPOSIT_CLOSED_FUNDING_PHASE) as exc_info:
        manager.estimate_deposit(web3.eth.accounts[0], Decimal("1"))
    assert exc_info.value.direction == "deposit"
    assert exc_info.value.phase == "preflight"
    assert exc_info.value.preflight_result == "deposit_closed"
    assert exc_info.value.next_open == datetime.datetime(2025, 11, 7, 8, 0)
    with pytest.raises(VaultFlowUnavailable, match=DEPOSIT_CLOSED_FUNDING_PHASE):
        manager.create_deposit_request(web3.eth.accounts[0], None, None, 1, True, True)
    with pytest.raises(VaultFlowUnavailable, match=REDEMPTION_CLOSED_FUNDS_CUSTODIED):
        manager.create_redemption_request(web3.eth.accounts[0], None, None, 1, True, True)
    settlement = manager.force_settle(None)
    assert settlement.settlement_required is False
    assert settlement.transaction_hashes == ()

    epoch_id = vault.fetch_current_epoch_id()
    assert epoch_id == 12

    epoch = vault.fetch_current_epoch_info()
    assert epoch == Epoch(funding_start=datetime.datetime(2025, 10, 6, 16, 0), epoch_start=datetime.datetime(2025, 10, 7, 16, 0), epoch_end=datetime.datetime(2025, 11, 7, 8, 0))

    # Verify D2-specific historical reader is returned
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, D2HistoricalReader)

    # Read vault state at the fork block using the historical reader
    block_number = web3.eth.block_number
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.timezone.utc).replace(tzinfo=None)

    calls = list(reader.construct_multicalls())
    call_results = [c.call_as_result(web3=web3, block_identifier=block_number) for c in calls]
    vault_read = reader.process_result(block_number, timestamp, call_results)

    assert vault_read.block_number == block_number
    assert vault_read.share_price == Decimal("1.393886")
    assert vault_read.total_assets == Decimal("3541406.718786")
    assert vault_read.total_supply == Decimal("2540670.540343")
    assert vault_read.max_deposit == Decimal("0")
    assert vault_read.max_redeem is None

    # D2-specific: at block 392_313_989 the vault is in epoch (trading), not funding, not redeemable
    assert vault_read.deposits_open is False
    assert vault_read.trading is True
    assert vault_read.redemption_open is False

    # Verify export round-trip
    exported = vault_read.export()
    assert exported["deposits_open"] == "false"
    assert exported["trading"] == "true"
    assert exported["redemption_open"] == "false"

    # Test deposit/redemption status methods
    deposit_reason = vault.fetch_deposit_closed_reason()
    redemption_reason = vault.fetch_redemption_closed_reason()
    deposit_next = vault.fetch_deposit_next_open()
    redemption_next = vault.fetch_redemption_next_open()

    # At block 392_313_989 the vault is in epoch (trading), not funding
    assert deposit_reason is not None
    assert DEPOSIT_CLOSED_FUNDING_PHASE in deposit_reason
    assert redemption_reason is not None
    assert REDEMPTION_CLOSED_FUNDS_CUSTODIED in redemption_reason

    # D2 should have timing info since it has epoch timing
    assert deposit_next is not None or "opens in" in (deposit_reason or "")
    assert redemption_next is not None or "opens in" in (redemption_reason or "")

    # Check maxDeposit and maxRedeem with address(0)
    # D2 uses these as global availability checks for epoch-based deposits/redemptions
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit == 0  # Deposits closed during trading epoch
    assert max_redeem == 0  # Redemptions closed during trading epoch
    assert vault.can_check_redeem() is False
