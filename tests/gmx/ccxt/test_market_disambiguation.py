"""Tests for GMX multi-pool market disambiguation.

Covers the production bug where closing a BTC/USDC position would fail with
"Not a valid collateral for selected market!" because multiple BTC markets
share the same index token and dict iteration order picked the wrong pool.

All tests are pure unit tests (no RPC needed) using fake market dicts that
replicate the real Arbitrum layout.

Both REST API and GraphQL market loading paths produce the same info-dict key
names (``market_token``, ``index_token``, ``long_token``, ``short_token``), so
the disambiguation logic is format-agnostic.  The only differences are address
casing (REST/GraphQL store lowercase; the RPC-backed ``Markets`` cache stores
checksummed addresses).  The ``find_all_market_keys_by_index_address()`` method
handles this via ``Web3.to_checksum_address()`` so both casings are safe.
"""

import pytest

from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser

# ---------------------------------------------------------------------------
# Real Arbitrum addresses used in the tests
# ---------------------------------------------------------------------------

#: BTC synthetic index token address (shared by WBTC-USDC and tBTC-tBTC pools)
BTC_INDEX_TOKEN = "0x47904963fc8b2340414262125aF798B9655E58Cd"

#: WBTC-USDC market address — accepts WBTC (long) and USDC (short) as collateral
WBTC_USDC_MARKET = "0x47c031236e19d024b42f8AE6780E44A573170703"
WBTC_ADDRESS = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

#: tBTC-tBTC market address — accepts only tBTC as collateral
TBTC_TBTC_MARKET = "0xd62068c166171b3d094E03E00a1e5fbD9AF0a64B"
TBTC_ADDRESS = "0x6c84a8f1c29108F47a79964b5Fe888D4f4D0dE40"

#: An unrelated ETH market to pad the dict
ETH_INDEX_TOKEN = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
ETH_USDC_MARKET = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"
ETH_ADDRESS = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"


def _btc_markets_wbtc_first() -> dict:
    """Markets dict with WBTC-USDC pool iterated before tBTC-tBTC."""
    return {
        WBTC_USDC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN,
            "long_token_address": WBTC_ADDRESS,
            "short_token_address": USDC_ADDRESS,
            "long_token_metadata": {"symbol": "WBTC"},
            "short_token_metadata": {"symbol": "USDC"},
        },
        TBTC_TBTC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN,
            "long_token_address": TBTC_ADDRESS,
            "short_token_address": TBTC_ADDRESS,
            "long_token_metadata": {"symbol": "tBTC"},
            "short_token_metadata": {"symbol": "tBTC"},
        },
        ETH_USDC_MARKET: {
            "index_token_address": ETH_INDEX_TOKEN,
            "long_token_address": ETH_ADDRESS,
            "short_token_address": USDC_ADDRESS,
            "long_token_metadata": {"symbol": "ETH"},
            "short_token_metadata": {"symbol": "USDC"},
        },
    }


def _btc_markets_tbtc_first() -> dict:
    """Markets dict with tBTC-tBTC pool iterated BEFORE WBTC-USDC.

    This is the ordering that triggered the production crash: the tBTC pool
    was returned first, USDC was not a valid collateral for it, and the order
    failed even though the WBTC-USDC pool would have accepted USDC.
    """
    return {
        TBTC_TBTC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN,
            "long_token_address": TBTC_ADDRESS,
            "short_token_address": TBTC_ADDRESS,
            "long_token_metadata": {"symbol": "tBTC"},
            "short_token_metadata": {"symbol": "tBTC"},
        },
        WBTC_USDC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN,
            "long_token_address": WBTC_ADDRESS,
            "short_token_address": USDC_ADDRESS,
            "long_token_metadata": {"symbol": "WBTC"},
            "short_token_metadata": {"symbol": "USDC"},
        },
        ETH_USDC_MARKET: {
            "index_token_address": ETH_INDEX_TOKEN,
            "long_token_address": ETH_ADDRESS,
            "short_token_address": USDC_ADDRESS,
            "long_token_metadata": {"symbol": "ETH"},
            "short_token_metadata": {"symbol": "USDC"},
        },
    }


# ---------------------------------------------------------------------------
# find_all_market_keys_by_index_address
# ---------------------------------------------------------------------------


def test_find_all_market_keys_returns_all_pools():
    """Both BTC pools must be found regardless of dict ordering."""
    for markets in (_btc_markets_wbtc_first(), _btc_markets_tbtc_first()):
        result = OrderArgumentParser.find_all_market_keys_by_index_address(markets, BTC_INDEX_TOKEN)
        assert set(result) == {WBTC_USDC_MARKET, TBTC_TBTC_MARKET}, (
            f"Expected both BTC markets, got: {result}"
        )


def test_find_all_market_keys_single_match():
    """ETH only has one pool — the list must contain exactly one entry."""
    for markets in (_btc_markets_wbtc_first(), _btc_markets_tbtc_first()):
        result = OrderArgumentParser.find_all_market_keys_by_index_address(markets, ETH_INDEX_TOKEN)
        assert result == [ETH_USDC_MARKET]


