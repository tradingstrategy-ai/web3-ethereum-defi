"""D2 Finance vault tests"""

import datetime
import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from decimal import Decimal

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.d2.vault import D2HistoricalReader, D2Vault, Epoch
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_FUNDING_PHASE,
    REDEMPTION_CLOSED_FUNDS_CUSTODIED,
    VaultTechnicalRisk,
)

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


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
