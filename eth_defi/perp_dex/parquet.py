"""Shared materialised perpetual-vault metrics in the price Parquet pipeline."""

import datetime
from collections.abc import Iterable
from decimal import Decimal

import pandas as pd
import pyarrow as pa
from packaging.version import Version

from eth_defi.perp_dex.metrics import PerpParquetDataStatus

PERP_METRICS_MAX_AGE = datetime.timedelta(hours=6)

#: Native chains whose price rows represent perpetual DEX vault accounts.
#: This is used only for the generic ``not_collected`` default; adapters own
#: real source observations and availability states.
PERP_DEX_NATIVE_CHAIN_IDS = frozenset({325, 9994, 9995, 9997, 9998, 9999})

PERP_VAULT_PARQUET_FIELDS = (
    pa.field("perp_long_notional", pa.float64()),
    pa.field("perp_short_notional", pa.float64()),
    pa.field("perp_open_position_count", pa.int64()),
    pa.field("perp_largest_position_notional", pa.float64()),
    pa.field("perp_quote_asset", pa.string()),
    pa.field("perp_position_data_status", pa.string()),
    pa.field("perp_metrics_observed_at", pa.timestamp("ms")),
)

PERP_METRIC_NUMERIC_COLUMNS = (
    "perp_long_notional",
    "perp_short_notional",
    "perp_open_position_count",
    "perp_largest_position_notional",
)