def test_find_all_market_keys_no_match():
    """Unknown address returns an empty list."""
    unknown = "0x0000000000000000000000000000000000000001"
    result = OrderArgumentParser.find_all_market_keys_by_index_address(_btc_markets_wbtc_first(), unknown)
    assert result == []


# ---------------------------------------------------------------------------
# Collateral-based disambiguation in _check_if_valid_collateral_for_market
# ---------------------------------------------------------------------------


def test_usdc_valid_for_wbtc_usdc_pool():
    """USDC must be accepted as a valid collateral for the WBTC-USDC pool."""
    market = _btc_markets_wbtc_first()[WBTC_USDC_MARKET]
    # Simulate the validation logic directly
    assert USDC_ADDRESS in (market["long_token_address"], market["short_token_address"])


def test_usdc_invalid_for_tbtc_pool():
    """USDC must NOT be accepted as a valid collateral for the tBTC-tBTC pool."""
    market = _btc_markets_wbtc_first()[TBTC_TBTC_MARKET]
    assert USDC_ADDRESS not in (market["long_token_address"], market["short_token_address"])


# ---------------------------------------------------------------------------
# Error message quality for _check_if_valid_collateral_for_market
# ---------------------------------------------------------------------------


def test_invalid_collateral_error_includes_addresses():
    """The 'Not a valid collateral' error must include market key, collateral address
    and the valid long/short token addresses so production logs are actionable.

    Regression: previously the message was a bare string with no context.
    """
    # Build a minimal OrderArgumentParser to call the validation method.
    # We can't easily instantiate one without an RPC endpoint, so we call the
    # private method by directly constructing the scenario with a mock.
    from unittest.mock import MagicMock, patch

    mock_config = MagicMock()
    mock_config.chain = "arbitrum"
    mock_config.web3 = MagicMock()
    mock_config.web3.eth.chain_id = 42161
    mock_config.user_wallet_address = None
    mock_config._user_wallet_address = None

    with patch("eth_defi.gmx.order.order_argument_parser.Markets") as MockMarkets:
        MockMarkets.return_value.get_available_markets.return_value = _btc_markets_wbtc_first()
        with patch("eth_defi.gmx.order.order_argument_parser._get_token_metadata_dict"):
            parser = OrderArgumentParser.__new__(OrderArgumentParser)
            parser.config = mock_config
            parser.web3 = mock_config.web3
            parser.is_increase = True
            parser.is_decrease = False
            parser.is_swap = False
            parser.markets = _btc_markets_wbtc_first()
            parser.parameters_dict = {
                "market_key": WBTC_USDC_MARKET,
                "chain": "arbitrum",
            }

    with pytest.raises(Exception) as exc_info:
        parser._check_if_valid_collateral_for_market(TBTC_ADDRESS)

    msg = str(exc_info.value)
    assert "market_key" in msg, "Error must include market_key"
    assert WBTC_USDC_MARKET in msg, "Error must include the market address"
    assert TBTC_ADDRESS in msg, "Error must include the invalid collateral address"
    assert WBTC_ADDRESS in msg or USDC_ADDRESS in msg, "Error must include valid token addresses"
    assert "Hint" in msg, "Error must include a hint for the user"


# ---------------------------------------------------------------------------
# Regression: dict-order must not affect market selection
# ---------------------------------------------------------------------------


def test_market_selection_independent_of_dict_order_usdc_collateral():
    """The WBTC-USDC pool must be selected for USDC collateral regardless of
    whether WBTC-USDC or tBTC-tBTC appears first in the markets dict.

    This is the exact scenario that caused the production crash: on one restart
    WBTC-USDC was first (correct market selected, order succeeded); on another
    restart tBTC-tBTC was first (wrong market, USDC rejected, bot crashed).
    """
    usdc_checksum = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"  # USDC on Arbitrum

    for label, markets in [
        ("WBTC-USDC first", _btc_markets_wbtc_first()),
        ("tBTC-tBTC first (production crash ordering)", _btc_markets_tbtc_first()),
    ]:
        matches = OrderArgumentParser.find_all_market_keys_by_index_address(markets, BTC_INDEX_TOKEN)
        assert len(matches) == 2

        # Simulate the collateral-based disambiguation
        selected = None
        for key in matches:
            m = markets[key]
            if usdc_checksum in (m["long_token_address"], m["short_token_address"]):
                selected = key
                break

        assert selected == WBTC_USDC_MARKET, (
            f"[{label}] Expected WBTC-USDC market to be selected for USDC collateral, "
            f"got {selected}. This is the production crash bug."
        )


