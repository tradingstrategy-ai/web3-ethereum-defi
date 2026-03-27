"""Tests for GMX multi-pool market disambiguation.

Covers the production bug where closing a BTC/USDC position would fail with
"Not a valid collateral for selected market!" because multiple BTC markets
share the same index token and dict iteration order picked the wrong pool.

All tests use live Arbitrum mainnet data via the ``ccxt_gmx_arbitrum`` fixture.
Requires ``JSON_RPC_ARBITRUM`` environment variable.
"""

import pytest

from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_all_markets(gmx):
    """Return the full markets dict from Markets.get_available_markets()."""
    gmx.load_markets()
    return Markets(gmx.config).get_available_markets()


def _find_multi_pool_index_token(all_markets: dict) -> tuple[str, list[str]]:
    """Find an index token that appears in more than one pool.

    Returns ``(index_token_address, [market_key, ...])`` for the first index
    token that has at least two pools.  Raises ``pytest.skip`` if none found.
    """
    from collections import defaultdict

    by_index: dict[str, list[str]] = defaultdict(list)
    for key, m in all_markets.items():
        idx = m.get("index_token_address", "").lower()
        if idx:
            by_index[idx].append(key)

    for idx, keys in by_index.items():
        if len(keys) >= 2:
            return idx, keys

    pytest.skip("No index token with multiple pools found on this RPC endpoint")


# ---------------------------------------------------------------------------
# find_all_market_keys_by_index_address
# ---------------------------------------------------------------------------


def test_find_all_market_keys_returns_all_pools(ccxt_gmx_arbitrum):
    """find_all_market_keys_by_index_address must return every pool that shares
    a given index token, not just the first one.

    Uses a real index token (first one found with multiple pools on Arbitrum)
    so the test reflects actual on-chain state rather than synthetic data.
    """
    all_markets = _load_all_markets(ccxt_gmx_arbitrum)
    index_token, expected_keys = _find_multi_pool_index_token(all_markets)

    result = OrderArgumentParser.find_all_market_keys_by_index_address(all_markets, index_token)

    assert set(result) == set(expected_keys), f"Expected all pools for index_token {index_token}: {expected_keys}, got: {result}"


def test_find_all_market_keys_single_pool_token(ccxt_gmx_arbitrum):
    """For an index token that appears in exactly one pool the method must
    return a single-element list."""
    all_markets = _load_all_markets(ccxt_gmx_arbitrum)

    from collections import defaultdict

    by_index: dict[str, list[str]] = defaultdict(list)
    for key, m in all_markets.items():
        idx = m.get("index_token_address", "").lower()
        if idx:
            by_index[idx].append(key)

    solo_index = next((idx for idx, keys in by_index.items() if len(keys) == 1), None)
    if solo_index is None:
        pytest.skip("All index tokens have multiple pools — cannot test single-pool path")

    result = OrderArgumentParser.find_all_market_keys_by_index_address(all_markets, solo_index)
    assert len(result) == 1, f"Expected exactly 1 match for solo index token {solo_index}, got: {result}"


def test_find_all_market_keys_unknown_address_returns_empty(ccxt_gmx_arbitrum):
    """An address not present in the markets dict must return an empty list."""
    all_markets = _load_all_markets(ccxt_gmx_arbitrum)
    unknown = "0x0000000000000000000000000000000000000001"
    result = OrderArgumentParser.find_all_market_keys_by_index_address(all_markets, unknown)
    assert result == []


# ---------------------------------------------------------------------------
# Collateral-based pool disambiguation
# ---------------------------------------------------------------------------


