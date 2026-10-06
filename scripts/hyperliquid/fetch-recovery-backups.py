"""Download explicitly selected raw daily backups from private R2 for recovery.

Set ``BACKUP_DATES`` (JSON array of ISO dates) and ``RECOVERY_SOURCE_DIR``.
Immutable local originals are never overwritten. The inventory records remote
ETags, server availability times, metadata and locally verified SHA-256 hashes.
"""

import datetime
import json
import logging
import os
import uuid
from pathlib import Path

from tqdm_loggable.auto import tqdm

from eth_defi.utils import setup_console_logging
from eth_defi.vault.backup import file_sha256, write_json_atomic
from eth_defi.vault.duckdb_backup import create_private_backup_client

logger = logging.getLogger(__name__)
MAXIMUM_ARCHIVE_DATES = 31


def main() -> None:
    """Download bounded, operator-selected private archives with progress.

    This script prepares immutable recovery sources. It does not alter scanner
    databases, upload any data, or infer exact permission receipt times.

    :return: ``None`` after writing the verified source inventory.
    """
    setup_console_logging(default_log_level="info")
    client = create_private_backup_client()
    bucket = os.environ["R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"]
    destination = Path(os.environ["RECOVERY_SOURCE_DIR"]).expanduser()
    inventory = []
    dates = json.loads(os.environ["BACKUP_DATES"])
    if not dates or len(dates) > MAXIMUM_ARCHIVE_DATES:
        msg = "Select between 1 and 31 archive dates per invocation"
        raise ValueError(msg)
    for date in dates:
        datetime.date.fromisoformat(date)
        key = f"daily/{date}/{os.environ.get('UPLOAD_PREFIX', '')}vault-prices-1h.parquet"
        head = client.head_object(Bucket=bucket, Key=key)
        path = destination / date / "vault-prices-1h.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = head.get("Metadata", {}).get("source_sha256")
        if not path.exists():
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                logger.info("Downloading selected recovery archive %s (%s bytes)", key, head["ContentLength"])
                with tqdm(total=head["ContentLength"], unit="B", unit_scale=True, desc=f"Downloading {date}") as progress:
                    client.download_file(bucket, key, str(temporary), Callback=progress.update)
                digest = file_sha256(temporary)
                if temporary.stat().st_size != head["ContentLength"] or (expected and digest != expected):
                    msg = "Recovery archive checksum/size mismatch"
                    raise ValueError(msg)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        digest = file_sha256(path)
        if path.stat().st_size != head["ContentLength"] or (expected and digest != expected):
            msg = "Existing immutable recovery source differs from the selected R2 archive"
            raise ValueError(msg)
        inventory.append({"path": str(path.resolve()), "key": key, "sha256": digest, "etag": head["ETag"], "source_available_at": head["LastModified"].replace(tzinfo=None).isoformat(), "size": path.stat().st_size})
    write_json_atomic(destination / f"r2-recovery-inventory-{uuid.uuid4().hex}.json", {"sources": inventory})
    logger.info("Prepared %s verified raw recovery archives", len(inventory))


if __name__ == "__main__":
    main()
