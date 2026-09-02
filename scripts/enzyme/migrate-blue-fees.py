#!/usr/bin/env python3
"""Refresh current Enzyme Blue fees without rewriting price history.

This migration discovers every factory-confirmed Enzyme Blue VaultProxy on
Ethereum, Polygon, Base and Arbitrum, then rereads its current FeeManager and
ProtocolFeeTracker configuration at one fixed head block per chain. It writes
the resulting management, performance, entrance, exit and protocol-reference
fees to the shared vault metadata database. Enzyme's protocol-access charge is
included in management; subtract the separately exported protocol value to
calculate the manager-only rate. No historical fee configuration, raw-price
Parquet, cleaned-price Parquet or reader state is modified.

Run this once after a Blue fee-reader change. Ordinary all-chain vault scans
subsequently refresh the same current metadata whenever their lead-discovery
cache expires (seven days by default), so JSON exports track later fee changes.

Usage::

    # Review the factory-discovered scope without writes
    source .local-test.env && DRY_RUN=true poetry run python scripts/enzyme/migrate-blue-fees.py

    # Apply the resumable current-fee refresh
    source .local-test.env && MAX_WORKERS=8 poetry run python scripts/enzyme/migrate-blue-fees.py

Environment variables:

- ``DRY_RUN``: print the discovery plan without writing, default ``false``.
- ``JSON_RPC_ETHEREUM``, ``JSON_RPC_POLYGON``, ``JSON_RPC_BASE`` and
  ``JSON_RPC_ARBITRUM``: required multi-provider RPC configurations.
- ``HYPERSYNC_API_KEY``: required for factory event discovery.
- ``MAX_WORKERS``: current-metadata worker count, default ``8``.
- ``ENZYME_METADATA_BATCH_SIZE``: durable batch size, default ``128``.
- ``ENZYME_CHECKPOINT_PATH``: optional resumable checkpoint path.
- ``VAULT_DB_PATH``: optional vault metadata database path.
- ``PIPELINE_LOCK_TIMEOUT``: seconds to wait for the shared writer lock,
  default ``60``.
"""

from collections.abc import MutableMapping
from pathlib import Path

from eth_defi.enzyme import migration

MIGRATION_OVERRIDES = {
    "ENZYME_SCAN_PRICES": "false",
    "ENZYME_CLEAN_PRICES": "false",
    "ENZYME_REFRESH_EXISTING_METADATA": "false",
    "ENZYME_REFRESH_BLUE_FEES": "true",
}

CHECKPOINT_FILENAME = "enzyme-blue-fees-state.json"


def configure_blue_fee_migration_environment(environment: MutableMapping[str, str]) -> None:
    """Force the Blue current-fee migration mode.

    This preserves every historical database and price file. The dedicated
    refresh flag selects every factory-confirmed Blue row. The shared engine
    still performs its normal incremental repairs for other Enzyme rows.

    :param environment: Process environment mapping to update in place.
    :return: None.
    """

    migration.configure_enzyme_migration_environment(environment, MIGRATION_OVERRIDES, CHECKPOINT_FILENAME)


def main() -> None:
    """Run the shared Enzyme engine in Blue current-fee refresh mode.

    :return: None after the delegated migration exits.
    """

    migration.run_enzyme_backfill_with_environment(MIGRATION_OVERRIDES, CHECKPOINT_FILENAME, Path(__file__).with_name("backfill-history.py"))


if __name__ == "__main__":
    main()
