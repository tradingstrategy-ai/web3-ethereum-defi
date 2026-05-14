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
