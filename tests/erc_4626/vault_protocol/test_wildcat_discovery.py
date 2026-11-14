"""Discover a Wildcat market from its registry event through Hypersync."""

import os

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None or HYPERSYNC_API_KEY is None, reason="JSON_RPC_ETHEREUM and HYPERSYNC_API_KEY needed to run these tests")

#: Wintermute's USDC market and its ``MarketAdded`` block.
WINTERMUTE_USDC = "0xc9499006a149c553d18171747ed19aa7c6dd19e2"
MARKET_ADDED_BLOCK = 21_989_438


def test_wildcat_market_discovery() -> None:
    """A registry ``MarketAdded`` event creates a direct Wildcat detection without probing."""
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    hypersync_config = configure_hypersync_from_env(web3)
    discover = HypersyncVaultDiscover(web3, MultiProviderWeb3Factory(JSON_RPC_ETHEREUM), hypersync_config.hypersync_client)

    report = discover.scan_vaults(MARKET_ADDED_BLOCK, MARKET_ADDED_BLOCK + 1, display_progress=False, hardcoded_lead_sources=())

    lead = report.leads[WINTERMUTE_USDC]
    assert lead.wildcat_market_candidate.created_block == MARKET_ADDED_BLOCK
    detection = report.detections[WINTERMUTE_USDC]
    assert detection.features == {ERC4626Feature.wildcat_like}
    assert detection.first_seen_at_block == MARKET_ADDED_BLOCK
