"""Unit tests for Hypersync session module.

Tests open_hypersync_stream() dispatch, ThrottledHypersyncClient tuning
parameters, and env var helpers. No network access or API key needed.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import hypersync

from eth_defi.hypersync.session import (
    DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE,
    ThrottledHypersyncClient,
    _get_positive_int_from_env,
    get_hypersync_concurrency_from_env,
    get_hypersync_rpm_from_env,
    open_hypersync_stream,
)


@pytest.fixture()
def mock_limiter() -> MagicMock:
    """A limiter that never blocks."""
    limiter = MagicMock()
    limiter.try_acquire = MagicMock()
    return limiter


@pytest.fixture()
def mock_native_client() -> AsyncMock:
    """A mock native HypersyncClient."""
    client = AsyncMock(spec=hypersync.HypersyncClient)
    client.stream = AsyncMock(return_value="native-receiver")
    return client


@pytest.fixture()
def throttled_client(mock_native_client: AsyncMock, mock_limiter: MagicMock) -> ThrottledHypersyncClient:
    """A ThrottledHypersyncClient with tuning params set."""
    return ThrottledHypersyncClient(
        mock_native_client,
        mock_limiter,
        concurrency=20,
        batch_size=5000,
    )


def test_open_hypersync_stream_native_and_throttled(
    mock_native_client: AsyncMock,
    throttled_client: ThrottledHypersyncClient,
    caplog,
):
    """Verify open_hypersync_stream dispatches correctly for both client types.

    1. Call with a native HypersyncClient — should pass bare StreamConfig()
    2. Call with a ThrottledHypersyncClient — should call stream(query) using stored params
    3. Assert tuning parameters appear in the log output for throttled client
    """
    query = MagicMock(spec=hypersync.Query)

    async def _run():
        # 1. Native client gets explicit StreamConfig
        result = await open_hypersync_stream(mock_native_client, query)
        assert result == "native-receiver"
        mock_native_client.stream.assert_called_once()
        call_args = mock_native_client.stream.call_args
        assert isinstance(call_args[0][1], hypersync.StreamConfig)

        # 2. Throttled client uses stored config
        mock_native_client.stream.reset_mock()
        with caplog.at_level(logging.INFO, logger="eth_defi.hypersync.session"):
            result = await open_hypersync_stream(throttled_client, query)

        # Verify stream was called with auto-built config
        mock_native_client.stream.assert_called_once()
        config_arg = mock_native_client.stream.call_args[0][1]
        assert isinstance(config_arg, hypersync.StreamConfig)
        assert config_arg.concurrency == 20
        assert config_arg.batch_size == 5000

        # 3. Verify tuning params logged
        assert "concurrency=20" in caplog.text
        assert "batch_size=5000" in caplog.text

    asyncio.run(_run())


def test_create_stream_config_overrides(throttled_client: ThrottledHypersyncClient):
    """Verify create_stream_config applies stored params and allows overrides.

    1. Build config from stored params
    2. Override one param
    3. Verify stored params unchanged
    """

    # 1. Stored params
    config = throttled_client.create_stream_config()
    assert config.concurrency == 20
    assert config.batch_size == 5000
    assert config.min_batch_size is None

    # 2. Override
    config2 = throttled_client.create_stream_config(concurrency=50, min_batch_size=100)
    assert config2.concurrency == 50
    assert config2.batch_size == 5000
    assert config2.min_batch_size == 100


def test_create_stream_config_no_params(mock_native_client: AsyncMock, mock_limiter: MagicMock):
    """Verify create_stream_config with no stored params produces bare config.

    1. Create client with no tuning params
    2. Build stream config
    3. All fields should be None (server defaults)
    """
    client = ThrottledHypersyncClient(mock_native_client, mock_limiter)
    config = client.create_stream_config()
    assert config.concurrency is None
    assert config.batch_size is None
    assert config.min_batch_size is None
    assert config.max_batch_size is None
    # Response byte params vary by platform — check whichever exists
    if hasattr(config, "response_bytes_ceiling"):
        assert config.response_bytes_ceiling is None
        assert config.response_bytes_floor is None
    if hasattr(config, "response_bytes_target"):
        assert config.response_bytes_target is None


def test_get_positive_int_from_env_valid():
    """Verify _get_positive_int_from_env parses valid values and defaults.

    1. Set env var to valid value
    2. Read it back
    3. Verify unset returns default
    """
    with patch.dict("os.environ", {"TEST_VAR": "42"}):
        assert _get_positive_int_from_env("TEST_VAR") == 42

    # Unset returns default
    with patch.dict("os.environ", {}, clear=True):
        assert _get_positive_int_from_env("TEST_VAR") is None
        assert _get_positive_int_from_env("TEST_VAR", 99) == 99


def test_get_positive_int_from_env_invalid():
    """Verify _get_positive_int_from_env rejects invalid values.

    1. Non-integer string raises ValueError
    2. Zero raises ValueError
    3. Negative raises ValueError
    """
    with patch.dict("os.environ", {"TEST_VAR": "abc"}):
        with pytest.raises(ValueError, match="positive integer"):
            _get_positive_int_from_env("TEST_VAR")

    with patch.dict("os.environ", {"TEST_VAR": "0"}):
        with pytest.raises(ValueError, match="positive integer"):
            _get_positive_int_from_env("TEST_VAR")

    with patch.dict("os.environ", {"TEST_VAR": "-5"}):
        with pytest.raises(ValueError, match="positive integer"):
            _get_positive_int_from_env("TEST_VAR")


def test_get_hypersync_concurrency_from_env():
    """Verify HYPERSYNC_CONCURRENCY env var reading.

    1. Set env var to valid value
    2. Verify unset returns None
    """
    with patch.dict("os.environ", {"HYPERSYNC_CONCURRENCY": "25"}):
        assert get_hypersync_concurrency_from_env() == 25

    with patch.dict("os.environ", {}, clear=True):
        assert get_hypersync_concurrency_from_env() is None


def test_get_hypersync_rpm_from_env_default():
    """Verify HYPERSYNC_RPM returns default (80) when unset.

    1. Unset env var
    2. Verify default returned
    """
    with patch.dict("os.environ", {}, clear=True):
        assert DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE == 80
        assert get_hypersync_rpm_from_env() == DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE
