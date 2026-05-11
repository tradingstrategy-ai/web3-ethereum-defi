"""Force-refresh on market_key miss inside ``OrderArgumentParser``.

These tests pin the second leg of the issue #67 fix: when
``_handle_missing_market_key`` cannot resolve an index token, instead of
raising immediately it must:

1. Log a warning, invalidate the class-level markets cache, and re-fetch.
2. Retry the lookup once.
3. Raise a ``ValueError`` *only* if the retry also fails — with a message
   that includes ``after forced cache refresh`` so on-call operators can
   distinguish a structural miss from a stale-cache miss.
4. Bound the retry to one refresh per parser instance — never loop.

See ``tradingstrategy-ai/gmx-strategies#67`` deep-dive
(``2026-05-11-gmx-market-cache-permanent-fix.md``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# Reused fixture addresses (the same checksum addresses pinned in
# ``test_markets_cache_ttl.py``).
_BTC_INDEX = "0x47904963fc8b2340414262125aF798B9655E58Cd"
_CHZ_INDEX = "0x5dB4692926C8ceebF6Da0995358Bbc438F3fd80C"  # the issue-#67 token
_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_BTC_MARKET_ADDR = "0x47c031236e19d024b42f8AE6780E44A573170703"
_CHZ_MARKET_ADDR = "0x4fDd333FF9cA409df583f306B6F5a7fFdC9573d1"


def _markets_with_btc_only() -> dict:
    return {
        _BTC_MARKET_ADDR: {
            "gmx_market_address": _BTC_MARKET_ADDR,
            "market_symbol": "BTC",
            "index_token_address": _BTC_INDEX,
            "long_token_address": _USDC,
            "short_token_address": _USDC,
            "market_metadata": {"symbol": "BTC", "decimals": 8},
            "long_token_metadata": {"symbol": "USDC", "decimals": 6},
            "short_token_metadata": {"symbol": "USDC", "decimals": 6},
        },
    }


def _markets_with_btc_and_chz() -> dict:
    return {
        _BTC_MARKET_ADDR: {
            "gmx_market_address": _BTC_MARKET_ADDR,
            "market_symbol": "BTC",
            "index_token_address": _BTC_INDEX,
            "long_token_address": _USDC,
            "short_token_address": _USDC,
            "market_metadata": {"symbol": "BTC", "decimals": 8},
            "long_token_metadata": {"symbol": "USDC", "decimals": 6},
            "short_token_metadata": {"symbol": "USDC", "decimals": 6},
        },
        _CHZ_MARKET_ADDR: {
            "gmx_market_address": _CHZ_MARKET_ADDR,
            "market_symbol": "CHZ",
            "index_token_address": _CHZ_INDEX,
            "long_token_address": _USDC,
            "short_token_address": _USDC,
            "market_metadata": {"symbol": "CHZ", "decimals": 8},
            "long_token_metadata": {"symbol": "USDC", "decimals": 6},
            "short_token_metadata": {"symbol": "USDC", "decimals": 6},
        },
    }


def _build_config() -> MagicMock:
    config = MagicMock()
    config.chain = "arbitrum"
    config.web3 = MagicMock()
    config.web3.eth.chain_id = 42161
    config.user_wallet_address = None
    return config


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset the class-level markets cache around every test."""
    from eth_defi.gmx.core.markets import Markets

    Markets.invalidate_cache()
    yield
    Markets.invalidate_cache()


def _build_parser(monkeypatch, initial_markets: dict):
    """Build an ``OrderArgumentParser`` whose initial ``self.markets`` snapshot
    is the provided dict.  Returns ``(parser, get_available_markets_mock)``."""
    from eth_defi.gmx.order import order_argument_parser as parser_mod
    from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser

    # Patch the Markets.get_available_markets so __init__ uses our snapshot.
    mock_get_available_markets = MagicMock(return_value=initial_markets)
    monkeypatch.setattr(
        parser_mod.Markets,
        "get_available_markets",
        lambda self: mock_get_available_markets(),
    )

    # Avoid triggering an actual GMXConfig construction.
    monkeypatch.setattr(parser_mod, "GMXConfig", MagicMock())

    config = _build_config()
    parser = OrderArgumentParser(config, is_increase=True)
    return parser, mock_get_available_markets


def test_first_miss_invalidates_and_retries_then_succeeds(monkeypatch):
    """First lookup misses CHZ; after refresh CHZ is present and resolves."""
    from eth_defi.gmx.core.markets import Markets

    parser, mock_get_available_markets = _build_parser(
        monkeypatch, _markets_with_btc_only()
    )

    # Now arrange that the next call returns the *expanded* market set.
    mock_get_available_markets.return_value = _markets_with_btc_and_chz()

    # Spy on Markets.invalidate_cache to confirm the parser actually invokes it.
    with patch.object(Markets, "invalidate_cache", wraps=Markets.invalidate_cache) as inv:
        parser.parameters_dict = {
            "chain": "arbitrum",
            "index_token_address": _CHZ_INDEX,
        }
        parser._handle_missing_market_key()

    assert parser.parameters_dict["market_key"] == _CHZ_MARKET_ADDR
    assert inv.called, "invalidate_cache must be called on the first miss"


def test_second_miss_after_refresh_raises_value_error(monkeypatch):
    """When CHZ is structurally absent, the retry also misses and raises."""
    parser, mock_get_available_markets = _build_parser(
        monkeypatch, _markets_with_btc_only()
    )
    # Even after the refresh, CHZ is not present anywhere.
    mock_get_available_markets.return_value = _markets_with_btc_only()

    parser.parameters_dict = {
        "chain": "arbitrum",
        "index_token_address": _CHZ_INDEX,
    }
    with pytest.raises(ValueError, match="after forced cache refresh"):
        parser._handle_missing_market_key()


def test_refresh_is_bounded_one_attempt_per_parser_instance(monkeypatch):
    """Two consecutive structural misses must NOT cause two cache refreshes."""
    from eth_defi.gmx.core.markets import Markets

    parser, mock_get_available_markets = _build_parser(
        monkeypatch, _markets_with_btc_only()
    )
    mock_get_available_markets.return_value = _markets_with_btc_only()

    with patch.object(Markets, "invalidate_cache", wraps=Markets.invalidate_cache) as inv:
        # First miss: refresh once, raise.
        parser.parameters_dict = {
            "chain": "arbitrum",
            "index_token_address": _CHZ_INDEX,
        }
        with pytest.raises(ValueError):
            parser._handle_missing_market_key()
        first_refresh_count = inv.call_count

        # Second miss on the SAME parser instance: must not refresh again.
        parser.parameters_dict = {
            "chain": "arbitrum",
            "index_token_address": _CHZ_INDEX,
        }
        with pytest.raises(ValueError):
            parser._handle_missing_market_key()
        second_refresh_count = inv.call_count

    assert second_refresh_count == first_refresh_count, (
        "A second miss on the same parser must not trigger another refresh"
    )


def test_initial_hit_does_not_invalidate_cache(monkeypatch):
    """When the lookup hits on the first try, no cache refresh should occur."""
    from eth_defi.gmx.core.markets import Markets

    parser, _ = _build_parser(monkeypatch, _markets_with_btc_and_chz())

    with patch.object(Markets, "invalidate_cache", wraps=Markets.invalidate_cache) as inv:
        parser.parameters_dict = {
            "chain": "arbitrum",
            "index_token_address": _CHZ_INDEX,
        }
        parser._handle_missing_market_key()

    assert parser.parameters_dict["market_key"] == _CHZ_MARKET_ADDR
    assert not inv.called, "invalidate_cache must NOT be called on a hit"
