"""Hypersync configuration utils."""

import os
from dataclasses import dataclass

from web3 import Web3

import hypersync

from eth_defi.hypersync.server import get_hypersync_server


@dataclass(slots=True, frozen=True)
class HypersyncBackendConfig:
    hypersync_client: "hypersync.HypersyncClient | None"
    hypersync_url: "str | None"
    scan_backend: str


def configure_hypersync_from_env(
    web3: Web3,
    hypersync_api_key: str | None = None,
) -> HypersyncBackendConfig:
    """Helper for scan-vaults and scan-prices scripts to configure Hypersync client from environment variables.

    - Some chains support HyperSync, others don't - autodetect support

    :param hypersync_api_key:
        Use given API key, instead of reading from env.\

    :return:
        A valid Hypersync config if the chain supports HyperSync
    """

    if not hypersync_api_key:
        hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY", None)

    scan_backend = os.environ.get("SCAN_BACKEND", "auto")
    if scan_backend == "auto":
        assert hypersync_api_key, f"HYPERSYNC_API_KEY must be set to use auto scan backend"

    match scan_backend:
        case "auto":
            hypersync_url = get_hypersync_server(web3, allow_missing=True)
            assert hypersync_api_key, "HYPERSYNC_API_KEY must be set to use HyperSync backend"
            if hypersync_url:
                hypersync_client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url, bearer_token=hypersync_api_key))
            else:
                hypersync_client = None
        case "hypersync":
            hypersync_url = get_hypersync_server(web3)
            assert hypersync_url, f"No HyperSync server available for chain {web3.eth.chain_id}"
            assert hypersync_api_key, "HYPERSYNC_API_KEY must be set to use HyperSync backend"
            hypersync_client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url, bearer_token=hypersync_api_key))
        case "rpc":
            hypersync_client = None
            hypersync_url = None
        case _:
            raise ValueError(f"Unknown SCAN_BACKEND value: {scan_backend}")

    return HypersyncBackendConfig(hypersync_client, hypersync_url, scan_backend)
