"""Reconcile cleaned vault histories against committed scanner inputs."""

import hashlib
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.neutral_trade.constants import is_public_metadata
from eth_defi.research.wrangle_vault_prices import (
    disambiguate_vault_names,
    generate_cleaned_vault_datasets,
    materialise_daily_crypto_prices,
    replace_cleaned_price_rows,
)
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.crypto_vaults import calculate_crypto_native_admission
from eth_defi.vault.denomination import DenominationFamily, classify_denomination
from eth_defi.vault.publication_state import duckdb_revision, file_revision
from eth_defi.vault.settlement_data import load_vault_settlements
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)

#: Invalidate previous histories when the cleaner or its shared policy changes.
CLEANER_VERSION = 1

NATIVE_FAMILIES = frozenset({DenominationFamily.eth, DenominationFamily.btc})
STABLECOIN_FAMILY = frozenset({DenominationFamily.stablecoin})
PUBLISHED_FAMILIES = NATIVE_FAMILIES | STABLECOIN_FAMILY


def _cleaning_policy() -> list[int | str]:
    """Identify policy and hash-engine changes that invalidate every history."""
    return [CLEANER_VERSION, duckdb.__version__, os.environ.get("CRYPTO_VAULTS_MIN_TVL_USD", "5000")]


def cleaning_inputs(raw_path: Path, vault_db_path: Path, settlement_path: Path) -> list:
    """Identify committed files before opening the full price histories.

    :param raw_path: Combined raw Parquet.
    :param vault_db_path: Catalogue pickle.
    :param settlement_path: Settlement DuckDB.
    :return: Input generations, including an in-place DuckDB update.
    """
    return [*_cleaning_policy(), file_revision(raw_path), file_revision(vault_db_path), duckdb_revision(settlement_path)]


def cleaning_snapshot(raw_path: Path, vault_db_path: Path, settlement_path: Path) -> dict[str, Any]:
    """Fingerprint committed history and cleaner metadata by canonical vault ID.

    A grouped DuckDB scan avoids loading the combined dataset into Pandas.
    Counts and two aggregates of the same row hashes record multiplicity and
    detect same-count corrections. Comparing input snapshots also covers raw
    and settlement writers outside the scheduler, including migrations.

    :param raw_path: Combined input prices.
    :param vault_db_path: Metadata pickle.
    :param settlement_path: Settlement database.
    :return: Schema and per-vault fingerprints for the publication checkpoint.
    """
    started_at = time.perf_counter()
    schema = pq.read_schema(raw_path)
    registry = sorted((key.decode(), value.decode()) for key, value in (schema.metadata or {}).items() if key.startswith(b"perp_dex."))
    contract = [*_cleaning_policy(), str(schema.remove_metadata()), [list(item) for item in registry]]

    catalogue = VaultDatabase.read(vault_db_path)
    policy = {}
    for vault_id, row in disambiguate_vault_names(catalogue.rows, logger.debug).items():
        family = classify_denomination(row.get("Denomination"))
        if family not in PUBLISHED_FAMILIES or not is_public_metadata(row):
            continue
        detection = row["_detection_data"]
        policy[vault_id] = [family.value, row["Name"], row["Protocol"], detection.deposit_count + detection.redeem_count, ERC4626Feature.buoy_like in detection.features]

    raw_by_vault = defaultdict(list)
    with duckdb.connect() as connection:
        observations = connection.execute(
            """
            SELECT chain, address, count(*), sum(hash(prices)::HUGEINT), bit_xor(hash(prices))
            FROM read_parquet(?) prices
            GROUP BY chain, address
            ORDER BY chain, address
            """,
            [str(raw_path)],
        ).fetchall()
    for chain, address, count, total, xor in observations:
        # Unsupported historical rows without catalogue metadata can still
        # exist in the raw file. The full cleaner already excludes them.
        try:
            vault_id = VaultSpec(chain, address).as_string_id()
        except AssertionError:
            continue
        if vault_id in policy:
            raw_by_vault[vault_id].append([address, count, total, xor])

    settlements = defaultdict(set)
    for chain, address, timestamp in load_vault_settlements(settlement_path)[["chain_id", "address", "timestamp"]].itertuples(index=False, name=None):
        vault_id = VaultSpec(int(chain), address).as_string_id()
        if vault_id in raw_by_vault:
            settlements[vault_id].add(timestamp.isoformat())

    vaults = {}
    for vault_id, history in raw_by_vault.items():
        family, *metadata = policy[vault_id]
        fingerprints = [history, metadata, sorted(settlements[vault_id])]
        vaults[vault_id] = [family, sum(item[1] for item in history), hashlib.sha256(repr(fingerprints).encode()).hexdigest()]
    logger.info("Compared committed cleaning inputs for %d vault histories in %.2fs", len(vaults), time.perf_counter() - started_at)
    return {"contract": contract, "vaults": vaults}


