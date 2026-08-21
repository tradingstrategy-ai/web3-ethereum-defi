import pytest

from eth_defi.gmx.ticker_validation import GMXInvalidPayloadError, validate_tickers_payload


def _ticker(address: str = "0xaaa", max_price: str = "1000") -> dict:
    return {"tokenAddress": address, "maxPrice": max_price}


def test_validate_tickers_payload_accepts_healthy_payload():
    payload = [_ticker(f"0x{i:03x}") for i in range(120)]
    assert validate_tickers_payload(payload) is True


def test_validate_tickers_payload_rejects_non_list():
    assert validate_tickers_payload({"markets": []}) is False


def test_validate_tickers_payload_rejects_empty_list():
    assert validate_tickers_payload([]) is False


def test_validate_tickers_payload_rejects_below_minimum_count():
    payload = [_ticker() for _ in range(10)]
    assert validate_tickers_payload(payload, min_expected_tickers=100) is False


def test_validate_tickers_payload_rejects_bad_schema_in_first_five():
    payload = [{"tokenAddress": "0x1"}] * 5 + [_ticker() for _ in range(120)]
    assert validate_tickers_payload(payload) is False


def test_validate_tickers_payload_rejects_truncated_below_ratio():
    payload = [_ticker() for _ in range(110)]
    # last_good_count=124 -> 80% floor = 99; 110 passes
    assert validate_tickers_payload(payload, last_good_count=124) is True
    truncated = [_ticker() for _ in range(90)]
    assert validate_tickers_payload(truncated, last_good_count=124) is False
