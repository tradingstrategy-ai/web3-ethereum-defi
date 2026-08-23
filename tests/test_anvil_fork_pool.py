"""Unit tests for the shared Anvil fork pool."""

from unittest.mock import ANY, Mock

import pytest

from eth_defi.provider import anvil as anvil_module
from eth_defi.provider.rpc_proxy import RPCProxy, RPCProxyConfig
from eth_defi.testing import anvil_fork_pool as pool_module
from eth_defi.testing.anvil_fork_pool import (
    POOL_WEB3_HTTP_TIMEOUT,
    POOL_WEB3_RETRIES,
    AnvilForkPool,
)


def test_anvil_fork_pool_bounds_nested_rpc_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound proxy and Web3 retries while preserving launch reuse.

    A pooled Anvil may stop answering while it waits for an unavailable
    archive provider. Verify that the proxy owns the provider failover budget
    and the outer Web3 client does not multiply the same localhost failure by
    its general-purpose retry defaults.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    launch = Mock(json_rpc_url="http://localhost:23456")
    fork_network_anvil = Mock(return_value=launch)
    first_client = Mock()
    second_client = Mock()
    create_web3 = Mock(side_effect=[first_client, second_client])
    monkeypatch.setattr(pool_module, "fork_network_anvil", fork_network_anvil)
    monkeypatch.setattr(pool_module, "create_multi_provider_web3", create_web3)
    # This test covers retry/reuse semantics, not liveness: keep the pooled fork
    # "healthy" so the reuse path does not probe the mock endpoint.
    monkeypatch.setattr(pool_module, "is_fork_alive", lambda _launch: True)

    pool = AnvilForkPool()
    rpc_url = "https://primary.example https://fallback.example"

    first_web3 = pool.get_web3(rpc_url, 123)
    second_web3 = pool.get_web3(rpc_url, 123)

    assert first_web3 is first_client
    assert second_web3 is second_client
    assert fork_network_anvil.call_count == 1
    assert create_web3.call_count == len((first_client, second_client))

    fork_network_anvil.assert_called_once_with(
        rpc_url,
        fork_block_number=123,
    )
    create_web3.assert_called_with(
        launch.json_rpc_url,
        default_http_timeout=POOL_WEB3_HTTP_TIMEOUT,
        retries=POOL_WEB3_RETRIES,
        hint=ANY,
    )
    # The hint carries the redacted upstream vendor domain(s) so a fork-setup
    # failure names which provider to investigate / top up.
    hint = create_web3.call_args.kwargs["hint"]
    assert "primary.example" in hint and "fallback.example" in hint


@pytest.mark.parametrize("provider_count", [2, 3, 4, 10])
def test_default_anvil_proxy_policy_is_bounded(provider_count: int) -> None:
    """Try each automatic upstream once within the local read timeout.

    :param provider_count:
        Number of configured standard upstream providers.

    :return:
        None.
    """

    config = anvil_module._create_default_anvil_proxy_config(provider_count)

    assert config.retries == provider_count
    assert config.backoff == 0
    combined_requests_timeout = min(config.timeout, 5.0) + config.timeout
    assert combined_requests_timeout * provider_count <= anvil_module.ANVIL_PROXY_TOTAL_TIMEOUT
    proxy = Mock(spec=RPCProxy)
    proxy.config = config
    assert anvil_module._get_proxy_client_timeout(proxy, minimum_timeout=3.0) <= anvil_module.ANVIL_PROXY_TOTAL_TIMEOUT + 1.0


def test_archive_preflight_uses_proxy_failover_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give archive preflight time to complete the proxy's bounded failover pass.

    1. Set up a three-attempt proxy and a successful bootstrap Web3 response.
    2. Stop launch immediately after capturing the archive-preflight arguments.
    3. Verify the preflight targets the proxy and uses its full client budget.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    # 1. Set up a three-attempt proxy and a successful bootstrap Web3 response.
    proxy = Mock(spec=RPCProxy)
    proxy.url = "http://127.0.0.1:23456"
    proxy.config = RPCProxyConfig(timeout=7.0, retries=3, backoff=0.5)
    expected_timeout = 38.25
    monkeypatch.setattr(anvil_module, "start_rpc_proxy", Mock(return_value=proxy))
    web3 = Mock()
    web3.eth.block_number = 100
    web3.eth.chain_id = 8453
    monkeypatch.setattr(anvil_module, "Web3", Mock(return_value=web3))

    preflight = Mock(side_effect=RuntimeError("stop after preflight"))
    monkeypatch.setattr(anvil_module, "_verify_archive_node_access", preflight)

    # 2. Stop launch immediately after capturing the archive-preflight arguments.
    with pytest.raises(RuntimeError, match="stop after preflight"):
        anvil_module.launch_anvil(
            "https://primary.example https://fallback.example",
            fork_block_number=50,
            proxy_multiple_upstream=True,
            test_request_timeout=3.0,
        )

    # 3. Verify the preflight targets the proxy and uses its full client budget.
    assert anvil_module._get_proxy_client_timeout(proxy, minimum_timeout=3.0) == pytest.approx(expected_timeout)
    assert preflight.call_args.kwargs["rpc_url"] == proxy.url
    assert preflight.call_args.kwargs["timeout"] == pytest.approx(expected_timeout)


