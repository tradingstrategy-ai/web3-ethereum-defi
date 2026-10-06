"""Take due private R2 backups for a named stopped-writer maintenance window.

Set ``MAINTENANCE_ID`` to the reservation and ``R2_BACKUP_REPORT`` to a local
receipt filename. The database owners must be stopped. The 48-hour gates remain
in effect; a not-yet-due member makes this command refuse migration readiness.
"""

import json
import logging
import os
from pathlib import Path

from eth_defi.utils import setup_console_logging
from eth_defi.vault.backup import write_json_atomic
from eth_defi.vault.data_file_export import get_data_file_paths
from eth_defi.vault.duckdb_backup import backup_databases, backup_key_root, create_private_backup_client, database_registry
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def main() -> None:
    """Back up only the named maintenance databases and write readiness receipts.

    These receipts provide each immutable ``PRE_MIGRATION_R2_KEY``. Recovery
    independently compares that object's hash with its own pre-mutation backup.

    :return: ``None`` after both snapshots are verified remotely.
    """
    setup_console_logging(default_log_level="info")
    base = get_pipeline_data_dir()
    names = json.loads(os.environ.get("DATABASE_NAMES", '["hyperliquid-vaults","hyperliquid-vaults-hf"]'))
    registry = [entry for entry in database_registry(base, get_data_file_paths(base)) if entry.name in names]
    if {entry.name for entry in registry} != set(names):
        msg = "Unregistered maintenance database requested"
        raise ValueError(msg)
    client = create_private_backup_client()
    bucket = os.environ["R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"]
    results = backup_databases(client, bucket, registry, maintenance_id=os.environ["MAINTENANCE_ID"])
    if any(value not in {"uploaded", "unchanged"} for value in results.values()):
        msg = "Maintenance snapshots are not all due/present; migration must wait"
        raise RuntimeError(msg)
    receipts = {}
    for entry in registry:
        response = client.get_object(Bucket=bucket, Key=backup_key_root() + entry.name + "/state.json")
        try:
            state = json.loads(response["Body"].read())
        finally:
            response["Body"].close()
        head = client.head_object(Bucket=bucket, Key=state["snapshot_key"])
        if head.get("Metadata", {}).get("sha256") != state["sha256"]:
            msg = "Maintenance snapshot checksum differs"
            raise ValueError(msg)
        receipts[entry.name] = {"path": str(entry.path.resolve()), "snapshot_key": state["snapshot_key"], "sha256": state["sha256"], "size": state["size"]}
    write_json_atomic(Path(os.environ["R2_BACKUP_REPORT"]), {"maintenance_id": os.environ["MAINTENANCE_ID"], "databases": receipts})
    logger.info("Verified maintenance snapshots for %s", ", ".join(receipts))


if __name__ == "__main__":
    main()
