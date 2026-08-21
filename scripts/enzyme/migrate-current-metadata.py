#!/usr/bin/env python3
"""Migrate all current Enzyme metadata required by the public vault export.

This is the production entry point for metadata introduced or corrected by the
Enzyme catalogue integration. It delegates discovery and batched adapter reads
to ``backfill-history.py`` while forcing historical price and cleaned-Parquet
work off. The migration therefore preserves existing price history, reader
state and timestamp caches.

For every factory-confirmed Enzyme Blue and Onyx vault, the migration provides:

- complete short and long descriptions;
- a direct address-specific Enzyme application link;
- current name, symbol, denomination, TVL, share supply and fee metadata;
- current Blue deposit permission and its qualification;
- the intentional ``unknown`` Onyx permission until handler indexing exists.

Already migrated rows are skipped. Database writes occur after each metadata
batch, and a metadata-specific checkpoint retains each chain's fixed factory
candidates, so rerunning the command after an interruption resumes without
another chain scan or discarding completed rows. Direct link repairs are
deterministic local updates and do not add token, fee or policy RPC calls.

The delegated engine holds the shared scanner writer lock from the initial
metadata-pickle read through the final write. If the looped scanner is active,
stop it first or wait until the lock becomes available.

Usage::

    # Non-mutating discovery and coverage plan
    DRY_RUN=true poetry run python scripts/enzyme/migrate-current-metadata.py

    # Apply the resumable metadata migration
    MAX_WORKERS=8 poetry run python scripts/enzyme/migrate-current-metadata.py

Environment variables:

- ``DRY_RUN``: print the discovery plan without writing, default ``false``.
- ``JSON_RPC_ETHEREUM``, ``JSON_RPC_POLYGON``, ``JSON_RPC_BASE`` and
  ``JSON_RPC_ARBITRUM``: required multi-provider RPC configurations.
- ``HYPERSYNC_API_KEY``: required for one factory-event discovery per chain.
- ``MAX_WORKERS``: current-metadata worker count, default ``8``.
- ``ENZYME_METADATA_BATCH_SIZE``: durable batch size, default ``128``.
- ``ENZYME_CHECKPOINT_PATH``: optional metadata-only resumable checkpoint path.
- ``VAULT_DB_PATH``: optional vault metadata database path.
- ``PIPELINE_LOCK_TIMEOUT``: seconds to wait for the shared writer lock,
  default ``60``.

``ENZYME_SCAN_PRICES``, ``ENZYME_CLEAN_PRICES`` and
``ENZYME_REFRESH_EXISTING_METADATA`` are deliberately overridden by this
entry point. Use ``backfill-history.py`` for historical price work or an
intentional unconditional metadata refresh.
"""

import os
import runpy
from collections.abc import MutableMapping
from pathlib import Path

from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE

#: Environment values temporarily forced while the delegated script runs.
MIGRATION_ENVIRONMENT_VARIABLES = (
    "ENZYME_SCAN_PRICES",
    "ENZYME_CLEAN_PRICES",
    "ENZYME_REFRESH_EXISTING_METADATA",
    "ENZYME_CHECKPOINT_PATH",
)


def configure_metadata_migration_environment(environment: MutableMapping[str, str]) -> None:
    """Force the safe, incremental current-metadata migration mode.

    Historical price scanning and cleaning are outside this command's scope.
    Existing healthy metadata must remain eligible for the incremental skip
    checks, rather than being re-read through a blanket refresh flag. A
    separate default checkpoint prevents this metadata-only command from
    marking an unfinished historical backfill complete.

    :param environment: Process environment mapping to update in place.
    :return: None.
    """

    environment["ENZYME_SCAN_PRICES"] = "false"
    environment["ENZYME_CLEAN_PRICES"] = "false"
    environment["ENZYME_REFRESH_EXISTING_METADATA"] = "false"
    vault_db_path = Path(environment.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    environment.setdefault("ENZYME_CHECKPOINT_PATH", str(vault_db_path.with_name("enzyme-current-metadata-state.json")))


def main() -> None:
    """Run the shared Enzyme migration engine in current-metadata-only mode.

    :return: None after the delegated migration exits.
    """

    previous_values = {name: os.environ.get(name) for name in MIGRATION_ENVIRONMENT_VARIABLES}
    try:
        configure_metadata_migration_environment(os.environ)
        migration_path = Path(__file__).with_name("backfill-history.py")
        runpy.run_path(str(migration_path), run_name="__main__")
    finally:
        for name, value in previous_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
