"""Minimal real Envio Hypersync integration coverage for Arc mainnet."""

import asyncio
import os

import pytest

from eth_defi.hypersync.utils import configure_hypersync_from_env

ARC_CHAIN_ID = 5042
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

pytestmark = pytest.mark.skipif(not HYPERSYNC_API_KEY, reason="Set HYPERSYNC_API_KEY to run this test")


def test_arc_mainnet_hypersync_endpoint() -> None:
    """Envio's Arc endpoint authenticates and reports its chain identity.

    This is a real provider check.  It confirms the endpoint added to the
    static Hypersync map is reachable with the configured API key.

    :return:
        ``None``.  The assertions validate the remote service response.
    """

    async def fetch_chain_metadata() -> tuple[int, int]:
        """Fetch the Arc chain id and indexed height from Envio Hypersync.

        :return:
            Arc chain id and the current indexed height.
        """
        config = configure_hypersync_from_env(ARC_CHAIN_ID, hypersync_api_key=HYPERSYNC_API_KEY)
        client = config.hypersync_client
        assert client is not None
        return await client.get_chain_id(), await client.get_height()

    chain_id, height = asyncio.run(fetch_chain_metadata())

    assert chain_id == ARC_CHAIN_ID
    assert height > 0
