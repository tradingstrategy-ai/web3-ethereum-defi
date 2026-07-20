"""Ethereum archive-node regression tests for FILQ bundle schemas."""

import os
from decimal import Decimal

import pytest

from eth_defi.chainlink.bundle_aggregator import fetch_chainlink_latest_bundle
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_BUNDLE_FIRST_SEEN_AT_BLOCK, FILQ_A_BUNDLE_PROXY_ADDRESS, FILQ_BUNDLE_AGGREGATOR_ADDRESS, FILQ_D_BUNDLE_FIRST_SEEN_AT_BLOCK, FILQ_D_BUNDLE_PROXY_ADDRESS

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run FILQ bundle integration tests")


@pytest.mark.parametrize(
    ("proxy_address", "block_number", "expected_decimals", "expected_nav"),
    (
        (FILQ_A_BUNDLE_PROXY_ADDRESS, FILQ_A_BUNDLE_FIRST_SEEN_AT_BLOCK, (0, 4, 9, 9, 0, 0), Decimal("100.0085")),
        (FILQ_D_BUNDLE_PROXY_ADDRESS, FILQ_D_BUNDLE_FIRST_SEEN_AT_BLOCK, (0, 2, 9, 9, 0, 0), Decimal(1)),
    ),
)
def test_filq_bundle_schema_at_first_report(proxy_address: str, block_number: int, expected_decimals: tuple[int, ...], expected_nav: Decimal) -> None:
    """Decode a known live FILQ report using its fixed-block proxy schema.

    :param proxy_address: FILQ Chainlink bundle proxy.
    :param block_number: First accepted report block for the share class.
    :param expected_decimals: Reviewed per-word decimal layout.
    :param expected_nav: Known NAV/share in the second bundle word.
    """

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    observation = fetch_chainlink_latest_bundle(web3, proxy_address, block_number)
    assert observation.aggregator_address.lower() == FILQ_BUNDLE_AGGREGATOR_ADDRESS
    assert observation.decimals == expected_decimals
    assert observation.decode_decimal(1) == expected_nav
