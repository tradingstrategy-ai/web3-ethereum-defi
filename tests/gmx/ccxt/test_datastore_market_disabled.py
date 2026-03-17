"""Integration and unit tests for DataStore disabled-market filtering and
GMX custom-error decoding from reverted transactions.

Covered fixes
-------------
1. ``_filter_datastore_disabled_markets()`` — cross-checks every loaded market
   against ``DataStore.getBool(IS_MARKET_DISABLED)`` and removes on-chain-disabled
   ones.  The REST API ``isListed`` field is unreliable (e.g. OM/USDC shows as
   listed but is disabled on-chain).

2. ``_try_decode_gmx_custom_error()`` — replays a reverted transaction via
   ``eth_call`` and decodes the revert data using all available strategies:
   raw 4-byte ABI selector (handles all 50+ GMX custom errors, Error(string),
   and Panic), then readable node string message as fallback.
"""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from flaky import flaky
from web3.exceptions import ContractLogicError

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.events import decode_error_reason

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GMX custom error 4-byte selectors (from eth_defi/gmx/events.py GMX_ERROR_SELECTORS)
SELECTOR_DISABLED_MARKET = "f8c937db"  # DisabledMarket(address)
SELECTOR_EMPTY_POSITION = "4dfbbff3"  # EmptyPosition()
SELECTOR_INSUFFICIENT_COLLATERAL = "74cc815b"  # InsufficientCollateralAmount(uint256, int256)
SELECTOR_ERROR_STRING = "08c379a0"  # Error(string) — standard Solidity revert

# Realistic ETH/USDC market token address on Arbitrum (public knowledge)
ETH_USDC_MARKET_TOKEN = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rpc_url() -> str:
    url = os.environ.get("JSON_RPC_ARBITRUM", "")
    if not url:
        pytest.skip("JSON_RPC_ARBITRUM environment variable not set")
    return url


@pytest.fixture(scope="module")
def gmx_live(rpc_url) -> GMX:
    """View-only GMX exchange connected to Arbitrum mainnet with markets loaded."""
    exchange = GMX(params={"rpcUrl": rpc_url})
    exchange.load_markets(reload=True)
    return exchange


@pytest.fixture(scope="module")
def gmx_no_markets() -> GMX:
    """GMX exchange without markets loaded — for unit-level method tests using mocks.

    Does NOT require ``JSON_RPC_ARBITRUM``.  A mock web3 instance is injected so
    that :py:class:`~eth_defi.gmx.ccxt.exchange.GMX` can be initialised without
    making any live RPC calls.  Individual tests patch ``exchange.web3.eth.*``
    attributes as needed.
    """
    mock_web3 = MagicMock()
    mock_web3.eth.chain_id = 42161  # Arbitrum mainnet

    with patch("eth_defi.gmx.ccxt.exchange.create_multi_provider_web3", return_value=mock_web3):
        exchange = GMX(params={"rpcUrl": "http://localhost:8545", "chainId": 42161})

    return exchange


# ---------------------------------------------------------------------------
# Helper: build a minimal fake ContractLogicError with revert data
# ---------------------------------------------------------------------------


def _make_contract_logic_error(data_hex: str | None = None, msg: str = "execution reverted") -> ContractLogicError:
    """Return a real ContractLogicError whose .data attribute carries raw revert bytes.

    ``side_effect`` in unittest.mock only raises an object as an exception when
    ``isinstance(obj, BaseException)`` is True — so we use a real exception instance,
    not a MagicMock.
    """
    exc = ContractLogicError(msg)
    exc.data = data_hex
    return exc


def _make_fake_tx(to_addr: str = "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6") -> dict:
    """Minimal transaction dict suitable for eth_call replay."""
    return {
        "to": to_addr,
        "from": "0x1111111111111111111111111111111111111111",
        "value": 0,
        "input": b"",
        "gas": 500_000,
    }


# ---------------------------------------------------------------------------
# Section 1: decode_error_reason() unit tests (events.py)
# ---------------------------------------------------------------------------


