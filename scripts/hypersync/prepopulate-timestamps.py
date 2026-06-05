"""Prepopulate the Hypersync block timestamp cache for all scanner chains.

Fills the DuckDB-backed timestamp cache used by the vault scanner pipeline.
Chains that already have a warm cache only fetch the delta since the last
cached block — this is safe to run repeatedly without re-downloading data.

Uses the same chain list as the main vault scanner
(:py:func:`eth_defi.vault.scan_all_chains.build_chain_configs`),
reads the same ``JSON_RPC_*`` environment variables, and respects
``HYPERSYNC_API_KEY`` and ``HYPERSYNC_RPM``.

Usage:

.. code-block:: shell

    # All chains (reads JSON_RPC_* env vars, skips unconfigured ones)
    source .local-test.env && poetry run python scripts/hypersync/prepopulate-timestamps.py

    # Specific chains only
    CHAIN_FILTER="Polygon,Binance,Plasma" \\
    source .local-test.env && poetry run python scripts/hypersync/prepopulate-timestamps.py

Environment variables:

    - ``JSON_RPC_<CHAIN>``: RPC URL for each chain (same as docker-compose)
    - ``HYPERSYNC_API_KEY``: Envio Hypersync API key (required)
    - ``HYPERSYNC_RPM``: Requests-per-minute limit (default: 150)
    - ``CHAIN_FILTER``: Comma-separated chain names to process (default: all)
    - ``LOG_LEVEL``: Logging level (default: "info")
"""

import logging
import os

import hypersync

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess_auto_backend
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.hypersync_timestamp import get_hypersync_block_height
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.session import create_throttled_hypersync_client, get_hypersync_rpm_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.utils import setup_console_logging
from eth_defi.vault.scan_all_chains import build_chain_configs

logger = logging.getLogger(__name__)


def prepopulate_chain(env_var: str, chain_name: str, hypersync_api_key: str):
    """Prepopulate timestamp cache for a single chain.

    - Reads the RPC URL from the given environment variable
    - Skips chains without Hypersync support
    - Only fetches the delta since the last cached block

    :param env_var:
        Environment variable name for the RPC URL (e.g. ``JSON_RPC_POLYGON``)

    :param chain_name:
        Human-readable chain name for logging

    :param hypersync_api_key:
        Envio Hypersync API key
    """

    rpc_url = os.environ.get(env_var)
    if not rpc_url:
        logger.info("%s: skipped — %s not set", chain_name, env_var)
        return

    web3 = create_multi_provider_web3(rpc_url)
    web3factory = MultiProviderWeb3Factory(rpc_url)
    chain_id = web3.eth.chain_id

    hypersync_url = get_hypersync_server(chain_id, allow_missing=True)
    if not hypersync_url:
        logger.info("%s (chain %d): skipped — no Hypersync server configured", chain_name, chain_id)
        return

    logger.info("%s (chain %d): using Hypersync server %s", chain_name, chain_id, hypersync_url)

    hypersync_client = create_throttled_hypersync_client(
        hypersync.ClientConfig(
            url=hypersync_url,
            bearer_token=hypersync_api_key,
        ),
        requests_per_minute=get_hypersync_rpm_from_env(),
    )

    last_block = get_hypersync_block_height(hypersync_client)
    logger.info("%s (chain %d): Hypersync head at block %s", chain_name, chain_id, f"{last_block:,}")

    timestamps = fetch_block_timestamps_multiprocess_auto_backend(
        web3factory=web3factory,
        chain_id=chain_id,
        start_block=1,
        end_block=last_block,
        step=1,
        hypersync_client=hypersync_client,
    )

    logger.info("%s (chain %d): done — cache covers %s blocks", chain_name, chain_id, f"{len(timestamps):,}")


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
    )

    hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY")
    if not hypersync_api_key:
        logger.error("HYPERSYNC_API_KEY environment variable is required")
        raise SystemExit(1)

    # Build chain list from the same source as the vault scanner
    all_chains = build_chain_configs()

    # Optional filter
    chain_filter = os.environ.get("CHAIN_FILTER", "").strip()
    if chain_filter:
        filter_names = {name.strip() for name in chain_filter.split(",")}
        chains = [c for c in all_chains if c.name in filter_names]
        unknown = filter_names - {c.name for c in chains}
        if unknown:
            logger.warning("CHAIN_FILTER contains unknown chain names: %s", ", ".join(sorted(unknown)))
    else:
        chains = all_chains

    logger.info(
        "Prepopulating timestamp cache at %s for %d chain(s): %s",
        DEFAULT_TIMESTAMP_CACHE_FOLDER,
        len(chains),
        ", ".join(c.name for c in chains),
    )

    succeeded = []
    failed = []

    for chain_config in chains:
        try:
            prepopulate_chain(chain_config.env_var, chain_config.name, hypersync_api_key)
            succeeded.append(chain_config.name)
        except Exception:
            logger.exception("%s: prepopulation failed", chain_config.name)
            failed.append(chain_config.name)

    logger.info(
        "Prepopulation complete: %d succeeded, %d failed. Succeeded: %s. Failed: %s",
        len(succeeded),
        len(failed),
        ", ".join(succeeded) or "(none)",
        ", ".join(failed) or "(none)",
    )

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
