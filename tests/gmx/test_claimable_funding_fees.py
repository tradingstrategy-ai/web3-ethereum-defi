"""Offline unit tests for the per-account GMX claimable funding-fee reader.

These tests cover :class:`eth_defi.gmx.core.claimable_funding_fees.GetClaimableFundingFees`
without any RPC or ``.local-test.env``:

- :meth:`~GetClaimableFundingFees._extract_amount` is a pure static helper and is
  exercised directly with fake ``EncodedCallResult``-shaped objects;
- :meth:`~GetClaimableFundingFees.get_claim_args` / ``get_claim_pairs`` /
  ``get_claimable_funding_fees`` are driven by a fixed per-market dict injected
  over ``get_per_market_claimable_funding_fees``, so no network read occurs.

The full constructor is intentionally bypassed (it fetches oracle prices), so the
reader instance is created with ``object.__new__``.
"""

from types import SimpleNamespace

import pytest
from eth_utils import keccak
from web3 import Web3

from eth_defi.gmx.constants import PRECISION
from eth_defi.gmx.core.claimable_funding_fees import (
    CLAIMABLE_FUNDING_FEES_PARAMETER,
    GetClaimableFundingFees,
)
from eth_defi.gmx.keys import claimable_funding_amount_key

#: Distinct fake addresses reused across the fixed fixtures.
MARKET_A = Web3.to_checksum_address("0x00000000000000000000000000000000000000a1")
MARKET_B = Web3.to_checksum_address("0x00000000000000000000000000000000000000b2")
MARKET_C = Web3.to_checksum_address("0x00000000000000000000000000000000000000c3")
MARKET_D = Web3.to_checksum_address("0x00000000000000000000000000000000000000d4")
TOKEN_LONG = Web3.to_checksum_address("0x00000000000000000000000000000000000000e5")
TOKEN_SHORT = Web3.to_checksum_address("0x00000000000000000000000000000000000000f6")
TOKEN_OTHER = Web3.to_checksum_address("0x0000000000000000000000000000000000000007")

#: Fixed per-market reader output, keyed by market symbol.
#:
#: - ``ETH/USD`` has both sides positive -> two claim pairs.
#: - ``BTC/USD`` has only the long side -> one claim pair.
#: - ``ZERO/USD`` is all zero -> no claim pair.
#: - ``TOKENLESS/USD`` has a positive raw amount but no token address -> skipped.
FIXED_MARKETS: dict[str, dict] = {
    "ETH/USD": {
        "market": MARKET_A,
        "long": 150.0,
        "short": 75.0,
        "total": 225.0,
        "long_token": TOKEN_LONG,
        "short_token": TOKEN_SHORT,
        "long_raw": 10**18,
        "short_raw": 2 * 10**18,
    },
    "BTC/USD": {
        "market": MARKET_B,
        "long": 40.0,
        "short": 0.0,
        "total": 40.0,
        "long_token": TOKEN_OTHER,
        "short_token": None,
        "long_raw": 3 * 10**8,
        "short_raw": 0,
    },
    "ZERO/USD": {
        "market": MARKET_C,
        "long": 0.0,
        "short": 0.0,
        "total": 0.0,
        "long_token": TOKEN_LONG,
        "short_token": TOKEN_SHORT,
        "long_raw": 0,
        "short_raw": 0,
    },
    "TOKENLESS/USD": {
        "market": MARKET_D,
        "long": 0.0,
        "short": 0.0,
        "total": 0.0,
        "long_token": None,
        "short_token": None,
        "long_raw": 5,
        "short_raw": 0,
    },
}


def _result(*, success: bool, result: bytes) -> SimpleNamespace:
    """Build a minimal ``EncodedCallResult``-shaped object."""
    return SimpleNamespace(success=success, result=result)


@pytest.fixture
def reader(monkeypatch) -> GetClaimableFundingFees:
    """A reader whose per-market lookup is stubbed with :data:`FIXED_MARKETS`."""
    instance = object.__new__(GetClaimableFundingFees)
    monkeypatch.setattr(instance, "get_per_market_claimable_funding_fees", lambda: FIXED_MARKETS)
    return instance


# =============================================================================
# _extract_amount
# =============================================================================


