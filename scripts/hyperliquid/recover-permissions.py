"""Recover both Hyperliquid scanner databases from the retained local archives.

Run with ``DRY_RUN=true`` to inspect recovery without writing anything, or
``DRY_RUN=false`` to apply with an automatic verified backup of each database.
Dry run is the default. No RPC, R2 credentials or migration-specific paths are
needed. The normal pipeline directory defaults to ``~/.tradingstrategy/vaults``.

This one-off repair for issue #1628 uses the reviewed scanner backups dated
2026-09-29 through 2026-10-05, newest first, and the 2026-10-05 raw price archive.
Missing archives are logged; flags without recoverable evidence become Unknown.
Later archives and the current target's flags cannot supply historical truth.
The common recovery engine also repairs cached permission metadata, so a
separate metadata migration or historical API backfill is unnecessary. See
https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1628.
"""

import json
import logging
import os
from contextlib import nullcontext
from pathlib import Path

from tqdm_loggable.auto import tqdm

from eth_defi.hyperliquid.permission_recovery import BACKUP_DATES, DATABASE_NAMES, recover_permissions
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.backup import observe_database_operation, write_json_atomic
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def migrate_databases(data_dir: Path, *, dry_run: bool = True) -> dict[str, dict]:
    """Repair both databases using the fixed archived source selection.

    Dry run opens targets read-only and logs counts without creating reports,
    backups or lock files. Apply holds the shared pipeline writer lock, creates
    each database's verified backup before its repair transaction, and persists
    the completed report after each database so partial completion is visible.

    :param data_dir: Existing pipeline directory; production uses the normal mounted state.
    :param dry_run: Analyse without persistent writes; defaults to ``True``.
    :return: Per-database recovery counts, source hashes and apply backup receipts.
    """
    data_dir = data_dir.resolve()
    targets = {name: data_dir / f"{name}.duckdb" for name in DATABASE_NAMES}
    for target in targets.values():
        if not target.is_file():
            raise FileNotFoundError(f"Required Hyperliquid database is missing: {target}")

    raw_archive = data_dir / "backups" / BACKUP_DATES[0] / "vault-prices-1h.parquet"
    parquets = [raw_archive] if raw_archive.is_file() else []
    if not parquets:
        logger.warning("Raw price archive unavailable: %s; recovering from available scanner backups", raw_archive)
    backup_dir = data_dir / "migration-backups" / "hypercore-permissions-1628"
    report_path = backup_dir / "recovery-report.json"
    reports = {}
    lock = nullcontext() if dry_run else wait_other_writers(data_dir / "scan-pipeline", timeout=60)
    if not dry_run:
        logger.info("Acquiring scan-pipeline writer lock; database owners must be stopped")
    with lock:
        for name, target in tqdm(targets.items(), desc="Recovering Hyperliquid databases"):
            candidates = [data_dir / "backups" / date / target.name for date in BACKUP_DATES]
            sources = [path for path in candidates if path.is_file()]
            missing = [str(path) for path in candidates if not path.is_file()]
            if missing:
                logger.warning("Unavailable scanner archives for %s: %s", name, ", ".join(missing))
            logger.info("%s %s using %d scanner backups and %d raw archives", "Analysing" if dry_run else "Applying recovery to", target, len(sources), len(parquets))
            with observe_database_operation(f"Recovery of {name}"):
                report = recover_permissions(target, sources, backup_dir, dry_run=dry_run, parquet_sources=parquets)
            reports[name] = report
            logger.info("%s: %s; inferred permission snapshots: %s", name, json.dumps(report["tables"]), report["legacy_price_timestamp_permissions"])
            if not dry_run:
                write_json_atomic(report_path, reports)
                logger.info("Verified pre-migration backup: %s", report["backup"]["backup"])
                logger.info("Recovery report saved to %s", report_path)
    logger.info("%s complete for both Hyperliquid databases", "Read-only dry run" if dry_run else "Backed-up recovery")
    return reports


def main() -> None:
    """Run the fixed migration with ``DRY_RUN`` as its only migration input.

    Resolve storage through the standard pipeline convention. No migration path,
    archive list or external backup scheduling configuration is required.

    :return: ``None`` after analysing or applying recovery to both databases.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_value = os.environ.get("DRY_RUN", "true").strip().lower()
    if dry_value not in {"true", "false"}:
        msg = "DRY_RUN must be true or false"
        raise ValueError(msg)
    migrate_databases(get_pipeline_data_dir(), dry_run=dry_value == "true")


if __name__ == "__main__":
    main()
