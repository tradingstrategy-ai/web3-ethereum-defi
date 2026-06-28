"""Unit tests for Webshare proxy failure state."""

import logging
from collections.abc import Callable
from pathlib import Path

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