def ensure_perp_metric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add typed-null-compatible common perp columns to a price DataFrame.

    :param frame:
        Raw or cleaned price rows.
    :return:
        Same frame with all seven common columns present.
    """
    defaults: dict[str, object] = {
        "perp_long_notional": float("nan"),
        "perp_short_notional": float("nan"),
        "perp_open_position_count": pd.NA,
        "perp_largest_position_notional": float("nan"),
        "perp_quote_asset": "",
        "perp_position_data_status": "",
        "perp_metrics_observed_at": pd.NaT,
    }
    for name, default in defaults.items():
        if name not in frame.columns:
            frame[name] = default
    return frame


def normalise_perp_metric_parquet_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise all materialised perp fields to their Arrow contract.

    Timestamps must be naive and exactly representable in milliseconds; this
    rejects a silent conversion that could alter as-of join ordering.

    :param frame:
        DataFrame containing all common perp columns.
    :return:
        DataFrame with nullable integer count and millisecond timestamps.
    """
    frame = ensure_perp_metric_columns(frame)
    timestamp = pd.to_datetime(frame["perp_metrics_observed_at"])
    if getattr(timestamp.dt, "tz", None) is not None:
        msg = "perp_metrics_observed_at must be timezone-naive UTC"
        raise ValueError(msg)
    non_null = timestamp.dropna()
    if not non_null.empty:
        nanos = [value.as_unit("ns").value for value in non_null]
        if any(value % 1_000_000 != 0 for value in nanos):
            msg = "perp_metrics_observed_at loses sub-millisecond precision"
            raise ValueError(msg)
    frame["perp_metrics_observed_at"] = timestamp.astype("datetime64[ms]")
    frame["perp_open_position_count"] = pd.array(frame["perp_open_position_count"], dtype="Int64")
    for column in ("perp_long_notional", "perp_short_notional", "perp_largest_position_notional"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    statuses = frame["perp_position_data_status"].replace("", pd.NA).dropna()
    invalid = set(statuses) - {status.value for status in PerpParquetDataStatus}
    if invalid:
        raise ValueError(f"Unknown perp Parquet statuses: {sorted(invalid)}")
    return frame


def _semantic_bundle_signature(account: pd.Series, positions: pd.DataFrame) -> tuple:
    """Build an immutable correction comparison signature excluding write IDs."""
    excluded = {"snapshot_id", "written_at"}
    account_values = tuple((key, str(value)) for key, value in sorted(account.items()) if key not in excluded)
    position_values = tuple(tuple((key, str(value)) for key, value in sorted(row.items()) if key != "snapshot_id") for _, row in positions.sort_values("source_market_id").iterrows())
    return account_values, position_values


def select_perp_observation_corrections(accounts: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Select one immutable bundle for every identity/effective-time correction set.

    The latest write wins; a same-write tie uses PEP 440 collector version.
    Equal-rank conflicting bundles fail hard instead of combining stale and
    corrected position rows.

    :param accounts:
        Account observation rows read from common DuckDB storage.
    :param positions:
        Position observation rows read from common DuckDB storage.
    :return:
        Selected account rows only.
    """
    if accounts.empty:
        return accounts.copy()
    required = ["protocol_slug", "deployment_slug", "dataset_chain_id", "dataset_address", "position_effective_at", "written_at", "collector_version", "snapshot_id"]
    missing = set(required) - set(accounts.columns)
    if missing:
        raise ValueError(f"Account observations missing correction columns: {sorted(missing)}")

    selected_indices: list[int] = []
    group_columns = ["protocol_slug", "deployment_slug", "dataset_chain_id", "dataset_address", "position_effective_at"]
    for _, group in accounts.groupby(group_columns, dropna=False, sort=False):
        latest_written_at = group["written_at"].max()
        latest = group[group["written_at"] == latest_written_at].copy()
        parsed_versions = latest["collector_version"].map(Version)
        winning_version = max(parsed_versions)
        winning = latest[parsed_versions == winning_version]
        if len(winning) == 1:
            selected_indices.append(int(winning.index[0]))
            continue
        signatures = {
            _semantic_bundle_signature(
                row,
                positions[positions["snapshot_id"] == row["snapshot_id"]],
            )
            for _, row in winning.iterrows()
        }
        if len(signatures) != 1:
            msg = "Ambiguous equal-rank perp observation correction"
            raise ValueError(msg)
        selected_indices.append(int(winning.sort_values("snapshot_id").index[0]))
    return accounts.loc[selected_indices].copy()


def derive_perp_vault_metric_snapshots(accounts: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Derive one materialised exposure row per selected account observation.

    The account table drives the calculation, so a complete available account
    with zero position rows produces zero values rather than disappearing.

    :param accounts:
        Append-only common account observation rows.
    :param positions:
        Append-only common non-zero position rows.
    :return:
        DataFrame ready for the generic price as-of join.
    """
    accounts = select_perp_observation_corrections(accounts, positions)
    rows: list[dict[str, object]] = []
    for _, account in accounts.iterrows():
        account_positions = positions[positions["snapshot_id"] == account["snapshot_id"]]
        status = str(account["position_data_status"])
        result: dict[str, object] = {
            "chain": int(account["dataset_chain_id"]),
            "address": str(account["dataset_address"]).lower(),
            "position_effective_at": pd.Timestamp(account["position_effective_at"]),
            "perp_quote_asset": account["quote_asset"] or "",
            "perp_position_data_status": status,
            "perp_metrics_observed_at": pd.Timestamp(account["position_effective_at"]),
        }
        if status == PerpParquetDataStatus.available.value and bool(account["position_set_complete"]):
            signed = [Decimal(str(value)) for value in account_positions["signed_notional"]]
            result.update(
                perp_long_notional=float(sum((value for value in signed if value > 0), Decimal(0))),
                perp_short_notional=float(sum((-value for value in signed if value < 0), Decimal(0))),
                perp_open_position_count=len(signed),
                perp_largest_position_notional=float(max((abs(value) for value in signed), default=Decimal(0))),
            )
        else:
            result.update(
                perp_long_notional=float("nan"),
                perp_short_notional=float("nan"),
                perp_open_position_count=pd.NA,
                perp_largest_position_notional=float("nan"),
            )
        rows.append(result)
    return normalise_perp_metric_parquet_dtypes(pd.DataFrame(rows)) if rows else pd.DataFrame(columns=["chain", "address", "position_effective_at", *[field.name for field in PERP_VAULT_PARQUET_FIELDS]])


def attach_perp_metrics_to_price_rows(price_rows: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    """Backward-join snapshots to native price rows without applying freshness.

    Inputs are sorted deterministically for ``merge_asof`` and restored to the
    original price order afterwards.  The finalisation helper is intentionally
    the only owner of stale/default-status policy.

    :param price_rows:
        Native raw price DataFrame containing chain, address and timestamp.
    :param snapshots:
        Metric snapshots from one or more protocol DuckDB files.
    :return:
        Price rows with the seven materialised metrics attached.
    """
    price_rows = ensure_perp_metric_columns(price_rows.copy())
    if snapshots.empty:
        return price_rows
    price_rows["_perp_original_order"] = range(len(price_rows))
    left = price_rows.sort_values(["timestamp", "chain", "address"], kind="stable")
    right = snapshots.sort_values(["position_effective_at", "chain", "address"], kind="stable")
    if right.duplicated(["chain", "address", "position_effective_at"]).any():
        msg = "Duplicate selected perp metric snapshots for price join"
        raise ValueError(msg)
    joined = pd.merge_asof(
        left,
        right,
        left_on="timestamp",
        right_on="position_effective_at",
        by=["chain", "address"],
        direction="backward",
        allow_exact_matches=True,
        suffixes=("", "_joined"),
    )
    for field in PERP_VAULT_PARQUET_FIELDS:
        joined_name = f"{field.name}_joined"
        if joined_name in joined.columns:
            joined[field.name] = joined[joined_name]
            joined.drop(columns=[joined_name], inplace=True)
    joined.drop(columns=["position_effective_at"], errors="ignore", inplace=True)
    return joined.sort_values("_perp_original_order", kind="stable").drop(columns="_perp_original_order")


def finalise_perp_metric_columns(
    frame: pd.DataFrame,
    registered_perp_vaults: Iterable[tuple[int, str]],
    maximum_age: datetime.timedelta = PERP_METRICS_MAX_AGE,
) -> pd.DataFrame:
    """Apply the single default and freshness policy for perp metric columns.

    Staleness is calculated only from each price row's timestamp minus the
    observed position time.  Static unavailable states never turn stale.

    :param frame:
        Raw or cleaned price rows with common perp columns.
    :param registered_perp_vaults:
        Protocol vault identities known to the native merge.
    :param maximum_age:
        Global acceptable observation age.
    :return:
        Finalised common metric columns.
    """
    frame = ensure_perp_metric_columns(frame)
    registered = {(int(chain), str(address).lower()) for chain, address in registered_perp_vaults}
    empty_status = frame["perp_position_data_status"].isna() | (frame["perp_position_data_status"] == "")
    is_registered = pd.Series([(int(row.chain), str(row.address).lower()) in registered for row in frame[["chain", "address"]].itertuples(index=False)], index=frame.index)
    frame.loc[empty_status & is_registered, "perp_position_data_status"] = PerpParquetDataStatus.not_collected.value
    frame.loc[empty_status & ~is_registered, "perp_position_data_status"] = PerpParquetDataStatus.not_applicable.value

    timestamps = pd.to_datetime(frame["timestamp"] if "timestamp" in frame.columns else frame.index)
    observed = pd.to_datetime(frame["perp_metrics_observed_at"])
    age = timestamps - observed
    if (age.dropna() < pd.Timedelta(0)).any():
        msg = "Perp metrics observation is later than its price row"
        raise ValueError(msg)
    stale = (frame["perp_position_data_status"] == PerpParquetDataStatus.available.value) & (age > maximum_age)
    frame.loc[stale, "perp_position_data_status"] = PerpParquetDataStatus.stale.value
    for column in PERP_METRIC_NUMERIC_COLUMNS:
        frame.loc[stale, column] = pd.NA
    return normalise_perp_metric_parquet_dtypes(frame)
