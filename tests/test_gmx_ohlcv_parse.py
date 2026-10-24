"""Pure GMX candle parsing needs neither RPC credentials nor an exchange instance."""

import pytest

from eth_defi.gmx.freqtrade.sensitive_filter import patch_logging

patch_logging()

from eth_defi.gmx.ccxt.exchange import GMX  # ruff: ignore[E402]


@pytest.mark.parametrize("prices", [[100, 102, 99, 101], ["100", "102", "99", "101"]])
def test_oracle_candle_volume_is_always_zero(prices: list) -> None:
    """Preserve timestamp/prices and use zero for the absent volume field."""
    candle = [1792713600, *prices]
    original = candle.copy()
    parsed = GMX.parse_ohlcv(None, candle)
    assert parsed == [1792713600000, 100.0, 102.0, 99.0, 101.0, 0.0]
    assert candle == original