def test_extract_amount_reads_big_endian_int():
    """A successful result decodes as a big-endian unsigned integer."""
    value = 2**128 + 5
    results = {"long": _result(success=True, result=value.to_bytes(32, byteorder="big"))}
    assert GetClaimableFundingFees._extract_amount(results, "long") == value


def test_extract_amount_reads_whole_token_amount():
    """The full 32-byte word is consumed (values above 2**64 included)."""
    value = 10**18 + 123
    results = {"short": _result(success=True, result=value.to_bytes(32, byteorder="big"))}
    assert GetClaimableFundingFees._extract_amount(results, "short") == value


def test_extract_amount_missing_token_type_returns_zero():
    """A missing token side defaults to zero."""
    assert GetClaimableFundingFees._extract_amount({}, "long") == 0


def test_extract_amount_failed_call_returns_zero():
    """A reverted/failed call defaults to zero even if it carries a payload."""
    results = {"short": _result(success=False, result=(999).to_bytes(32, byteorder="big"))}
    assert GetClaimableFundingFees._extract_amount(results, "short") == 0


def test_extract_amount_empty_result_returns_zero():
    """An empty (falsy) result payload defaults to zero."""
    results = {"long": _result(success=True, result=b"")}
    assert GetClaimableFundingFees._extract_amount(results, "long") == 0


# =============================================================================
# get_claim_pairs / get_claim_args
# =============================================================================


def test_get_claim_pairs_only_positive_receipts(reader: GetClaimableFundingFees):
    """Only positive raw receipts with a token are turned into claim pairs."""
    pairs = reader.get_claim_pairs()

    assert all(raw > 0 for _market, _token, raw in pairs)
    assert pairs == [
        (MARKET_A, TOKEN_LONG, 10**18),
        (MARKET_A, TOKEN_SHORT, 2 * 10**18),
        (MARKET_B, TOKEN_OTHER, 3 * 10**8),
    ]


def test_get_claim_args_returns_aligned_arrays(reader: GetClaimableFundingFees):
    """The markets and tokens arrays are parallel and index-aligned."""
    markets, tokens = reader.get_claim_args()

    assert len(markets) == len(tokens)
    assert markets == [MARKET_A, MARKET_A, MARKET_B]
    assert tokens == [TOKEN_LONG, TOKEN_SHORT, TOKEN_OTHER]


def test_get_claim_args_skips_zero_and_tokenless_sides(reader: GetClaimableFundingFees):
    """All-zero markets and token-less receipts never reach the claim arrays."""
    markets, tokens = reader.get_claim_args()

    assert MARKET_C not in markets, "an all-zero market must be excluded"
    assert MARKET_D not in markets, "a token-less receipt must be excluded"
    assert None not in tokens


# =============================================================================
# get_claimable_funding_fees
# =============================================================================


def test_get_claimable_funding_fees_aggregates_totals(reader: GetClaimableFundingFees):
    """The summary exposes the parameter name and the summed USD total."""
    data = reader.get_claimable_funding_fees()

    assert data["parameter"] == CLAIMABLE_FUNDING_FEES_PARAMETER
    assert data["parameter"] == "total_funding_fees"
    assert data["markets"] == FIXED_MARKETS
    assert data["total_fees"] == pytest.approx(sum(market["total"] for market in FIXED_MARKETS.values()))


# =============================================================================
# Shared stubs for the multicall / valuation tests
# =============================================================================

#: First four bytes of the ``DataStore.getUint(bytes32)`` selector.
GET_UINT_SELECTOR = keccak(text="getUint(bytes32)")[:4]

#: DataStore and account addresses used by the multicall and valuation tests.
DATASTORE_ADDRESS = Web3.to_checksum_address("0x00000000000000000000000000000000000000d0")
ACCOUNT_ADDRESS = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")


class _FakeMarkets:
    """Minimal ``Markets`` stub exposing token addresses and decimal factors."""

    def __init__(self, *, long_token, short_token, long_decimals: int = 18, short_decimals: int = 18):
        self._long_token = long_token
        self._short_token = short_token
        self._long_decimals = long_decimals
        self._short_decimals = short_decimals

    def get_long_token_address(self, _market_key=None) -> str | None:
        return self._long_token

    def get_short_token_address(self, _market_key=None) -> str | None:
        return self._short_token

    def get_decimal_factor(self, market_key, long: bool = False, short: bool = False) -> int:  # noqa: ARG002, FBT001, FBT002 - stub mirrors the Markets API surface
        return self._long_decimals if long else self._short_decimals


