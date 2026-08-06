"""Characterise the published Shift extUSD vault on a fixed Base fork."""

import datetime
import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK
from eth_defi.tokenised_fund.shift.vault import ShiftVault

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

SHIFT_EXTUSD_BASE = "0x4cE3ec1b7B4FFb33A0B70c64a0560A3F341AA2E1"
SHIFT_LTPARA_BASE = "0xaf69Bf9ea9E0166498c0502aF5B5945980Ed1E0E"
SHIFT_LTPARA_PERFORMANCE_FEE = 0.05

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests"),
    pytest.mark.xdist_group("fork:base:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return a read-only shared Base fork at the canonical midnight block."""

    return anvil_fork_pool.get_web3(JSON_RPC_BASE, BASE_MIDNIGHT_BLOCK)


def test_shift_extusd_vault_reads_public_tvl_feed(web3: Web3) -> None:
    """Decode Shift's non-ERC-4626 share price using USDC decimal precision."""

    vault = create_vault_instance_autodetect(web3, SHIFT_EXTUSD_BASE)

    assert isinstance(vault, ShiftVault)
    assert vault.features == {ERC4626Feature.shift_like}
    assert vault.name == "Shift Extended Basis USD"
    assert vault.symbol == "extUSD"
    assert vault.denomination_token.symbol == "USDC"
    assert vault.fetch_share_price() == Decimal("1.026897")
    assert vault.fetch_total_supply() == Decimal("268424.523928582999214656")
    assert vault.fetch_total_assets() == Decimal("275644.3383486900961445326024")
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.fetch_info() == {
        "base_token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "timelock_seconds": 604800,
        "whitelist_enabled": False,
    }
    assert vault.fetch_deposit_closed_reason() == "Shift deposit requests require executor approval; no public deposit manager is implemented"
    assert vault.fetch_redemption_closed_reason() == "Shift withdrawals require executor batch resolution and a timelock; no public redemption manager is implemented"


def test_shift_ltpara_fee_and_stale_price_semantics(web3: Web3) -> None:
    """Retain a stale feed's zero price instead of inventing a share valuation."""

    vault = create_vault_instance_autodetect(web3, SHIFT_LTPARA_BASE)

    assert isinstance(vault, ShiftVault)
    assert vault.symbol == "ltPARA"
    assert vault.fetch_share_price() == Decimal(0)
    assert vault.fetch_total_assets() == Decimal("0E-18")
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == SHIFT_LTPARA_PERFORMANCE_FEE
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
