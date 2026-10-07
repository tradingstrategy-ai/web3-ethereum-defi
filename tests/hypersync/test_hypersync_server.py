"""Test Hypersync server metadata and Nest chain endpoints."""

import asyncio
import os
from pathlib import Path

import pytest

from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached_async
from eth_defi.hypersync.server import get_hypersync_server, is_hypersync_supported_chain
from eth_defi.hypersync.utils import configure_hypersync_from_env

SAMPLED_HEADER_COUNT = 10


def test_robinhood_hypersync_server() -> None:
    """Robinhood Chain has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(4663) is True
    assert get_hypersync_server(4663) == "https://4663.hypersync.xyz"


def test_arc_testnet_hypersync_server() -> None:
    """Arc Testnet has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(5042002) is True
    assert get_hypersync_server(5042002) == "https://arc-testnet.hypersync.xyz"


def test_arc_mainnet_hypersync_server() -> None:
    """Arc mainnet has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(5042) is True
    assert get_hypersync_server(5042) == "https://arc.hypersync.xyz"


def test_tempo_hypersync_server() -> None:
    """Tempo has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(4217) is True
    assert get_hypersync_server(4217) == "https://tempo.hypersync.xyz"


@pytest.mark.parametrize(("chain_id", "hostname"), [(480, "worldchain"), (98866, "plume")])
def test_nest_hypersync_servers(chain_id: int, hostname: str) -> None:
    """Nest's Worldchain and Plume routes have Envio endpoints."""

    assert is_hypersync_supported_chain(chain_id)
    assert get_hypersync_server(chain_id) == f"https://{hostname}.hypersync.xyz"


@pytest.mark.skipif(not os.environ.get("HYPERSYNC_API_KEY"), reason="Set HYPERSYNC_API_KEY to check the live Envio endpoints")
@pytest.mark.parametrize("chain_id", [480, 98866])
def test_nest_hypersync_endpoints_live(chain_id: int, tmp_path: Path) -> None:
    """Read real timestamp pages through the authenticated, throttled client.

    Ten recent headers exercise the new pager and persistent cache on each
    migrated chain. The private cache excludes accidental cache-only success.

    :param chain_id: Worldchain or Plume network identifier.
    :param tmp_path: Private timestamp cache for this live integration test.
    """

    async def fetch_chain_metadata() -> tuple[int, int]:
        """Read the remote chain identity and indexed height.

        :return: Chain ID and latest indexed block.
        """
        config = configure_hypersync_from_env(chain_id, hypersync_api_key=os.environ["HYPERSYNC_API_KEY"])
        client = config.hypersync_client
        assert client is not None
        reported_chain_id, height = await client.get_chain_id(), await client.get_height()
        timestamps = await fetch_block_timestamps_using_hypersync_cached_async(client, chain_id, height - SAMPLED_HEADER_COUNT, height - 1, cache_path=tmp_path, display_progress=False)
        try:
            assert len(timestamps) == SAMPLED_HEADER_COUNT
            assert timestamps[height - 1] > timestamps[height - SAMPLED_HEADER_COUNT]
        finally:
            timestamps.close()
        return reported_chain_id, height

    reported_chain_id, height = asyncio.run(fetch_chain_metadata())
    assert reported_chain_id == chain_id
    assert height > 0
