"""Unit tests for Webshare proxy failure state."""

import datetime
import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from _pytest.logging import LogCaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from eth_defi.event_reader import webshare
from eth_defi.event_reader.webshare import ProxyStateManager, WebshareProxy, load_proxy_rotator, load_proxy_urls


def _make_proxy(username: str) -> WebshareProxy:
    """Create a synthetic Webshare proxy for status tests.

    The proxy is never contacted over network. It only needs enough identity
    fields for :class:`~eth_defi.event_reader.webshare.ProxyStateManager`.

    :param username:
        Proxy username used as the backbone proxy identifier.
    :return:
        Synthetic proxy entry.
    """
    return WebshareProxy(
        proxy_address=None,
        port=80,
        username=username,
        password="password",
        country_code="FI",
        city_name="Helsinki",
    )


def _make_fetch_proxy_list(proxies: list[WebshareProxy]) -> Callable[[str, str], list[WebshareProxy]]:
    """Create a Webshare proxy list mock.

    The mock validates the expected test API key and proxy mode so that the
    tests cover the production call signature.

    :param proxies:
        Synthetic proxies to return.
    :return:
        Mock function for :func:`eth_defi.event_reader.webshare.fetch_proxy_list`.
    """

    def _fetch_proxy_list(api_key: str, mode: str = "backbone") -> list[WebshareProxy]:
        assert api_key == "test-key"
        assert mode == "backbone"
        return proxies

    return _fetch_proxy_list


def test_load_proxy_rotator_warns_how_to_reset_exhausted_proxy_status(monkeypatch: MonkeyPatch, tmp_path: Path, caplog: LogCaptureFixture) -> None:
    """All blocked proxies emit reset instructions as a warning.

    Operators need actionable instructions when cached Webshare proxy failure
    status has blocked the whole pool.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    :param tmp_path:
        Temporary path fixture.
    :param caplog:
        Pytest log capture fixture.
    """
    state_path = tmp_path / "webshare-proxy-state.json"
    proxies = [_make_proxy("proxy-1"), _make_proxy("proxy-2")]
    state_manager = ProxyStateManager(state_path=state_path)
    for proxy in proxies:
        state_manager.record_failure(proxy, "test_failure")

    monkeypatch.setenv("WEBSHARE_API_KEY", "test-key")
    monkeypatch.setattr(webshare, "DEFAULT_PROXY_STATE_PATH", state_path)
    monkeypatch.setattr(webshare, "fetch_proxy_list", _make_fetch_proxy_list(proxies))

    caplog.set_level(logging.WARNING, logger=webshare.__name__)

    rotator = load_proxy_rotator()

    assert rotator is None
    assert any(record.levelno == logging.WARNING and "reset-proxy-state.py" in record.message for record in caplog.records)
    assert str(state_path) in caplog.text


def test_load_proxy_urls_warns_how_to_reset_exhausted_proxy_status(monkeypatch: MonkeyPatch, tmp_path: Path, caplog: LogCaptureFixture) -> None:
    """Proxy URL loading logs reset instructions when all proxies are blocked.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    :param tmp_path:
        Temporary path fixture.
    :param caplog:
        Pytest log capture fixture.
    """
    state_path = tmp_path / "webshare-proxy-state.json"
    proxies = [_make_proxy("proxy-1"), _make_proxy("proxy-2")]
    state_manager = ProxyStateManager(state_path=state_path)
    for proxy in proxies:
        state_manager.record_failure(proxy, "test_failure")

    monkeypatch.setenv("WEBSHARE_API_KEY", "test-key")
    monkeypatch.setattr(webshare, "DEFAULT_PROXY_STATE_PATH", state_path)
    monkeypatch.setattr(webshare, "fetch_proxy_list", _make_fetch_proxy_list(proxies))

    caplog.set_level(logging.WARNING, logger=webshare.__name__)

    urls = load_proxy_urls()

    assert urls == []
    assert any(record.levelno == logging.WARNING and "reset-proxy-state.py" in record.message for record in caplog.records)
    assert str(state_path) in caplog.text


@pytest.mark.parametrize("age_seconds,reset", [(-3600, False), (0, False), (86399, False), (86400, True), (172800, True)])
def test_refresh_stale_proxy_state(monkeypatch: MonkeyPatch, tmp_path: Path, caplog: LogCaptureFixture, age_seconds: int, *, reset: bool) -> None:
    """Only an untouched day-old file releases cached proxy blocks.

    File age survives scanner restarts. A missing file after a refresh must
    remain a no-op on later ticks until new failures are recorded.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary directory for the persisted failure state.
    :param caplog: Pytest log capture fixture.
    :param age_seconds: File age relative to the fixed UTC clock.
    :param reset: Whether the age should trigger a refresh.
    """
    now = datetime.datetime(2026, 11, 12, 12, tzinfo=datetime.UTC).replace(tzinfo=None)
    state_path = tmp_path / "webshare-proxy-state.json"
    proxy = _make_proxy("proxy-1")
    manager = ProxyStateManager(state_path=state_path)
    manager.record_failure(proxy, "rate_limited")
    original = state_path.read_bytes()
    modified_at = now.replace(tzinfo=datetime.UTC).timestamp() - age_seconds
    os.utime(state_path, (modified_at, modified_at))
    monkeypatch.setattr(webshare, "native_datetime_utc_now", lambda: now)
    monkeypatch.setattr(webshare, "DEFAULT_PROXY_STATE_PATH", state_path)
    monkeypatch.setenv("WEBSHARE_API_KEY", "test-key")
    monkeypatch.setattr(webshare, "fetch_proxy_list", _make_fetch_proxy_list([proxy]))
    caplog.set_level(logging.INFO, logger=webshare.__name__)

    assert webshare.refresh_stale_proxy_state() is reset
    assert state_path.exists() is not reset
    if reset:
        rotator = load_proxy_rotator()
        assert rotator is not None
        assert len(rotator) == 1
        assert rotator.blocked_count == 0
        assert "blocked proxies will be retried" in caplog.text
        assert webshare.refresh_stale_proxy_state() is False
    else:
        assert state_path.read_bytes() == original


def test_refresh_stale_proxy_state_missing_file(tmp_path: Path) -> None:
    """A scanner without persisted proxy failures needs no refresh.

    The check must not create a state file or require Webshare credentials.

    :param tmp_path: Temporary directory for the absent state file.
    """
    path = tmp_path / "missing.json"
    assert webshare.refresh_stale_proxy_state(path) is False
    assert not path.exists()


def test_refresh_stale_proxy_state_filesystem_failure(monkeypatch: MonkeyPatch, tmp_path: Path, caplog: LogCaptureFixture) -> None:
    """A failed reset keeps state and warns so the next tick can retry.

    Optional proxy maintenance must not abort scheduled vault scans when the
    shared state directory temporarily cannot be modified.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary directory for stale failure state.
    :param caplog: Pytest log capture fixture.
    """
    path = tmp_path / "state.json"
    path.write_text("{}")
    os.utime(path, (0, 0))

    def deny_unlink(self: Path) -> None:
        assert self == path
        message = "read-only state directory"
        raise PermissionError(message)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny_unlink)
        assert webshare.refresh_stale_proxy_state(path) is False

    assert path.exists()
    assert "Webshare proxy state refresh deferred" in caplog.text
    assert not any(record.exc_info for record in caplog.records)
    assert webshare.refresh_stale_proxy_state(path) is True