def test_archive_preflight_rejects_proxy_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a proxy response after every upstream archive request has failed.

    1. Make the archive probe receive the proxy's HTTP 502 JSON-RPC error.
    2. Run the archive preflight against the fixed historical block.
    3. Verify that it fails immediately instead of starting Anvil.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    # 1. Make the archive probe receive the proxy's HTTP 502 JSON-RPC error.
    response = Mock(ok=False, status_code=502, headers={})
    response.json.return_value = {"error": {"code": -32603, "message": "All upstream providers failed"}}
    monkeypatch.setattr(anvil_module.requests, "post", Mock(return_value=response))

    # 2-3. Run the preflight and reject the failed proxy response.
    with pytest.raises(anvil_module.ArchiveNodeRequired, match="HTTP 502"):
        anvil_module._verify_archive_node_access(
            web3=Mock(),
            rpc_url="http://127.0.0.1:23456",
            fork_block_number=50,
            current_block=100,
        )


def test_launch_anvil_preserves_proxy_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire the bounded automatic policy without changing explicit modes.

    The deliberately dead upstreams stop each launch at its smoke test, after
    the proxy-selection branch has run but before an Anvil process is spawned.
    When automatic or caller-provided proxying is enabled, the raised URL must
    be the proxy, proving both ordinary bootstrap calls and archive preflight
    can use the configured upstream failover policy.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    rpc_url = "http://127.0.0.1:1 http://127.0.0.1:2"
    managed_proxy = Mock(spec=RPCProxy)
    managed_proxy.url = "http://127.0.0.1:23456"
    managed_proxy.config = anvil_module._create_default_anvil_proxy_config(2)
    start_rpc_proxy = Mock(return_value=managed_proxy)
    monkeypatch.setattr(anvil_module, "start_rpc_proxy", start_rpc_proxy)

    with pytest.raises(ValueError, match="127.0.0.1:23456"):
        anvil_module.launch_anvil(
            rpc_url,
            proxy_multiple_upstream=True,
            test_request_timeout=0.01,
        )

    automatic_config = start_rpc_proxy.call_args.kwargs["config"]
    expected_config = anvil_module._create_default_anvil_proxy_config(2)
    assert automatic_config == expected_config
    assert start_rpc_proxy.call_args.kwargs["suppress_client_disconnect_errors"] is True

    explicit_config = RPCProxyConfig(timeout=7.0, retries=3)
    start_rpc_proxy.reset_mock()
    with pytest.raises(ValueError, match="127.0.0.1:23456"):
        anvil_module.launch_anvil(
            rpc_url,
            proxy_multiple_upstream=explicit_config,
            test_request_timeout=0.01,
        )
    assert start_rpc_proxy.call_args.kwargs["config"] is explicit_config

    start_rpc_proxy.reset_mock()
    with pytest.raises(ValueError, match="RPC smoke test failed for http://127.0.0.1:[12]"):
        anvil_module.launch_anvil(
            rpc_url,
            proxy_multiple_upstream=False,
            test_request_timeout=0.01,
        )
    start_rpc_proxy.assert_not_called()

    caller_proxy = object.__new__(RPCProxy)
    caller_proxy.url = "http://127.0.0.1:23457"
    caller_proxy.config = RPCProxyConfig(timeout=7.0, retries=3)
    with pytest.raises(ValueError, match="127.0.0.1:23457"):
        anvil_module.launch_anvil(
            rpc_url,
            proxy_multiple_upstream=caller_proxy,
            test_request_timeout=0.01,
        )
    start_rpc_proxy.assert_not_called()


