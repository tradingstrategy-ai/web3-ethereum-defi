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
batch, and the shared checkpoint retains each chain's fixed factory candidates,
so rerunning the command after an interruption resumes without another chain
scan or discarding completed rows. Direct link repairs are deterministic local
updates and do not add token, fee or policy RPC calls.

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
- ``ENZYME_CHECKPOINT_PATH``: optional shared resumable checkpoint path.
- ``VAULT_DB_PATH``: optional vault metadata database path.

``ENZYME_SCAN_PRICES``, ``ENZYME_CLEAN_PRICES`` and
``ENZYME_REFRESH_EXISTING_METADATA`` are deliberately overridden by this
entry point. Use ``backfill-history.py`` for historical price work or an
intentional unconditional metadata refresh.
"""

import os
import runpy
from collections.abc import MutableMapping
from pathlib import Path


def configure_metadata_migration_environment(environment: MutableMapping[str, str]) -> None:
    """Force the safe, incremental current-metadata migration mode.

    Historical price scanning and cleaning are outside this command's scope.
    Existing healthy metadata must remain eligible for the incremental skip
    checks, rather than being re-read through a blanket refresh flag.

    :param environment: Process environment mapping to update in place.
    :return: None.
    """

    environment["ENZYME_SCAN_PRICES"] = "false"
    environment["ENZYME_CLEAN_PRICES"] = "false"
    environment["ENZYME_REFRESH_EXISTING_METADATA"] = "false"


def main() -> None:
    """Run the shared Enzyme migration engine in current-metadata-only mode.

    :return: None after the delegated migration exits.
    """

    configure_metadata_migration_environment(os.environ)
    migration_path = Path(__file__).with_name("backfill-history.py")
    runpy.run_path(str(migration_path), run_name="__main__")


if __name__ == "__main__":
    main()