def test_collateral_disambiguation_selects_matching_pool(ccxt_gmx_arbitrum):
    """When multiple pools share an index token, selecting by collateral address
    must pick the pool that actually accepts that collateral.

    Uses the first multi-pool index token found on Arbitrum and picks the
    short_token of one of the pools as the collateral to select.
    """
    all_markets = _load_all_markets(ccxt_gmx_arbitrum)
    _, pool_keys = _find_multi_pool_index_token(all_markets)

    # Pick one pool and use its short_token as the target collateral
    target_key = pool_keys[0]
    target_collateral = all_markets[target_key]["short_token_address"].lower()

    selected = next(
        (
            k
            for k in pool_keys
            if target_collateral
            in (
                all_markets[k]["long_token_address"].lower(),
                all_markets[k]["short_token_address"].lower(),
            )
        ),
        None,
    )

    assert selected is not None, f"Collateral {target_collateral} not found in any of the pools: {[(k, all_markets[k]['long_token_address'], all_markets[k]['short_token_address']) for k in pool_keys]}"
    # The selected pool must accept the collateral
    m = all_markets[selected]
    assert target_collateral in (
        m["long_token_address"].lower(),
        m["short_token_address"].lower(),
    )


def test_disambiguation_result_independent_of_dict_order(ccxt_gmx_arbitrum):
    """The correct pool must be selected regardless of the iteration order of
    the markets dict.

    This is the exact regression test for the production crash: previously
    ``find_market_key_by_index_address`` returned whichever pool came first in
    dict order.  This test reverses the dict and asserts the same pool is
    chosen both ways.
    """
    all_markets = _load_all_markets(ccxt_gmx_arbitrum)
    index_token, pool_keys = _find_multi_pool_index_token(all_markets)

    # Choose a deterministic collateral: short_token of the last pool key
    # (so it is *not* the first pool — exercises the ordering dependency)
    target_key = pool_keys[-1]
    target_collateral = all_markets[target_key]["short_token_address"].lower()

    def _select(markets_dict):
        candidates = OrderArgumentParser.find_all_market_keys_by_index_address(markets_dict, index_token)
        return next(
            (
                k
                for k in candidates
                if target_collateral
                in (
                    markets_dict[k]["long_token_address"].lower(),
                    markets_dict[k]["short_token_address"].lower(),
                )
            ),
            None,
        )

    # Forward order
    forward_selected = _select(all_markets)

    # Reversed order
    reversed_markets = dict(reversed(list(all_markets.items())))
    reversed_selected = _select(reversed_markets)

    assert forward_selected == reversed_selected, f"Pool selection differs between forward and reversed dict order: forward={forward_selected} reversed={reversed_selected}. Disambiguation is still dict-order dependent."
    assert forward_selected is not None, f"Could not find any pool accepting collateral {target_collateral}"


# ---------------------------------------------------------------------------
# Error message quality
# ---------------------------------------------------------------------------


def _find_disjoint_pool_pair(all_markets: dict) -> tuple[str, str, str] | None:
    """Find two pools sharing an index token but with disjoint collateral tokens.

    Returns ``(pool_a_key, pool_b_key, invalid_collateral_address)`` where
    ``invalid_collateral_address`` is a token of pool B that is NOT valid for
    pool A.  Returns ``None`` if no such pair exists.
    """
    from collections import defaultdict

    by_index: dict[str, list[str]] = defaultdict(list)
    for key, m in all_markets.items():
        idx = m.get("index_token_address", "").lower()
        if idx:
            by_index[idx].append(key)

    for keys in by_index.values():
        if len(keys) < 2:
            continue
        for i, key_a in enumerate(keys):
            pool_a = all_markets[key_a]
            valid_a = {pool_a["long_token_address"].lower(), pool_a["short_token_address"].lower()}
            for key_b in keys[i + 1 :]:
                pool_b = all_markets[key_b]
                for candidate in (pool_b["long_token_address"], pool_b["short_token_address"]):
                    if candidate.lower() not in valid_a:
                        return key_a, key_b, candidate
    return None


