"""Real backend and disk-cache coverage for CCXT market discovery."""

from pathlib import Path
from unittest.mock import patch

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.core.markets import Markets


def test_load_markets_rest_api_mode(chain_rpc_url: str) -> None:
    """Load real REST markets without a disk cache or GraphQL fallback."""
    gmx = GMX(params={"rpcUrl": chain_rpc_url, "chainId": 42161}, options={"disable_market_cache": True})
    gmx.subsquid = None
    with patch.object(gmx.api, "get_markets_info", wraps=gmx.api.get_markets_info) as fetch, patch.object(Markets, "get_available_markets", side_effect=AssertionError("Unexpected RPC discovery fallback")):
        markets = gmx.load_markets()
        fetch.assert_called_once()
    assert markets
    market = next(iter(markets.values()))
    assert {"symbol", "base", "quote", "info"}.issubset(market)
    assert {"market_token", "index_token"}.issubset(market["info"])


def test_load_markets_graphql_mode(chain_rpc_url: str) -> None:
    """Load real GraphQL markets and fail if discovery falls back."""
    gmx = GMX(params={"rpcUrl": chain_rpc_url, "chainId": 42161}, options={"graphql_only": True, "disable_market_cache": True})
    assert gmx.subsquid is not None
    with patch.object(gmx, "_load_markets_from_graphql", wraps=gmx._load_markets_from_graphql) as fetch, patch.object(gmx, "_load_markets_from_rest_api", side_effect=AssertionError("Unexpected REST fallback")), patch.object(Markets, "get_available_markets", side_effect=AssertionError("Unexpected RPC discovery fallback")):
        assert gmx.load_markets()
        fetch.assert_called_once()


def test_load_markets_rpc_mode(chain_rpc_url: str) -> None:
    """Load real onchain markets with both external discovery backends disabled."""
    gmx = GMX(params={"rpcUrl": chain_rpc_url, "chainId": 42161}, options={"rest_api_mode": False, "disable_market_cache": True})
    gmx.subsquid = None
    original = Markets.get_available_markets
    with patch.object(Markets, "get_available_markets", autospec=True, side_effect=original) as fetch, patch.object(gmx, "_load_markets_from_rest_api", side_effect=AssertionError("Unexpected REST discovery")):
        assert gmx.load_markets()
        fetch.assert_called_once()


def test_fetch_apy_all_markets(chain_rpc_url: str) -> None:
    """Require non-empty numeric APY data from the actual REST endpoint."""
    gmx = GMX(params={"rpcUrl": chain_rpc_url, "chainId": 42161}, options={"disable_market_cache": True})
    gmx.subsquid = None
    all_apy = gmx.fetch_apy(period="30d")
    assert isinstance(all_apy, dict)
    assert all_apy, "Successful APY integration must contain mapped markets"
    assert all(isinstance(value, (int, float)) for value in all_apy.values())


def test_cache_persistence(chain_rpc_url: str, tmp_path: Path) -> None:
    """Reuse persisted discovery data while retaining fresh disabled-market checks.

    The second instance must not download market metadata. Onchain DataStore
    validation remains enabled because a cached market can become disabled.
    """
    options = {"market_cache_dir": str(tmp_path)}
    first = GMX(params={"rpcUrl": chain_rpc_url, "chainId": 42161}, options=options)
    first.subsquid = None
    try:
        markets = first.load_markets()
        assert markets
        assert first._market_cache.get_markets("rest_api")
    finally:
        first._market_cache.close()
    assert (tmp_path / "markets_arbitrum.sqlite").exists()

    second = GMX(params={"rpcUrl": chain_rpc_url, "chainId": 42161}, options=options)
    second.subsquid = None
    try:
        with patch.object(second.api, "get_markets_info", side_effect=AssertionError("Market discovery must come from disk")) as fetch, patch.object(second.api, "get_tokens", side_effect=AssertionError("Tokens must come from disk")) as tokens, patch.object(Markets, "get_available_markets", side_effect=AssertionError("Unexpected RPC discovery fallback")):
            assert set(second.load_markets()) == set(markets)
            fetch.assert_not_called()
            tokens.assert_not_called()
    finally:
        second._market_cache.close()
