"""Purge corrupted Royco tranche vault data from the uncleaned price parquet.

Before the RoycoTrancheHistoricalReader was deployed (2026-06-05), the generic
ERC-4626 reader decoded the first word of the Royco AssetClaims tuple as a
single uint256 instead of using ``claims.nav``. The first word (``stAssets``)
can be plausibly small yet still wrong, so a threshold filter is unreliable.
This script removes **all** historical rows for affected Royco tranche vaults
and resets their reader states so the scanner re-populates them from scratch
with the correct reader.

This is also the reference example for vault classification migrations that
corrupted historical price rows or reader-state progress. For metadata-only
feature drift where ``_detection_data.features`` is populated but the top-level
``features`` field is missing or empty, use
``scripts/erc-4626/repair-vault-features.py`` instead.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/purge-royco-tranche-data.py

Environment variables:

- ``VAULT_DB``: Path to the vault metadata pickle. Defaults to
  ``~/.tradingstrategy/vaults/vault-metadata-db.pickle``.
- ``UNCLEANED_PRICE_DATABASE``: Path to the uncleaned price parquet. Same env
  var as ``scan-prices.py``. Defaults to
  ``~/.tradingstrategy/vaults/vault-prices-1h.parquet``.
- ``READER_STATE_DATABASE``: Path to the reader state pickle. Same env var as
  ``scan-prices.py``. Defaults to
  ``~/.tradingstrategy/vaults/vault-reader-state-1h.pickle``.
- ``DRY_RUN``: Set to ``true`` to only report without modifying files.
"""

import logging
import os
import pickle
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


#: Error marker produced by the generic ERC-4626 reader when it sees Royco's
#: ``AssetClaims`` tuple return.
NON_STANDARD_ABI_ERROR = "non-standard ABI"

#: Names that indicate Royco tranche vaults in metadata rows.
TRANCHE_NAME_MARKER = "Tranche"


@dataclass(slots=True, frozen=True)
class MetadataRepairResult:
    """Result of repairing Royco tranche metadata features.

    :param specs:
        All Royco tranche specs the purge script should operate on.

    :param repaired_rows:
        Number of vault metadata rows that would be or were updated.
    """

    specs: set[VaultSpec]
    repaired_rows: int


def collect_non_standard_abi_specs(parquet_path: Path) -> set[VaultSpec]:
    """Collect vault specs that produced Royco tuple ABI errors.

    These rows were read with the generic ERC-4626 reader, which rejects
    Royco ``AssetClaims`` tuple payloads after the guard was added. They are
    strong evidence that the stored metadata features are stale and should be
    repaired to ``royco_tranche_like``.

    :param parquet_path:
        Path to the uncleaned price parquet.

    :return:
        Set of affected ``VaultSpec`` instances.
    """
    if not parquet_path.exists():
        logger.warning("Cannot scan non-standard ABI rows, parquet file not found: %s", parquet_path)
        return set()

    df = pd.read_parquet(parquet_path, columns=["chain", "address", "errors"])
    errors = df["errors"].fillna("").astype(str)
    affected_df = df[errors.str.contains(NON_STANDARD_ABI_ERROR, na=False)]
    specs = {VaultSpec(int(row.chain), str(row.address).lower()) for row in affected_df[["chain", "address"]].drop_duplicates().itertuples(index=False)}
    logger.info("Found %d specs with non-standard ABI errors", len(specs))
    return specs


def is_royco_tranche_metadata_row(
    spec: VaultSpec,
    row: dict,
    error_specs: set[VaultSpec],
) -> bool:
    """Check whether a vault metadata row should be treated as a Royco tranche.

    The primary signal is ``royco_tranche_like`` in stored features. For stale
    rows we also accept names like ``Royco Senior Tranche ...`` and any row
    that produced a Royco tuple ABI error in the uncleaned parquet.

    :param spec:
        Vault spec for the metadata row.

    :param row:
        Vault metadata row from :class:`VaultDatabase`.

    :param error_specs:
        Specs collected from ``non-standard ABI`` parquet errors.

    :return:
        ``True`` if the row should use the Royco tranche reader.
    """
    features = set(row.get("features") or set())
    detection = row.get("_detection_data")
    if detection is not None:
        features.update(detection.features)

    if ERC4626Feature.royco_tranche_like in features:
        return True

    if spec in error_specs:
        return True

    name = row.get("Name") or ""
    return "Royco" in name and TRANCHE_NAME_MARKER in name