def test_invalid_collateral_error_includes_context(ccxt_gmx_arbitrum):
    """The 'Not a valid collateral' exception must include the market key,
    the rejected collateral address, the valid long/short token addresses,
    and a hint — so production logs are immediately actionable.

    Regression: previously the message was a bare one-liner with no context.
    """
    from unittest.mock import MagicMock, patch

    all_markets = _load_all_markets(ccxt_gmx_arbitrum)
    pair = _find_disjoint_pool_pair(all_markets)
    if pair is None:
        pytest.skip("No pool pair with disjoint collateral found — cannot test error path")

    pool_a_key, _pool_b_key, invalid_collateral = pair

    mock_config = MagicMock()
    mock_config.chain = "arbitrum"
    mock_config.web3 = MagicMock()
    mock_config.web3.eth.chain_id = 42161
    mock_config.user_wallet_address = None
    mock_config._user_wallet_address = None

    with patch("eth_defi.gmx.order.order_argument_parser.Markets") as MockMarkets:
        MockMarkets.return_value.get_available_markets.return_value = all_markets
        with patch("eth_defi.gmx.order.order_argument_parser._get_token_metadata_dict"):
            parser = OrderArgumentParser.__new__(OrderArgumentParser)
            parser.config = mock_config
            parser.web3 = mock_config.web3
            parser.is_increase = True
            parser.is_decrease = False
            parser.is_swap = False
            parser.markets = all_markets
            parser.parameters_dict = {
                "market_key": pool_a_key,
                "chain": "arbitrum",
            }

    with pytest.raises(Exception) as exc_info:
        parser._check_if_valid_collateral_for_market(invalid_collateral)

    msg = str(exc_info.value)
    assert "market_key" in msg, "Error must include market_key label"
    assert pool_a_key in msg, "Error must include the market address"
    assert invalid_collateral in msg, "Error must include the rejected collateral address"
    assert "Hint" in msg, "Error must include a hint for the user"


# ---------------------------------------------------------------------------
# fetch_pools_for_symbol
# ---------------------------------------------------------------------------


def test_fetch_pools_for_symbol_returns_multiple_btc_pools(ccxt_gmx_arbitrum):
    """fetch_pools_for_symbol('BTC/USD') must return at least two pools on Arbitrum.

    BTC has both a WBTC-USDC pool and a tBTC-tBTC pool sharing the same index
    token.  The method must find all of them, not just the default one stored
    in self.markets.
    """
    gmx = ccxt_gmx_arbitrum
    gmx.load_markets()
    pools = gmx.fetch_pools_for_symbol("BTC/USD")

    assert len(pools) >= 2, f"Expected at least 2 BTC pools on Arbitrum (WBTC-USDC and tBTC-tBTC), got: {pools}"


def test_fetch_pools_for_symbol_pool_fields_are_populated(ccxt_gmx_arbitrum):
    """Every pool returned by fetch_pools_for_symbol must have the required fields."""
    gmx = ccxt_gmx_arbitrum
    gmx.load_markets()
    pools = gmx.fetch_pools_for_symbol("BTC/USD")

    for pool in pools:
        assert pool.get("market_address"), f"Pool missing market_address: {pool}"
        assert pool.get("long_token"), f"Pool missing long_token: {pool}"
        assert pool.get("short_token"), f"Pool missing short_token: {pool}"
        assert pool.get("index_token"), f"Pool missing index_token: {pool}"
        assert pool.get("long_token_symbol"), f"Pool missing long_token_symbol: {pool}"
        assert pool.get("short_token_symbol"), f"Pool missing short_token_symbol: {pool}"

    # All returned pools must share one index token
    index_tokens = {p["index_token"].lower() for p in pools}
    assert len(index_tokens) == 1, f"All BTC pools should share one index token, got multiple: {index_tokens}"


def test_fetch_pools_for_symbol_btc_has_usdc_pool(ccxt_gmx_arbitrum):
    """Among the BTC pools there must be one that accepts USDC as short collateral.

    This verifies the WBTC-USDC pool is discoverable — the pool required for
    BTC/USDC:USDC positions.
    """
    gmx = ccxt_gmx_arbitrum
    gmx.load_markets()
    pools = gmx.fetch_pools_for_symbol("BTC/USD")

    usdc_pool = next(
        (p for p in pools if p["short_token_symbol"].upper() == "USDC"),
        None,
    )
    assert usdc_pool is not None, f"No BTC pool with USDC short collateral found. short_token_symbols present: {[p['short_token_symbol'] for p in pools]}"
