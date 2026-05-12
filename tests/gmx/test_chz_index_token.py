"""CHZ index-token regression guard — issue #67.

CHZ was the token that crash-looped the bot for 4 days: the old oracle-snapshot
filter excluded it because its Pyth feed lagged behind the GMX listing, producing
``ValueError: No GMX market found for index_token_address=0x5dB4...`` on every
trade signal.  These tests are the permanent regression guard.

Two coverage levels:
1. **REST-only** (no RPC) — confirms gmxinfra.io returns CHZ in ``/markets``.
2. **Full pipeline** (requires ``JSON_RPC_ARBITRUM``) — confirms the index token
   list that ``OrderArgumentParser`` builds from ``Markets.get_available_markets()``
   contains CHZ.
"""

from __future__ import annotations

import os

import pytest
from flaky import flaky
from web3 import Web3

from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.markets import _normalize_rest_market  # noqa: PLC2701
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser

_CHZ_INDEX_TOKEN = "0x5dB4692926C8ceebF6Da0995358Bbc438F3fd80C"


@flaky(max_runs=3, min_passes=1)
def test_chz_present_in_rest_markets_endpoint():
    """CHZ index token must appear in the REST /markets response.

    Pure REST call — no RPC, no GMXConfig.  Hits gmxinfra.io with automatic
    fallback to gmxinfra2.io and gmxapi.ai via the existing retry logic.
    """
    api = GMXAPI(chain="arbitrum")
    data = api.get_markets(use_cache=False)
    raw_list = data.get("markets", [])
    assert raw_list, "REST /markets returned an empty list"

    index_tokens = set()
    for entry in raw_list:
        normalised = _normalize_rest_market(entry)
        if normalised:
            index_tokens.add(normalised["index_token_address"].lower())

    assert _CHZ_INDEX_TOKEN.lower() in index_tokens, (
        f"CHZ ({_CHZ_INDEX_TOKEN}) not found in REST /markets index tokens. "
        f"Total index tokens: {len(index_tokens)}. "
        f"Sample (first 5): {sorted(index_tokens)[:5]}"
    )


@flaky(max_runs=3, min_passes=1)
@pytest.mark.skipif(
    not os.getenv("JSON_RPC_ARBITRUM"),
    reason="requires JSON_RPC_ARBITRUM",
)
def test_chz_present_in_order_argument_parser_index_tokens():
    """CHZ must be in the index-token list that OrderArgumentParser builds.

    Exercises the full pipeline:
    REST /markets → _normalize_rest_market → _process_markets
    → Markets.get_available_markets() → OrderArgumentParser.markets

    Requires a live Arbitrum RPC (JSON_RPC_ARBITRUM).
    """
    rpc = os.environ["JSON_RPC_ARBITRUM"]
    web3 = Web3(Web3.HTTPProvider(rpc))
    config = GMXConfig(web3)

    parser = OrderArgumentParser(config, is_increase=True)
    index_tokens = {v["index_token_address"].lower() for v in parser.markets.values()}

    assert _CHZ_INDEX_TOKEN.lower() in index_tokens, (
        f"CHZ ({_CHZ_INDEX_TOKEN}) not found in OrderArgumentParser.markets. "
        f"Total index tokens: {len(index_tokens)}. "
        f"Sample (first 5): {sorted(index_tokens)[:5]}"
    )