def repair_royco_tranche_metadata(
    vault_db_path: Path,
    parquet_path: Path,
    *,
    dry_run: bool,
) -> MetadataRepairResult:
    """Repair stored Royco tranche features in the vault metadata DB.

    ``scan-prices.py`` creates vault instances from the stored
    ``_detection_data.features`` set. If a tranche vault was discovered before
    the Royco chain probes covered its chain, it is stored with an empty
    feature set and the generic reader is selected. This function adds
    ``royco_tranche_like`` and refreshes the row protocol so subsequent scans
    instantiate :class:`RoycoTrancheVault`.

    :param vault_db_path:
        Path to the vault metadata pickle.

    :param parquet_path:
        Path to the uncleaned price parquet, used to discover rows that already
        emitted ``non-standard ABI`` errors.

    :param dry_run:
        If ``True``, report only without modifying files.

    :return:
        Specs to purge/rescan and number of repaired metadata rows.
    """
    try:
        db = VaultDatabase.read(vault_db_path)
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("Could not read vault database %s: %s", vault_db_path, e)
        return MetadataRepairResult(specs=set(), repaired_rows=0)

    error_specs = collect_non_standard_abi_specs(parquet_path)
    specs: set[VaultSpec] = set()
    repaired_rows = 0

    for spec, row in db.rows.items():
        if not is_royco_tranche_metadata_row(spec, row, error_specs):
            continue

        specs.add(spec)

        changed = False
        features = set(row.get("features") or set())
        if ERC4626Feature.royco_tranche_like not in features:
            features.add(ERC4626Feature.royco_tranche_like)
            row["features"] = features
            changed = True

        detection = row.get("_detection_data")
        if detection is not None and ERC4626Feature.royco_tranche_like not in detection.features:
            detection.features.add(ERC4626Feature.royco_tranche_like)
            changed = True

        protocol_name = get_vault_protocol_name(features)
        if row.get("Protocol") != protocol_name:
            row["Protocol"] = protocol_name
            changed = True

        if changed:
            repaired_rows += 1
            logger.info("Repairing metadata for %s: Protocol=%s, features=%s", spec, row.get("Protocol"), sorted(f.value for f in features))

    if repaired_rows == 0:
        logger.info("No Royco tranche metadata feature repairs needed")
    elif dry_run:
        logger.info("DRY RUN: would repair %d vault metadata rows in %s", repaired_rows, vault_db_path)
    else:
        backup_path = vault_db_path.with_suffix(".pickle.bak-royco-purge")
        logger.info("Creating vault DB backup at %s", backup_path)
        shutil.copy2(vault_db_path, backup_path)
        db.write(vault_db_path)
        logger.info("Repaired %d vault metadata rows in %s", repaired_rows, vault_db_path)

    return MetadataRepairResult(specs=specs, repaired_rows=repaired_rows)


def find_royco_tranche_specs(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    parquet_path: Path = DEFAULT_UNCLEANED_PRICE_DATABASE,
) -> set[VaultSpec]:
    """Find Royco tranche vault specs from the vault metadata DB.

    This compatibility wrapper performs a dry-run metadata repair and returns
    the specs the main purge flow would operate on.

    :param vault_db_path:
        Path to the vault metadata pickle.

    :param parquet_path:
        Path to the uncleaned price parquet.

    :return:
        Set of ``VaultSpec`` instances.
    """
    return repair_royco_tranche_metadata(vault_db_path, parquet_path, dry_run=True).specs


