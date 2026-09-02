#!/usr/bin/env python3
"""Migrate all current Enzyme metadata required by the public vault export.

This is the production entry point for metadata introduced or corrected by the
Enzyme catalogue integration. It delegates discovery and batched adapter reads
to ``backfill-history.py`` while forcing historical price and cleaned-Parquet
work off. The migration therefore preserves existing price history, reader
state and timestamp caches.

For every factory-confirmed Enzyme Blue and Onyx vault, the migration provides:

- cached optional Blue descriptions and the explicit unavailable marker for
  Onyx;
- a direct address-specific Enzyme application link;
- mandatory current name, symbol and denomination, plus best-effort total
  value in the vault's accounting unit, share supply and fee metadata where
  deprecated contracts still execute;
- current Blue PolicyManager deposit permission and its qualification;
- current Onyx deposit permission reconstructed from active handler events and
  resolved with a single batched Multicall handler read.

Already migrated rows are skipped. Database writes occur after each metadata
batch, and a metadata-specific checkpoint retains each chain's fixed factory
candidates and Onyx handler set. Factory and Onyx handler events are collected
in one Hypersync stream per chain, so rerunning the command after an
interruption resumes without another chain scan or discarding completed rows.
Direct link repairs are deterministic local updates and do not add token, fee
or policy RPC calls.

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

``ENZYME_SCAN_PRICES``, ``ENZYME_CLEAN_PRICES`` and refresh flags are
deliberately overridden by this entry point. Use ``migrate-blue-fees.py`` for
an intentional Blue-fee refresh, or ``backfill-history.py`` for historical
price work.
"""

from collections.abc import MutableMapping
from pathlib import Path

from eth_defi.enzyme import migration

MIGRATION_OVERRIDES = {
    "ENZYME_SCAN_PRICES": "false",
    "ENZYME_CLEAN_PRICES": "false",
    "ENZYME_REFRESH_EXISTING_METADATA": "false",
    "ENZYME_REFRESH_BLUE_FEES": "false",
}

CHECKPOINT_FILENAME = "enzyme-current-metadata-state.json"


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

    migration.configure_enzyme_migration_environment(environment, MIGRATION_OVERRIDES, CHECKPOINT_FILENAME)


def main() -> None:
    """Run the shared Enzyme migration engine in current-metadata-only mode.

    :return: None after the delegated migration exits.
    """

    migration.run_enzyme_backfill_with_environment(MIGRATION_OVERRIDES, CHECKPOINT_FILENAME, Path(__file__).with_name("backfill-history.py"))


if __name__ == "__main__":
    main()
