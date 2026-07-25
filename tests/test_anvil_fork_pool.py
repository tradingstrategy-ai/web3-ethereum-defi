"""Unit tests for the shared Anvil fork pool."""

from unittest.mock import Mock

import pytest

from eth_defi.provider.rpc_proxy import RPCProxy, RPCProxyConfig
from eth_defi.testing import anvil_fork_pool as pool_module
from eth_defi.testing.anvil_fork_pool import (
    POOL_PROXY_TIMEOUT,
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

    pool = AnvilForkPool()
    rpc_url = "https://primary.example https://fallback.example"

    first_web3 = pool.get_web3(rpc_url, 123)
    second_web3 = pool.get_web3(rpc_url, 123)

    assert first_web3 is first_client
    assert second_web3 is second_client
    assert fork_network_anvil.call_count == 1
    assert create_web3.call_count == len((first_client, second_client))

    proxy_config = fork_network_anvil.call_args.kwargs["proxy_multiple_upstream"]
    assert isinstance(proxy_config, RPCProxyConfig)
    assert proxy_config.timeout == POOL_PROXY_TIMEOUT
    assert proxy_config.retries == len(rpc_url.split())
    create_web3.assert_called_with(
        launch.json_rpc_url,
        default_http_timeout=POOL_WEB3_HTTP_TIMEOUT,
        retries=POOL_WEB3_RETRIES,
    )


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


def test_anvil_fork_pool_tries_every_standard_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Size the bounded proxy attempt count to the usable provider set.

    :param monkeypatch:
        Pytest monkeypatch fixture.

    :return:
        None.
    """
    launch = Mock(json_rpc_url="http://localhost:23458")
    fork_network_anvil = Mock(return_value=launch)
    monkeypatch.setattr(pool_module, "fork_network_anvil", fork_network_anvil)

    pool = AnvilForkPool()
    standard_rpc_urls = (
        "https://primary.example",
        "https://fallback.example",
        "https://last-resort.example",
    )
    pool.get_launch(
        " ".join(("mev+https://transactions.example", *standard_rpc_urls)),
        789,
    )

    proxy_config = fork_network_anvil.call_args.kwargs["proxy_multiple_upstream"]
    assert proxy_config.retries == len(standard_rpc_urls)


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
    )


def test_anvil_fork_pool_keys_running_proxy_by_identity() -> None:
    """Avoid deep-copying the locks and server state of a running proxy.

    :return:
        None.
    """
    running_proxy = object.__new__(RPCProxy)

    assert pool_module._freeze(running_proxy) == (RPCProxy, id(running_proxy))
