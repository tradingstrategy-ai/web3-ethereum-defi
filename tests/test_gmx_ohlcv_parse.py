"""Pure GMX candle parsing needs neither RPC credentials nor an exchange instance."""

import pytest

from eth_defi.gmx.freqtrade.sensitive_filter import is_logging_patched, patch_logging, unpatch_logging

_logging_was_patched = is_logging_patched()
patch_logging()

from eth_defi.gmx.ccxt.exchange import GMX  # ruff: ignore[module-import-not-at-top-of-file]

if not _logging_was_patched:
    unpatch_logging()


@pytest.fixture(autouse=True)
def scoped_sensitive_logging():
    """Keep redaction scoped so collection cannot change unrelated log tests."""
    already_patched = is_logging_patched()
    patch_logging()
    try:
        yield
    finally:
        if not already_patched:
            unpatch_logging()


@pytest.mark.parametrize("prices", [[100, 102, 99, 101], ["100", "102", "99", "101"]])
def test_oracle_candle_volume_is_always_zero(prices: list) -> None:
    """Preserve timestamp/prices and use zero for the absent volume field."""
    candle = [1792713600, *prices]
    original = candle.copy()
    parsed = GMX.parse_ohlcv(None, candle)
    assert parsed == [1792713600000, 100.0, 102.0, 99.0, 101.0, 0.0]
    assert candle == original