def purge_tranche_rows(
    parquet_path: Path,
    specs: set[VaultSpec],
    dry_run: bool,
) -> int:
    """Remove all rows for affected Royco tranche vaults from the uncleaned price parquet.

    The generic reader decoded the first word of the ``AssetClaims`` tuple
    (``stAssets``) instead of ``nav``. The first word can be plausibly small
    yet still wrong, so a threshold filter is unreliable. We remove **all**
    historical rows for the affected specs and let the scanner re-populate
    them with the correct ``RoycoTrancheHistoricalReader``.

    Matches on both ``chain`` and ``address`` columns to avoid deleting
    data from unrelated chains that happen to share an address.

    :param parquet_path:
        Path to the uncleaned price parquet.

    :param specs:
        Set of ``VaultSpec`` (chain_id, address) pairs to purge.

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

    # Build a set of (chain_id, lowercase_address) tuples for matching
    spec_pairs = {(spec.chain_id, spec.vault_address.lower()) for spec in specs}

    # Find all rows belonging to affected Royco tranche vaults
    affected_mask = pd.Series(
        [(int(chain), addr.lower()) in spec_pairs for chain, addr in zip(df["chain"], df["address"])],
        index=df.index,
    )
    affected_count = affected_mask.sum()

    if affected_count == 0:
        logger.info("No rows found for %d Royco tranche specs", len(specs))
        return 0

    # Show details of what we're removing
    affected_df = df[affected_mask]
    per_vault = affected_df.groupby(["chain", "address"]).agg(
        rows=("address", "count"),
        min_timestamp=("timestamp", "min"),
        max_timestamp=("timestamp", "max"),
    )
    logger.info("Rows to purge:")
    for (chain, addr), row in per_vault.iterrows():
        logger.info(
            "  chain=%d %s: %d rows, date range %s to %s",
            chain,
            addr,
            row["rows"],
            row["min_timestamp"],
            row["max_timestamp"],
        )

    if dry_run:
        logger.info("DRY RUN: would remove %d of %d rows", affected_count, original_len)
        return affected_count

    # Create backup
    backup_path = parquet_path.with_suffix(".parquet.bak-royco-purge")
    logger.info("Creating backup at %s", backup_path)
    shutil.copy2(parquet_path, backup_path)

    # Remove all rows for affected vaults and write with canonical schema
    df_clean = df[~affected_mask]
    logger.info("Writing cleaned parquet: %d -> %d rows (removed %d)", original_len, len(df_clean), affected_count)
    VaultHistoricalRead.write_uncleaned_parquet(df_clean, parquet_path)

    return affected_count


def clear_reader_states(
    reader_state_path: Path,
    specs: set[VaultSpec],
    dry_run: bool,
) -> int:
    """Clear reader states for Royco tranche vaults so they rescan from scratch.

    Matches on full ``VaultSpec`` (chain_id, address) to avoid clearing
    unrelated chains.

    :param reader_state_path:
        Path to the reader state pickle.

    :param specs:
        Set of ``VaultSpec`` (chain_id, address) pairs to clear.

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

    to_clear = [spec for spec in states if spec in specs]

    if not to_clear:
        logger.info("No reader states found for Royco tranche specs")
        return 0

    logger.info("Found %d reader states to clear", len(to_clear))
    for spec in to_clear:
        state = states[spec]
        logger.info(
            "  %s (chain %d): entry_count=%s, last_block=%s",
            spec.vault_address,
            spec.chain_id,
            state.get("entry_count"),
            state.get("last_block"),
        )

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

    # Use the same env vars as scan-prices.py so operators with custom paths
    # purge the same files the scanner reads/writes.
    vault_db_path = Path(os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE))).expanduser()
    parquet_path = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", str(DEFAULT_UNCLEANED_PRICE_DATABASE))).expanduser()
    reader_state_path = Path(os.environ.get("READER_STATE_DATABASE", str(DEFAULT_READER_STATE_DATABASE))).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if dry_run:
        logger.info("DRY RUN MODE — no files will be modified")

    # 1. Repair stale metadata features and collect tranche specs
    metadata_repair = repair_royco_tranche_metadata(vault_db_path, parquet_path, dry_run=dry_run)
    tranche_specs = metadata_repair.specs
    if not tranche_specs:
        logger.warning("No Royco tranche vaults found in vault metadata DB")
        logger.info("You can also specify addresses manually by extending this script")
        return

    logger.info("Found %d Royco tranche vault specs", len(tranche_specs))

    # 2. Purge all rows for affected tranche vaults from parquet
    purged = purge_tranche_rows(parquet_path, tranche_specs, dry_run)

    # 3. Clear reader states so vaults rescan from beginning
    cleared = clear_reader_states(reader_state_path, tranche_specs, dry_run)

    # 4. Summary
    action = "would be " if dry_run else ""
    logger.info("Royco tranche specs found: %d", len(tranche_specs))
    logger.info("Vault metadata rows %srepaired: %d", action, metadata_repair.repaired_rows)
    logger.info("Tranche rows %spurged: %d", action, purged)
    logger.info("Reader states %scleared: %d", action, cleared)

    if purged > 0 or cleared > 0:
        # Group specs by chain_id for per-chain rescan commands.
        # scan-prices.py scans one chain at a time, so we need one command per chain.
        # VAULT_ID + START_BLOCK=1 forces a targeted rescan from the first block
        # instead of resuming from the max last_block of other chain vaults.
        from eth_defi.chain import get_chain_name

        specs_by_chain: dict[int, list[VaultSpec]] = {}
        for spec in tranche_specs:
            specs_by_chain.setdefault(spec.chain_id, []).append(spec)

        logger.info("")
        logger.info("Next steps — rescan affected vaults per chain:")
        for chain_id, chain_specs in sorted(specs_by_chain.items()):
            vault_ids = ",".join(sorted(str(s) for s in chain_specs))
            chain_name = get_chain_name(chain_id)
            rpc_var = f"JSON_RPC_{chain_name.upper().replace(' ', '_')}"
            logger.info("")
            logger.info("  # %s (chain %d)", chain_name, chain_id)
            logger.info(
                '  VAULT_ID="%s" START_BLOCK=1 JSON_RPC_URL=$%s poetry run python scripts/erc-4626/scan-prices.py',
                vault_ids,
                rpc_var,
            )
        logger.info("")
        logger.info("After rescanning, re-run the post-processing pipeline:")
        logger.info("  poetry run python scripts/erc-4626/post-process-prices.py")


if __name__ == "__main__":
    main()