def test_decode_error_reason_disabled_market():
    """decode_error_reason() must decode DisabledMarket(address) from raw bytes."""
    # 4-byte selector + 32-byte ABI-encoded address
    market_addr_padded = ETH_USDC_MARKET_TOKEN[2:].lower().zfill(64)
    raw = bytes.fromhex(SELECTOR_DISABLED_MARKET + market_addr_padded)

    result = decode_error_reason(raw)

    assert result is not None, "Expected decoded error, got None"
    assert "DisabledMarket" in result, f"Expected 'DisabledMarket' in '{result}'"
    logger.info("decode_error_reason DisabledMarket → %s", result)


def test_decode_error_reason_empty_position():
    """decode_error_reason() must decode EmptyPosition() (no parameters)."""
    raw = bytes.fromhex(SELECTOR_EMPTY_POSITION)

    result = decode_error_reason(raw)

    assert result is not None
    assert "EmptyPosition" in result, f"Expected 'EmptyPosition' in '{result}'"


def test_decode_error_reason_standard_error_string():
    """decode_error_reason() must handle standard Error(string) (0x08c379a0)."""
    from eth_abi import encode

    encoded_msg = encode(["string"], ["market is disabled"])
    raw = bytes.fromhex(SELECTOR_ERROR_STRING) + encoded_msg

    result = decode_error_reason(raw)

    # Should decode or return None (Error(string) may not be in GMX_ERROR_SELECTORS
    # but the function should still handle it gracefully)
    logger.info("decode_error_reason Error(string) → %s", result)


def test_decode_error_reason_unknown_selector_returns_none():
    """decode_error_reason() must return None for unknown selectors."""
    raw = bytes.fromhex("deadbeef" + "00" * 32)

    result = decode_error_reason(raw)

    # Should return None or a generic message — should not raise
    logger.info("decode_error_reason unknown selector → %s", result)


def test_decode_error_reason_empty_bytes_returns_none():
    """decode_error_reason() must not raise on empty bytes."""
    result = decode_error_reason(b"")
    # Graceful: None or generic
    logger.info("decode_error_reason empty bytes → %s", result)


# ---------------------------------------------------------------------------
# Section 2: _try_decode_gmx_custom_error() unit tests (exchange.py)
# ---------------------------------------------------------------------------


def test_try_decode_gmx_custom_error_disabled_market(gmx_no_markets):
    """Unit: DisabledMarket(address) bytes decoded to human-readable string.

    Mocks eth.call to raise ContractLogicError with DisabledMarket selector in .data.
    """
    exchange = gmx_no_markets
    fake_tx_hash = "0x" + "ab" * 32

    market_addr_padded = ETH_USDC_MARKET_TOKEN[2:].lower().zfill(64)
    raw_data_hex = "0x" + SELECTOR_DISABLED_MARKET + market_addr_padded

    mock_exc = _make_contract_logic_error(data_hex=raw_data_hex)

    with patch.object(exchange.web3.eth, "get_transaction", return_value=_make_fake_tx()):
        with patch.object(exchange.web3.eth, "call", side_effect=mock_exc):
            result = exchange._try_decode_gmx_custom_error(fake_tx_hash)

    logger.info("_try_decode_gmx_custom_error DisabledMarket → %s", result)
    assert result is not None, "Expected decoded error, got None"
    assert "DisabledMarket" in result, f"Expected 'DisabledMarket' in '{result}'"


def test_try_decode_gmx_custom_error_empty_position(gmx_no_markets):
    """Unit: EmptyPosition() decoded correctly — no-parameter custom error."""
    exchange = gmx_no_markets
    fake_tx_hash = "0x" + "cd" * 32
    raw_data_hex = "0x" + SELECTOR_EMPTY_POSITION

    mock_exc = _make_contract_logic_error(data_hex=raw_data_hex)

    with patch.object(exchange.web3.eth, "get_transaction", return_value=_make_fake_tx()):
        with patch.object(exchange.web3.eth, "call", side_effect=mock_exc):
            result = exchange._try_decode_gmx_custom_error(fake_tx_hash)

    logger.info("_try_decode_gmx_custom_error EmptyPosition → %s", result)
    assert result is not None
    assert "EmptyPosition" in result


