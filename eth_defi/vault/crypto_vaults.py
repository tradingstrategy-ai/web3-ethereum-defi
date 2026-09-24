"""Build isolated metadata for the private crypto-vaults bundle.

The module intentionally does not reuse the public top-vaults selection or its
state path.  It shares only the established metric calculations and JSON
serialisation helpers so that public stablecoin exports remain unchanged.
"""

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd
import psutil
from atomicwrites import atomic_write
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.currency_api.cleaning import KNOWN_BAD_RATES
from eth_defi.currency_api.constants import SOURCE_NAME
from eth_defi.feed.stablecoin_rate import StablecoinRateFeeder
from eth_defi.research.metrics_freshness import (
    CRYPTO_METRICS_STATE_FILENAME,
    clear_period_rankings,
    compute_vault_tvl_observations,
    free_memory,
    load_metrics_state,
    load_valid_previous_crypto_records,
    partition_due_vault_ids,
    refresh_metrics_state,
    save_metrics_state,
)
from eth_defi.research.vault_metrics import (
    UNUSED_METRIC_PRICE_COLUMNS,
    USD_RATE_ERROR_INSUFFICIENT_COVERAGE,
    USD_RATE_ERROR_INVALID_SERIES,
    USD_RATE_ERROR_MISSING_SERIES,
    CryptoUSDConversionContext,
    calculate_lifetime_metrics,
    calculate_sparse_daily_returns_for_all_vaults,
    calculate_vault_record,
    export_lifetime_row,
    slugify_vaults,
)
from eth_defi.research.wrangle_vault_prices import (
    filter_vaults_by_denomination_families,
    generate_cleaned_vault_datasets,
    materialise_daily_crypto_prices,
)
from eth_defi.vault.base import VaultSpec, verify_parquet_file
from eth_defi.vault.denomination import (
    BTC_USD_GUIDELINE_RATE,
    CRYPTO_DENOMINATION_FAMILY_NAMES,
    ETH_USD_GUIDELINE_RATE,
    DenominationFamily,
    classify_denomination,
    convert_usd_threshold_to_denomination,
    get_denomination_whitelist_digest,
    get_denomination_wrapper_kind,
)
from eth_defi.vault.top_vaults_json import build_export_metadata, validate_strict_json_serialisable
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

#: Bundle identifier stored in crypto metadata and manifest documents.
CRYPTO_VAULTS_BUNDLE_NAME = "crypto-vaults"

#: Stable schema version for crypto metadata, sticky state and manifests.
CRYPTO_VAULTS_SCHEMA_VERSION = 2

#: Private daily Parquet filename for the isolated crypto-vaults bundle.
CRYPTO_CLEANED_PRICE_FILENAME = "crypto-cleaned-vault-prices-1d.parquet"

logger = logging.getLogger(__name__)

#: Maximum provider-day gap filled when building the effective USD rate curve.
#: A longer gap starts a new metric segment rather than inventing a price.
USD_RATE_FORWARD_FILL_DAYS = 3

#: Broad plausibility ranges for inverted USD-per-native exchange rates.
#: They reject malformed provider values without turning a normal market move
#: into an unsupported denomination failure.
USD_RATE_BOUNDS = {
    DenominationFamily.eth.value: (10.0, 100_000.0),
    DenominationFamily.btc.value: (100.0, 1_000_000.0),
}

#: Hard native-unit admission thresholds for the private crypto bundle.
#:
#: These values deliberately do not use a live USD conversion.  The source
#: denomination family is the unit used by ``total_assets`` in the cleaned
#: price data, so the policy cannot drift as exchange rates change.
CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS = MappingProxyType(
    {
        DenominationFamily.eth: Decimal("2.5"),
        DenominationFamily.btc: Decimal("0.1"),
    }
)

#: Columns read by ``calculate_vault_record`` on the native fast path.
#:
#: Keeping this projection explicit prevents large descriptive/object columns
#: from being copied into every per-vault group. Optional columns retain the
#: protocol-specific fields that the common record builder can export.
NATIVE_METRIC_COLUMNS = (
    "id",
    "chain",
    "event_count",
    "block_number",
    "share_price",
    "total_assets",
    "vault_poll_frequency",
    "available_liquidity",
    "utilisation",
    "leader_fraction",
    "leader_commission",
    "account_pnl",
    "follower_count",
    "cumulative_volume",
    "perp_position_data_status",
    "perp_long_notional",
    "perp_short_notional",
    "perp_largest_position_notional",
    "perp_open_position_count",
    "perp_metrics_observed_at",
    "perp_quote_asset",
)


@dataclass(frozen=True, slots=True)
class USDExchangeRateSeriesBuild:
    """Validated effective USD-rate series for one denomination family.

    The result keeps the optional rate curve, its export provenance and a
    stable error together, avoiding positional tuple handling at the caller.
    """

    #: Effective USD-per-underlying daily rate curve when validation succeeds.
    rates: pd.Series | None

    #: JSON-serialisable source coverage information when source rows exist.
    coverage: dict[str, Any] | None

    #: Stable reason USD metrics cannot be calculated for this family.
    error_reason: str | None


@dataclass(frozen=True, slots=True)
class CryptoNativeAdmission:
    """Summarise native-unit admission for one cleaned price frame.

    The summary is calculated from the lifetime maximum finite
    ``total_assets`` value for each ETH/BTC vault. It is also used to filter
    the cleaned Parquet before the expensive metadata calculation.
    """

    #: Native ETH/BTC vault IDs represented by the input frame.
    native_ids: frozenset[str]

    #: IDs whose lifetime peak reaches the configured family threshold.
    qualifying_ids: frozenset[str]

    #: Number of ETH/BTC source rows represented by the input frame.
    native_row_count: int

    #: Number of source rows belonging to qualifying IDs.
    qualifying_row_count: int

    #: Per-family vault and row counts for audit logging.
    family_counts: dict[str, dict[str, int]]

    @property
    def skipped_ids(self) -> frozenset[str]:
        """Return native IDs rejected by the hard threshold.

        The difference is computed from immutable ID sets, so callers cannot
        accidentally alter the admission decision.

        :return:
            Rejected native vault IDs.
        """
        return self.native_ids - self.qualifying_ids

    @property
    def skipped_row_count(self) -> int:
        """Return the number of native rows belonging to rejected IDs.

        This count is derived from the source and qualifying row totals used
        by the admission log.

        :return:
            Number of rejected source rows.
        """
        return self.native_row_count - self.qualifying_row_count


