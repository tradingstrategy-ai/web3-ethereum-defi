"""Hypersync configuration utils."""

import os
from dataclasses import dataclass

from web3 import Web3

import hypersync
from pyrate_limiter import Limiter

from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.session import ThrottledHypersyncClient, create_throttled_hypersync_client, _create_limiter


@dataclass(slots=True, frozen=True)
class HypersyncBackendConfig:
    hypersync_client: "hypersync.HypersyncClient | ThrottledHypersyncClient | None"
    hypersync_url: "str | None"
    scan_backend: str


def configure_hypersync_from_env(
    web3: Web3,
    hypersync_api_key: str | None = None,
    limiter: Limiter | None = None,
) -> HypersyncBackendConfig:
    """Helper for scan-vaults and scan-prices scripts to configure Hypersync client from environment variables.

    - Some chains support HyperSync, others don't - autodetect support
    - When a *limiter* is provided, the returned client is a
      :py:class:`~eth_defi.hypersync.session.ThrottledHypersyncClient`
      that rate-limits every API call through the shared limiter.
      When ``None`` and the environment variable ``HYPERSYNC_RPM`` is
      set, a limiter is created automatically.

    :param hypersync_api_key:
        Use given API key, instead of reading from env.

    :param limiter:
        Optional shared :py:class:`pyrate_limiter.Limiter`.  Pass the
        same instance to multiple ``configure_hypersync_from_env`` calls
        (e.g. lead discovery + price scanning) so they coordinate rate
        limits via one SQLite bucket.

    :return:
        A valid Hypersync config if the chain supports HyperSync
    """

    if not hypersync_api_key:
        hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY", None)

    scan_backend = os.environ.get("SCAN_BACKEND", "auto")
    if scan_backend == "auto":
        assert hypersync_api_key, f"HYPERSYNC_API_KEY must be set to use auto scan backend"

    # Auto-create limiter from env if not provided
    rpm_env = os.environ.get("HYPERSYNC_RPM")
    if limiter is None and rpm_env:
        limiter = _create_limiter(requests_per_minute=int(rpm_env))

    def _make_client(url: str) -> "hypersync.HypersyncClient | ThrottledHypersyncClient":
        config = hypersync.ClientConfig(url=url, bearer_token=hypersync_api_key)
        if limiter is not None:
            return create_throttled_hypersync_client(config, limiter=limiter)
        return hypersync.HypersyncClient(config)

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
