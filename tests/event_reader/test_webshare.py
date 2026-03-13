"""Integration tests for Webshare proxy module.

These tests require the ``WEBSHARE_API_KEY`` environment variable to be set.
They are skipped automatically when the key is not available.
"""

import os

import pytest

from eth_defi.event_reader.webshare import (
    ProxyRotator,
    ProxyStateManager,
    WebshareProxy,
    check_proxy_health,
    fetch_proxy_list,
    load_proxy_rotator,
    load_proxy_urls,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("WEBSHARE_API_KEY"),
    reason="WEBSHARE_API_KEY not set",
)


def test_fetch_proxy_list():
    """Fetch proxy list from Webshare API and verify structure."""
    api_key = os.environ["WEBSHARE_API_KEY"]
    proxies = fetch_proxy_list(api_key)

    assert len(proxies) > 0, "Expected at least one proxy from Webshare"

    proxy = proxies[0]
    assert isinstance(proxy, WebshareProxy)
    assert proxy.port > 0
    assert proxy.username
    assert proxy.password

    # Verify URL generation
    url = proxy.to_proxy_url()
    assert url.startswith("http://")
    assert str(proxy.port) in url


def test_load_proxy_rotator():
    """Load a full proxy rotator and verify rotation works."""
    rotator = load_proxy_rotator()

    assert rotator is not None
    assert len(rotator) > 0
    assert rotator.total_from_api > 0

    first = rotator.current()
    assert isinstance(first, WebshareProxy)
    assert rotator.generation == 0

    second = rotator.rotate()
    assert rotator.generation == 1
    if len(rotator) > 1:
        assert second != first


def test_load_proxy_urls():
    """Load proxy URLs and verify format."""
    urls = load_proxy_urls()

    assert len(urls) > 0
    for url in urls:
        assert url.startswith("http://")
        assert "@" in url, "Proxy URL should contain auth credentials"


def test_proxy_health_check():
    """Verify that at least one proxy can reach the internet."""
    rotator = load_proxy_rotator()
    assert rotator is not None

    healthy = check_proxy_health(rotator)
    assert healthy, "At least one proxy should pass health check"


def test_proxy_state_manager_roundtrip(tmp_path):
    """Verify state manager save/load cycle with a real proxy."""
    api_key = os.environ["WEBSHARE_API_KEY"]
    proxies = fetch_proxy_list(api_key)
    assert len(proxies) > 0

    proxy = proxies[0]
    state_path = tmp_path / "proxy-state.json"

    # Record a failure
    mgr = ProxyStateManager(state_path=state_path)
    mgr.record_failure(proxy, "test_failure")
    assert mgr.is_blocked(proxy)

    # Reload from disk
    mgr2 = ProxyStateManager(state_path=state_path)
    mgr2.load()
    assert mgr2.is_blocked(proxy)
    assert mgr2.get_blocked_count() == 1

    proxy_id = mgr2.get_proxy_id(proxy)
    assert mgr2._failed_proxies[proxy_id].failure_count == 1
    assert mgr2._failed_proxies[proxy_id].reason == "test_failure"
