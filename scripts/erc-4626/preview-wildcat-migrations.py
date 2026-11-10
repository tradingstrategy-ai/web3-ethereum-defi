"""Preview the Wildcat onboarding migrations on a copy of local pipeline data.

A read-only smoke test of ``063-wildcat-market-leads.py`` and
``064-wildcat-price-backfill.py``. It copies a pipeline snapshot into a
temporary directory, then runs only these two numbered migrations in preview
mode through the ordinary runner. The price backfill scans only the first
``MIGRATION_BACKFILL_BLOCKS`` blocks of every market range. Nothing is written
to the snapshot, the migration journal or the provider caches; the temporary
copy is deleted afterwards.

This is not a migration entrypoint. Production applies the migrations with
``vault-migrate run`` as described in ``.claude/docs/migration-scripts.md``.

Required environment, normally from ``.local-test.env``:

- ``JSON_RPC_ETHEREUM``: Ethereum archive RPC
- ``HYPERSYNC_API_KEY``: Hypersync credentials for block timestamps

Optional overrides:

- ``SNAPSHOT_DIR``: pipeline data to copy, default ``~/.tradingstrategy/vaults``
- ``MIGRATION_BACKFILL_BLOCKS``: blocks scanned per market, default ``20000``
- ``WORK_DIR``: parent of the temporary copy, default ``SNAPSHOT_DIR``'s parent
- ``TIMESTAMP_CACHE``, ``MAX_WORKERS``, ``HYPERSYNC_RPM``, ``LOG_LEVEL``

Example:

.. code-block:: shell

    source .local-test.env && PYTHONPATH="$PWD" poetry run python scripts/erc-4626/preview-wildcat-migrations.py
"""

import logging
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from eth_defi.utils import setup_console_logging
from eth_defi.vault_protocols import migration as runner

logger = logging.getLogger(__name__)

#: Snapshot files read by the two migrations.
SNAPSHOT_FILES = ("vault-metadata-db.pickle", "migration.json", "vault-prices-1h.parquet", "vault-reader-state-1h.pickle")

#: Numbered migrations previewed by this smoke test.
WILDCAT_MIGRATIONS = ("063-wildcat-market-leads.py", "064-wildcat-price-backfill.py")


def main() -> None:
    """Copy the snapshot and preview the Wildcat migrations on the copy.

    :raise FileNotFoundError: If the snapshot lacks a required file.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    snapshot_dir = Path(os.environ.get("SNAPSHOT_DIR", "~/.tradingstrategy/vaults")).expanduser()
    work_dir = Path(os.environ.get("WORK_DIR", str(snapshot_dir.parent))).expanduser()
    os.environ.setdefault("MIGRATION_BACKFILL_BLOCKS", "20000")
    # The runner rejects legacy overrides that disagree with its data directory.
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        os.environ.pop(name, None)

    missing = [name for name in SNAPSHOT_FILES if name != "migration.json" and not (snapshot_dir / name).is_file()]
    if missing:
        message = f"Snapshot {snapshot_dir} is missing {missing}"
        raise FileNotFoundError(message)

    with TemporaryDirectory(prefix="wildcat-migration-preview-", dir=work_dir) as temporary:
        data_dir = Path(temporary) / "data"
        scripts_dir = Path(temporary) / "scripts"
        data_dir.mkdir()
        scripts_dir.mkdir()
        for name in SNAPSHOT_FILES:
            if (snapshot_dir / name).is_file():
                logger.info("Copying %s", snapshot_dir / name)
                shutil.copy2(snapshot_dir / name, data_dir / name)
        for spec in runner.discover_migrations():
            if spec.name in WILDCAT_MIGRATIONS:
                (scripts_dir / spec.name).write_bytes(spec.source)

        logger.info("Previewing %s with MIGRATION_BACKFILL_BLOCKS=%s", ", ".join(WILDCAT_MIGRATIONS), os.environ["MIGRATION_BACKFILL_BLOCKS"])
        previewed = runner.run_pending_migrations(dry_run=True, data_dir=data_dir, scripts_dir=scripts_dir)
        logger.info("Previewed migrations: %s", previewed)


if __name__ == "__main__":
    main()