@dataclass(frozen=True, slots=True)
class CryptoVaultPaths:
    """Explicit local paths for one crypto-vaults build.

    Instances are constructed by :func:`resolve_crypto_vault_paths`, which
    derives every payload from one bundle directory.
    """

    #: Bundle directory below the pipeline data directory.
    directory: Path

    #: Daily observation-preserving Parquet file.
    cleaned_price_path: Path

    #: JSON metadata output.
    metadata_path: Path

    #: Brotli-compressed JSON metadata output.
    compressed_metadata_path: Path

    #: Private sticky qualification state.
    sticky_state_path: Path

    #: Current bundle manifest, created during R2 publication.
    manifest_path: Path


def resolve_crypto_vault_paths(data_dir: Path, directory: Path | None = None) -> CryptoVaultPaths:
    """Resolve crypto bundle paths under one pipeline data directory.

    Every private bundle artefact is derived here so cleaning, metadata
    generation and publication cannot silently choose different locations.

    :param data_dir:
        Root pipeline data directory.
    :param directory:
        Optional explicit crypto bundle directory.
    :return:
        Explicit bundle paths using backup-safe unique basenames.
    """
    bundle_dir = directory if directory is not None else data_dir / CRYPTO_VAULTS_BUNDLE_NAME
    metadata_path = bundle_dir / "crypto-vault-metadata.json"
    return CryptoVaultPaths(
        directory=bundle_dir,
        cleaned_price_path=bundle_dir / CRYPTO_CLEANED_PRICE_FILENAME,
        metadata_path=metadata_path,
        compressed_metadata_path=metadata_path.with_suffix(".json.br"),
        sticky_state_path=bundle_dir / "crypto-vault-export-state.json",
        manifest_path=bundle_dir / "crypto-vault-manifest.json",
    )


def _family_by_vault_id(vault_db: VaultDatabase) -> dict[str, DenominationFamily]:
    """Build the current denomination-family lookup keyed by vault ID.

    Classification stays in one helper so admission and metadata partitioning
    always use the same reviewed denomination policy.

    :param vault_db:
        Vault metadata database containing the reviewed denomination symbols.
    :return:
        Mapping from serialised vault ID to its classified denomination family.
    """
    return {spec.as_string_id(): classify_denomination(row.get("Denomination")) for spec, row in vault_db.rows.items()}


def _calculate_native_family_admission(native_frame: pd.DataFrame, peak_assets: pd.Series) -> tuple[dict[str, dict[str, int]], frozenset[str]]:
    """Calculate per-family native admission counts and qualifying IDs.

    Each family is compared directly in its native unit. The returned counts
    are diagnostic only and do not become part of the exported data contract.

    :param native_frame:
        Projected native rows with ``id``, ``family`` and ``total_assets``.
    :param peak_assets:
        Finite lifetime maximum asset values indexed by vault ID.
    :return:
        Per-family audit counts and the union of qualifying IDs.
    """
    qualifying_ids: set[str] = set()
    family_counts: dict[str, dict[str, int]] = {}
    for family, decimal_threshold in CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS.items():
        family_mask = native_frame["family"] == family
        family_ids = set(native_frame.loc[family_mask, "id"])
        threshold = float(decimal_threshold)
        qualifying_family_ids = set(peak_assets.loc[peak_assets.index.isin(family_ids) & peak_assets.ge(threshold)].index)
        qualifying_ids.update(qualifying_family_ids)
        family_rows = int(family_mask.sum())
        qualifying_rows = int(native_frame["id"].isin(qualifying_family_ids).sum())
        family_counts[family.value] = {
            "vaults": len(family_ids),
            "qualifying": len(qualifying_family_ids),
            "skipped": len(family_ids - qualifying_family_ids),
            "rows": family_rows,
            "qualifying_rows": qualifying_rows,
            "skipped_rows": family_rows - qualifying_rows,
        }
    return family_counts, frozenset(qualifying_ids)


def calculate_crypto_native_admission(vault_db: VaultDatabase, prices_df: pd.DataFrame) -> CryptoNativeAdmission:
    """Calculate hard ETH/BTC admission from native lifetime peak assets.

    The helper reads only ``id`` and ``total_assets`` plus the denomination
    family from the vault database. Invalid, negative and non-finite asset
    observations cannot qualify a vault. Threshold comparisons use the
    float64 representation actually stored in the Parquet, with the Decimal
    constants converted once at the policy boundary.

    :param vault_db:
        Vault metadata database used to classify each price row.
    :param prices_df:
        Cleaned price rows with ``id`` and ``total_assets`` columns.
    :return:
        Native admission summary including qualifying IDs and audit counts.
    :raises ValueError:
        If the input frame lacks the columns required for native admission.
    """
    required_columns = {"id", "total_assets"}
    missing_columns = required_columns - set(prices_df.columns)
    if missing_columns:
        raise ValueError(f"Native admission requires columns: {sorted(missing_columns)!r}")

    family_by_id = _family_by_vault_id(vault_db)
    ids = prices_df["id"].astype(str)
    families = ids.map(family_by_id)
    native_mask = families.isin(tuple(CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS))
    native_frame = pd.DataFrame(
        {
            "id": ids.loc[native_mask],
            "family": families.loc[native_mask],
            "total_assets": pd.to_numeric(prices_df.loc[native_mask, "total_assets"], errors="coerce").astype("float64"),
        }
    )
    native_ids = frozenset(native_frame["id"].unique())
    if native_frame.empty:
        return CryptoNativeAdmission(
            native_ids=frozenset(),
            qualifying_ids=frozenset(),
            native_row_count=0,
            qualifying_row_count=0,
            family_counts={family.value: {"vaults": 0, "qualifying": 0, "skipped": 0, "rows": 0, "qualifying_rows": 0, "skipped_rows": 0} for family in CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS},
        )

    valid_assets = native_frame["total_assets"].notna() & np.isfinite(native_frame["total_assets"]) & native_frame["total_assets"].ge(0)
    valid_native = native_frame.loc[valid_assets]
    peak_assets = valid_native.groupby("id", sort=False, observed=True)["total_assets"].max()

    family_counts, qualifying_ids = _calculate_native_family_admission(native_frame, peak_assets)
    qualifying_row_count = int(native_frame["id"].isin(qualifying_ids).sum())
    return CryptoNativeAdmission(
        native_ids=native_ids,
        qualifying_ids=frozenset(qualifying_ids),
        native_row_count=len(native_frame),
        qualifying_row_count=qualifying_row_count,
        family_counts=family_counts,
    )


