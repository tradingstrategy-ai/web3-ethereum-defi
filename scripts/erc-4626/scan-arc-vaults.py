#!/usr/bin/env python3
"""Run an isolated initial ERC-4626 vault discovery scan for Arc mainnet.

The script stores its Arc vault database and incremental discovery cursor under
a dedicated local directory. It never scans prices, performs post-processing,
or changes the shared production vault database. Chain-keyed token and protocol
metadata caches may still be refreshed by the normal metadata readers.

Usage:

.. code-block:: shell

    source .local-test.env
    poetry run python scripts/erc-4626/scan-arc-vaults.py

``JSON_RPC_ARC`` is preferred.  If it is absent, the script derives an Arc
Goldsky endpoint from the Goldsky entry in ``JSON_RPC_ETHEREUM`` by replacing
its final chain-id path segment (``1``) with Arc's chain id (``5042``).
"""

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from tabulate import tabulate

from eth_defi.provider.broken_provider import verify_archive_node
from eth_defi.utils import setup_console_logging
from eth_defi.vault.scan_all_chains import scan_vaults_for_chain

logger = logging.getLogger(__name__)

ARC_CHAIN_ID = 5042
GOLDSKY_HOST = "edge.goldsky.com"
DEFAULT_PIPELINE_DATA_DIR = Path.home() / ".tradingstrategy" / "vaults" / "arc-initial-scan"


def derive_arc_goldsky_rpc_url(ethereum_rpc_url: str) -> str:
    """Derive an Arc RPC endpoint from an Ethereum Goldsky endpoint.

    Goldsky uses the EVM chain id as the final path component of its private
    project endpoint.  The parser retains any path prefix, query string and
    credential material without logging it.

    :param ethereum_rpc_url:
        One JSON-RPC endpoint from ``JSON_RPC_ETHEREUM``.
    :return:
        The equivalent Arc endpoint.
    :raises ValueError:
        If the endpoint is not a Goldsky Ethereum endpoint with final path
        component ``1``.
    """
    parsed = urlsplit(ethereum_rpc_url)
    if parsed.hostname != GOLDSKY_HOST:
        message = "Expected a Goldsky RPC endpoint"
        raise ValueError(message)

    path_parts = parsed.path.rstrip("/").split("/")
    if path_parts[-1] != "1":
        message = "Expected Goldsky Ethereum endpoint path to end in chain id 1"
        raise ValueError(message)

    path_parts[-1] = str(ARC_CHAIN_ID)
    return urlunsplit((parsed.scheme, parsed.netloc, "/".join(path_parts), parsed.query, parsed.fragment))


def resolve_arc_rpc_url() -> tuple[str, str]:
    """Resolve the configured or derived Arc JSON-RPC URL.

    A dedicated Arc configuration takes precedence.  Otherwise the function
    finds a compatible Goldsky endpoint among the space-separated Ethereum
    multi-provider configuration.

    :return:
        The resolved RPC configuration and a credential-free source label.
    :raises ValueError:
        If neither a direct Arc configuration nor a derivable Goldsky Ethereum
        endpoint is available.
    """
    arc_rpc_url = os.environ.get("JSON_RPC_ARC")
    if arc_rpc_url:
        return arc_rpc_url, "JSON_RPC_ARC"

    ethereum_rpc_url = os.environ.get("JSON_RPC_ETHEREUM", "")
    for endpoint in ethereum_rpc_url.split():
        try:
            return derive_arc_goldsky_rpc_url(endpoint), "JSON_RPC_ETHEREUM Goldsky endpoint"
        except ValueError:
            continue

    message = "Set JSON_RPC_ARC or configure a Goldsky Ethereum endpoint ending in /1 in JSON_RPC_ETHEREUM"
    raise ValueError(message)


def resolve_pipeline_data_dir() -> Path:
    """Resolve the isolated local directory for the Arc bootstrap scan.

    :return:
        Directory containing the Arc-only metadata database, discovery state
        and log file.
    """
    configured = os.environ.get("ARC_PIPELINE_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_PIPELINE_DATA_DIR


def main() -> None:
    """Discover Arc vaults into isolated local scanner state.

    The scan requires an Envio Hypersync API key and uses the configured Arc
    Hypersync endpoint for historical events.  JSON-RPC is only used for
    archive-node validation and current contract metadata reads.

    :raises AssertionError:
        If ``HYPERSYNC_API_KEY`` is not configured.
    :raises SystemExit:
        If the vault discovery operation fails.
    """
    assert os.environ.get("HYPERSYNC_API_KEY"), "HYPERSYNC_API_KEY must be set for the Arc Hypersync discovery scan"

    data_dir = resolve_pipeline_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=data_dir / "scan-arc-vaults.log",
    )

    rpc_url, rpc_source = resolve_arc_rpc_url()
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))
    force_lead_discovery = os.environ.get("ARC_FORCE_LEAD_DISCOVERY", "false").lower() == "true"
    verified_rpc_url, latest_block = verify_archive_node(rpc_url, "Arc")
    logger.info(
        "Starting Arc vault discovery from %s at block %s with %d workers; local state is %s",
        rpc_source,
        f"{latest_block:,}",
        max_workers,
        data_dir,
    )

    success, metrics = scan_vaults_for_chain(
        rpc_url=verified_rpc_url,
        max_workers=max_workers,
        vault_db_path=data_dir / "vault-metadata-db.pickle",
        force_lead_discovery=force_lead_discovery,
    )
    if not success:
        logger.error("Arc vault discovery failed: %s", metrics.get("error", "unknown error"))
        raise SystemExit(1)

    summary = {
        "chain": "Arc",
        "chain_id": metrics["chain_id"],
        "end_block": metrics["end_block"],
        "vaults": metrics["vault_count"],
        "new_vaults": metrics["new_vaults"],
        "events_scanned": metrics["items_scanned"],
        "cache_hit": metrics["lead_discovery_cache_hit"],
        "state_directory": str(data_dir),
    }
    print(tabulate([summary], headers="keys", tablefmt="simple"))


if __name__ == "__main__":
    main()
