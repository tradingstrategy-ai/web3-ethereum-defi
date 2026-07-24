"""Hypersync configuration utils."""

import os
from dataclasses import dataclass

from web3 import Web3

import hypersync
from pyrate_limiter import Limiter

from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.session import ThrottledHypersyncClient, create_throttled_hypersync_client, _create_limiter, get_hypersync_rpm_from_env, get_hypersync_concurrency_from_env


@dataclass(slots=True, frozen=True)
class HypersyncBackendConfig:
    hypersync_client: "ThrottledHypersyncClient | None"
    hypersync_url: "str | None"
    scan_backend: str


def configure_hypersync_from_env(
    web3: Web3,
    hypersync_api_key: str | None = None,
    limiter: Limiter | None = None,
    concurrency: int | None = None,
) -> HypersyncBackendConfig:
    """Helper for scan-vaults and scan-prices scripts to configure Hypersync client from environment variables.

    - Some chains support HyperSync, others don't - autodetect support
    - The returned client is always a
      :py:class:`~eth_defi.hypersync.session.ThrottledHypersyncClient`
      that rate-limits every API call.  The rate defaults to 80 RPM
      and can be overridden with the ``HYPERSYNC_RPM`` environment
      variable or by passing an explicit *limiter*.
    - Stream concurrency defaults to the Hypersync server default (10)
      and can be overridden with the ``HYPERSYNC_CONCURRENCY``
      environment variable or via the *concurrency* parameter.

    :param hypersync_api_key:
        Use given API key, instead of reading from env.

    :param limiter:
        Optional shared :py:class:`pyrate_limiter.Limiter`.  Pass the
        same instance to multiple ``configure_hypersync_from_env`` calls
        (e.g. lead discovery + price scanning) so they coordinate rate
        limits via one SQLite bucket.

    :param concurrency:
        Number of requests in flight for stream tuning.
        ``None`` falls back to the ``HYPERSYNC_CONCURRENCY`` env var,
        then to the Hypersync server default.

    :return:
        A valid Hypersync config if the chain supports HyperSync
    """

    if not hypersync_api_key:
        hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY", None)

    if concurrency is None:
        concurrency = get_hypersync_concurrency_from_env()

    scan_backend = os.environ.get("SCAN_BACKEND", "auto")
    if scan_backend == "auto":
        assert hypersync_api_key, f"HYPERSYNC_API_KEY must be set to use auto scan backend"

    def _make_client(url: str) -> ThrottledHypersyncClient:
        """Create a throttled client, lazily building the limiter on first use."""
        nonlocal limiter
        requests_per_minute = get_hypersync_rpm_from_env()
        if limiter is None:
            limiter = _create_limiter(requests_per_minute=requests_per_minute)
        config = hypersync.ClientConfig(url=url, bearer_token=hypersync_api_key)
        return create_throttled_hypersync_client(config, requests_per_minute=requests_per_minute, limiter=limiter, concurrency=concurrency)

    match scan_backend:
        case "auto":
            hypersync_url = get_hypersync_server(web3, allow_missing=True)
            assert hypersync_api_key, "HYPERSYNC_API_KEY must be set to use HyperSync backend"
            if hypersync_url:
                hypersync_client = _make_client(hypersync_url)
            else:
                hypersync_client = None
        case "hypersync":
            hypersync_url = get_hypersync_server(web3)
            assert hypersync_url, f"No HyperSync server available for chain {web3.eth.chain_id}"
            assert hypersync_api_key, "HYPERSYNC_API_KEY must be set to use HyperSync backend"
            hypersync_client = _make_client(hypersync_url)
        case "rpc":
            hypersync_client = None
            hypersync_url = None
        case _:
            raise ValueError(f"Unknown SCAN_BACKEND value: {scan_backend}")

    return HypersyncBackendConfig(hypersync_client, hypersync_url, scan_backend)
