"""Unit tests for HyperliquidSession.post_info rotation behaviour.

Covers the 429-vs-connection-error distinction added for market-data
bulk downloads: throttled responses must rotate without marking the
proxy as dead; connection errors still mark it dead.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from eth_defi.event_reader.webshare import ProxyRotator, WebshareProxy
from eth_defi.hyperliquid.session import create_hyperliquid_session


def _make_proxy(idx: int) -> WebshareProxy:
    return WebshareProxy(
        proxy_address=f"10.0.0.{idx}",
        port=8080 + idx,
        username="u",
        password="p",
        country_code="US",
        city_name="NYC",
    )


def _fake_rotator(n_proxies: int = 3) -> ProxyRotator:
    """Build a :class:`ProxyRotator` with a mocked state manager.

    The state manager is a :class:`~unittest.mock.MagicMock` so tests can
    assert whether ``record_failure`` was called without touching the
    on-disk grace-period store.
    """
    state_mgr = MagicMock()
    state_mgr.is_blocked.return_value = False
    state_mgr.record_failure = MagicMock()
    return ProxyRotator(
        proxies=[_make_proxy(i) for i in range(n_proxies)],
        state_manager=state_mgr,
    )


@pytest.fixture
def session_with_proxies():
    rotator = _fake_rotator()
    return create_hyperliquid_session(rotator=rotator)


def test_429_rotates_without_recording_failure(session_with_proxies):
    """A 429 response must rotate the proxy but NOT mark it dead."""
    rotator = session_with_proxies._rotator
    state_mgr = rotator.state_manager
    initial_proxy = rotator.current()

    # First call returns 429, second returns 200
    responses = [
        MagicMock(status_code=429),
        MagicMock(status_code=200),
    ]
    with patch.object(session_with_proxies, "post", side_effect=responses) as m:
        resp = session_with_proxies.post_info({"type": "meta"})

    assert resp.status_code == 200
    assert m.call_count == 2
    # Rotator advanced to the next proxy
    assert rotator.current() != initial_proxy
    # But state manager was NOT told to record a failure
    state_mgr.record_failure.assert_not_called()


def test_503_rotates_without_recording_failure(session_with_proxies):
    """503 (upstream overload) behaves the same as 429."""
    rotator = session_with_proxies._rotator
    state_mgr = rotator.state_manager
    responses = [MagicMock(status_code=503), MagicMock(status_code=200)]
    with patch.object(session_with_proxies, "post", side_effect=responses):
        session_with_proxies.post_info({"type": "meta"})
    state_mgr.record_failure.assert_not_called()


def test_504_rotates_without_recording_failure(session_with_proxies):
    """504 gateway timeout is treated as throttle, not a dead proxy."""
    rotator = session_with_proxies._rotator
    state_mgr = rotator.state_manager
    responses = [MagicMock(status_code=504), MagicMock(status_code=200)]
    with patch.object(session_with_proxies, "post", side_effect=responses):
        session_with_proxies.post_info({"type": "meta"})
    state_mgr.record_failure.assert_not_called()


def test_connection_error_rotates_and_records_failure(session_with_proxies):
    """A real connection error must mark the proxy dead via ProxyStateManager."""
    rotator = session_with_proxies._rotator
    state_mgr = rotator.state_manager
    initial_proxy = rotator.current()

    side_effects = [
        requests.ConnectionError("boom"),
        MagicMock(status_code=200),
    ]
    with patch.object(session_with_proxies, "post", side_effect=side_effects):
        session_with_proxies.post_info({"type": "meta"})

    # Rotator advanced
    assert rotator.current() != initial_proxy
    # State manager WAS told to record a failure
    assert state_mgr.record_failure.called
    recorded = state_mgr.record_failure.call_args
    # First arg is the failed proxy, second is the reason string
    assert recorded.args[0].proxy_address == initial_proxy.proxy_address


def test_timeout_rotates_and_records_failure(session_with_proxies):
    """A requests.Timeout is a genuine connectivity signal — mark dead."""
    rotator = session_with_proxies._rotator
    state_mgr = rotator.state_manager
    side_effects = [
        requests.Timeout("slow"),
        MagicMock(status_code=200),
    ]
    with patch.object(session_with_proxies, "post", side_effect=side_effects):
        session_with_proxies.post_info({"type": "meta"})
    assert state_mgr.record_failure.called


def test_rotation_budget_respected(session_with_proxies):
    """After max_proxy_rotations, stop rotating and return the last response."""
    session_with_proxies.max_proxy_rotations = 2
    # Every request returns 429 — rotate twice then give up
    responses = [MagicMock(status_code=429)] * 5
    with patch.object(session_with_proxies, "post", side_effect=responses) as m:
        resp = session_with_proxies.post_info({"type": "meta"})
    assert resp.status_code == 429
    # 1 initial + 2 rotations = 3 calls
    assert m.call_count == 3


def test_no_rotation_when_proxies_disabled():
    """Without a rotator, 429 just returns to the caller without rotation."""
    session = create_hyperliquid_session()  # no rotator
    responses = [MagicMock(status_code=429)]
    with patch.object(session, "post", side_effect=responses) as m:
        resp = session.post_info({"type": "meta"})
    assert resp.status_code == 429
    assert m.call_count == 1


def test_successful_200_short_circuits(session_with_proxies):
    """A 200 response returns immediately without rotation."""
    rotator = session_with_proxies._rotator
    state_mgr = rotator.state_manager
    initial_proxy = rotator.current()

    responses = [MagicMock(status_code=200)]
    with patch.object(session_with_proxies, "post", side_effect=responses) as m:
        resp = session_with_proxies.post_info({"type": "meta"})

    assert resp.status_code == 200
    assert m.call_count == 1
    assert rotator.current() == initial_proxy
    state_mgr.record_failure.assert_not_called()