def _log_crypto_native_admission(admission: CryptoNativeAdmission, *, phase: str) -> None:
    """Log one auditable native admission summary for a long-running phase.

    The message exposes both accepted and excluded work without adding
    transient run counters to the persistent metadata schema.

    :param admission:
        Calculated native admission summary.
    :param phase:
        Human-readable phase name for the log record.
    :return:
        ``None``.
    """
    btc = admission.family_counts[DenominationFamily.btc.value]
    eth = admission.family_counts[DenominationFamily.eth.value]
    logger.info(
        "Crypto native admission (%s): BTC %d/%d vaults, ETH %d/%d vaults; %d vaults and %d rows excluded",
        phase,
        btc["qualifying"],
        btc["vaults"],
        eth["qualifying"],
        eth["vaults"],
        len(admission.skipped_ids),
        admission.skipped_row_count,
    )


def build_crypto_vault_prices(  # noqa: PLR0914 - coordinator keeps timed source and publication stages explicit
    *,
    vault_db_path: Path,
    uncleaned_path: Path,
    cleaned_path: Path,
    cleaned_stablecoin_path: Path,
    cleaned_stablecoin_daily_path: Path | None = None,
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
    :param cleaned_stablecoin_daily_path:
        Optional daily sidecar written by the standard cleaner while its
        hourly frame is resident.  If absent, the hourly source is read and
        materialised as before.
    :param settlement_db_path:
        Optional vault-settlement DuckDB database.
    :return:
        ``None``. Raises if price cleaning cannot safely complete.

    Performance history
    -------------------

    Baseline (2026-09-22 production log): the crypto phase reread and
    materialised 10,116,623 hourly stablecoin rows and took about 1m30s.  With
    the in-memory daily sidecar, the production-shaped bundle builder took
    23.36 seconds for 2,409,598 output rows; the primary cleaner's sidecar
    write took 5.91 seconds, for 29.27 seconds of integrated extra work. The
    production inputs were not retained as an immutable before/after pair, so
    no like-for-like speed-up is claimed. Peak RSS was 7.1 GiB for the builder
    process; the old phase did not record RSS. The sidecar is used only when
    its modification time is at least as new as the hourly source. This is a
    best-effort stale-file guard for restored artefacts, not a cryptographic
    generation identifier; the hourly file remains the fallback authority.
    """
    total_started_at = time.perf_counter()
    vault_db = VaultDatabase.read(vault_db_path)
    if not cleaned_stablecoin_path.is_file():
        raise FileNotFoundError(cleaned_stablecoin_path)
    sidecar_is_fresh = False
    if cleaned_stablecoin_daily_path is not None and cleaned_stablecoin_daily_path.is_file():
        # A sidecar is a derivative of the hourly public file.  When an
        # operator reruns post-processing with ``skip_cleaning`` or restores
        # only one artefact, an older sidecar must not silently become the
        # crypto source.  The cleaner replaces the hourly file first and the
        # sidecar second, so nanosecond mtimes provide a cheap generation
        # ordering check without reading 10 million rows just to compare
        # metadata. Copied or restored files can retain unrelated modification
        # times, so this remains a best-effort guard rather than proof that the
        # two files came from the same cleaning invocation.
        sidecar_is_fresh = cleaned_stablecoin_daily_path.stat().st_mtime_ns >= cleaned_stablecoin_path.stat().st_mtime_ns
    use_daily_sidecar = cleaned_stablecoin_daily_path is not None and sidecar_is_fresh
    stage_started_at = time.perf_counter()
    if use_daily_sidecar:
        logger.info("Loading daily stablecoin sidecar %s", cleaned_stablecoin_daily_path)
        stable_prices = pd.read_parquet(cleaned_stablecoin_daily_path, dtype_backend="pyarrow")
        # Reapply current metadata membership because the sidecar can retain
        # historical IDs whose denomination was corrected after it was built.
        stable_prices = filter_vaults_by_denomination_families(
            vault_db.rows,
            stable_prices,
            {DenominationFamily.stablecoin},
            logger=logger.info,
        )
        logger.info("Crypto stablecoin sidecar load: %d rows in %.2f seconds", len(stable_prices), time.perf_counter() - stage_started_at)
    else:
        if cleaned_stablecoin_daily_path is not None and cleaned_stablecoin_daily_path.is_file() and not sidecar_is_fresh:
            logger.warning("Ignoring stale daily stablecoin sidecar %s; hourly source is newer", cleaned_stablecoin_daily_path)
        logger.info("Loading existing stablecoin prices %s", cleaned_stablecoin_path)
        stable_prices = pd.read_parquet(cleaned_stablecoin_path, dtype_backend="pyarrow")
        # Reapply current metadata membership because the public cleaned file can
        # retain historical IDs whose denomination was corrected after it was built.
        stable_prices = filter_vaults_by_denomination_families(
            vault_db.rows,
            stable_prices,
            {DenominationFamily.stablecoin},
            logger=logger.info,
        )
        stable_prices = materialise_daily_crypto_prices(stable_prices)
        logger.info("Crypto stablecoin daily materialisation: %d rows in %.2f seconds", len(stable_prices), time.perf_counter() - stage_started_at)
    eth_btc_families = frozenset({DenominationFamily.eth, DenominationFamily.btc})
    raw_vault_specs = {spec for spec, row in vault_db.rows.items() if classify_denomination(row.get("Denomination")) in eth_btc_families}
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    price_frames = [stable_prices]
    if raw_vault_specs:
        stage_started_at = time.perf_counter()
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
            eth_btc_prices = pd.read_parquet(eth_btc_path, dtype_backend="pyarrow")
            admission = calculate_crypto_native_admission(vault_db, eth_btc_prices)
            _log_crypto_native_admission(admission, phase="cleaning")
            qualifying_ids = admission.qualifying_ids
            eth_btc_prices = eth_btc_prices.loc[eth_btc_prices["id"].astype(str).isin(qualifying_ids)]
            price_frames.append(eth_btc_prices)
            logger.info("Crypto ETH/BTC daily cleaning: %d rows in %.2f seconds", len(price_frames[-1]), time.perf_counter() - stage_started_at)
    else:
        logger.info("No ETH/BTC vaults in the metadata database")
    stage_started_at = time.perf_counter()
    combined = pd.concat(price_frames).sort_values(["id", "timestamp"], kind="stable")
    logger.info("Crypto bundle combination and sort: %d rows in %.2f seconds", len(combined), time.perf_counter() - stage_started_at)
    temporary_fd, temporary_path_text = tempfile.mkstemp(suffix=".parquet", dir=cleaned_path.parent)
    os.close(temporary_fd)
    temporary_path = Path(temporary_path_text)
    stage_started_at = time.perf_counter()
    try:
        combined.to_parquet(temporary_path, compression="zstd")
        verify_parquet_file(temporary_path, expected_rows=len(combined), required_columns=["id", "share_price", "timestamp", "returns_1h"])
        os.replace(temporary_path, cleaned_path)
        logger.info("Crypto bundle write complete: %d rows in %.2f seconds", len(combined), time.perf_counter() - stage_started_at)
    finally:
        temporary_path.unlink(missing_ok=True)
    logger.info("Crypto bundle processing complete: %d rows in %.2f seconds total", len(combined), time.perf_counter() - total_started_at)


def _load_sticky_state(path: Path) -> dict[str, Any]:
    """Load the isolated sticky state without resetting corrupt production data.

    Missing state starts clean, version-one state is migrated explicitly, and
    malformed or unknown versions fail closed to preserve operator evidence.

    :param path:
        Sticky-state JSON file.
    :return:
        Valid state document or an empty initial document.
    """
    if not path.exists():
        return {"schema_version": CRYPTO_VAULTS_SCHEMA_VERSION, "vaults": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("vaults"), dict):
        raise ValueError(f"Invalid crypto vault sticky state: {path}")
    schema_version = state.get("schema_version")
    if schema_version == CRYPTO_VAULTS_SCHEMA_VERSION:
        return state
    if schema_version == 1:
        logger.info("Migrating crypto vault sticky state from schema version 1: %s", path)
        return {
            "schema_version": CRYPTO_VAULTS_SCHEMA_VERSION,
            "vaults": dict(state["vaults"]),
        }
    raise ValueError(f"Invalid crypto vault sticky state schema version in {path}: {schema_version!r}")


def _save_json_atomic(payload: dict[str, Any], path: Path) -> None:
    """Validate and atomically write one JSON document.

    Validation rejects non-finite values before the temporary file replaces
    an existing production artefact.

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


def build_crypto_vault_record(record: dict[str, Any], vault_row: VaultRow, threshold_usd: Decimal) -> tuple[dict[str, Any], Decimal]:
    """Convert one common metric record to the crypto bundle schema.

    The transformation preserves the observed denomination while replacing
    mixed-unit rankings and choosing the applicable stablecoin or native
    qualification policy.

    :param record:
        Serialised common metric record.
    :param vault_row:
        Source vault database row.
    :param threshold_usd:
        Fixed USD qualification guideline for stablecoin records. ETH/BTC
        records use ``CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS`` instead.
    :return:
        Native-unit crypto record and its resolved qualification threshold.
    """
    symbol = vault_row["Denomination"]
    family = classify_denomination(symbol)
    assert family is not DenominationFamily.unsupported
    wrapper_kind = get_denomination_wrapper_kind(symbol)
    assert family is DenominationFamily.stablecoin or wrapper_kind is not None
    token_data = vault_row.get("_denomination_token") or {}
    if family in CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS:
        threshold = CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS[family]
    else:
        threshold = convert_usd_threshold_to_denomination(threshold_usd, symbol)
    result = dict(record)
    # Reuse the established JSON fields for the observed token and asset unit;
    # do not add crypto-only symbol, decimals or unit aliases.
    result["denomination"] = symbol
    result.setdefault("denomination_token_address", token_data.get("address"))
    result.setdefault("denomination_decimals", token_data.get("decimals"))
    mapped_underlying = "USD" if family is DenominationFamily.stablecoin else family.value.upper()
    result["denomination_family"] = family.value
    # ``canonical_underlying`` is the wrapper mapping, e.g. ``WBTC`` -> ``BTC``.
    result["canonical_underlying"] = mapped_underlying
    # ``stablecoinish`` is the existing source-history flag: true records reuse
    # standard stablecoin history, while false ETH/BTC records use crypto history.
    result["stablecoinish"] = family is DenominationFamily.stablecoin
    result["wrapper_kind"] = "stablecoin" if family is DenominationFamily.stablecoin else wrapper_kind
    if family is DenominationFamily.stablecoin:
        # A mixed metrics DataFrame represents absent ETH/BTC values as NaN.
        # Do not expose that implementation detail as a nullable USD metric.
        result.pop("periodic_metrics_usd", None)
    result["current_total_assets"] = result.pop("current_nav", None)
    result["peak_total_assets"] = result.pop("peak_nav", None)
    result["qualification_threshold"] = float(threshold)
    # Rankings produced by the common metrics calculator use USD TVL gates and
    # a mixed comparison set. They are not meaningful for native ETH/BTC units,
    # so retain the shared period schema while explicitly leaving ranks unset.
    clear_period_rankings(result)
    result.pop("denomination_token_rate", None)
    return result, threshold


def _validate_crypto_price_rows(vault_db: VaultDatabase, prices_df: pd.DataFrame) -> None:
    """Ensure crypto price rows have matching supported vault metadata.

    Validation happens before calculations so stale IDs or newly unsupported
    denominations cannot produce a partial private bundle.

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


def build_crypto_usd_conversion_context(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    exchange_rate_parquet_path: Path,
) -> tuple[CryptoUSDConversionContext, dict[str, Any]]:
    """Build effective USD rate curves and provenance for crypto metrics.

    Currency API values are stored as quote units per USD. The provider label
    for day ``D`` represents the value fetched around 00:00 UTC, so each raw
    USD→ETH/BTC value becomes an effective USD price for ``D - 1``. Bounded
    forward fill only bridges short provider gaps; metric calculation later
    isolates the newest remaining contiguous segment.

    :param vault_db:
        Common vault metadata used to map vault IDs to denomination families.
    :param prices_df:
        Crypto cleaned prices with a naive UTC DatetimeIndex.
    :param exchange_rate_parquet_path:
        The verified cleaned exchange-rate snapshot used by this export.
    :return:
        Shared calculation context and JSON-serialisable provenance.
    :raises FileNotFoundError:
        If the shared exchange-rate snapshot is absent.
    :raises ValueError:
        If its schema does not support a reliable USD conversion.
    """
    if not exchange_rate_parquet_path.is_file():
        raise FileNotFoundError(exchange_rate_parquet_path)
    expected_columns = {"date", "base_currency", "quote_currency", "rate", "source"}
    exchange_rates = pd.read_parquet(exchange_rate_parquet_path)
    missing_columns = expected_columns - set(exchange_rates.columns)
    if missing_columns:
        raise ValueError(f"Exchange-rate Parquet is missing columns: {sorted(missing_columns)!r}")

    vault_families = {spec.as_string_id(): family.value for spec, row in vault_db.rows.items() if (family := classify_denomination(row.get("Denomination"))) in {DenominationFamily.eth, DenominationFamily.btc}}
    rate_rows = exchange_rates.loc[(exchange_rates["base_currency"] == "usd") & (exchange_rates["source"] == SOURCE_NAME) & exchange_rates["quote_currency"].isin(("eth", "btc"))].copy()
    if rate_rows.empty:
        message = "Exchange-rate Parquet has no fawazahmed0 USD→ETH/BTC rates"
        raise ValueError(message)
    rate_rows["date"] = pd.to_datetime(rate_rows["date"], errors="raise").dt.normalize()
    rate_rows["rate"] = pd.to_numeric(rate_rows["rate"], errors="coerce")

    price_end = prices_df.index.max().normalize()
    family_builds = {family: _build_effective_usd_rate_series(family, rate_rows, price_end) for family in (DenominationFamily.eth.value, DenominationFamily.btc.value)}
    rates_by_family = {family: build.rates for family, build in family_builds.items() if build.rates is not None}
    errors_by_family = {family: build.error_reason for family, build in family_builds.items() if build.error_reason is not None}
    coverage = {family: build.coverage for family, build in family_builds.items() if build.coverage is not None}

    selected_rates = rate_rows.sort_values(["quote_currency", "date"], kind="stable").to_csv(index=False).encode("utf-8")
    cleaning_policy = repr(KNOWN_BAD_RATES).encode("utf-8")
    provenance = {
        "provider": SOURCE_NAME,
        "rate_parquet_filename": exchange_rate_parquet_path.name,
        "rate_parquet_sha256": hashlib.sha256(exchange_rate_parquet_path.read_bytes()).hexdigest(),
        "selected_rate_rows_sha256": hashlib.sha256(selected_rates).hexdigest(),
        "cleaning_policy_sha256": hashlib.sha256(cleaning_policy).hexdigest(),
        "stored_rate_direction": "quote units per USD",
        "applied_rate_direction": "USD per ETH/BTC",
        "effective_date_policy": "provider date minus one UTC day",
        "forward_fill_limit_days": USD_RATE_FORWARD_FILL_DAYS,
        "coverage": coverage,
        "errors": errors_by_family,
    }
    context = CryptoUSDConversionContext(
        rates_by_family=MappingProxyType(rates_by_family),
        errors_by_family=MappingProxyType(errors_by_family),
        vault_families=MappingProxyType(vault_families),
    )
    return context, provenance


def _build_effective_usd_rate_series(
    family: str,
    rate_rows: pd.DataFrame,
    price_end: pd.Timestamp,
) -> USDExchangeRateSeriesBuild:
    """Validate and regularise one ETH/BTC provider series.

    Provider observations are inverted to USD per native asset, shifted to
    their effective vault date and forward-filled only across bounded gaps.

    :param family:
        Canonical ``eth`` or ``btc`` denomination family.
    :param rate_rows:
        Cleaned USD-base provider rows for both supported families.
    :param price_end:
        Latest UTC date in the crypto price Parquet.
    :return:
        Validated rate-series build result.
    """
    family_rows = rate_rows.loc[rate_rows["quote_currency"] == family].sort_values("date", kind="stable")
    if family_rows.empty:
        return USDExchangeRateSeriesBuild(None, None, USD_RATE_ERROR_MISSING_SERIES)
    raw_rates = family_rows["rate"]
    minimum, maximum = USD_RATE_BOUNDS[family]
    valid_rates = raw_rates.notna() & (raw_rates > 0)
    usd_per_native = 1.0 / raw_rates.loc[valid_rates]
    valid_rates.loc[valid_rates] &= usd_per_native.between(minimum, maximum)
    rejected_rows = int((~valid_rates).sum())
    family_rows = family_rows.loc[valid_rates].copy()
    usd_per_native = usd_per_native.loc[usd_per_native.between(minimum, maximum)]
    if family_rows.empty:
        return USDExchangeRateSeriesBuild(None, None, USD_RATE_ERROR_INVALID_SERIES)

    effective_dates = family_rows["date"] - pd.Timedelta(days=1)
    if effective_dates.duplicated().any():
        return USDExchangeRateSeriesBuild(None, None, USD_RATE_ERROR_INVALID_SERIES)
    raw_effective_rates = pd.Series(usd_per_native.to_numpy(), index=effective_dates, dtype="float64").sort_index()
    if price_end < raw_effective_rates.index.min():
        coverage = {
            "provider_observation_start": family_rows["date"].min().date().isoformat(),
            "provider_observation_end": family_rows["date"].max().date().isoformat(),
            "effective_observation_start": None,
            "effective_observation_end": None,
            "provider_rows": len(family_rows),
            "rejected_provider_rows": rejected_rows,
            "covered_effective_days": 0,
            "usd_per_underlying_min": float(usd_per_native.min()),
            "usd_per_underlying_max": float(usd_per_native.max()),
        }
        return USDExchangeRateSeriesBuild(None, coverage, USD_RATE_ERROR_INSUFFICIENT_COVERAGE)
    effective_index = pd.date_range(raw_effective_rates.index.min(), price_end, freq="D")
    effective_rates = raw_effective_rates.reindex(effective_index).ffill(limit=USD_RATE_FORWARD_FILL_DAYS)
    coverage = {
        "provider_observation_start": family_rows["date"].min().date().isoformat(),
        "provider_observation_end": family_rows["date"].max().date().isoformat(),
        "effective_observation_start": effective_rates.index.min().date().isoformat(),
        "effective_observation_end": effective_rates.index.max().date().isoformat(),
        "provider_rows": len(family_rows),
        "rejected_provider_rows": rejected_rows,
        "covered_effective_days": int(effective_rates.notna().sum()),
        "usd_per_underlying_min": float(usd_per_native.min()),
        "usd_per_underlying_max": float(usd_per_native.max()),
    }
    return USDExchangeRateSeriesBuild(effective_rates, coverage, None)


def _build_native_crypto_metrics(
    prices_df: pd.DataFrame,
    vault_db: VaultDatabase,
    stablecoin_rate_feeder: StablecoinRateFeeder,
    crypto_usd_conversion_context: CryptoUSDConversionContext | None,
) -> pd.DataFrame:
    """Calculate native ETH/BTC records without whole-frame regularisation.

    Native records do not support the stablecoin ERC-4626 flow estimator, and
    ``calculate_vault_record`` already regularises each vault's share-price
    series for its period metrics. Calling it directly avoids resampling all
    49 source columns and avoids the mixed-unit ranking pass. The projected
    frame retains every column read by the common record builder, including
    protocol-specific optional fields.

    :param prices_df:
        Cleaned, threshold-filtered native price rows with a UTC datetime index.
    :param vault_db:
        Vault metadata for the native rows.
    :param stablecoin_rate_feeder:
        One shared feeder used to avoid duplicate metadata scans.
    :param crypto_usd_conversion_context:
        Optional exchange-rate context for additive USD period metrics.
    :return:
        One raw metric Series per native vault, or an empty DataFrame.
    """
    if prices_df.empty:
        return pd.DataFrame()
    if not isinstance(prices_df.index, pd.DatetimeIndex):
        message = "Native crypto metrics require a DatetimeIndex"
        raise TypeError(message)
    required_columns = {"id", "chain", "event_count", "block_number", "share_price", "total_assets"}
    missing_columns = required_columns - set(prices_df.columns)
    if missing_columns:
        raise ValueError(f"Native crypto metrics require columns: {sorted(missing_columns)!r}")

    projected_columns = [column for column in NATIVE_METRIC_COLUMNS if column in prices_df.columns]
    projected_prices = prices_df.loc[:, projected_columns].sort_index(kind="stable")
    price_ids = set(projected_prices["id"].astype(str))
    vault_rows = {spec: row for spec, row in vault_db.rows.items() if spec.as_string_id() in price_ids}
    slugify_vaults(vaults=vault_rows)
    month_ago = projected_prices.index.max() - pd.Timedelta(days=30)
    three_months_ago = projected_prices.index.max() - pd.Timedelta(days=90)
    grouped_vaults = projected_prices.groupby("id", group_keys=False, sort=False, observed=True)
    generated_at = pd.Timestamp(native_datetime_utc_now())
    records: list[pd.Series] = []
    for vault_id, group in tqdm(grouped_vaults, desc="Calculating native crypto metrics", total=grouped_vaults.ngroups):
        try:
            # The old full-frame route keeps one midnight row per observed day,
            # then forward-fills calendar gaps. Reproduce that semantic on the
            # projected metric columns only; this avoids resampling the full
            # object-heavy source frame while preserving lifetime sample counts.
            group = group.sort_index(kind="stable")
            group = group.resample("D").last().ffill()
            group["id"] = str(vault_id)
            record = calculate_vault_record(
                group,
                vault_rows,
                month_ago,
                three_months_ago,
                vault_id=str(vault_id),
                stablecoin_rate_feeder=stablecoin_rate_feeder,
                crypto_usd_conversion_context=crypto_usd_conversion_context,
            )
        except (ArithmeticError, AssertionError, KeyError, TypeError, ValueError):
            logger.exception("Skipping invalid native crypto metrics record for %s", vault_id)
            continue
        record["generated_at"] = generated_at
        records.append(record)

    return pd.DataFrame(records)


def build_crypto_vault_metadata(  # noqa: PLR0914 - this coordinator keeps the isolated export phases explicit
    *,
    vault_db_path: Path,
    cleaned_price_path: Path,
    metadata_path: Path,
    sticky_state_path: Path,
    exchange_rate_parquet_path: Path | None = None,
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
    :param exchange_rate_parquet_path:
        Optional verified snapshot used to add USD period metrics to ETH/BTC
        vaults. Stablecoin records deliberately do not get a duplicate USD
        metric view.
    :param threshold_usd:
        Optional fixed USD guideline for stablecoin sticky admission; defaults
        to environment/config value. ETH/BTC admission is native-unit based.
    :return:
        JSON-serialisable metadata document.
    """
    if threshold_usd is None:
        threshold_usd = Decimal(os.environ.get("CRYPTO_VAULTS_MIN_TVL_USD", "5000"))
    vault_db = VaultDatabase.read(vault_db_path)
    read_started_at = time.perf_counter()
    prices_df = pd.read_parquet(cleaned_price_path)
    if not isinstance(prices_df.index, pd.DatetimeIndex):
        prices_df["timestamp"] = pd.to_datetime(prices_df["timestamp"])
        prices_df.set_index("timestamp", inplace=True)
    prices_df["id"] = prices_df["id"].astype(str)
    logger.info("Crypto metric source read: %d rows, %d columns in %.2fs", len(prices_df), len(prices_df.columns), time.perf_counter() - read_started_at)
    _validate_crypto_price_rows(vault_db, prices_df)

    admission_started = time.perf_counter()
    admission = calculate_crypto_native_admission(vault_db, prices_df)
    _log_crypto_native_admission(admission, phase="metadata")
    family_by_id = _family_by_vault_id(vault_db)
    family_series = prices_df["id"].map(family_by_id)
    stable_mask = family_series == DenominationFamily.stablecoin
    native_mask = family_series.isin(tuple(CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS)) & prices_df["id"].isin(admission.qualifying_ids)
    stable_prices_df = prices_df.loc[stable_mask]
    native_prices_df = prices_df.loc[native_mask]
    stable_price_ids = set(stable_prices_df["id"])
    stable_vault_rows = {spec: row for spec, row in vault_db.rows.items() if family_by_id[spec.as_string_id()] is DenominationFamily.stablecoin and spec.as_string_id() in stable_price_ids}
    logger.info(
        "Prepared crypto metric inputs: %d stablecoin rows/%d vaults, %d native rows/%d vaults in %.2fs",
        len(stable_prices_df),
        stable_prices_df["id"].nunique(),
        len(native_prices_df),
        native_prices_df["id"].nunique(),
        time.perf_counter() - admission_started,
    )

    # Freshness gate: recalculate low-TVL stablecoin vaults only every
    # LOW_TVL_METRICS_MAX_AGE. Native ETH/BTC vaults are deliberately not
    # gated: their admission already requires a lifetime peak at or above the
    # native export threshold, so every admitted native vault is
    # export-relevant and must stay fresh. There are two skip classes:
    #
    # 1. Sticky-active vaults (previously exported or retained): a skipped
    #    vault's previous record is read back from the metadata JSON (before
    #    it is overwritten) and re-attached, but only when the whole previous
    #    document and the per-vault family and threshold still match this
    #    process.
    # 2. Never-exported vaults with no sticky entry and a raw peak below the
    #    export threshold: they provably cannot enter the export this run (no
    #    qualification, no sticky retention), so nothing needs to be patched
    #    and the record is simply absent, exactly as when they are computed.
    freshness_started_at = time.perf_counter()
    now = native_datetime_utc_now()
    state = _load_sticky_state(sticky_state_path)
    metrics_state_path = sticky_state_path.parent / CRYPTO_METRICS_STATE_FILENAME
    metrics_state = load_metrics_state(metrics_state_path, now)
    previous_records = load_valid_previous_crypto_records(
        metadata_path,
        schema_version=CRYPTO_VAULTS_SCHEMA_VERSION,
        whitelist_sha256=get_denomination_whitelist_digest(),
    )
    seen_stable_ids = set(stable_prices_df["id"])
    current_tvl_by_id, peak_tvl_by_id = compute_vault_tvl_observations(stable_prices_df)
    stable_family_by_id = dict.fromkeys(seen_stable_ids, DenominationFamily.stablecoin.value)
    stable_export_threshold_by_id = {vault_id: float(convert_usd_threshold_to_denomination(threshold_usd, vault_db.rows[VaultSpec.parse_string(vault_id, separator="-")]["Denomination"])) for vault_id in seen_stable_ids}
    record_patchable_ids = {vault_id for vault_id, record in previous_records.items() if vault_id in seen_stable_ids and record.get("denomination_family") == DenominationFamily.stablecoin.value and record.get("qualification_threshold") == stable_export_threshold_by_id.get(vault_id)}
    no_record_skippable_ids = {vault_id for vault_id in seen_stable_ids if vault_id not in state["vaults"] and (peak_tvl_by_id.get(vault_id) is None or peak_tvl_by_id[vault_id] < stable_export_threshold_by_id[vault_id])}
    due_stable_ids, skipped_stable_ids = partition_due_vault_ids(
        seen_stable_ids,
        metrics_state,
        current_tvl_by_id,
        peak_tvl_by_id,
        stable_family_by_id,
        stable_export_threshold_by_id,
        now,
        patchable_ids=record_patchable_ids | no_record_skippable_ids,
    )
    logger.info("Metrics freshness: %d stablecoin vaults due, %d low-TVL vaults still fresh", len(due_stable_ids), len(skipped_stable_ids))
    stable_prices_df = stable_prices_df.loc[stable_prices_df["id"].isin(due_stable_ids)]
    stable_price_ids = set(stable_prices_df["id"])
    stable_vault_rows = {spec: row for spec, row in stable_vault_rows.items() if spec.as_string_id() in stable_price_ids}
    logger.info("Crypto stablecoin freshness filtering: %d due vaults, %d rows in %.2fs", len(due_stable_ids), len(stable_prices_df), time.perf_counter() - freshness_started_at)

    crypto_usd_conversion_context = None
    usd_metrics_provenance = None
    if exchange_rate_parquet_path is not None and not native_prices_df.empty:
        crypto_usd_conversion_context, usd_metrics_provenance = build_crypto_usd_conversion_context(
            vault_db,
            prices_df,
            exchange_rate_parquet_path,
        )

    # Free the full crypto price frame and the admission masks before the
    # memory-peak metrics phases; nothing below needs them.
    del prices_df, family_series, stable_mask, native_mask
    free_memory()

    stablecoin_rate_feeder = StablecoinRateFeeder()
    stable_metrics_started = time.perf_counter()
    if stable_prices_df.empty:
        stable_metrics_df = pd.DataFrame()
        logger.info("Crypto stablecoin daily preparation skipped: no due rows")
        del stable_prices_df
    else:
        prep_started_at = time.perf_counter()
        metric_columns = [column for column in stable_prices_df.columns if column not in UNUSED_METRIC_PRICE_COLUMNS]
        metric_prices_df = stable_prices_df.loc[:, metric_columns]
        daily_stable_prices_df = calculate_sparse_daily_returns_for_all_vaults(metric_prices_df)
        logger.info(
            "Crypto stablecoin daily preparation: %d source rows, %d daily rows, %d of %d columns in %.2fs, RSS %.2f GiB",
            len(stable_prices_df),
            len(daily_stable_prices_df),
            len(metric_columns),
            len(stable_prices_df.columns),
            time.perf_counter() - prep_started_at,
            psutil.Process().memory_info().rss / (1024**3),
        )
        del metric_prices_df, stable_prices_df
        metric_started_at = time.perf_counter()
        stable_metrics_df = calculate_lifetime_metrics(
            daily_stable_prices_df,
            stable_vault_rows,
            stablecoin_rate_feeder=stablecoin_rate_feeder,
        )
        logger.info("Crypto stablecoin lifetime metrics: %d vaults in %.2fs", len(stable_metrics_df), time.perf_counter() - metric_started_at)
        # Free the daily stablecoin frame before the native metrics phase.
        del daily_stable_prices_df
        free_memory()
    logger.info("Calculated stablecoin crypto metrics in %.2fs", time.perf_counter() - stable_metrics_started)

    native_metrics_started = time.perf_counter()
    native_metrics_df = _build_native_crypto_metrics(
        native_prices_df,
        vault_db,
        stablecoin_rate_feeder,
        crypto_usd_conversion_context,
    )
    logger.info("Calculated native crypto metrics in %.2fs", time.perf_counter() - native_metrics_started)

    # Free the remaining price frames before the serialisation and record
    # loop; nothing below needs them.
    del native_prices_df
    free_memory()

    serialisation_started = time.perf_counter()
    selected_records: list[dict[str, Any]] = []
    current_ids: set[str] = set()

    for metric_df in (stable_metrics_df, native_metrics_df):
        for _, metric_row in metric_df.iterrows():
            vault_id = str(metric_row["id"])
            vault_row = vault_db.rows[VaultSpec.parse_string(vault_id, separator="-")]
            record, resolved_threshold = build_crypto_vault_record(export_lifetime_row(metric_row), vault_row, threshold_usd)
            current_ids.add(vault_id)
            family = record["denomination_family"]
            peak_assets = record.get("peak_total_assets")
            qualifies = peak_assets is not None and float(peak_assets) >= float(resolved_threshold)
            prior = state["vaults"].get(vault_id)
            if family in {DenominationFamily.eth.value, DenominationFamily.btc.value}:
                include = vault_id in admission.qualifying_ids
                qualifies = include
            else:
                include = qualifies or (prior and prior.get("denomination_family") == family)
            if include:
                record["sticky_export"] = not qualifies
                selected_records.append(record)
                state["vaults"][vault_id] = {
                    "denomination_family": family,
                    "denomination_symbol": record["denomination"],
                    "threshold": float(resolved_threshold),
                    "updated_at": native_datetime_utc_now().isoformat(),
                }

    # Re-attach validated previous records for skipped low-TVL stablecoin
    # vaults. The per-vault generated_at inside each record is the "last
    # metrics updated" signal, and rankings were already cleared when the
    # record was built. Skipped vaults without a patchable record (never
    # exported, peak below the export threshold) contribute nothing, exactly
    # as when they are computed.
    for vault_id in sorted(skipped_stable_ids & record_patchable_ids):
        selected_records.append(previous_records[vault_id])
        current_ids.add(vault_id)

    native_family_names = {DenominationFamily.eth.value, DenominationFamily.btc.value}
    state["vaults"] = {key: value for key, value in state["vaults"].items() if key in current_ids and (value.get("denomination_family") not in native_family_names or key in admission.qualifying_ids)}
    selected_records.sort(key=lambda record: str(record.get("id", "")))
    metadata = {
        "bundle": CRYPTO_VAULTS_BUNDLE_NAME,
        "schema_version": CRYPTO_VAULTS_SCHEMA_VERSION,
        "generated_at": native_datetime_utc_now().isoformat(),
        "metadata": build_export_metadata(),
        "denomination_whitelist_sha256": get_denomination_whitelist_digest(),
        "denomination_families": list(CRYPTO_DENOMINATION_FAMILY_NAMES),
        "threshold_usd_guideline": float(threshold_usd),
        "fixed_usd_rates": {"ETH": float(ETH_USD_GUIDELINE_RATE), "BTC": float(BTC_USD_GUIDELINE_RATE)},
        "native_min_peak_total_assets": {family.value: float(threshold) for family, threshold in CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS.items()},
        "vaults": selected_records,
    }
    if usd_metrics_provenance is not None:
        metadata["usd_metrics"] = usd_metrics_provenance
    _save_json_atomic(metadata, metadata_path)
    _save_json_atomic(state, sticky_state_path)

    # The freshness state is committed after the output JSON and sticky state:
    # a crash between the writes leaves state stale so vaults recompute next
    # run, never the reverse (state fresh but no published record).
    computed_stable_ids = set(stable_metrics_df["id"].astype(str)) if len(stable_metrics_df) else set()
    # Vaults skipped without a patchable record provably cannot enter the
    # export (no sticky entry, peak below the export threshold), so their
    # freshness timestamp still advances: not advancing it would make the
    # whole never-exported cohort due again on the next run.
    refresh_metrics_state(
        metrics_state,
        seen_stable_ids,
        computed_stable_ids | (skipped_stable_ids - record_patchable_ids),
        current_tvl_by_id,
        peak_tvl_by_id,
        stable_family_by_id,
        now,
    )
    save_metrics_state(metrics_state, metrics_state_path)
    logger.info(
        "Built and saved %d crypto vault records in %.2fs",
        len(selected_records),
        time.perf_counter() - serialisation_started,
    )
    return metadata
