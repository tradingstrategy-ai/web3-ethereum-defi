#!/usr/bin/env python3
"""Refresh current Enzyme Blue and Onyx fees without rewriting price history.

This migration discovers every factory-confirmed Enzyme Blue VaultProxy and
Onyx Shares vehicle, then rereads each current fee configuration at one fixed
head block per chain. It writes management, performance, entrance and exit
fees to the shared vault metadata database. Blue additionally exports its
ProtocolFeeTracker component as a transparent breakdown included in management.

An absent Blue fee plugin or Onyx FeeHandler/tracker is a confirmed zero when
the authoritative configuration enumeration succeeds. Failed or unsupported
reads remain unavailable; the migration never substitutes a zero for an RPC
or contract-read failure, and leaves that row eligible for a later retry. No
historical fee configuration, raw-price Parquet, cleaned-price Parquet or
reader state is modified.

Usage::

    source .local-test.env && DRY_RUN=true poetry run python scripts/enzyme/migrate-enzyme-fees.py
    source .local-test.env && MAX_WORKERS=8 poetry run python scripts/enzyme/migrate-enzyme-fees.py

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
    "ENZYME_REFRESH_BLUE_FEES": "false",
    "ENZYME_REFRESH_ENZYME_FEES": "true",
}

CHECKPOINT_FILENAME = "enzyme-fees-state.json"


def configure_enzyme_fee_migration_environment(environment: MutableMapping[str, str]) -> None:
    """Force the all-Enzyme current-fee migration mode.

    The shared migration engine still performs its normal repair work, but
    this explicit flag selects every factory-confirmed Blue and Onyx row for a
    current fee read. Historical price and reader state files remain untouched.

    :param environment: Process environment mapping to update in place.
    :return: None.
    """

    migration.configure_enzyme_migration_environment(environment, MIGRATION_OVERRIDES, CHECKPOINT_FILENAME)


def main() -> None:
    """Run the shared Enzyme engine in all current-fee refresh mode.

    :return: None after the delegated migration exits.
    """

    migration.run_enzyme_backfill_with_environment(MIGRATION_OVERRIDES, CHECKPOINT_FILENAME, Path(__file__).with_name("backfill-history.py"))


if __name__ == "__main__":
    main()
