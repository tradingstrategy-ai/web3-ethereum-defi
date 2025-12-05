"""Tests for GMX Freqtrade backtest timerange validation.

Tests the validation logic that checks if backtest timeranges fall within
available historical data stored in feather files.
"""

from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError

# Try to import GMXExchange, skip all tests if freqtrade is not installed
try:
    from eth_defi.gmx.freqtrade.gmx_exchange import GMXExchange

    FREQTRADE_AVAILABLE = True
except ImportError:
    FREQTRADE_AVAILABLE = False
    GMXExchange = None

pytestmark = pytest.mark.skipif(not FREQTRADE_AVAILABLE, reason="Freqtrade not installed - these tests require freqtrade package")


def test_validate_backtest_timerange_with_valid_date():
    """Test validation passes when requested date matches available data date.

    - Creates feather file with data starting on 2025-10-31 at 21:05:00
    - Requests timerange starting 2025-10-31 at 00:00:00 (midnight)
    - Validation should pass because dates match (time difference ignored)
    """
    with TemporaryDirectory() as tmpdir:
        # Create test data directory structure
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create feather file with data starting 2025-10-31 21:05:00
        dates = pd.date_range(
            start="2025-10-31 21:05:00",
            periods=100,
            freq="5min",
            tz=timezone.utc,
        )
        df = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * 100,
                "high": [101.0] * 100,
                "low": [99.0] * 100,
                "close": [100.5] * 100,
                "volume": [1000.0] * 100,
            }
        )

        feather_path = datadir / "ETH_USDC_USDC-5m-futures.feather"
        df.to_feather(feather_path)

        # Create minimal config for validation
        config = {
            "runmode": "backtest",
            "timerange": "20251031-20251205",
            "timeframe": "5m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Create exchange instance and validate
        exchange = GMXExchange(config)
        exchange.validate_config(config)

        # If we get here without exception, validation passed


def test_validate_backtest_timerange_with_future_date():
    """Test validation fails when requested date is after available data.

    - Creates feather file with data ending 2025-12-05
    - Requests timerange starting 2026-01-01 (future date)
    - Should raise InsufficientHistoricalDataError
    """
    with TemporaryDirectory() as tmpdir:
        # Create test data directory structure
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create feather file with recent data
        dates = pd.date_range(
            start="2025-10-31 21:05:00",
            end="2025-12-05 14:45:00",
            freq="5min",
            tz=timezone.utc,
        )
        df = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * len(dates),
                "high": [101.0] * len(dates),
                "low": [99.0] * len(dates),
                "close": [100.5] * len(dates),
                "volume": [1000.0] * len(dates),
            }
        )

        feather_path = datadir / "ETH_USDC_USDC-5m-futures.feather"
        df.to_feather(feather_path)

        # Create config with future timerange
        config = {
            "runmode": "backtest",
            "timerange": "20260101-20260131",
            "timeframe": "5m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Validation should fail
        with pytest.raises(InsufficientHistoricalDataError) as exc_info:
            exchange = GMXExchange(config)
            exchange.validate_config(config)

        # Verify error message contains relevant information
        error = exc_info.value
        assert error.symbol == "ETH/USDC:USDC"
        assert error.timeframe == "5m"
        assert error.available_start is not None
        assert error.available_end is not None


def test_validate_backtest_timerange_with_missing_file():
    """Test validation fails when feather file doesn't exist.

    - Requests data for a pair that has no feather file
    - Should raise InsufficientHistoricalDataError with 0 candles received
    """
    with TemporaryDirectory() as tmpdir:
        # Create empty data directory (no feather files)
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create config requesting data that doesn't exist
        config = {
            "runmode": "backtest",
            "timerange": "20251031-20251205",
            "timeframe": "5m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Validation should fail with file not found
        with pytest.raises(InsufficientHistoricalDataError) as exc_info:
            exchange = GMXExchange(config)
            exchange.validate_config(config)

        # Verify error indicates no data available
        error = exc_info.value
        assert error.symbol == "ETH/USDC:USDC"
        assert error.candles_received == 0
        assert error.available_start is None
        assert error.available_end is None


def test_validate_backtest_timerange_with_gap_in_data():
    """Test validation fails when data starts after requested date.

    - Creates feather file with data starting 2025-11-01
    - Requests timerange starting 2025-10-01 (before data availability)
    - Should raise InsufficientHistoricalDataError
    """
    with TemporaryDirectory() as tmpdir:
        # Create test data directory structure
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create feather file with data starting November 1st
        dates = pd.date_range(
            start="2025-11-01 00:00:00",
            periods=1000,
            freq="5min",
            tz=timezone.utc,
        )
        df = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * 1000,
                "high": [101.0] * 1000,
                "low": [99.0] * 1000,
                "close": [100.5] * 1000,
                "volume": [1000.0] * 1000,
            }
        )

        feather_path = datadir / "ETH_USDC_USDC-5m-futures.feather"
        df.to_feather(feather_path)

        # Request data from October (before available data)
        config = {
            "runmode": "backtest",
            "timerange": "20251001-20251130",
            "timeframe": "5m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Validation should fail
        with pytest.raises(InsufficientHistoricalDataError) as exc_info:
            exchange = GMXExchange(config)
            exchange.validate_config(config)

        # Verify error details
        error = exc_info.value
        assert error.symbol == "ETH/USDC:USDC"
        # Requested Oct 1, but data starts Nov 1
        assert error.requested_start < error.available_start


def test_validate_backtest_no_timerange_specified():
    """Test validation skips when no timerange is specified.

    - Creates config without timerange parameter
    - Validation should skip (use all available data)
    - Should not raise any error
    """
    with TemporaryDirectory() as tmpdir:
        # Create test data directory structure
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create feather file with some data
        dates = pd.date_range(
            start="2025-10-31 00:00:00",
            periods=100,
            freq="5min",
            tz=timezone.utc,
        )
        df = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * 100,
                "high": [101.0] * 100,
                "low": [99.0] * 100,
                "close": [100.5] * 100,
                "volume": [1000.0] * 100,
            }
        )

        feather_path = datadir / "ETH_USDC_USDC-5m-futures.feather"
        df.to_feather(feather_path)

        # Create config without timerange
        config = {
            "runmode": "backtest",
            "timeframe": "5m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Validation should pass (skip validation when no timerange)
        exchange = GMXExchange(config)
        exchange.validate_config(config)


def test_validate_backtest_multiple_pairs():
    """Test validation checks all pairs in whitelist.

    - Creates data for ETH/USDC:USDC only
    - Requests backtest for both ETH/USDC:USDC and BTC/USDC:USDC
    - Should fail because BTC/USDC:USDC data is missing
    """
    with TemporaryDirectory() as tmpdir:
        # Create test data directory structure
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create feather file for ETH/USDC only
        dates = pd.date_range(
            start="2025-10-31 00:00:00",
            periods=100,
            freq="5min",
            tz=timezone.utc,
        )
        df = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * 100,
                "high": [101.0] * 100,
                "low": [99.0] * 100,
                "close": [100.5] * 100,
                "volume": [1000.0] * 100,
            }
        )

        eth_path = datadir / "ETH_USDC_USDC-5m-futures.feather"
        df.to_feather(eth_path)

        # Request both ETH and BTC (but BTC data doesn't exist)
        config = {
            "runmode": "backtest",
            "timerange": "20251031-20251105",
            "timeframe": "5m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC", "BTC/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Validation should fail on BTC/USDC:USDC
        with pytest.raises(InsufficientHistoricalDataError) as exc_info:
            exchange = GMXExchange(config)
            exchange.validate_config(config)

        # Verify it failed on BTC pair
        error = exc_info.value
        assert error.symbol == "BTC/USDC:USDC"
        assert error.candles_received == 0


def test_validate_backtest_different_timeframe():
    """Test validation uses correct timeframe for file lookup.

    - Creates 15m timeframe data file
    - Requests validation for 15m timeframe
    - Should pass validation
    """
    with TemporaryDirectory() as tmpdir:
        # Create test data directory structure
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create 15m timeframe data
        dates = pd.date_range(
            start="2025-10-31 00:00:00",
            periods=100,
            freq="15min",
            tz=timezone.utc,
        )
        df = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * 100,
                "high": [101.0] * 100,
                "low": [99.0] * 100,
                "close": [100.5] * 100,
                "volume": [1000.0] * 100,
            }
        )

        # Save with 15m timeframe in filename
        feather_path = datadir / "ETH_USDC_USDC-15m-futures.feather"
        df.to_feather(feather_path)

        # Request validation for 15m timeframe
        config = {
            "runmode": "backtest",
            "timerange": "20251031-20251103",
            "timeframe": "15m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Validation should pass
        exchange = GMXExchange(config)
        exchange.validate_config(config)


def test_validate_backtest_only_runs_in_backtest_mode():
    """Test validation only runs when runmode is backtest or hyperopt.

    - Creates config with runmode='dry_run'
    - Validation should skip entirely
    - Should not check for data files or raise errors
    """
    with TemporaryDirectory() as tmpdir:
        # Create empty data directory (no files)
        datadir = Path(tmpdir) / "data" / "gmx" / "futures"
        datadir.mkdir(parents=True)

        # Create config with dry_run mode (not backtest)
        config = {
            "runmode": "dry_run",
            "timerange": "20251031-20251205",
            "timeframe": "5m",
            "user_data_dir": tmpdir,
            "datadir": str(datadir),
            "exchange": {
                "name": "gmx",
                "pair_whitelist": ["ETH/USDC:USDC"],
            },
            "margin_mode": "isolated",
        }

        # Validation should skip (no backtest validation for dry_run)
        exchange = GMXExchange(config)
        exchange.validate_config(config)