def changed_histories(previous: dict, current: dict) -> tuple[set[str], set[str]] | None:
    """Select dirty stablecoin and ETH/BTC histories for each product.

    Removed histories stay in the work set. A denomination move updates both
    products. Large changes use the established full cleaner.

    :param previous: Snapshot from the last successful publication.
    :param current: Snapshot for the committed source files.
    :return: Two sets of vault IDs, or `None` when full cleaning is required.
    """
    if current["contract"] != previous.get("contract"):
        return None
    old = previous["vaults"]
    new = current["vaults"]
    stable = set()
    native = set()
    dirty_rows = 0
    for vault_id in old.keys() | new.keys():
        before = old.get(vault_id)
        after = new.get(vault_id)
        if before == after:
            continue
        sides = [side for side in (before, after) if side is not None]
        dirty_rows += max(side[1] for side in sides)
        families = {side[0] for side in sides}
        if DenominationFamily.stablecoin in families:
            stable.add(vault_id)
        if NATIVE_FAMILIES & families:
            native.add(vault_id)
    total_rows = sum(entry[1] for entry in new.values())
    logger.info("Cleaning input changes: %d stablecoin vaults, %d ETH/BTC vaults, %d dirty raw rows of %d", len(stable), len(native), dirty_rows, total_rows)
    if dirty_rows * 2 > total_rows:
        return None
    return stable, native


def _clean_selected(
    ids: set[str],
    families: frozenset[DenominationFamily],
    raw_path: Path,
    vault_db_path: Path,
    settlement_path: Path,
    destination: Path,
) -> pd.DataFrame:
    """Run the full transform on just the requested complete histories.

    :param ids: Canonical vault IDs present in this family.
    :param families: Denomination families to include.
    :param raw_path: Committed combined prices.
    :param vault_db_path: Full metadata catalogue.
    :param settlement_path: Committed settlements.
    :param destination: Scratch Parquet for the selected histories.
    :return: Cleaned rows for the selected vaults, potentially empty.
    """
    if not ids:
        return pd.DataFrame()
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db_path,
        price_df_path=raw_path,
        cleaned_price_df_path=destination,
        settlement_db_path=settlement_path,
        denomination_families=families,
        raw_vault_specs={VaultSpec.parse_string(vault_id) for vault_id in ids},
        daily_materialisation=families == NATIVE_FAMILIES,
        logger=logger.info,
    )
    return pd.read_parquet(destination, dtype_backend="pyarrow").reset_index()


def replace_changed_histories(
    current: dict,
    stable_ids: set[str],
    native_ids: set[str],
    *,
    raw_path: Path,
    vault_db_path: Path,
    settlement_path: Path,
    hourly_path: Path,
    daily_path: Path,
    crypto_path: Path,
) -> bool:
    """Rebuild dirty histories and atomically replace their output rows.

    All three products use complete vault histories. The caller acknowledges
    the snapshot after every required output has reached its new generation.

    :param current: Latest committed input snapshot.
    :param stable_ids: Dirty stablecoin IDs, including removals.
    :param native_ids: Dirty ETH/BTC IDs, including removals.
    :param raw_path: Committed combined prices.
    :param vault_db_path: Full metadata catalogue.
    :param settlement_path: Committed settlements.
    :param hourly_path: Existing stablecoin hourly Parquet.
    :param daily_path: Existing stablecoin daily sidecar.
    :param crypto_path: Existing combined stablecoin and ETH/BTC daily Parquet.
    :return: Whether the daily derivatives completed after the public prices.
    """
    if not stable_ids and not native_ids:
        logger.info("Vault price cleaning skipped: committed histories unchanged")
        return True
    by_id = current["vaults"]
    stable_inputs = {vault_id for vault_id in stable_ids if vault_id in by_id and by_id[vault_id][0] in STABLECOIN_FAMILY}
    native_inputs = {vault_id for vault_id in native_ids if vault_id in by_id and by_id[vault_id][0] in NATIVE_FAMILIES}

    with TemporaryDirectory(dir=hourly_path.parent, prefix="dirty-vaults-") as directory:
        scratch = Path(directory)
        hourly = _clean_selected(stable_inputs, STABLECOIN_FAMILY, raw_path, vault_db_path, settlement_path, scratch / "stable.parquet")
        if stable_ids:
            replace_cleaned_price_rows(hourly_path, stable_ids, hourly, logger=logger.info, allow_empty_output=False, allow_same_timestamp_blocks=True)
        try:
            stable_daily = materialise_daily_crypto_prices(hourly).reset_index() if not hourly.empty else pd.DataFrame()
            if stable_ids:
                replace_cleaned_price_rows(daily_path, stable_ids, stable_daily, logger=logger.info)
            native_daily = _clean_selected(native_inputs, NATIVE_FAMILIES, raw_path, vault_db_path, settlement_path, scratch / "native.parquet")
            if not native_daily.empty:
                admission = calculate_crypto_native_admission(VaultDatabase.read(vault_db_path), native_daily)
                native_daily = native_daily.loc[native_daily["id"].isin(admission.qualifying_ids)]
            frames = [frame for frame in (stable_daily, native_daily) if not frame.empty]
            combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            replace_cleaned_price_rows(crypto_path, stable_ids | native_ids, combined, logger=logger.info)
        except (OSError, ValueError, KeyError, pa.ArrowException) as exc:
            logger.warning("Daily vault price publication deferred: %s", exc)
            return False
    return True
