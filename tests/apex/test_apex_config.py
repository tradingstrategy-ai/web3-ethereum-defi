"""ApeX command configuration tests."""

# ruff: noqa: PLR2004, S108

import datetime

import pytest

from eth_defi.apex.config import ApexReaderConfig, parse_apex_duration


@pytest.mark.parametrize(
    ("raw", "seconds"),
    (
        ("30s", 30),
        ("30m", 1_800),
        ("90m", 5_400),
        ("1.5h", 5_400),
        ("2d", 172_800),
    ),
)
def test_parse_apex_duration(raw: str, seconds: float) -> None:
    """Accept supported positive decimal duration units."""
    assert parse_apex_duration(raw) == datetime.timedelta(seconds=seconds)


@pytest.mark.parametrize("raw", ("", "0h", "-1h", "1", "1w", "nanh", "infh"))
def test_parse_apex_duration_rejects_invalid(raw: str) -> None:
    """Reject malformed, unsupported and non-positive durations."""
    with pytest.raises(ValueError):
        parse_apex_duration(raw)


def test_parse_apex_environment() -> None:
    """Parse and deduplicate a complete command environment."""
    config = ApexReaderConfig.from_environment(
        {
            "LOG_LEVEL": "debug",
            "DB_PATH": "/tmp/custom-apex.duckdb",
            "VAULT_IDS": "2,1,2",
            "MAX_WORKERS": "3",
            "REQUESTS_PER_SECOND": "2.5",
            "HISTORY_MODE": "none",
            "HISTORY_REFRESH_INTERVAL": "30m",
            "LOOP": "true",
            "SCAN_INTERVAL": "1.5h",
        }
    )
    assert config.log_level == "debug"
    assert str(config.db_path) == "/tmp/custom-apex.duckdb"
    assert config.vault_ids == ("2", "1")
    assert config.max_workers == 3
    assert config.requests_per_second == 2.5
    assert config.history_mode == "none"
    assert config.loop is True
    assert config.scan_interval == datetime.timedelta(minutes=90)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("MAX_WORKERS", "0"),
        ("REQUESTS_PER_SECOND", "nan"),
        ("CONNECT_TIMEOUT", "inf"),
        ("MAX_RESPONSE_BYTES", "-1"),
        ("HISTORY_MODE", "daily"),
    ),
)
def test_parse_apex_environment_rejects_invalid(name: str, value: str) -> None:
    """Reject invalid finite-positive and mode settings."""
    with pytest.raises(ValueError):
        ApexReaderConfig.from_environment({name: value})
