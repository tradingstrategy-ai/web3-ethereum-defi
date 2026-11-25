"""Calculate ERC-4626 APY"""

import datetime
import os

import flaky
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.profit_and_loss import estimate_4626_recent_profitability, estimate_4626_profitability
from eth_defi.erc_4626.vault import ERC4626VaultInfo, ERC4626Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.timestamp import estimate_block_number_for_timestamp_by_findblock
from eth_defi.vault.base import VaultSpec


JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI") == "true"

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


@pytest.fixture(scope="module")
def ipor_usdc_address() -> HexAddress:
    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    return "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"


@pytest.fixture(scope="module")
def vault(web3, ipor_usdc_address) -> ERC4626VaultInfo:
    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    spec = VaultSpec(web3.eth.chain_id, ipor_usdc_address)
    return ERC4626Vault(web3, spec)


# RPC broken?
# RuntimeError: Failed to read timestamp for block 39935174, chain: 8453: ***'code': -32014, 'message': 'block not found with number 0x2615cc6'***
@flaky.flaky
def test_4626_recent_profitability(vault: ERC4626Vault) -> None:
    """Check returns against the observed share prices over a live seven-day window."""

    profitability_data = estimate_4626_recent_profitability(
        vault,
        lookback_window=datetime.timedelta(days=7),
    )

    start_price, end_price = profitability_data.get_share_price_range()
    assert start_price > 0 and end_price > 0
    expected = float(end_price / start_price - 1)
    assert profitability_data.calculate_profitability(annualise=False) == pytest.approx(expected)

    start_at, end_at = profitability_data.get_time_range()
    duration = (end_at - start_at).total_seconds()
    assert duration > 0
    year = datetime.timedelta(days=365).total_seconds()
    assert profitability_data.calculate_profitability(annualise=True) == pytest.approx(expected * year / duration)


# @pytest.mark.skipif(CI, reason="Getting Response: 429 ***error:Too many requests*** from FindBlock on Github")
@pytest.mark.skip(reason="FindBlock too unstable to work")
def test_4626_profitability_historical(
    vault: ERC4626Vault,
):
    """Calculate profitability for IPOR USDC Base vault.

    - Use historical time range
    """
    web3 = vault.web3
    chain_id = web3.eth.chain_id
    start_at = datetime.datetime(2025, 3, 1, tzinfo=None)
    end_at = datetime.datetime(2025, 5, 1, tzinfo=None)
    start_block_find = estimate_block_number_for_timestamp_by_findblock(chain_id, start_at)
    end_block_find = estimate_block_number_for_timestamp_by_findblock(chain_id, end_at)

    profitability_data = estimate_4626_profitability(
        vault,
        start_block=start_block_find.block_number,
        end_block=end_block_find.block_number,
    )

    yearly_profitability = profitability_data.calculate_profitability(annualise=True)
    assert yearly_profitability == pytest.approx(0.05319605877774247)
