"""Do a discovery scan for ERC-4626 vaults on a chain.

- Discover new vaults on a chain
- Store the metadata in a vault database file
- Support incremental scanning

Usage:

.. code-block:: shell

    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-vaults.py

Or:

.. code-block:: shell

    # TAC
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_TAC MAX_GETLOGS_RANGE=1000 python scripts/erc-4626/scan-vaults.py

    # Arbitrum
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ARBITRUM python scripts/erc-4626/scan-vaults.py

    # Hyperliquid
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_HYPERLIQUID python scripts/erc-4626/scan-vaults.py

    # Mainnet
    SCAN_BACKEND=rpc LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ETHEREUM python scripts/erc-4626/scan-vaults.py

    # Monad
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_MONAD python scripts/erc-4626/scan-vaults.py


Or for faster small sample scan limit the end block:

    END_BLOCK=5555721 python scripts/erc-4626/scan-vaults.py

"""

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import duckdb
from filelock import Timeout as FileLockTimeout

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.rpcdb import RPCRequestStats, RPCUsageDatabase, format_rpc_usage_report, resolve_rpc_tracking_database_path
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, get_pipeline_data_dir

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e


logger = logging.getLogger(__name__)

RESET_LEADS_REMOVED_MESSAGE = "RESET_LEADS has been removed. Use the generated protocol-specific migration script described in eth_defi/erc_4626/README-vault-leads.md"

assert "RESET_LEADS" not in os.environ, RESET_LEADS_REMOVED_MESSAGE

# Read JSON_RPC_CONFIGURATION from the environment
JSON_RPC_URL = os.environ.get("JSON_RPC_URL")
if JSON_RPC_URL is None:
    try:
        urlparse(JSON_RPC_URL)
    except ValueError as e:
        raise ValueError(f"Invalid JSON_RPC URL: {JSON_RPC_URL}") from e


def _run_scan(stats: RPCRequestStats, metrics: dict) -> None:
    """Run lead discovery while updating outer accounting metadata."""

    # How many CPUs / subprocess we use
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))
    # max_workers = 1  # To debug, set workers to 1

    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "warning"),
        log_file=Path(f"logs/scan-vaults.log"),
    )

    logger.info("Using log level: %s", default_log_level)
    end_block = os.environ.get("END_BLOCK")

    os.makedirs(DEFAULT_VAULT_DATABASE.parent, exist_ok=True)
    vault_db_file = DEFAULT_VAULT_DATABASE

    # Debug bad RPCs
    max_getlogs_range = os.environ.get("MAX_GETLOGS_RANGE", None)
    if max_getlogs_range:
        max_getlogs_range = int(max_getlogs_range)

    # Choose a different scan mode
    scan_backend = os.environ.get("SCAN_BACKEND", "auto")

    hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY", None)

    if scan_backend == "auto":
        assert hypersync_api_key, f"HYPERSYNC_API_KEY must be set to use auto scan backend"

    try:
        web3 = create_multi_provider_web3(JSON_RPC_URL, rpc_request_stats=stats)
        metrics["chain_id"] = web3.eth.chain_id
        report = scan_leads(
            json_rpc_urls=JSON_RPC_URL,
            vault_db_file=vault_db_file,
            max_workers=max_workers,
            start_block=None,
            end_block=end_block,
            printer=print,
            backend=scan_backend,
            max_getlogs_range=max_getlogs_range,
            hypersync_api_key=hypersync_api_key,
            rpc_request_stats=stats,
            web3=web3,
        )
        metrics["items_scanned"] = report.items_scanned

        print("All ok")
    except Exception as e:
        print("Died with error: %s", e)
        raise


def main() -> None:
    """Run lead discovery under the shared pipeline and DuckDB writer lock."""

    pipeline_lock_path = get_pipeline_data_dir() / "scan-pipeline"
    database_path = resolve_rpc_tracking_database_path()
    with wait_other_writers(pipeline_lock_path, timeout=60):
        with RPCUsageDatabase(database_path) as database:
            cycle_started = native_datetime_utc_now().date()
            cycle_number = database.allocate_cycle()
            stats = RPCRequestStats()
            metrics = {"chain_id": None, "items_scanned": 0}
            try:
                _run_scan(stats, metrics)
            finally:
                chain_id = metrics["chain_id"]
                if chain_id is not None:
                    try:
                        database.record_scan(
                            chain=chain_id,
                            phase="lead_discovery",
                            cycle_started=cycle_started,
                            cycle_number=cycle_number,
                            stats=stats,
                            items_scanned=metrics["items_scanned"],
                        )
                        report = format_rpc_usage_report(database, chain_id, cycle_started, cycle_number)
                        print(report)
                        logger.info("%s", report)
                    except (duckdb.Error, RuntimeError, AssertionError, TypeError, ValueError):
                        logger.exception("Could not persist lead-discovery RPC usage")


if __name__ == "__main__":
    try:
        main()
    except FileLockTimeout:
        logger.error("Vault scan pipeline is locked by another scanner; stop it or retry after it finishes")
        raise SystemExit(1) from None
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