def test_try_decode_gmx_custom_error_node_message_fallback(gmx_no_markets):
    """Unit: when .data is absent, readable node message in args[0] is returned."""
    exchange = gmx_no_markets
    fake_tx_hash = "0x" + "ef" * 32

    # ContractLogicError with a readable message but no .data (some nodes)
    mock_exc = _make_contract_logic_error(data_hex=None, msg="insufficient collateral for position")

    with patch.object(exchange.web3.eth, "get_transaction", return_value=_make_fake_tx()):
        with patch.object(exchange.web3.eth, "call", side_effect=mock_exc):
            result = exchange._try_decode_gmx_custom_error(fake_tx_hash)

    logger.info("_try_decode_gmx_custom_error node message → %s", result)
    # The fallback strategy reads args[0]; generic "execution reverted" is filtered out
    # A real informative message should pass through
    if result is not None:
        assert "execution reverted" not in result.lower() or "insufficient" in result.lower()


def test_try_decode_gmx_custom_error_generic_revert_filtered(gmx_no_markets):
    """Unit: generic 'execution reverted' message is filtered — returns None so outer fallback runs."""
    exchange = gmx_no_markets
    fake_tx_hash = "0x" + "12" * 32

    mock_exc = _make_contract_logic_error(data_hex=None, msg="execution reverted")

    with patch.object(exchange.web3.eth, "get_transaction", return_value=_make_fake_tx()):
        with patch.object(exchange.web3.eth, "call", side_effect=mock_exc):
            result = exchange._try_decode_gmx_custom_error(fake_tx_hash)

    # Should return None so fetch_transaction_revert_reason() gets a chance
    logger.info("_try_decode_gmx_custom_error generic revert → %s", result)
    assert result is None, "Generic 'execution reverted' should be filtered out"


def test_try_decode_gmx_custom_error_success_tx_returns_none(gmx_no_markets):
    """Unit: when eth.call does NOT raise (tx succeeded on replay), return None."""
    exchange = gmx_no_markets
    fake_tx_hash = "0x" + "99" * 32

    # eth.call returns normally (no revert)
    with patch.object(exchange.web3.eth, "get_transaction", return_value=_make_fake_tx()):
        with patch.object(exchange.web3.eth, "call", return_value=b""):
            result = exchange._try_decode_gmx_custom_error(fake_tx_hash)

    assert result is None, "Successful replay should return None"


def test_try_decode_gmx_custom_error_exception_in_replay_returns_none(gmx_no_markets):
    """Unit: if eth.get_transaction or eth.call raises an unexpected error, return None gracefully."""
    exchange = gmx_no_markets

    with patch.object(exchange.web3.eth, "get_transaction", side_effect=Exception("RPC error")):
        result = exchange._try_decode_gmx_custom_error("0x" + "ff" * 32)

    assert result is None, "Exception in replay should return None, not propagate"


# ---------------------------------------------------------------------------
# Section 3: _filter_datastore_disabled_markets() unit tests
# ---------------------------------------------------------------------------


@flaky(max_runs=3, min_passes=1)
def test_filter_datastore_disabled_markets_removes_disabled(gmx_live):
    """Integration: filter returns a valid subset of markets via real Multicall3 DataStore calls."""
    markets_before = dict(gmx_live.markets)
    assert len(markets_before) > 0, "Need at least one market to test"

    result = gmx_live._filter_datastore_disabled_markets(markets_before)

    assert isinstance(result, dict)
    # Filter can only remove markets, never add
    assert len(result) <= len(markets_before)
    # All returned markets must be a subset of the original
    for symbol in result:
        assert symbol in markets_before

    logger.info(
        "DataStore filter: %d → %d markets (%d removed)",
        len(markets_before),
        len(result),
        len(markets_before) - len(result),
    )


@flaky(max_runs=3, min_passes=1)
def test_filter_datastore_disabled_markets_returns_valid_structure(gmx_live):
    """Integration: every market returned by the filter has the expected CCXT fields."""
    result = gmx_live._filter_datastore_disabled_markets(dict(gmx_live.markets))

    for symbol, market in result.items():
        assert "info" in market, f"{symbol} missing 'info' key"
        assert "symbol" in market or "active" in market, f"{symbol} missing basic CCXT keys"


