"""Purge corrupted Royco tranche vault data from the uncleaned price parquet.

Before the RoycoTrancheHistoricalReader was deployed (2026-06-05), the generic
ERC-4626 reader decoded the Royco AssetClaims tuple as a single uint256,
producing astronomically large total_assets and share_price values. This script
removes those corrupted rows so the scanner can re-populate them correctly.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/purge-royco-tranche-data.py

Environment variables:

- ``PRICE_PARQUET``: Path to the uncleaned price parquet. Defaults to
  ``~/.tradingstrategy/vaults/vault-prices-1h.parquet``.
- ``READER_STATE``: Path to the reader state pickle. Defaults to
  ``~/.tradingstrategy/vaults/vault-reader-state-1h.pickle``.
- ``DRY_RUN``: Set to ``true`` to only report without modifying files.
- ``MAX_REASONABLE_TOTAL_ASSETS``: Maximum acceptable total_assets value.
  Rows above this are treated as corrupted. Default: 1e12 (1 trillion).
"""

import logging
import os
import pickle
import shutil
from pathlib import Path

import pandas as pd

from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


#: Royco tranche vaults are identified by the royco_tranche_like feature.
#: We find them in the vault metadata DB by their Protocol field.
ROYCO_PROTOCOL_NAME = "Royco"

#: Default threshold for corrupted values. Standard ERC-4626 totalAssets
#: should never exceed this for legitimate DeFi vaults.
DEFAULT_MAX_REASONABLE_TOTAL_ASSETS = 1e12


def find_royco_tranche_addresses() -> set[str]:
    """Find Royco tranche vault addresses from the vault metadata DB.

    These are vaults with Protocol='Royco' and names matching tranche patterns.

    :return:
        Set of lowercase vault addresses.
    """
    try:
        db = VaultDatabase.read()
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("Could not read vault database: %s", e)
        return set()

    addresses = set()
    for spec, row in db.rows.items():
        if row.get("Protocol") != ROYCO_PROTOCOL_NAME:
            continue
        name = row.get("Name", "")
        # Tranche vaults have "Senior Tranche" or "Junior Tranche" in their name
        if "Tranche" in name:
            addresses.add(spec.vault_address.lower())

    return addresses


