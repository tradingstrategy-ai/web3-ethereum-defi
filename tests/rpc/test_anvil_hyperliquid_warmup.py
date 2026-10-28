"""HyperEVM Anvil warm-up integration tests.

These tests exercise the optional ``warm_up_block`` launch path against a live
HyperEVM archive RPC. The goal is not to prove an exact latency threshold, but
to verify that:

- Anvil launches successfully on a HyperEVM fork with warm-up enabled
- the warm-up path does not break the forked node
- a subsequent full-block read succeeds against the already running local fork
"""

import logging
import os
import shutil
import time

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.testing.fork_blocks import HYPERLIQUID_MIDNIGHT_BLOCK

logger = logging.getLogger(__name__)

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(
    (JSON_RPC_HYPERLIQUID is None) or (shutil.which("anvil") is None),
    reason="JSON_RPC_HYPERLIQUID env and anvil command are required",
)


@pytest.fixture()
def anvil_hyperliquid_warm() -> AnvilLaunch:
    """Launch a HyperEVM fork with eager full-block warm-up enabled."""
    launch = fork_network_anvil(
        JSON_RPC_HYPERLIQUID,
        fork_block_number=HYPERLIQUID_MIDNIGHT_BLOCK,
        gas_limit=30_000_000,
        warm_up_block=True,
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.mark.timeout(180)
def test_anvil_hyperliquid_warm_up_block_smoke(anvil_hyperliquid_warm: AnvilLaunch):
    """Warm up the fork block and then fetch the same full block again.

    This mirrors the suspected expensive Anvil path:
    ``eth_getBlockByNumber(fork_block, true)``.

    We do not assert on a hard latency threshold because upstream RPC
    performance is variable. Instead, we log the timing and verify the call
    succeeds after Anvil has already performed its eager warm-up.
    """
    web3 = Web3(
        HTTPProvider(
            anvil_hyperliquid_warm.json_rpc_url,
            request_kwargs={"timeout": 90.0},
        )
    )

    chain_id = web3.eth.chain_id
    current_block = web3.eth.block_number

    assert chain_id == 999
    assert current_block == HYPERLIQUID_MIDNIGHT_BLOCK

    started_at = time.perf_counter()
    block = web3.eth.get_block(current_block, full_transactions=True)
    elapsed = time.perf_counter() - started_at

    assert block["number"] == current_block
    assert isinstance(block["transactions"], list)

    logger.info(
        "HyperEVM Anvil warm-up smoke test fetched full block %d with %d transactions in %.3f seconds",
        current_block,
        len(block["transactions"]),
        elapsed,
    )
