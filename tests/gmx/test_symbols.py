"""Coverage for :mod:`eth_defi.gmx.symbols`.

The catalog and order paths both depend on ``SYMBOL_NORMALISE`` to strip the
``k`` prefix from 1000x-denomination tokens (kBONK, kSHIB, kPEPE, kFLOKI) and
to bridge versioned tokens (XAUT.v2) back to their canonical names.  Missing
entries cause silent bugs where freqtrade's ``BONK/USDC:USDC`` pair never
matches the on-chain ``kBONK`` market.
"""

from __future__ import annotations

import pytest


def test_symbol_normalise_covers_known_k_prefix_tokens():
    """Each token GMX exposes with a ``k`` prefix must round-trip to its bare
    symbol — otherwise the freqtrade↔on-chain bridge silently breaks for
    that pair.
    """
    from eth_defi.gmx.symbols import SYMBOL_NORMALISE

    expected = {
        "kBONK": "BONK",
        "kSHIB": "SHIB",
        "kPEPE": "PEPE",
        "kFLOKI": "FLOKI",
    }
    for raw, canonical in expected.items():
        assert SYMBOL_NORMALISE.get(raw) == canonical, f"{raw} must normalise to {canonical} for the catalog to find the market"


def test_symbol_normalise_covers_versioned_xaut():
    from eth_defi.gmx.symbols import SYMBOL_NORMALISE

    assert SYMBOL_NORMALISE.get("XAUT.v2") == "XAUT"


def test_symbol_normalise_passthrough_for_unmapped_symbols():
    """Bare symbols (no ``k`` prefix, no ``.v`` suffix) are not in the map.
    Lookup should miss — callers fall back to the raw symbol.
    """
    from eth_defi.gmx.symbols import SYMBOL_NORMALISE

    for unmapped in ("BTC", "ETH", "USDC", "WETH", "WBTC", "ARB", "SOL"):
        assert unmapped not in SYMBOL_NORMALISE


def test_deprecated_market_tokens_is_lowercase_only():
    """Lookups happen on ``address.lower()`` — entries with mixed case
    would silently miss.
    """
    from eth_defi.gmx.symbols import DEPRECATED_MARKET_TOKENS

    for addr in DEPRECATED_MARKET_TOKENS:
        assert addr == addr.lower(), f"DEPRECATED entry {addr!r} is not lowercase"


#: Retired pool -> active replacement, both live on Arbitrum (2026-11-04).
RETIRED_TO_ACTIVE_POOLS = {
    "0x587759c237acCa739bCE3911647BacF56C876E60": "0x2CeEafcA180B07C9d1d2f1086f8f59AAFd282832",  # ZEC
    "0xBcb8FE13d02b023e8f94f6881Cc0192fd918A5C0": "0x162C091263fBb3dfAf1D55DCC979533EB341DA5f",  # HYPE
    "0x0CCB4fAa6f1F1B30911619f1184082aB4E25813c": "0xc9ED746D613eEfC0F196658f6030e90482593004",  # XRP
    "0x63Dc80EE90F26363B3FCD609007CC9e14c8991BE": "0x54bc430A788DFFD4ae35e751E10899489f5a953f",  # NEAR
}


@pytest.mark.parametrize("retired, active", list(RETIRED_TO_ACTIVE_POOLS.items()))
def test_zero_cap_retired_pools_are_deprecated_and_replacements_are_not(retired: str, active: str):
    """The 0-cap pool is skipped; its USDG replacement must stay loadable."""
    from eth_defi.gmx.symbols import DEPRECATED_MARKET_TOKENS

    assert retired.lower() in DEPRECATED_MARKET_TOKENS
    assert active.lower() not in DEPRECATED_MARKET_TOKENS


def test_graphql_loader_drops_retired_zec_pool_instead_of_routing_to_it():
    """Live shape on 2026-11-04: retired ZEC pool (WBTC/USDC, caps 0) plus its
    single-token USDG replacement.

    Before the fix ``ZEC/USDC:USDC`` resolved to the retired pool and every
    open was keeper-cancelled with ``MaxOpenInterestExceeded(maxAllowed: 0)``.
    Now the USDC pair is absent and only the USDG market (``ZEC2``) remains.
    """
    from types import SimpleNamespace

    from eth_defi.gmx.ccxt.exchange import GMX

    zec_index = "0x" + "1" * 40
    usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    usdg = "0x004B506865409877C9fA29bfb1ebA929984B9bbC"
    wbtc = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"

    def info(market: str, long_token: str, short_token: str, cap: str) -> dict:
        return {
            "marketTokenAddress": market,
            "indexTokenAddress": zec_index,
            "longTokenAddress": long_token,
            "shortTokenAddress": short_token,
            "maxOpenInterestLong": cap,
            "maxOpenInterestShort": cap,
            "minCollateralFactor": "5000000000000000000000000000",
        }

    retired, replacement = next(iter(RETIRED_TO_ACTIVE_POOLS.items()))
    tokens = [
        {"address": zec_index, "symbol": "ZEC", "decimals": 18},
        {"address": usdc, "symbol": "USDC", "decimals": 6},
        {"address": usdg, "symbol": "USDG", "decimals": 6},
        {"address": wbtc, "symbol": "BTC", "decimals": 8},
    ]
    exchange = GMX.__new__(GMX)
    exchange.subsquid = SimpleNamespace(
        get_market_infos=lambda limit=200: [
            info(retired, wbtc, usdc, "0"),
            info(replacement, usdg, usdg, "5909000" + "0" * 30),
        ]
    )
    exchange.api = SimpleNamespace(get_tokens=lambda: {"tokens": tokens})

    markets = exchange._load_markets_from_graphql()

    assert "ZEC/USDC:USDC" not in markets
    assert all(m["info"]["market_token"].lower() != retired.lower() for m in markets.values())
