"""Scan Euler vault metadata"""

import datetime
import os
from decimal import Decimal
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusHistoricalReader, PlutusVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.erc_4626.vault_protocol.umami.vault import UmamiVault
from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.vault.base import REDEMPTION_CLOSED_BY_ADMIN, VaultTechnicalRisk

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Shared with the other Arbitrum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Arbitrum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


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

    # Values read at the pinned Arbitrum midnight fork block (see fork_blocks.py);
    # update them if the canonical block is bumped.
    assert vault_read.block_number == block_number
    assert vault_read.share_price == Decimal("1.265705")
    assert vault_read.total_assets == Decimal("241950.925652")
    assert vault_read.total_supply == Decimal("191158.930808")
    assert vault_read.max_deposit == Decimal("805419.231092")
    assert vault_read.max_redeem == Decimal("0")

    # Plutus derives deposit/redemption state from maxDeposit/maxRedeem:
    # maxDeposit > 0 so deposits open, maxRedeem == 0 so redemptions closed.
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

    # At the pinned midnight block: deposits open (maxDeposit > 0), redemptions closed (maxRedeem == 0)
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
