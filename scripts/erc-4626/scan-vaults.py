"""Do a discovery scan for ERC-4626 vaults on a chain.

- Discover new vaults on a chain
- Store the metadata in a vault database file
- Support incremental scanning

Usage:

.. code-block:: shell

    export JSON_RPC_URL=...
    poetry run python scripts/erc-4626/scan-vaults.py

Or:

.. code-block:: shell

    # TAC
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_TAC poetry run python scripts/erc-4626/scan-vaults.py

    # Arbitrum
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ARBITRUM poetry run python scripts/erc-4626/scan-vaults.py

    # Hyperliquid
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_HYPERLIQUID poetry run python scripts/erc-4626/scan-vaults.py

    # Mainnet
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ETHEREUM poetry run python scripts/erc-4626/scan-vaults.py

    # Monad
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_MONAD poetry run python scripts/erc-4626/scan-vaults.py


Or for faster small sample scan limit the end block:

    END_BLOCK=5555721 poetry run python scripts/erc-4626/scan-vaults.py

"""

import logging
import os
from pathlib import Path

import duckdb
from filelock import Timeout as FileLockTimeout

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.hyperliquid.constants import HYPEREVM_MULTICALL_GREYLIST
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.rpcdb import RPCRequestStats, RPCUsageDatabase, format_rpc_usage_report, resolve_rpc_tracking_database_path
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.rpc_scan_state import load_rpc_scan_state
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, get_pipeline_data_dir

logger = logging.getLogger(__name__)

RESET_LEADS_REMOVED_MESSAGE = "RESET_LEADS has been removed. Use the generated protocol-specific migration script described in eth_defi/erc_4626/README-vault-leads.md"

assert "RESET_LEADS" not in os.environ, RESET_LEADS_REMOVED_MESSAGE


def _run_scan(stats: RPCRequestStats, metrics: dict) -> None:
    """Run Hypersync discovery with explicit script configuration.

    The outer accounting boundary owns failure outcomes and persistence. Keeping
    this function free of catch-and-reraise wrappers produces one useful failure
    traceback while retaining partial request counts in its accumulator.

    :param stats: Physical-attempt accumulator shared with discovery workers.
    :param metrics: Mutable chain identity and scanned-item diagnostics.
    :return: None; persists discovery through the shared core scanner.
    """

    json_rpc_url = os.environ.get("JSON_RPC_URL")
    assert json_rpc_url, "JSON_RPC_URL must be set for vault contract reads"
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))

    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/scan-vaults.log"),
    )

    logger.info("Using log level: %s", default_log_level)
    end_block = int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else None

    # Resolve data and lock paths through the same helper. A relocated pipeline
    # must not lock one directory while discovery writes the home-directory DB.
    vault_db_file = get_pipeline_data_dir() / DEFAULT_VAULT_DATABASE.name
    vault_db_file.parent.mkdir(parents=True, exist_ok=True)

    hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY", None)

    assert hypersync_api_key, "HYPERSYNC_API_KEY must be set for vault event discovery"

    # Both entrypoints expose the same intentional repair override. Fine-grained
    # library callers use scan_leads() arguments instead of hidden global flags.
    force_refresh = os.environ.get("FORCE_LEAD_DISCOVERY", "false").lower() == "true"
    web3 = create_multi_provider_web3(json_rpc_url, rpc_request_stats=stats)
    metrics["chain_id"] = web3.eth.chain_id
    # The CLI owns chain selection; classification receives only a generic
    # target policy so standalone and all-chain discovery isolate the same reads.
    greylist = HYPEREVM_MULTICALL_GREYLIST if metrics["chain_id"] == 999 else frozenset()
    report = scan_leads(
        json_rpc_urls=json_rpc_url,
        vault_db_file=vault_db_file,
        max_workers=max_workers,
        start_block=None,
        end_block=end_block,
        printer=print,
        hypersync_api_key=hypersync_api_key,
        rpc_request_stats=stats,
        web3=web3,
        force_metadata_refresh=force_refresh,
        force_classification_refresh=force_refresh,
        greylist=greylist,
    )
    metrics["items_scanned"] = report.items_scanned
    # A successful discovery return can leave deferred metadata. Match the
    # all-chain outcome denominator instead of reporting queued work as complete.
    metrics["pending_candidates"] = len(load_rpc_scan_state(vault_db_file.parent / f"rpc-pending-metadata-{metrics['chain_id']}.json"))
    logger.info("Vault discovery completed")


def main() -> None:
    """Run lead discovery under the shared pipeline and DuckDB writer lock.

    This boundary retains partial physical attempts when discovery fails or is
    cancelled. Accounting failures are logged separately so reporting cannot
    replace the original scan exception or trigger an expensive discovery replay.

    :return: None; persists and reports the attempted phase before propagating failures.
    """

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
            except BaseException as error:
                # Accounting still runs on cancellation or a failed scan. Mark
                # the outcome with the exception class instead of private provider
                # text; record_scan drops this marker from persisted metrics.
                # Propagate the original failure after the finally block.
                metrics["error"] = type(error).__name__
                raise
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
                            metrics=metrics,
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