def purge_corrupted_rows(
    parquet_path: Path,
    addresses: set[str],
    max_total_assets: float,
    dry_run: bool,
) -> int:
    """Remove corrupted rows from the uncleaned price parquet.

    :param parquet_path:
        Path to the uncleaned price parquet.

    :param addresses:
        Set of vault addresses (lowercase) to check.

    :param max_total_assets:
        Maximum reasonable total_assets threshold.

    :param dry_run:
        If True, report only without modifying the file.

    :return:
        Number of rows removed.
    """
    if not parquet_path.exists():
        logger.error("Parquet file not found: %s", parquet_path)
        return 0

    logger.info("Reading parquet from %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    original_len = len(df)

    # Find corrupted rows: vault address matches AND total_assets exceeds threshold
    corrupted_mask = df["address"].str.lower().isin(addresses) & (df["total_assets"] > max_total_assets)
    corrupted_count = corrupted_mask.sum()

    if corrupted_count == 0:
        logger.info("No corrupted rows found for %d Royco tranche addresses", len(addresses))
        return 0

    # Show details of what we're removing
    corrupted_df = df[corrupted_mask]
    per_vault = corrupted_df.groupby("address").agg(
        rows=("address", "count"),
        max_total_assets=("total_assets", "max"),
        min_timestamp=("timestamp", "min"),
        max_timestamp=("timestamp", "max"),
    )
    logger.info("Corrupted rows found:")
    for addr, row in per_vault.iterrows():
        logger.info(
            "  %s: %d rows, max_total_assets=%.2e, date range %s to %s",
            addr,
            row["rows"],
            row["max_total_assets"],
            row["min_timestamp"],
            row["max_timestamp"],
        )

    if dry_run:
        logger.info("DRY RUN: would remove %d of %d rows", corrupted_count, original_len)
        return corrupted_count

    # Create backup
    backup_path = parquet_path.with_suffix(".parquet.bak-royco-purge")
    logger.info("Creating backup at %s", backup_path)
    shutil.copy2(parquet_path, backup_path)

    # Remove corrupted rows
    df_clean = df[~corrupted_mask]
    logger.info("Writing cleaned parquet: %d -> %d rows (removed %d)", original_len, len(df_clean), corrupted_count)
    df_clean.to_parquet(parquet_path, index=False)

    return corrupted_count


def clear_reader_states(
    reader_state_path: Path,
    addresses: set[str],
    dry_run: bool,
) -> int:
    """Clear reader states for Royco tranche vaults so they rescan from scratch.

    :param reader_state_path:
        Path to the reader state pickle.

    :param addresses:
        Set of vault addresses (lowercase) to clear.

    :param dry_run:
        If True, report only.

    :return:
        Number of reader states cleared.
    """
    if not reader_state_path.exists():
        logger.warning("Reader state file not found: %s", reader_state_path)
        return 0

    with open(reader_state_path, "rb") as f:
        states = pickle.load(f)

    to_clear = [spec for spec in states if spec.vault_address.lower() in addresses]

    if not to_clear:
        logger.info("No reader states found for Royco tranche addresses")
        return 0

    logger.info("Found %d reader states to clear", len(to_clear))
    for spec in to_clear:
        state = states[spec]
        logger.info("  %s (chain %d): entry_count=%s, last_block=%s", spec.vault_address, spec.chain_id, state.get("entry_count"), state.get("last_block"))

    if dry_run:
        logger.info("DRY RUN: would clear %d reader states", len(to_clear))
        return len(to_clear)

    # Create backup
    backup_path = reader_state_path.with_suffix(".pickle.bak-royco-purge")
    shutil.copy2(reader_state_path, backup_path)

    for spec in to_clear:
        del states[spec]

    with open(reader_state_path, "wb") as f:
        pickle.dump(states, f)

    logger.info("Cleared %d reader states, saved to %s", len(to_clear), reader_state_path)
    return len(to_clear)


def main():
    setup_console_logging(default_log_level="INFO")

    parquet_path = Path(os.environ.get("PRICE_PARQUET", str(DEFAULT_UNCLEANED_PRICE_DATABASE))).expanduser()
    reader_state_path = Path(os.environ.get("READER_STATE", str(parquet_path.parent / "vault-reader-state-1h.pickle"))).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    max_total_assets = float(os.environ.get("MAX_REASONABLE_TOTAL_ASSETS", str(DEFAULT_MAX_REASONABLE_TOTAL_ASSETS)))

    if dry_run:
        logger.info("DRY RUN MODE — no files will be modified")

    # Find tranche vault addresses
    tranche_addresses = find_royco_tranche_addresses()
    if not tranche_addresses:
        logger.warning("No Royco tranche vaults found in vault metadata DB")
        logger.info("You can also specify addresses manually by extending this script")
        return

    logger.info("Found %d Royco tranche vault addresses", len(tranche_addresses))

    # Purge corrupted rows from parquet
    purged = purge_corrupted_rows(parquet_path, tranche_addresses, max_total_assets, dry_run)

    # Clear reader states so vaults rescan from beginning
    cleared = clear_reader_states(reader_state_path, tranche_addresses, dry_run)

    # Summary
    print()
    print(f"Royco tranche addresses found: {len(tranche_addresses)}")
    print(f"Corrupted parquet rows {'would be ' if dry_run else ''}purged: {purged}")
    print(f"Reader states {'would be ' if dry_run else ''}cleared: {cleared}")

    if not dry_run and purged > 0:
        print()
        print("Next steps:")
        print("  1. Re-run the price scanner to repopulate tranche data with the correct reader:")
        print(f"     source .local-test.env && poetry run python scripts/erc-4626/scan-prices.py")
        print("  2. Wait for several scan cycles to accumulate enough data points")
        print("  3. Re-run the post-processing pipeline to regenerate cleaned data")


if __name__ == "__main__":
    main()
