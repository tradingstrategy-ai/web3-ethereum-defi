"""Report, snapshot or safely reset vault scanner RPC counters.

See README-vault-scripts.md for mounted production maintenance and recovery.
The default is read-only reporting. RESET_RPC_COUNTERS=true requires a stable
RPC_COUNTER_RESET_ID. BACKUP_RPC_COUNTERS=true takes a snapshot without reset.
"""

import logging
import os
from pathlib import Path

import duckdb
from tabulate import tabulate

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.provider.rpc_counter_maintenance import backup_rpc_counters, fetch_counter_inventory
from eth_defi.provider.rpcdb import resolve_rpc_tracking_database_path
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def main() -> None:
    """Run environment-configured counter maintenance under the pipeline lock.

    No scanning, provider requests or reader progress changes are performed.
    The operator must first arrange idle accounting writers.

    :return: None; logs the verified result or raises on failed safety checks.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    source = resolve_rpc_tracking_database_path()
    pipeline_dir = Path(os.environ.get("PIPELINE_DATA_DIR", str(get_pipeline_data_dir()))).expanduser()
    backup_dir = Path(os.environ.get("RPC_COUNTER_BACKUP_DIR", str(source.parent / "backups" / "rpc-counters"))).expanduser()
    reset = os.environ.get("RESET_RPC_COUNTERS", "false").lower() == "true"
    snapshot = os.environ.get("BACKUP_RPC_COUNTERS", "false").lower() == "true"
    reset_id = os.environ.get("RPC_COUNTER_RESET_ID")
    if reset and not reset_id:
        raise ValueError("RESET_RPC_COUNTERS=true requires RPC_COUNTER_RESET_ID; reuse it on recovery")
    logger.info("Waiting for the scan-pipeline lock; accounting writers must be idle")
    with wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60):
        if reset or snapshot:
            pipeline_files = {path for pattern in ("*.pickle", "*.parquet", "*state*.json", "rpc-*.json") for path in pipeline_dir.glob(pattern)}
            timestamp_folders = {DEFAULT_TIMESTAMP_CACHE_FOLDER, pipeline_dir.parent / "block-timestamp"}
            timestamp_files = {path for folder in timestamp_folders for path in folder.glob("*.duckdb*")}
            protected_paths = tuple(sorted(pipeline_files | timestamp_files))
            manifest = backup_rpc_counters(source, backup_dir, reset_id if reset else None, protected_paths=protected_paths)
            logger.info("Verified backup: %s; reset committed: %s", manifest["backup_path"], manifest["reset_committed"])
        else:
            if not source.is_file():
                raise FileNotFoundError(source)
            with duckdb.connect(str(source), read_only=True) as connection:
                inventory = fetch_counter_inventory(connection)
            logger.info("Read-only counter inventory:\n%s", tabulate([(table, data["rows"], data.get("calls", data.get("errors", "")), data.get("max_cycle", "")) for table, data in inventory.items()], headers=["Table", "Rows", "Attempts/errors", "Max cycle"]))


if __name__ == "__main__":
    main()
