"""Shared materialised perpetual-vault metrics in the price Parquet pipeline."""

import datetime
from collections.abc import Iterable
from decimal import Decimal

import pandas as pd
import pyarrow as pa
from packaging.version import Version

from eth_defi.perp_dex.metrics import PerpParquetDataStatus

PERP_METRICS_MAX_AGE = datetime.timedelta(hours=6)

#: Allow a newly collected account observation to attach to the latest native
#: price row when a daily or otherwise delayed price source has not emitted a
#: row at the collection time yet. Lighter's daily feed can remain at the
#: previous UTC midnight until the following day, requiring a 48-hour bound.
#: The original observation timestamp is retained, and only the latest row for
#: an account can receive this alignment.
PERP_METRICS_MAX_FORWARD_ALIGNMENT = datetime.timedelta(days=2)

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
    # One-second resolution is sufficient for account-level perp metrics.
    # This timestamp remains attached to forward-aligned and stale values so
    # consumers can calculate their exact measurement age. Parquet uses its
    # millisecond logical type because it cannot round-trip timestamp[s].
    pa.field("perp_metrics_observed_at", pa.timestamp("ms")),
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

    Observation timestamps are deliberately truncated to whole seconds.
    Account-level exposure does not need sub-second precision, while a stable
    second-resolution contract avoids leaking collector clock precision into
    Parquet schema inference.

    :param frame:
        DataFrame containing all common perp columns.
    :return:
        DataFrame with nullable integer count and second-resolution timestamps.
    """
    frame = ensure_perp_metric_columns(frame)
    timestamp = pd.to_datetime(frame["perp_metrics_observed_at"])
    if getattr(timestamp.dt, "tz", None) is not None:
        msg = "perp_metrics_observed_at must be timezone-naive UTC"
        raise ValueError(msg)
    frame["perp_metrics_observed_at"] = timestamp.dt.floor("s").astype("datetime64[ms]")
    frame["perp_open_position_count"] = pd.array(frame["perp_open_position_count"], dtype="Int64")
    for column in ("perp_long_notional", "perp_short_notional", "perp_largest_position_notional"):
        numeric = pd.to_numeric(frame[column], errors="raise").astype("float64")
        if numeric.abs().eq(float("inf")).any():
            raise ValueError(f"{column} must contain only finite monetary values")
        if numeric.dropna().lt(0).any():
            raise ValueError(f"{column} cannot contain negative values")
        frame[column] = numeric
    if frame["perp_open_position_count"].dropna().lt(0).any():
        msg = "perp_open_position_count cannot contain negative values"
        raise ValueError(msg)
    statuses = frame["perp_position_data_status"].replace("", pd.NA).dropna()
    invalid = set(statuses) - {status.value for status in PerpParquetDataStatus}
    if invalid:
        raise ValueError(f"Unknown perp Parquet statuses: {sorted(invalid)}")
    measured = frame["perp_position_data_status"].isin((PerpParquetDataStatus.available.value, PerpParquetDataStatus.stale.value))
    if frame.loc[measured, "perp_metrics_observed_at"].isna().any():
        msg = "Available and stale perp metrics require perp_metrics_observed_at"
        raise ValueError(msg)
    if frame.loc[measured, "perp_quote_asset"].fillna("").eq("").any():
        msg = "Available and stale perp metrics require perp_quote_asset"
        raise ValueError(msg)
    return frame


def _semantic_bundle_signature(account: pd.Series, positions: pd.DataFrame) -> tuple:
    """Build an immutable correction comparison signature excluding write IDs.

    Account values and source-market-sorted position values are converted to
    stable strings so equal-rank corrections can be compared deterministically.

    :param account:
        One account observation row.
    :param positions:
        Position rows whose ``snapshot_id`` matches the account.
    :return:
        Hashable account and position value tuple.
    """
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
    if accounts.empty:
        return pd.DataFrame(columns=["chain", "address", "position_effective_at", *[field.name for field in PERP_VAULT_PARQUET_FIELDS]])

    position_facts = positions[["snapshot_id", "signed_notional"]].copy()
    if position_facts.empty:
        aggregates = pd.DataFrame(columns=["snapshot_id", "perp_long_notional", "perp_short_notional", "perp_open_position_count", "perp_largest_position_notional"])
    else:
        signed = position_facts["signed_notional"].map(lambda value: Decimal(str(value)))
        position_facts["perp_long_notional"] = signed.map(lambda value: value if value > 0 else Decimal(0))
        position_facts["perp_short_notional"] = signed.map(lambda value: -value if value < 0 else Decimal(0))
        position_facts["perp_largest_position_notional"] = signed.map(abs)
        aggregates = position_facts.groupby("snapshot_id", as_index=False).agg(
            perp_long_notional=("perp_long_notional", "sum"),
            perp_short_notional=("perp_short_notional", "sum"),
            perp_open_position_count=("signed_notional", "size"),
            perp_largest_position_notional=("perp_largest_position_notional", "max"),
        )

    selected = accounts.merge(aggregates, on="snapshot_id", how="left", validate="one_to_one")
    available = (selected["position_data_status"] == PerpParquetDataStatus.available.value) & selected["position_set_complete"].astype(bool)
    monetary_columns = ["perp_long_notional", "perp_short_notional", "perp_largest_position_notional"]
    selected.loc[available, monetary_columns] = selected.loc[available, monetary_columns].fillna(Decimal(0))
    selected.loc[~available, monetary_columns] = float("nan")
    selected.loc[available, "perp_open_position_count"] = selected.loc[available, "perp_open_position_count"].fillna(0)
    selected.loc[~available, "perp_open_position_count"] = pd.NA

    snapshots = pd.DataFrame(
        {
            "chain": selected["dataset_chain_id"].astype("int64"),
            "address": selected["dataset_address"].astype("string").str.lower(),
            "position_effective_at": pd.to_datetime(selected["position_effective_at"]),
            "perp_long_notional": selected["perp_long_notional"],
            "perp_short_notional": selected["perp_short_notional"],
            "perp_open_position_count": selected["perp_open_position_count"],
            "perp_largest_position_notional": selected["perp_largest_position_notional"],
            "perp_quote_asset": selected["quote_asset"].fillna(""),
            "perp_position_data_status": selected["position_data_status"].astype("string"),
            "perp_metrics_observed_at": pd.to_datetime(selected["position_effective_at"]),
        }
    )
    return normalise_perp_metric_parquet_dtypes(snapshots)


def align_latest_perp_metrics_to_price_rows(
    price_rows: pd.DataFrame,
    snapshots: pd.DataFrame,
    maximum_forward_alignment: datetime.timedelta,
) -> pd.DataFrame:
    """Overlay a newer account observation on only the latest native price row.

    Some native price feeds, notably Lighter's daily history, timestamp the
    current price row at UTC midnight even though the account observation is
    collected later that day. For each account this helper overlays only the
    newest eligible snapshot on the latest price row. Older price history
    cannot receive the newer observation, and
    ``perp_metrics_observed_at`` always retains the real measurement time.

    :param price_rows:
        Result of the ordinary backward as-of join, containing one or more
        rows per ``(chain, address)`` and all common metric columns.
    :param snapshots:
        Unique, correction-selected metric snapshots. Each row contains
        ``chain``, ``address``, ``position_effective_at`` and all common metric
        columns.
    :param maximum_forward_alignment:
        Maximum permitted distance from the latest price row to the newer
        observation.
    :return:
        Price-row copy with eligible latest rows overlaid.
    """
    aligned = price_rows.copy()
    if aligned.empty or snapshots.empty:
        return aligned

    if aligned["address"].isna().any() or snapshots["address"].isna().any():
        msg = "Perp metric alignment requires non-null price and snapshot addresses"
        raise ValueError(msg)
    alignment_chain = "_perp_alignment_chain"
    alignment_address = "_perp_alignment_address"
    aligned[alignment_chain] = pd.to_numeric(aligned["chain"], errors="raise").astype("int64")
    aligned[alignment_address] = aligned["address"].astype("string").str.lower()
    normalised_snapshots = snapshots.copy()
    normalised_snapshots[alignment_chain] = pd.to_numeric(normalised_snapshots["chain"], errors="raise").astype("int64")
    normalised_snapshots[alignment_address] = normalised_snapshots["address"].astype("string").str.lower()
    identity_columns = [alignment_chain, alignment_address]
    metric_columns = [field.name for field in PERP_VAULT_PARQUET_FIELDS]
    aligned["_perp_alignment_row"] = range(len(aligned))
    latest_prices = aligned.sort_values("timestamp", kind="stable").drop_duplicates(identity_columns, keep="last")[["_perp_alignment_row", *identity_columns, "timestamp"]].rename(columns={"timestamp": "_perp_latest_price_at"})

    candidates = latest_prices.merge(
        normalised_snapshots,
        on=identity_columns,
        how="inner",
        validate="one_to_many",
    )
    delta = pd.to_datetime(candidates["position_effective_at"]) - pd.to_datetime(candidates["_perp_latest_price_at"])
    candidates = candidates[(delta > pd.Timedelta(0)) & (delta <= maximum_forward_alignment)]
    candidates = candidates[candidates["perp_position_data_status"] != PerpParquetDataStatus.source_error.value]
    if not candidates.empty:
        candidates = candidates.sort_values("position_effective_at", kind="stable").drop_duplicates(identity_columns, keep="last")
        aligned.set_index("_perp_alignment_row", inplace=True)
        updates = candidates.set_index("_perp_alignment_row")
        aligned.loc[updates.index, metric_columns] = updates[metric_columns]
        aligned.reset_index(inplace=True)

    return aligned.drop(columns=["_perp_alignment_row", alignment_chain, alignment_address])


def attach_perp_metrics_to_price_rows(
    price_rows: pd.DataFrame,
    snapshots: pd.DataFrame,
    maximum_forward_alignment: datetime.timedelta = PERP_METRICS_MAX_FORWARD_ALIGNMENT,
) -> pd.DataFrame:
    """Join snapshots to native price rows and align the latest delayed feed.

    Inputs are sorted deterministically for ``merge_asof`` and restored to the
    original price order afterwards. A bounded post-join overlay makes the
    newest account observation available on the newest daily/delayed price row
    without changing source timestamps or leaking it into older rows. The
    finalisation helper remains the sole owner of stale/default-status policy.

    :param price_rows:
        Native raw price DataFrame containing chain, address and timestamp.
    :param snapshots:
        Metric snapshots from one or more protocol DuckDB files.
    :param maximum_forward_alignment:
        Maximum distance for aligning a newer observation to the latest price
        row for the same account.
    :return:
        Price rows with the seven materialised metrics attached.
    """
    price_rows = ensure_perp_metric_columns(price_rows.copy())
    if snapshots.empty:
        return price_rows
    if price_rows["address"].isna().any() or snapshots["address"].isna().any():
        msg = "Perp metric price join requires non-null price and snapshot addresses"
        raise ValueError(msg)
    price_rows["_perp_original_order"] = range(len(price_rows))
    join_chain = "_perp_join_chain"
    join_address = "_perp_join_address"
    price_rows[join_chain] = pd.to_numeric(price_rows["chain"], errors="raise").astype("int64")
    price_rows[join_address] = price_rows["address"].astype("string").str.lower()
    right = snapshots.copy()
    right[join_chain] = pd.to_numeric(right["chain"], errors="raise").astype("int64")
    right[join_address] = right["address"].astype("string").str.lower()
    right.drop(columns=["address", "chain"], inplace=True)
    left = price_rows.sort_values(["timestamp", join_chain, join_address], kind="stable")
    right = right.sort_values(["position_effective_at", join_chain, join_address], kind="stable")
    if right.duplicated([join_chain, join_address, "position_effective_at"]).any():
        msg = "Duplicate selected perp metric snapshots for price join"
        raise ValueError(msg)
    joined = pd.merge_asof(
        left,
        right,
        left_on="timestamp",
        right_on="position_effective_at",
        by=[join_chain, join_address],
        direction="backward",
        allow_exact_matches=True,
        suffixes=("", "_joined"),
    )
    for field in PERP_VAULT_PARQUET_FIELDS:
        joined_name = f"{field.name}_joined"
        if joined_name in joined.columns:
            joined[field.name] = joined[joined_name]
            joined.drop(columns=[joined_name], inplace=True)
    joined = align_latest_perp_metrics_to_price_rows(
        joined,
        snapshots,
        maximum_forward_alignment,
    )
    joined.drop(columns=["position_effective_at"], errors="ignore", inplace=True)
    return joined.sort_values("_perp_original_order", kind="stable").drop(columns=["_perp_original_order", join_chain, join_address])


def build_registered_perp_vault_index(frame: pd.DataFrame) -> pd.MultiIndex:
    """Build the unique native perp account index from price rows.

    This helper keeps account registration vectorised and centralises the
    lower-case comparison key without altering stored address values.

    :param frame:
        Price rows containing ``chain`` and ``address``.
    :return:
        Unique ``(chain, lower-case address)`` index for native perp rows.
    """
    native = frame.loc[frame["chain"].isin(PERP_DEX_NATIVE_CHAIN_IDS), ["chain", "address"]].dropna().drop_duplicates().copy()
    native["chain"] = native["chain"].astype("int64")
    native["address"] = native["address"].astype("string").str.lower()
    return pd.MultiIndex.from_frame(native, names=["chain", "address"])


def finalise_perp_metric_columns(
    frame: pd.DataFrame,
    registered_perp_vaults: Iterable[tuple[int, str]],
    maximum_age: datetime.timedelta = PERP_METRICS_MAX_AGE,
) -> pd.DataFrame:
    """Apply the single default and freshness policy for perp metric columns.

    Staleness is calculated from each price row's timestamp minus the observed
    position time. Static unavailable states never turn stale. Numeric values
    remain attached when stale: the status and original observation timestamp
    give consumers the age needed to accept or reject the measurement.

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
    registered_tuples = tuple(registered_perp_vaults)
    registered = pd.MultiIndex.from_tuples(registered_tuples, names=["chain", "address"]) if registered_tuples else pd.MultiIndex.from_arrays([[], []], names=["chain", "address"])
    empty_status = frame["perp_position_data_status"].isna() | (frame["perp_position_data_status"] == "")
    frame_identity = pd.MultiIndex.from_arrays(
        [
            pd.to_numeric(frame["chain"], errors="raise").astype("int64"),
            frame["address"].astype("string").str.lower(),
        ],
        names=["chain", "address"],
    )
    is_registered = pd.Series(frame_identity.isin(registered), index=frame.index)
    frame.loc[empty_status & is_registered, "perp_position_data_status"] = PerpParquetDataStatus.not_collected.value
    frame.loc[empty_status & ~is_registered, "perp_position_data_status"] = PerpParquetDataStatus.not_applicable.value

    timestamps = pd.to_datetime(frame["timestamp"] if "timestamp" in frame.columns else frame.index)
    observed = pd.to_datetime(frame["perp_metrics_observed_at"])
    age = timestamps - observed
    # A negative age is intentional on the latest row of a delayed native
    # price feed when the bounded alignment step attaches an observation taken
    # later that day.  ``perp_metrics_observed_at`` preserves that fact.
    if (age < -PERP_METRICS_MAX_FORWARD_ALIGNMENT).any():
        msg = "Perp metrics observation exceeds the maximum forward-alignment window"
        raise ValueError(msg)
    stale = (frame["perp_position_data_status"] == PerpParquetDataStatus.available.value) & (age > maximum_age)
    frame.loc[stale, "perp_position_data_status"] = PerpParquetDataStatus.stale.value
    return normalise_perp_metric_parquet_dtypes(frame)
