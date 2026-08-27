"""Build isolated metadata for the private crypto-vaults bundle.

The module intentionally does not reuse the public top-vaults selection or its
state path.  It shares only the established metric calculations and JSON
serialisation helpers so that public stablecoin exports remain unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from atomicwrites import atomic_write

from eth_defi.compat import native_datetime_utc_now
from eth_defi.research.vault_metrics import calculate_lifetime_metrics, export_lifetime_row
from eth_defi.research.wrangle_vault_prices import filter_vaults_by_denomination_families, generate_cleaned_vault_datasets, materialise_daily_crypto_prices
from eth_defi.vault.base import VaultSpec, verify_parquet_file
from eth_defi.vault.denomination import (
    BTC_USD_GUIDELINE_RATE,
    CRYPTO_DENOMINATION_FAMILY_NAMES,
    ETH_USD_GUIDELINE_RATE,
    DenominationFamily,
    classify_denomination,
    convert_usd_threshold_to_denomination,
    get_denomination_whitelist_digest,
    get_denomination_whitelist_entry,
    normalise_denomination_symbol,
)
from eth_defi.vault.top_vaults_json import build_export_metadata, validate_strict_json_serialisable
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

#: Stable schema version for the isolated metadata and sticky-state documents.
CRYPTO_VAULTS_SCHEMA_VERSION = 1

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CryptoVaultPaths:
    """Explicit local paths for one crypto-vaults build.

    :param directory:
        Bundle directory below the pipeline data directory.
    :param cleaned_price_path:
        Daily observation-preserving Parquet file.
    :param metadata_path:
        JSON metadata output.
    :param sticky_state_path:
        Private sticky qualification state.
    :param manifest_path:
        Current bundle manifest, created during R2 publication.
    """

    directory: Path
    cleaned_price_path: Path
    metadata_path: Path
    sticky_state_path: Path
    manifest_path: Path

    @classmethod
    def from_directory(cls, directory: Path) -> CryptoVaultPaths:
        """Construct all local bundle paths beneath one explicit directory.

        :param directory:
            Destination directory for the isolated bundle.
        :return:
            Paths with names that cannot collide in flattened local backups.
        """
        return cls(
            directory=directory,
            cleaned_price_path=directory / "cleaned-crypto-vault-prices-1d.parquet",
            metadata_path=directory / "crypto-vault-metadata.json",
            sticky_state_path=directory / "crypto-vault-export-state.json",
            manifest_path=directory / "crypto-vault-manifest.json",
        )


def resolve_crypto_vault_paths(data_dir: Path) -> CryptoVaultPaths:
    """Resolve crypto bundle paths under one pipeline data directory.

    :param data_dir:
        Root pipeline data directory.
    :return:
        Explicit bundle paths using backup-safe unique basenames.
    """
    return CryptoVaultPaths.from_directory(data_dir / "crypto-vaults")


def build_crypto_vault_prices(
    *,
    vault_db_path: Path,
    uncleaned_path: Path,
    cleaned_path: Path,
    cleaned_stablecoin_path: Path,
    settlement_db_path: Path | None = None,
) -> None:
    """Create the isolated daily stablecoin/ETH/BTC price Parquet.

    Stablecoin rows are derived from the existing standard cleaned Parquet;
    ETH/BTC rows are cleaned from raw data. The result retains the final real
    observation for each vault and UTC day.
    It does not forward fill the exported rows; the shared lifetime-metrics
    calculation forward fills only its internal calendar-day series.

    :param vault_db_path:
        Common scanner vault-metadata pickle.
    :param uncleaned_path:
        Shared raw vault-price Parquet source.
    :param cleaned_path:
        Isolated daily crypto Parquet destination.
    :param cleaned_stablecoin_path:
        Existing stablecoin-only cleaned Parquet from the standard cleaner.
    :param settlement_db_path:
        Optional vault-settlement DuckDB database.
    :return:
        ``None``. Raises if price cleaning cannot safely complete.
    """
    if not cleaned_stablecoin_path.is_file():
        raise FileNotFoundError(cleaned_stablecoin_path)
    vault_db = VaultDatabase.read(vault_db_path)
    logger.info("Loading existing stablecoin prices %s", cleaned_stablecoin_path)
    stable_prices = pd.read_parquet(cleaned_stablecoin_path, dtype_backend="pyarrow")
    stable_prices = filter_vaults_by_denomination_families(
        vault_db.rows,
        stable_prices,
        {DenominationFamily.stablecoin},
        add_denomination_family=True,
        logger=logger.info,
    )
    stable_prices = materialise_daily_crypto_prices(stable_prices)
    eth_btc_families = frozenset({DenominationFamily.eth, DenominationFamily.btc})
    raw_vault_specs = {spec for spec, row in vault_db.rows.items() if classify_denomination(row.get("Denomination")) in eth_btc_families}
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    price_frames = [stable_prices]
    if raw_vault_specs:
        logger.info("Cleaning ETH/BTC prices for %d vaults from %s", len(raw_vault_specs), uncleaned_path)
        with tempfile.TemporaryDirectory(dir=cleaned_path.parent, prefix="crypto-vaults-") as temporary_directory:
            eth_btc_path = Path(temporary_directory) / "eth-btc-prices.parquet"
            generate_cleaned_vault_datasets(
                vault_db_path=vault_db_path,
                price_df_path=uncleaned_path,
                cleaned_price_df_path=eth_btc_path,
                settlement_db_path=settlement_db_path,
                denomination_families=eth_btc_families,
                daily_materialisation=True,
                raw_vault_specs=raw_vault_specs,
                vault_db=vault_db,
                logger=logger.info,
            )
            price_frames.append(pd.read_parquet(eth_btc_path, dtype_backend="pyarrow"))
    else:
        logger.info("No ETH/BTC vaults in the metadata database")
    combined = pd.concat(price_frames).sort_values(["id", "timestamp"], kind="stable")
    temporary_fd, temporary_path_text = tempfile.mkstemp(suffix=".parquet", dir=cleaned_path.parent)
    os.close(temporary_fd)
    temporary_path = Path(temporary_path_text)
    try:
        table = pa.Table.from_pandas(combined)
        metadata = dict(table.schema.metadata or {})
        metadata.update(
            {
                b"crypto_vaults_bundle_schema_version": b"1",
                b"crypto_vaults_whitelist_sha256": get_denomination_whitelist_digest().encode("ascii"),
                b"crypto_vaults_fixed_usd_rates": json.dumps({"BTC": float(BTC_USD_GUIDELINE_RATE), "ETH": float(ETH_USD_GUIDELINE_RATE), "USD": 1}, sort_keys=True).encode("ascii"),
            }
        )
        pq.write_table(table.replace_schema_metadata(metadata), temporary_path, compression="zstd")
        verify_parquet_file(temporary_path, expected_rows=len(combined), required_columns=["id", "share_price", "timestamp", "denomination_family", "returns_1d"])
        os.replace(temporary_path, cleaned_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _load_sticky_state(path: Path) -> dict[str, Any]:
    """Load the isolated sticky state without resetting corrupt production data.

    :param path:
        Sticky-state JSON file.
    :return:
        Valid state document or an empty initial document.
    """
    if not path.exists():
        return {"schema_version": CRYPTO_VAULTS_SCHEMA_VERSION, "vaults": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != CRYPTO_VAULTS_SCHEMA_VERSION or not isinstance(state.get("vaults"), dict):
        raise ValueError(f"Invalid crypto vault sticky state: {path}")
    return state


def _save_json_atomic(payload: dict[str, Any], path: Path) -> None:
    """Validate and atomically write one JSON document.

    :param payload:
        Strictly JSON-serialisable document.
    :param path:
        Destination path.
    :return:
        ``None``.
    """
    validate_strict_json_serialisable(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True, encoding="utf-8") as output:
        json.dump(payload, output, indent=2, ensure_ascii=False, allow_nan=False)


def _vault_record_with_native_units(record: dict[str, Any], vault_row: VaultRow, threshold_usd: Decimal) -> tuple[dict[str, Any], Decimal]:
    """Convert one common metric record to the crypto bundle schema.

    :param record:
        Serialised common metric record.
    :param vault_row:
        Source vault database row.
    :param threshold_usd:
        Fixed USD qualification guideline.
    :return:
        Native-unit crypto record and its resolved qualification threshold.
    """
    symbol = vault_row["Denomination"]
    family = classify_denomination(symbol)
    assert family is not DenominationFamily.unsupported
    whitelist_entry = get_denomination_whitelist_entry(symbol)
    assert family is DenominationFamily.stablecoin or whitelist_entry is not None
    token_data = vault_row.get("_denomination_token") or {}
    threshold = convert_usd_threshold_to_denomination(threshold_usd, symbol)
    result = dict(record)
    result["denomination_family"] = family.value
    result["canonical_underlying"] = "USD" if family is DenominationFamily.stablecoin else whitelist_entry.canonical_underlying
    result["wrapper_kind"] = "stablecoin" if family is DenominationFamily.stablecoin else whitelist_entry.wrapper_kind
    result["denomination_token_symbol"] = normalise_denomination_symbol(symbol)
    result["denomination_token_address"] = token_data.get("address")
    result["denomination_token_decimals"] = token_data.get("decimals")
    result["total_assets_unit"] = symbol
    result["current_total_assets"] = result.pop("current_nav", None)
    result["peak_total_assets"] = result.pop("peak_nav", None)
    result["qualification_threshold"] = float(threshold)
    result["qualification_threshold_usd_guideline"] = float(threshold_usd)
    result.pop("denomination_token_rate", None)
    return result, threshold


def _validate_crypto_price_rows(vault_db: VaultDatabase, prices_df: pd.DataFrame) -> None:
    """Ensure crypto price rows have matching supported vault metadata.

    :param vault_db:
        Common vault metadata database.
    :param prices_df:
        Crypto cleaned price rows with an ``id`` column.
    :return:
        ``None``. Raises when an input row cannot be represented safely.
    """
    price_vault_ids = set(prices_df["id"].astype(str))
    vault_rows_by_id = {spec.as_string_id(): row for spec, row in vault_db.rows.items()}
    metadata_vault_ids = set(vault_rows_by_id)
    unknown_vault_ids = price_vault_ids - metadata_vault_ids
    if unknown_vault_ids:
        raise ValueError(f"Crypto cleaned prices contain vaults absent from metadata: {sorted(unknown_vault_ids)!r}")
    unsupported_vault_ids = {vault_id for vault_id in price_vault_ids if classify_denomination(vault_rows_by_id[vault_id]["Denomination"]) is DenominationFamily.unsupported}
    if unsupported_vault_ids:
        raise ValueError(f"Crypto cleaned prices contain unsupported denominations: {sorted(unsupported_vault_ids)!r}")


def build_crypto_vault_metadata(
    *,
    vault_db_path: Path,
    cleaned_price_path: Path,
    metadata_path: Path,
    sticky_state_path: Path,
    threshold_usd: Decimal | None = None,
) -> dict[str, Any]:
    """Calculate and atomically persist private crypto-vault metadata.

    The function follows the existing top-vaults commit order: metadata JSON is
    written first and sticky state immediately afterwards.  A later R2 failure
    leaves the local state advanced, ready for the next successful publication.

    :param vault_db_path:
        Common vault metadata pickle.
    :param cleaned_price_path:
        Crypto daily cleaned Parquet path.
    :param metadata_path:
        Crypto metadata JSON destination.
    :param sticky_state_path:
        Crypto sticky-state JSON destination.
    :param threshold_usd:
        Optional fixed USD guideline; defaults to environment/config value.
    :return:
        JSON-serialisable metadata document.
    """
    threshold_usd = threshold_usd or Decimal(os.environ.get("CRYPTO_VAULTS_MIN_TVL_USD", "5000"))
    vault_db = VaultDatabase.read(vault_db_path)
    prices_df = pd.read_parquet(cleaned_price_path)
    if not isinstance(prices_df.index, pd.DatetimeIndex):
        prices_df["timestamp"] = pd.to_datetime(prices_df["timestamp"])
        prices_df.set_index("timestamp", inplace=True)
    _validate_crypto_price_rows(vault_db, prices_df)
    metrics_df = calculate_lifetime_metrics(prices_df, vault_db)
    state = _load_sticky_state(sticky_state_path)
    selected_records: list[dict[str, Any]] = []
    current_ids: set[str] = set()

    for _, metric_row in metrics_df.iterrows():
        vault_id = str(metric_row["id"])
        vault_row = vault_db.rows[VaultSpec.parse_string(vault_id, separator="-")]
        record, resolved_threshold = _vault_record_with_native_units(export_lifetime_row(metric_row), vault_row, threshold_usd)
        current_ids.add(vault_id)
        peak_assets = record.get("peak_total_assets")
        qualifies = peak_assets is not None and float(peak_assets) >= float(resolved_threshold)
        prior = state["vaults"].get(vault_id)
        if qualifies or (prior and prior.get("denomination_family") == record["denomination_family"]):
            record["sticky_export"] = not qualifies
            selected_records.append(record)
            state["vaults"][vault_id] = {
                "denomination_family": record["denomination_family"],
                "denomination_symbol": record["denomination_token_symbol"],
                "threshold": float(resolved_threshold),
                "updated_at": native_datetime_utc_now().isoformat(),
            }

    state["vaults"] = {key: value for key, value in state["vaults"].items() if key in current_ids}
    metadata = {
        "bundle": "crypto-vaults",
        "schema_version": CRYPTO_VAULTS_SCHEMA_VERSION,
        "generated_at": native_datetime_utc_now().isoformat(),
        "metadata": build_export_metadata(),
        "denomination_whitelist_sha256": get_denomination_whitelist_digest(),
        "denomination_families": list(CRYPTO_DENOMINATION_FAMILY_NAMES),
        "threshold_usd_guideline": float(threshold_usd),
        "fixed_usd_rates": {"ETH": float(ETH_USD_GUIDELINE_RATE), "BTC": float(BTC_USD_GUIDELINE_RATE)},
        "vaults": selected_records,
    }
    _save_json_atomic(metadata, metadata_path)
    _save_json_atomic(state, sticky_state_path)
    return metadata