def _make_reader(monkeypatch, *, markets: _FakeMarkets, oracle_prices: dict | None = None, account: str = ACCOUNT_ADDRESS) -> GetClaimableFundingFees:
    """Build a reader without running the constructor's oracle/live reads."""
    reader = object.__new__(GetClaimableFundingFees)
    reader.account = account
    reader.markets = markets
    reader.datastore_contract = SimpleNamespace(address=DATASTORE_ADDRESS)
    reader.oracle_prices = {} if oracle_prices is None else oracle_prices
    reader._long_token_address = None
    reader._short_token_address = None

    def _fake_get_token_addresses(market_key):
        reader._long_token_address = reader.markets.get_long_token_address(market_key)
        reader._short_token_address = reader.markets.get_short_token_address(market_key)

    monkeypatch.setattr(reader, "_get_token_addresses", _fake_get_token_addresses)
    return reader


def _word(value: int) -> bytes:
    """Encode a uint256 as a 32-byte big-endian word."""
    return value.to_bytes(32, byteorder="big")


# =============================================================================
# generate_all_multicalls
# =============================================================================


def test_generate_all_multicalls_encodes_getuint_per_side(monkeypatch):
    """One ``DataStore.getUint`` call per side, keyed by the claimable-funding key."""
    markets = _FakeMarkets(long_token=TOKEN_LONG, short_token=TOKEN_SHORT)
    reader = _make_reader(monkeypatch, markets=markets)

    calls = list(reader.generate_all_multicalls({MARKET_A: {}}))

    assert len(calls) == 2, "a two-sided market must yield a long and a short read"  # noqa: PLR2004
    long_call, short_call = calls

    expected_keys = {
        "long": claimable_funding_amount_key(MARKET_A, TOKEN_LONG, ACCOUNT_ADDRESS),
        "short": claimable_funding_amount_key(MARKET_A, TOKEN_SHORT, ACCOUNT_ADDRESS),
    }

    assert long_call.extra_data["token_type"] == "long"
    assert short_call.extra_data["token_type"] == "short"

    for call in calls:
        token_type = call.extra_data["token_type"]
        assert call.address == DATASTORE_ADDRESS
        assert call.data[:4] == GET_UINT_SELECTOR, "the calldata must start with the getUint(bytes32) selector"
        assert call.data[4:] == expected_keys[token_type], "the payload must be the claimable-funding key"
        assert call.func_name == f"{token_type}_funding"
        assert call.extra_data["market_key"] == MARKET_A
        assert call.extra_data["function"] == f"{token_type}_funding"


def test_generate_all_multicalls_skips_missing_side(monkeypatch):
    """A side without a token address does not produce a call."""
    markets = _FakeMarkets(long_token=TOKEN_LONG, short_token=None)
    reader = _make_reader(monkeypatch, markets=markets)

    calls = list(reader.generate_all_multicalls({MARKET_A: {}}))

    assert len(calls) == 1
    assert calls[0].extra_data["token_type"] == "long"
    assert calls[0].data[4:] == claimable_funding_amount_key(MARKET_A, TOKEN_LONG, ACCOUNT_ADDRESS)


# =============================================================================
# _process_market: shared long/short token dedup
# =============================================================================


def test_process_market_zeroes_shared_long_short_receipt(monkeypatch):
    """A market whose long/short tokens are identical stores one shared balance."""
    shared_token = TOKEN_LONG
    markets = _FakeMarkets(long_token=shared_token, short_token=shared_token)
    reader = _make_reader(monkeypatch, markets=markets, oracle_prices={})

    raw = 7 * 10**18
    results = {
        "long": _result(success=True, result=_word(raw)),
        "short": _result(success=True, result=_word(raw)),
    }

    record = reader._process_market(MARKET_A, results)

    assert record["long_raw"] == raw
    assert record["short_raw"] == 0, "the shared balance must not be counted twice"
    assert record["long_token"].lower() == shared_token.lower()
    assert record["short_token"].lower() == shared_token.lower()


