"""Recover a stopped Hyperliquid scanner database; defaults to read-only dry run.

Set ``TARGET_DATABASE``, ``BACKUP_SOURCES`` (JSON array of immutable filenames),
``MIGRATION_BACKUP_DIR`` and ``RECOVERY_REPORT``. Explicit ``DRY_RUN=false``
enables a backed-up transaction. Use a copied target for local rehearsals.
"""

import datetime
import json
import logging
import os
from pathlib import Path

from eth_defi.hyperliquid.permission_recovery import recover_permissions
from eth_defi.utils import setup_console_logging
from eth_defi.vault.backup import write_json_atomic
from eth_defi.vault.duckdb_backup import create_private_backup_client

logger = logging.getLogger(__name__)


def main() -> None:
    """Run recovery from explicit environment configuration.

    Database owners must be stopped. The script creates its own verified backup
    before applying any repair; an external backup is additional protection.

    :return: ``None`` after writing the machine-readable recovery report.
    """
    setup_console_logging(default_log_level="info")
    logger.info("Starting HyperCore recovery analysis")
    dry_value = os.environ.get("DRY_RUN", "true").lower()
    if dry_value not in {"true", "false"}:
        msg = "DRY_RUN must be true or false"
        raise ValueError(msg)
    target = Path(os.environ["TARGET_DATABASE"]).expanduser()
    sources = [Path(value).expanduser() for value in json.loads(os.environ.get("BACKUP_SOURCES", "[]"))]
    backup_dir = Path(os.environ.get("MIGRATION_BACKUP_DIR", str(target.parent / "migration-backups"))).expanduser()
    report_path = Path(os.environ.get("RECOVERY_REPORT", str(target.with_suffix(".recovery.json")))).expanduser()
    bounds = {str(Path(path).expanduser().resolve()): datetime.datetime.fromisoformat(value) for path, value in json.loads(os.environ.get("SOURCE_AVAILABLE_AT", "{}")).items()}
    offhost_key = os.environ.get("PRE_MIGRATION_R2_KEY")
    require_offhost = os.environ.get("REQUIRE_OFFHOST_BACKUP", "false").lower() == "true" or target.resolve().is_relative_to(Path("/root/.tradingstrategy"))
    if dry_value == "false" and require_offhost and not offhost_key:
        msg = "Production apply requires PRE_MIGRATION_R2_KEY from a gated stopped-writer backup"
        raise ValueError(msg)

    def verify_offhost(receipt: dict) -> None:
        """Bind the exact own backup to an immutable private R2 snapshot.

        Compare uploaded metadata before the repair transaction starts; mismatched
        content or length aborts apply rather than accept a stale live backup.

        :param receipt: Own pre-mutation backup digest and byte count.
        :return: ``None`` after verification, or when offhost verification is optional.
        """
        if not offhost_key:
            return
        if not offhost_key.startswith("duckdb-backups/") or not offhost_key.endswith(".duckdb"):
            msg = "Pre-migration R2 key must be an immutable registered DuckDB snapshot"
            raise ValueError(msg)
        client = create_private_backup_client()
        head = client.head_object(Bucket=os.environ["R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"], Key=offhost_key)
        if head.get("Metadata", {}).get("sha256") != receipt["sha256"] or head["ContentLength"] != receipt["size"]:
            msg = "Pre-migration offhost snapshot differs from the stopped target; refusing stale apply"
            raise ValueError(msg)
        logger.info("Verified exact pre-migration private R2 snapshot %s", offhost_key)

    report = recover_permissions(target, sources, backup_dir, dry_run=dry_value == "true", source_available_at=bounds, parquet_sources=[Path(value).expanduser() for value in json.loads(os.environ.get("PARQUET_SOURCES", "[]"))], pre_mutation_check=verify_offhost)
    write_json_atomic(report_path, report)
    logger.info("Recovery %s: %s", "dry run" if report["dry_run"] else "applied", json.dumps(report["tables"]))
    logger.info("Report saved to %s", report_path)


if __name__ == "__main__":
    main()
