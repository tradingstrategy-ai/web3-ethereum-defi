"""Unit tests for proxy rotation reason logging.

These tests do not need network access or a Webshare API key — they build a
:class:`ProxyRotator` from synthetic proxies and assert on captured log output.
"""

import logging
from pathlib import Path

import pytest

from eth_defi.event_reader.webshare import ProxyRotator, ProxyStateManager, WebshareProxy


def _make_proxy(port: int, country: str) -> WebshareProxy:
    """Build a synthetic backbone proxy entry for testing."""
    return WebshareProxy(
        proxy_address=None,
        port=port,
        username=f"user-{country}-{port}",
        password="secret",
        country_code=country,
        city_name=None,
    )


@pytest.fixture()
def state_manager(tmp_path: Path) -> ProxyStateManager:
    """A state manager backed by a temp file so failure recording is observable."""
    return ProxyStateManager(state_path=tmp_path / "proxy-state.json")


@pytest.fixture()
def rotator(state_manager: ProxyStateManager) -> ProxyRotator:
    """A two-proxy rotator logging at INFO so caplog can capture reasons."""
    return ProxyRotator(
        proxies=[_make_proxy(10000, "FI"), _make_proxy(10001, "US")],
        state_manager=state_manager,
        log_level=logging.INFO,
    )


def test_rotation_logs_proactive_reason(
    rotator: ProxyRotator,
    state_manager: ProxyStateManager,
    caplog: pytest.LogCaptureFixture,
):
    """Proactive rotations log the reason without marking the proxy as failed.

    1. Rotate with an explicit ``reason`` (no failure recorded).
    2. Assert the rotation log line contains that reason verbatim.
    3. Assert no proxy was recorded as blocked by the state manager.
    """
    # 1. Rotate with an explicit reason (load-distribution style rotation)
    with caplog.at_level(logging.INFO, logger="eth_defi.event_reader.webshare"):
        rotator.rotate(reason="new source request: https://example.com/rss/")

    # 2. The reason must appear in the emitted log line
    assert "reason: new source request: https://example.com/rss/" in caplog.text

    # 3. A proactive rotation must NOT mark the previous proxy as dead
    assert state_manager.get_blocked_count() == 0


def test_rotation_logs_failure_reason(
    rotator: ProxyRotator,
    state_manager: ProxyStateManager,
    caplog: pytest.LogCaptureFixture,
):
    """Failure-triggered rotations log the failure and mark the proxy as failed.

    1. Rotate with a ``failure_reason`` and no separate ``reason``.
    2. Assert the failure string is logged as the rotation reason.
    3. Assert the previous proxy was recorded as blocked.
    """
    # 1. Rotate due to a failure of the previous proxy
    with caplog.at_level(logging.INFO, logger="eth_defi.event_reader.webshare"):
        rotator.rotate(failure_reason="HTTP 503")

    # 2. failure_reason doubles as the logged rotation reason
    assert "reason: HTTP 503" in caplog.text

    # 3. A failure rotation records the previous proxy in the state manager
    assert state_manager.get_blocked_count() == 1


def test_rotation_logs_sentinel_when_no_reason(rotator: ProxyRotator, caplog: pytest.LogCaptureFixture):
    """When neither reason is given, a sentinel keeps the cause non-silent.

    1. Rotate with no reason at all.
    2. Assert the fallback sentinel reason is logged rather than nothing.
    """
    # 1. Rotate with no reason supplied at all
    with caplog.at_level(logging.INFO, logger="eth_defi.event_reader.webshare"):
        rotator.rotate()

    # 2. A sentinel reason is logged so the cause is never silently blank
    assert "reason: no reason given" in caplog.text