def test_process_market_keeps_distinct_sides(monkeypatch):
    """Distinct long/short tokens keep their independent raw receipts."""
    markets = _FakeMarkets(long_token=TOKEN_LONG, short_token=TOKEN_SHORT)
    reader = _make_reader(monkeypatch, markets=markets, oracle_prices={})

    long_raw, short_raw = 3 * 10**18, 5 * 10**18
    results = {
        "long": _result(success=True, result=_word(long_raw)),
        "short": _result(success=True, result=_word(short_raw)),
    }

    record = reader._process_market(MARKET_A, results)

    assert record["long_raw"] == long_raw
    assert record["short_raw"] == short_raw


# =============================================================================
# _claimable_usd valuation math
# =============================================================================


def test_claimable_usd_matches_oracle_mid_price(monkeypatch):
    """USD value is ``(raw / 10**decimals) * (mid / 10**(PRECISION - decimals))``."""
    long_decimals, short_decimals = 18, 6
    long_min = long_max = 2_000_000_000_000_000_000_000_000_000
    short_min = short_max = 1_000_000_000_000_000_000_000_000_000
    oracle_prices = {
        TOKEN_LONG: {"minPriceFull": str(long_min), "maxPriceFull": str(long_max)},
        TOKEN_SHORT: {"minPriceFull": str(short_min), "maxPriceFull": str(short_max)},
    }
    markets = _FakeMarkets(
        long_token=TOKEN_LONG,
        short_token=TOKEN_SHORT,
        long_decimals=long_decimals,
        short_decimals=short_decimals,
    )
    reader = _make_reader(monkeypatch, markets=markets, oracle_prices=oracle_prices)

    long_raw = 4 * 10**long_decimals
    long_mid = (long_max + long_min) / 2.0
    long_expected = (long_raw / 10**long_decimals) * (long_mid / 10 ** (PRECISION - long_decimals))
    assert reader._claimable_usd(MARKET_A, TOKEN_LONG, long_raw, is_long=True) == pytest.approx(long_expected)

    short_raw = 9 * 10**short_decimals
    short_mid = (short_max + short_min) / 2.0
    short_expected = (short_raw / 10**short_decimals) * (short_mid / 10 ** (PRECISION - short_decimals))
    assert reader._claimable_usd(MARKET_A, TOKEN_SHORT, short_raw, is_long=False) == pytest.approx(short_expected)


def test_claimable_usd_zero_for_unpriced_token(monkeypatch):
    """A token absent from the oracle feed is valued at zero."""
    markets = _FakeMarkets(long_token=TOKEN_LONG, short_token=TOKEN_SHORT)
    reader = _make_reader(monkeypatch, markets=markets, oracle_prices={TOKEN_OTHER: {"minPriceFull": "1", "maxPriceFull": "1"}})

    assert reader._claimable_usd(MARKET_A, TOKEN_LONG, 10**18, is_long=True) == pytest.approx(0.0)


def test_claimable_usd_zero_for_incomplete_oracle_entry(monkeypatch):
    """An oracle entry missing a min/max price field is valued at zero."""
    markets = _FakeMarkets(long_token=TOKEN_LONG, short_token=TOKEN_SHORT)
    reader = _make_reader(monkeypatch, markets=markets, oracle_prices={TOKEN_LONG: {"minPriceFull": "1"}})

    assert reader._claimable_usd(MARKET_A, TOKEN_LONG, 10**18, is_long=True) == pytest.approx(0.0)


def test_claimable_usd_zero_for_nonpositive_amount(monkeypatch):
    """A zero raw amount is never priced."""
    markets = _FakeMarkets(long_token=TOKEN_LONG, short_token=TOKEN_SHORT)
    reader = _make_reader(monkeypatch, markets=markets, oracle_prices={TOKEN_LONG: {"minPriceFull": "1", "maxPriceFull": "1"}})

    assert reader._claimable_usd(MARKET_A, TOKEN_LONG, 0, is_long=True) == pytest.approx(0.0)


def test_claimable_usd_zero_for_missing_token(monkeypatch):
    """A missing token address is valued at zero."""
    markets = _FakeMarkets(long_token=TOKEN_LONG, short_token=TOKEN_SHORT)
    reader = _make_reader(monkeypatch, markets=markets, oracle_prices={})

    assert reader._claimable_usd(MARKET_A, None, 10**18, is_long=True) == pytest.approx(0.0)
