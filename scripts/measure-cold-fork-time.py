"""Measure cold vs warm Anvil fork setup time per chain.

Diagnostic for the vault-protocol / GMX CI failures where fork setup fails with a
60-second ``eth_chainId`` read timeout (misleadingly hinted as "out of API
credits"). The real cause is that a **cold** fork of a fixed historical block
must fetch that block's state from the upstream archive, and on a slow provider
that first fetch can exceed the Web3 read timeout. This script measures how long
that actually takes so the fork-setup timeout can be set above the real cold-fork
latency. See :mod:`eth_defi.testing.anvil_fork_pool` and
:file:`docs/README-test-suite-performance.md`.

For each chain that has a ``JSON_RPC_<CHAIN>`` environment variable set and a
canonical midnight block (:mod:`eth_defi.testing.fork_blocks`), it:

1. deletes any existing Foundry RPC cache for that block (forces a cold fork),
2. forks the block with Anvil and times *fork launch + first block read* — the
   same work the test ``web3`` fixtures do on setup,
3. immediately repeats the fork with the now-warm cache to time a warm setup.

Output is a table of cold/warm setup seconds and the redacted provider domain(s),
so the slowest cold fork gives the minimum safe read timeout.

Run (from a checkout with the venv and secrets)::

    source .local-test.env && poetry run python scripts/measure-cold-fork-time.py

Environment variables:

- ``JSON_RPC_<CHAIN>`` — upstream archive RPC(s) per chain (space-separated
  multi-provider format supported).
- ``FORK_CHAINS`` — optional comma-separated chain-id allowlist (default: every
  chain that has both a midnight block and a configured RPC).
"""

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.rpc_failure import classify_rpc_failure
from eth_defi.testing.fork_blocks import MIDNIGHT_BLOCKS
from eth_defi.utils import get_url_domain

logger = logging.getLogger(__name__)

#: Chain id → ``JSON_RPC_<CHAIN>`` environment variable name.
CHAIN_ENV_VARS: dict[int, str] = {
    1: "JSON_RPC_ETHEREUM",
    56: "JSON_RPC_BINANCE",
    137: "JSON_RPC_POLYGON",
    146: "JSON_RPC_SONIC",
    999: "JSON_RPC_HYPERLIQUID",
    8453: "JSON_RPC_BASE",
    9745: "JSON_RPC_PLASMA",
    42161: "JSON_RPC_ARBITRUM",
    43114: "JSON_RPC_AVALANCHE",
    80094: "JSON_RPC_BERACHAIN",
}


@dataclass(slots=True)
class ForkTiming:
    """Measured fork-setup timing for one chain.

    :ivar chain_id: EVM chain id.
    :ivar block: Fixed midnight block forked.
    :ivar providers: Redacted upstream provider domain(s).
    :ivar cold_seconds: Fork launch + first block read with an empty cache.
    :ivar warm_seconds: Same with a warm on-disk RPC cache.
    :ivar error: Populated when the cold fork failed (e.g. timed out).
    """

    chain_id: int
    block: int
    providers: str
    cold_seconds: float | None
    warm_seconds: float | None
    error: str | None


def clear_block_cache(block: int) -> None:
    """Delete any Foundry RPC cache for a fork block to force a cold fork.

    The cache lives under ``~/.foundry/cache/rpc/<network>/<block>/``; the network
    directory name is not the chain id, so match every network for the block.

    :param block: Fork block number whose cache directories are removed.
    """
    cache_root = Path.home() / ".foundry" / "cache" / "rpc"
    for path in cache_root.glob(f"*/{block}"):
        shutil.rmtree(path, ignore_errors=True)


def time_fork(rpc_url: str, block: int) -> float:
    """Fork a block and return seconds for launch + first block read.

    Mirrors what a test ``web3`` fixture pays on setup: launch Anvil forking the
    block, build the Web3 client, and make the first archive-backed call.

    :param rpc_url: Upstream archive RPC(s), space-separated multi-provider ok.
    :param block: Fixed block to fork.
    :return: Wall-clock seconds for the fork setup.
    """
    start = time.time()
    launch = fork_network_anvil(rpc_url, fork_block_number=block)
    try:
        # Generous 10-minute read timeout so we measure the *true* cold-fork
        # latency instead of capping at the production 60s read timeout.
        web3 = create_multi_provider_web3(launch.json_rpc_url, default_http_timeout=(5.0, 600.0))
        # First archive-backed read; this is what stalls on a slow cold fork.
        web3.eth.get_block(block)
    finally:
        launch.close(log_level=logging.ERROR)
    return time.time() - start


def measure_chain(chain_id: int, rpc_url: str) -> ForkTiming:
    """Measure cold then warm fork-setup time for one chain.

    :param chain_id: EVM chain id (must have a canonical midnight block).
    :param rpc_url: Upstream archive RPC(s) for the chain.
    :return: The populated :class:`ForkTiming`.
    """
    block = MIDNIGHT_BLOCKS[chain_id]
    providers = ", ".join(get_url_domain(u) for u in rpc_url.split() if u)
    logger.info("Measuring chain %d block %d via %s", chain_id, block, providers)

    clear_block_cache(block)
    try:
        cold = time_fork(rpc_url, block)
    except Exception as e:  # noqa: BLE001 - diagnostic script records any failure
        # Record the classified failure mode + exception type, never the raw
        # message (it can embed the upstream RPC URL / API key).
        mode = classify_rpc_failure(e).value
        logger.warning("Cold fork failed for chain %d: failure_mode=%s (%s)", chain_id, mode, type(e).__name__)
        return ForkTiming(chain_id, block, providers, None, None, f"{mode}: {type(e).__name__}")

    warm = time_fork(rpc_url, block)
    return ForkTiming(chain_id, block, providers, cold, warm, None)


def main() -> None:
    """Measure and print cold/warm fork-setup times for the configured chains."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    allow = os.environ.get("FORK_CHAINS")
    allowed = {int(c) for c in allow.split(",")} if allow else None

    results: list[ForkTiming] = []
    for chain_id, env_var in CHAIN_ENV_VARS.items():
        if chain_id not in MIDNIGHT_BLOCKS:
            continue
        if allowed is not None and chain_id not in allowed:
            continue
        rpc_url = os.environ.get(env_var)
        if not rpc_url:
            logger.info("Skipping chain %d: %s not set", chain_id, env_var)
            continue
        results.append(measure_chain(chain_id, rpc_url))

    rows = [
        [
            r.chain_id,
            r.block,
            r.providers,
            f"{r.cold_seconds:.1f}" if r.cold_seconds is not None else "FAIL",
            f"{r.warm_seconds:.1f}" if r.warm_seconds is not None else "-",
            r.error or "",
        ]
        for r in results
    ]
    print(tabulate(rows, headers=["chain", "block", "provider(s)", "cold s", "warm s", "error"]))


if __name__ == "__main__":
    main()
