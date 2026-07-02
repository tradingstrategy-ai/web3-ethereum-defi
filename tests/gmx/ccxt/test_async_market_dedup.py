"""Offline tests for async GMX market-loader synthetic-pool disambiguation.

Regression for the live incident where a Freqtrade GMX vault's BTC/USDC:USDC
OPEN orders were keeper-cancelled with ``InvalidCollateralTokenForMarket``.

Root cause: the async GraphQL market loader
(:meth:`eth_defi.gmx.ccxt.async_support.exchange.GMX._load_markets_from_graphql`)
lacked the synthetic-market ``"2"``-suffix disambiguation the **sync** loader
has (``eth_defi/gmx/ccxt/exchange.py``). A synthetic single-sided BTC pool
(``long_token == short_token``) collided with the real USDC-paired pool under
one ``BTC/USDC:USDC`` symbol; whichever the GraphQL response returned first
silently won. Freqtrade copies the async-loaded map onto the sync order-placing
client on every hourly reload, so the collision winner decided which pool live
orders targeted — sometimes a pool that does not accept USDC collateral.

These tests drive the real loader methods with mocked data sources (no RPC), so
they are fast and deterministic. Live end-to-end coverage lives in
``test_market_disambiguation.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX

# Distinct lowercase addresses used across the crafted payloads.
_BTC_INDEX = "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f"  # BTC index token
_USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
_WBTC = "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f"  # long token of the real pool
_TBTC = "0x6c84a8f1c29108f47a79964b5fe888d4f4d0de40"  # synthetic single-sided token
_REAL_BTC_MARKET = "0x47c031236e19d024b42f8ae6780e44a573170703"  # WBTC-USDC pool
_SYNTH_BTC_MARKET = "0xd62068697bcc92af253225676d618b0c9f17c663"  # synthetic pool


def _tokens_payload() -> list[dict]:
    """GMX /tokens response mapping the BTC index token to the ``BTC`` symbol."""
    return [
        {"address": _BTC_INDEX, "symbol": "BTC", "decimals": 8},
        {"address": _USDC, "symbol": "USDC", "decimals": 6},
    ]


def _market_infos_synthetic_first() -> list[dict]:
    """Two BTC markets sharing the BTC index token, synthetic listed FIRST.

    This is the poisoning order: pre-fix, the synthetic pool claims
    ``BTC/USDC:USDC`` and the real USDC-paired pool is dropped by the dedup
    guard.
    """
    return [
        {
            # Synthetic single-sided pool — long == short.
            "indexTokenAddress": _BTC_INDEX,
            "marketTokenAddress": _SYNTH_BTC_MARKET,
            "longTokenAddress": _TBTC,
            "shortTokenAddress": _TBTC,
            "minCollateralFactor": "5000000000000000000000000000000",
        },
        {
            # Real USDC-collateral pool — long != short.
            "indexTokenAddress": _BTC_INDEX,
            "marketTokenAddress": _REAL_BTC_MARKET,
            "longTokenAddress": _WBTC,
            "shortTokenAddress": _USDC,
            "minCollateralFactor": "5000000000000000000000000000000",
        },
    ]


async def _run_graphql_loader(market_infos: list[dict]) -> dict:
    """Construct an offline AsyncGMX and drive ``_load_markets_from_graphql``."""
    gmx = AsyncGMX({})
    subsquid = AsyncMock()
    subsquid.get_market_infos = AsyncMock(return_value=market_infos)
    gmx.subsquid = subsquid
    gmx._fetch_tokens_async = AsyncMock(return_value=_tokens_payload())
    # Skip the on-chain DataStore filter (Multicall3 RPC) — identity in the test.
    gmx._filter_datastore_disabled_markets = lambda markets: markets
    return await gmx._load_markets_from_graphql()


@pytest.mark.asyncio
async def test_graphql_loader_synthetic_first_does_not_poison_canonical_symbol() -> None:
    """BTC/USDC:USDC must resolve to the REAL USDC pool even when the synthetic
    single-sided pool is listed first.

    The synthetic pool is disambiguated to ``BTC2`` and then dropped by
    ``EXCLUDED_SYMBOLS`` (like the sync loader), so it can never claim the
    canonical ``BTC/USDC:USDC`` symbol. Pre-fix, the synthetic (first) claimed
    ``BTC/USDC:USDC`` and the real pool was dedup-skipped — the exact poisoning
    that reached the sync order client via freqtrade's hourly reload.
    """
    markets = await _run_graphql_loader(_market_infos_synthetic_first())

    assert "BTC/USDC:USDC" in markets, markets.keys()
    # Canonical symbol resolves to the REAL USDC-paired pool, not the synthetic.
    assert markets["BTC/USDC:USDC"]["info"]["market_token"] == _REAL_BTC_MARKET
    # The synthetic single-sided pool is excluded (BTC2 is in EXCLUDED_SYMBOLS),
    # so it never appears and never collides.
    assert "BTC2/USDC:USDC" not in markets, markets.keys()
    assert all(
        m["info"]["market_token"] != _SYNTH_BTC_MARKET for m in markets.values()
    ), "synthetic pool must not appear under any symbol"


@pytest.mark.asyncio
async def test_graphql_loader_order_independent() -> None:
    """Canonical BTC/USDC:USDC maps to the real pool regardless of payload order."""
    real_first = list(reversed(_market_infos_synthetic_first()))
    markets = await _run_graphql_loader(real_first)

    assert markets["BTC/USDC:USDC"]["info"]["market_token"] == _REAL_BTC_MARKET
    assert "BTC2/USDC:USDC" not in markets


# ---------------------------------------------------------------------------
# REST fallback loader (used when GraphQL returns empty)
# ---------------------------------------------------------------------------


def _rest_markets_synthetic_first() -> list[dict]:
    """REST /markets/info entries (different field names than GraphQL)."""
    return [
        {
            "marketToken": _SYNTH_BTC_MARKET,
            "indexToken": _BTC_INDEX,
            "longToken": _TBTC,
            "shortToken": _TBTC,
            "isListed": True,
        },
        {
            "marketToken": _REAL_BTC_MARKET,
            "indexToken": _BTC_INDEX,
            "longToken": _WBTC,
            "shortToken": _USDC,
            "isListed": True,
        },
    ]


async def _run_rest_loader(markets_list: list[dict]) -> dict:
    """Drive ``_load_markets_from_rest_api`` offline with a mocked REST payload."""
    gmx = AsyncGMX({})
    gmx._market_cache = None  # skip disk cache
    gmx.chain = "arbitrum"
    gmx.session = object()  # truthy; the API call is patched out
    subsquid = AsyncMock()
    subsquid.get_market_infos = AsyncMock(return_value=[])
    gmx.subsquid = subsquid
    gmx._fetch_tokens_async = AsyncMock(return_value=_tokens_payload())
    gmx._filter_datastore_disabled_markets = lambda markets: markets

    async def _fake_api(*args, **kwargs):
        # Only /markets/info is consumed here; tokens come from _fetch_tokens_async.
        return {"markets": markets_list}

    with patch(
        "eth_defi.gmx.ccxt.async_support.exchange.async_make_gmx_api_request",
        new=_fake_api,
    ):
        return await gmx._load_markets_from_rest_api()


@pytest.mark.asyncio
async def test_rest_loader_synthetic_first_does_not_poison_canonical_symbol() -> None:
    """REST fallback: BTC/USDC:USDC resolves to the real USDC pool; synthetic dropped.

    Pre-fix the REST loader used a divergent settle-currency suffix
    (``BTC/USDC:USDC2``) that let the synthetic pool survive under a tradeable
    symbol — inconsistent with every other loader.
    """
    markets = await _run_rest_loader(_rest_markets_synthetic_first())

    assert markets["BTC/USDC:USDC"]["info"]["market_token"] == _REAL_BTC_MARKET
    # No entry under the old divergent scheme, and the synthetic is excluded.
    assert "BTC/USDC:USDC2" not in markets
    assert "BTC2/USDC:USDC" not in markets
    assert all(
        m["info"]["market_token"] != _SYNTH_BTC_MARKET for m in markets.values()
    )


# ---------------------------------------------------------------------------
# Malformed-record guard (adversarial-review finding: "" == "" false-synthetic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "long_token,short_token",
    [
        (None, None),  # explicit nulls
        ("", ""),  # empty strings — pre-guard this satisfied long == short
        (None, "0x6c84a8f1c29108f47a79964b5fe888d4f4d0de40"),  # one-sided
        ("0x6c84a8f1c29108f47a79964b5fe888d4f4d0de40", ""),  # other side
    ],
)
async def test_graphql_loader_skips_records_with_missing_token_fields(
    long_token: str | None, short_token: str | None
) -> None:
    """A record missing long/short token fields is skipped, never mislabelled.

    Pre-guard, both-missing records satisfied ``"" == ""`` → falsely synthetic
    → BTC renamed BTC2 → silently excluded. The real pool must still load.
    """
    malformed = {
        "indexTokenAddress": _BTC_INDEX,
        "marketTokenAddress": "0x1111111111111111111111111111111111111111",
        "longTokenAddress": long_token,
        "shortTokenAddress": short_token,
    }
    markets = await _run_graphql_loader([malformed] + _market_infos_synthetic_first())

    # The malformed record never claims any symbol...
    assert all(
        m["info"]["market_token"] != malformed["marketTokenAddress"]
        for m in markets.values()
    )
    # ...and the real pool still resolves the canonical symbol.
    assert markets["BTC/USDC:USDC"]["info"]["market_token"] == _REAL_BTC_MARKET


@pytest.mark.asyncio
async def test_rest_loader_skips_records_with_missing_token_fields() -> None:
    """REST fallback: same malformed-record guard as the GraphQL path."""
    malformed = {
        "marketToken": "0x1111111111111111111111111111111111111111",
        "indexToken": _BTC_INDEX,
        "longToken": None,
        "shortToken": None,
        "isListed": True,
    }
    markets = await _run_rest_loader([malformed] + _rest_markets_synthetic_first())

    assert all(
        m["info"]["market_token"] != malformed["marketToken"]
        for m in markets.values()
    )
    assert markets["BTC/USDC:USDC"]["info"]["market_token"] == _REAL_BTC_MARKET


@pytest.mark.asyncio
async def test_graphql_and_rest_loaders_agree_on_keys() -> None:
    """Parity invariant: the two async loaders produce the same symbol set and
    map each shared symbol to the same market token.

    A divergence here is exactly what let the incident happen — the fix keeps
    all loaders on one scheme.
    """
    graphql_markets = await _run_graphql_loader(_market_infos_synthetic_first())
    rest_markets = await _run_rest_loader(_rest_markets_synthetic_first())

    assert set(graphql_markets) == set(rest_markets)
    for symbol in graphql_markets:
        assert (
            graphql_markets[symbol]["info"]["market_token"]
            == rest_markets[symbol]["info"]["market_token"]
        ), symbol