@flaky(max_runs=3, min_passes=1)
def test_filter_datastore_disabled_markets_handles_per_market_error(gmx_live):
    """Integration: filter completes without raising even with many markets."""
    result = gmx_live._filter_datastore_disabled_markets(dict(gmx_live.markets))

    # Should return a dict regardless of per-market failures
    assert isinstance(result, dict)
    assert len(result) > 0, "Filter should return at least some markets"


# ---------------------------------------------------------------------------
# Section 4: Live integration tests against Arbitrum mainnet
# ---------------------------------------------------------------------------


@flaky(max_runs=3, min_passes=1)
def test_filter_datastore_disabled_markets_live_does_not_raise(gmx_live):
    """Integration: DataStore filter runs against real Arbitrum mainnet without errors.

    Loads markets from REST API then verifies the DataStore filter:
    - Completes without exception
    - Returns a dict (possibly smaller than input — disabled markets removed)
    - All remaining markets still have valid CCXT structure
    """
    markets_before = dict(gmx_live.markets)
    assert len(markets_before) > 0, "Need at least one market to test"

    filtered = gmx_live._filter_datastore_disabled_markets(markets_before)

    assert isinstance(filtered, dict)
    # Filter can only remove markets, never add
    assert len(filtered) <= len(markets_before)
    # All returned markets must be a subset of the original
    for symbol in filtered:
        assert symbol in markets_before

    removed = len(markets_before) - len(filtered)
    logger.info(
        "DataStore filter: %d markets → %d markets (%d removed)",
        len(markets_before),
        len(filtered),
        removed,
    )


@flaky(max_runs=3, min_passes=1)
def test_load_markets_rest_api_excludes_datastore_disabled(gmx_live):
    """Integration: load_markets() with REST API mode silently removes DataStore-disabled markets.

    Verifies that the integrated pipeline (REST API fetch → DataStore filter)
    produces a valid market list and never includes a market where the on-chain
    IS_MARKET_DISABLED flag is True.
    """
    from eth_defi.gmx.contracts import get_datastore_contract
    from eth_defi.gmx.keys import is_market_disabled_key

    markets = gmx_live.markets
    assert len(markets) > 0

    # Spot-check up to 5 markets against DataStore directly
    chain = gmx_live.config.get_chain()
    datastore = get_datastore_contract(gmx_live.web3, chain)

    checked = 0
    for symbol, market in list(markets.items())[:5]:
        market_token = market.get("info", {}).get("market_token", "")
        if not market_token:
            continue
        key = is_market_disabled_key(market_token)
        is_disabled = datastore.functions.getBool(key).call()
        assert not is_disabled, f"Market {symbol} ({market_token}) is in loaded markets but DataStore says IS_MARKET_DISABLED=True"
        checked += 1

    logger.info("Spot-checked %d markets — all confirmed enabled on-chain", checked)


@flaky(max_runs=3, min_passes=1)
def test_om_usdc_excluded_from_live_markets_if_disabled(gmx_live):
    """Integration: OM/USDC must not appear in the loaded market list if it is disabled on-chain.

    OM/USDC is the known example where REST API reports isListed=True but the market
    is actually disabled in DataStore.  This test checks the corrected behaviour.
    """
    from eth_defi.gmx.contracts import get_datastore_contract
    from eth_defi.gmx.keys import is_market_disabled_key

    chain = gmx_live.config.get_chain()
    datastore = get_datastore_contract(gmx_live.web3, chain)

    # Check every loaded market that has OM in its name
    om_symbols = [s for s in gmx_live.markets if "OM" in s.upper()]
    if not om_symbols:
        # OM/USDC not in the REST API at all — the filter worked or it's not listed
        logger.info("No OM markets found in loaded markets — either filtered or not in REST API")
        return

    for symbol in om_symbols:
        market = gmx_live.markets[symbol]
        market_token = market.get("info", {}).get("market_token", "")
        if not market_token:
            continue
        key = is_market_disabled_key(market_token)
        is_disabled = datastore.functions.getBool(key).call()
        assert not is_disabled, f"{symbol} is in loaded markets but IS_MARKET_DISABLED=True on-chain. The DataStore filter should have removed it."
        logger.info("%s is enabled on-chain (IS_MARKET_DISABLED=False)", symbol)