def test_anvil_fork_pool_preserves_explicit_proxy_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not replace an explicit caller proxy configuration.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    launch = Mock(json_rpc_url="http://localhost:23457")
    fork_network_anvil = Mock(return_value=launch)
    monkeypatch.setattr(pool_module, "fork_network_anvil", fork_network_anvil)

    explicit_config = RPCProxyConfig(timeout=7.0, retries=3)
    pool = AnvilForkPool()
    returned_launch = pool.get_launch(
        "https://primary.example https://fallback.example",
        456,
        proxy_multiple_upstream=explicit_config,
    )

    assert returned_launch is launch
    assert fork_network_anvil.call_args.kwargs["proxy_multiple_upstream"] is explicit_config


def test_anvil_fork_pool_allows_web3_policy_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow known slow callers to override the bounded Web3 defaults.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    launch = Mock(json_rpc_url="http://localhost:23459")
    monkeypatch.setattr(
        pool_module,
        "fork_network_anvil",
        Mock(return_value=launch),
    )
    create_web3 = Mock(return_value=Mock())
    monkeypatch.setattr(pool_module, "create_multi_provider_web3", create_web3)

    pool = AnvilForkPool()
    pool.get_web3(
        "https://primary.example",
        987,
        web3_retries=4,
        web3_http_timeout=(5.0, 120.0),
    )

    create_web3.assert_called_once_with(
        launch.json_rpc_url,
        default_http_timeout=(5.0, 120.0),
        retries=4,
        hint=ANY,
    )


def test_anvil_fork_pool_keys_running_proxy_by_identity() -> None:
    """Avoid deep-copying the locks and server state of a running proxy.

    :return:
        None.
    """
    running_proxy = object.__new__(RPCProxy)

    assert pool_module._freeze(running_proxy) == (RPCProxy, id(running_proxy))


def test_pool_reuses_live_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    """A responsive pooled fork is reused without relaunching.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    launch = Mock(json_rpc_url="http://localhost:23470")
    fork_network_anvil = Mock(return_value=launch)
    monkeypatch.setattr(pool_module, "fork_network_anvil", fork_network_anvil)
    monkeypatch.setattr(pool_module, "is_fork_alive", lambda _launch: True)

    pool = AnvilForkPool()
    first = pool.get_launch("https://a.example https://b.example", 100)
    second = pool.get_launch("https://a.example https://b.example", 100)

    assert first is second
    assert fork_network_anvil.call_count == 1
    launch.close.assert_not_called()


def test_pool_recycles_wedged_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresponsive pooled fork is disposed and relaunched.

    Without this, every remaining test sharing the fork fails at setup with a
    60 s read timeout against localhost.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    wedged = Mock(json_rpc_url="http://localhost:23471")
    fresh = Mock(json_rpc_url="http://localhost:23472")
    fork_network_anvil = Mock(side_effect=[wedged, fresh])
    monkeypatch.setattr(pool_module, "fork_network_anvil", fork_network_anvil)

    pool = AnvilForkPool()
    monkeypatch.setattr(pool_module, "is_fork_alive", lambda _launch: True)
    assert pool.get_launch("https://a.example https://b.example", 200) is wedged

    # The fork stops answering before the next test in the group asks for it.
    monkeypatch.setattr(pool_module, "is_fork_alive", lambda _launch: False)
    with pytest.warns(pool_module.WedgedForkRecycledWarning):
        replacement = pool.get_launch("https://a.example https://b.example", 200)

    assert replacement is fresh
    assert fork_network_anvil.call_count == 2
    wedged.close.assert_called_once()
    # The dead launch must not stay cached.
    monkeypatch.setattr(pool_module, "is_fork_alive", lambda _launch: True)
    assert pool.get_launch("https://a.example https://b.example", 200) is fresh


def test_dispose_survives_close_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged process that cannot be closed is still dropped from the pool.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    wedged = Mock(json_rpc_url="http://localhost:23473")
    wedged.close.side_effect = OSError("process already gone")
    fresh = Mock(json_rpc_url="http://localhost:23474")
    monkeypatch.setattr(pool_module, "fork_network_anvil", Mock(side_effect=[wedged, fresh]))

    pool = AnvilForkPool()
    monkeypatch.setattr(pool_module, "is_fork_alive", lambda _launch: True)
    pool.get_launch("https://a.example https://b.example", 300)

    monkeypatch.setattr(pool_module, "is_fork_alive", lambda _launch: False)
    with pytest.warns(pool_module.WedgedForkRecycledWarning):
        assert pool.get_launch("https://a.example https://b.example", 300) is fresh
