"""Restore HyperCore leader shares and policy caps lost during permission recovery.

Run ``DRY_RUN=true poetry run python scripts/hyperliquid/recover-leader-shares.py``
to inspect, or ``DRY_RUN=false`` to apply. Dry run is the default; apply takes
its own verified backup before changing either database. Stop database owners
before applying. The standard pipeline directory and retained pre-repair
archives are discovered automatically. Prices and permissions are preserved.
See https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1633.
"""

import json
import logging
import os
from contextlib import nullcontext
from pathlib import Path

from tqdm_loggable.auto import tqdm

from eth_defi.hyperliquid.leader_share_recovery import recover_leader_shares
from eth_defi.hyperliquid.permission_recovery import BACKUP_DATES, DATABASE_NAMES
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.backup import observe_database_operation, write_json_atomic
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def migrate_databases(data_dir: Path, *, dry_run: bool = True) -> dict[str, dict]:
    """Recover both scanner databases using retained backups and embedded evidence.

    Dry run writes no files. Apply holds the shared pipeline lock, takes each
    database's own backup and records its result before moving to the next.
    Missing archives are logged; embedded evidence can still recover values.

    :param data_dir: Existing pipeline directory, or an isolated rehearsal copy.
    :param dry_run: Read-only analysis unless explicitly disabled.
    :return: Daily and HF recovery reports with counts and backup receipts.
    """
    targets = [data_dir / f"{name}.duckdb" for name in DATABASE_NAMES]
    for target in targets:
        if not target.is_file():
            raise FileNotFoundError(f"Required database missing: {target}")
    backup_dir = data_dir / "migration-backups" / "hypercore-leader-shares-1633"
    raw = data_dir / "backups" / BACKUP_DATES[0] / "vault-prices-1h.parquet"
    parquets = [raw] if raw.is_file() else []
    reports = {}
    lock = nullcontext() if dry_run else wait_other_writers(data_dir / "scan-pipeline", timeout=60)
    with lock:
        for target in tqdm(targets, desc="Recovering HyperCore leader shares"):
            sources = [data_dir / "backups" / date / target.name for date in BACKUP_DATES]
            available = [path for path in sources if path.is_file()]
            if len(available) < len(sources):
                logger.warning("%s: %d/%d scanner archives available; also using retained migration evidence", target.name, len(available), len(sources))
            with observe_database_operation(f"Recovering policy values in {target.name}"):
                report = recover_leader_shares(target, available, backup_dir, parquet_sources=parquets, dry_run=dry_run)
            reports[target.stem] = report
            logger.info("%s: %s", target.stem, json.dumps(report))
            if not dry_run:
                write_json_atomic(backup_dir / "recovery-report.json", reports)
    return reports


def main() -> None:
    """Run the recovery with only the standard ``DRY_RUN`` migration switch.

    Storage follows the other vault scripts' ``PIPELINE_DATA_DIR`` convention.
    No API backfill, independent clock or remote credentials are required.

    :return: ``None`` after printing dry-run or applied recovery results.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_value = os.environ.get("DRY_RUN", "true").strip().lower()
    if dry_value not in {"true", "false"}:
        msg = "DRY_RUN must be true or false"
        raise ValueError(msg)
    migrate_databases(get_pipeline_data_dir(), dry_run=dry_value == "true")


if __name__ == "__main__":
    main()