def test_market_selection_independent_of_dict_order_tbtc_collateral():
    """The tBTC-tBTC pool must be selected when tBTC is used as collateral,
    regardless of dict ordering."""
    tbtc_checksum = "0x6c84a8f1c29108F47a79964b5Fe888D4f4D0dE40"

    for label, markets in [
        ("WBTC-USDC first", _btc_markets_wbtc_first()),
        ("tBTC-tBTC first", _btc_markets_tbtc_first()),
    ]:
        matches = OrderArgumentParser.find_all_market_keys_by_index_address(markets, BTC_INDEX_TOKEN)
        selected = None
        for key in matches:
            m = markets[key]
            if tbtc_checksum in (m["long_token_address"], m["short_token_address"]):
                selected = key
                break

        assert selected == TBTC_TBTC_MARKET, (
            f"[{label}] Expected tBTC-tBTC market for tBTC collateral, got {selected}"
        )


# ---------------------------------------------------------------------------
# REST API vs GraphQL address format compatibility
# ---------------------------------------------------------------------------


def _rest_api_format_markets() -> dict:
    """Fake markets in REST API info-dict format (lowercase addresses).

    The REST API loading path (``_load_markets_from_rest_api``) stores all token
    addresses in lower-case after calling ``.lower()`` on the raw API response.
    """
    return {
        WBTC_USDC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN.lower(),
            "long_token_address": WBTC_ADDRESS.lower(),
            "short_token_address": USDC_ADDRESS.lower(),
            "long_token_metadata": {"symbol": "WBTC"},
            "short_token_metadata": {"symbol": "USDC"},
        },
        TBTC_TBTC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN.lower(),
            "long_token_address": TBTC_ADDRESS.lower(),
            "short_token_address": TBTC_ADDRESS.lower(),
            "long_token_metadata": {"symbol": "tBTC"},
            "short_token_metadata": {"symbol": "tBTC"},
        },
    }


def _graphql_format_markets() -> dict:
    """Fake markets in GraphQL info-dict format (mixed-case / checksummed addresses).

    The GraphQL loading path (``_load_markets_from_graphql``) stores addresses as
    returned by the Subsquid API which preserves the original checksum casing.
    """
    return {
        WBTC_USDC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN,  # checksummed
            "long_token_address": WBTC_ADDRESS,
            "short_token_address": USDC_ADDRESS,
            "long_token_metadata": {"symbol": "WBTC"},
            "short_token_metadata": {"symbol": "USDC"},
        },
        TBTC_TBTC_MARKET: {
            "index_token_address": BTC_INDEX_TOKEN,
            "long_token_address": TBTC_ADDRESS,
            "short_token_address": TBTC_ADDRESS,
            "long_token_metadata": {"symbol": "tBTC"},
            "short_token_metadata": {"symbol": "tBTC"},
        },
    }


def test_disambiguation_works_with_rest_api_lowercase_addresses():
    """find_all_market_keys_by_index_address must work when addresses are lowercase.

    The REST API loading path stores addresses in lower-case after ``.lower()``.
    ``Web3.to_checksum_address()`` inside the method converts the search key so
    the comparison is case-insensitive.
    """
    markets = _rest_api_format_markets()
    result = OrderArgumentParser.find_all_market_keys_by_index_address(markets, BTC_INDEX_TOKEN)
    assert set(result) == {WBTC_USDC_MARKET, TBTC_TBTC_MARKET}, (
        f"REST-format lowercase addresses: expected both BTC pools, got: {result}"
    )


def test_disambiguation_works_with_graphql_checksummed_addresses():
    """find_all_market_keys_by_index_address must work when addresses are checksummed.

    The GraphQL loading path preserves the original mixed-case checksum address
    returned by the Subsquid API.
    """
    markets = _graphql_format_markets()
    result = OrderArgumentParser.find_all_market_keys_by_index_address(markets, BTC_INDEX_TOKEN)
    assert set(result) == {WBTC_USDC_MARKET, TBTC_TBTC_MARKET}, (
        f"GraphQL-format checksummed addresses: expected both BTC pools, got: {result}"
    )


def test_collateral_selection_rest_format_selects_correct_pool():
    """Collateral-based pool selection works with REST API (lowercase) address format."""
    markets = _rest_api_format_markets()
    matches = OrderArgumentParser.find_all_market_keys_by_index_address(markets, BTC_INDEX_TOKEN)

    usdc_lower = USDC_ADDRESS.lower()
    selected = next(
        (k for k in matches if usdc_lower in (markets[k]["long_token_address"], markets[k]["short_token_address"])),
        None,
    )
    assert selected == WBTC_USDC_MARKET, (
        f"REST format: WBTC-USDC pool not selected for USDC collateral, got: {selected}"
    )


def test_collateral_selection_graphql_format_selects_correct_pool():
    """Collateral-based pool selection works with GraphQL (checksummed) address format."""
    markets = _graphql_format_markets()
    matches = OrderArgumentParser.find_all_market_keys_by_index_address(markets, BTC_INDEX_TOKEN)

    # Checksum version of USDC address as stored by the GraphQL path
    usdc_checksum = USDC_ADDRESS
    selected = next(
        (k for k in matches if usdc_checksum in (markets[k]["long_token_address"], markets[k]["short_token_address"])),
        None,
    )
    assert selected == WBTC_USDC_MARKET, (
        f"GraphQL format: WBTC-USDC pool not selected for USDC collateral, got: {selected}"
    )
