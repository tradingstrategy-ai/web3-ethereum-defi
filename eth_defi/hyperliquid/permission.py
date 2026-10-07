"""Independent, auditable HyperCore deposit-permission observations.

The portfolio timestamp in ``vaultDetails`` describes prices, not permission.
Keep successful response receipts, explicit unknown snapshots and historical
uncertainty intervals independently of price updates. See the `info endpoint
<https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint>`__.
"""

import datetime
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.vault import HyperliquidVault, VaultInfo, classify_hyperliquid_vault_deposit
from eth_defi.types import Percent

PERMISSION_FILENAME = "hypercore-vault-permissions.parquet"
PERMISSION_SCHEMA_VERSION = 1


@dataclass(slots=True)
class PermissionObservation:
    """A coherent source response or a retrospective uncertainty interval.

    Legacy availability bounds are separate from exact response receipt times.
    Unknown flags in a successful response supersede older known flags.
    """

    #: Stable evidence identity; genuine new fetches receive distinct IDs.
    observation_id: str
    #: Lowercase Hypercore vault address.
    vault_address: HexAddress
    #: ``observation`` or ``uncertainty_boundary``.
    record_kind: str = "observation"
    #: API receipt clock, or price clock for ``legacy_price_timestamp`` provenance.
    permission_observed_at: datetime.datetime | None = None
    #: Persistence time, separate from permission freshness.
    written_at: datetime.datetime | None = None
    #: Verified archive availability bound for deny-only evidence.
    evidence_available_at: datetime.datetime | None = None
    #: Nullable closure flag from this same response.
    is_closed: bool | None = None
    #: Nullable leader deposit-policy flag from this same response.
    allow_deposits: bool | None = None
    #: Vault relationship used to interpret the same capacity input.
    relationship_type: str | None = None
    #: Recorded leader capital fraction, including recoverable archive values.
    leader_fraction: Percent | None = None
    #: Independent capacity receipt clock, absent for inferred flags.
    capacity_observed_at: datetime.datetime | None = None
    #: Clock/evidence quality, including observed and legacy_price_timestamp.
    provenance: str = "observed_unknown"
    #: Origin of the retained source response or archive evidence.
    source_endpoint: str = "POST /info vaultDetails"
    #: Collector schema/behaviour version for this record.
    collector_version: str = "2"
    #: Original coherent policy inputs or complete recovered source row.
    payload_json: str = "{}"
    #: Digest of the retained payload text.
    payload_sha256: str = ""
    #: Earlier record superseded by an explicit correction, if supplied.
    correction_parent_id: str | None = None
    #: Original scanner/raw price key for inferred archive evidence.
    source_row_key: str | None = None
    #: Start of a retrospective uncertainty interval, inclusive.
    effective_from: datetime.datetime | None = None
    #: End of a retrospective uncertainty interval, if bounded.
    effective_to: datetime.datetime | None = None
    #: Human-readable interpretation or uncertainty explanation.
    reason: str | None = None
    #: Recorded trading-policy cap in USDC; zero is distinct from missing.
    max_deposit: float | None = None


def initialise_permission_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create unconstrained permission, error and source-reference tables.

    The owner serialises writes; application deduplication avoids ART indexes
    on large Python 3.14 file-backed ingestion tables.

    :param connection: Caller-owned DuckDB connection.
    :return: ``None``.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS vault_permission_observations (
            observation_id VARCHAR, vault_address VARCHAR, record_kind VARCHAR,
            permission_observed_at TIMESTAMP, written_at TIMESTAMP,
            evidence_available_at TIMESTAMP, is_closed BOOLEAN, allow_deposits BOOLEAN,
            relationship_type VARCHAR, leader_fraction DOUBLE, capacity_observed_at TIMESTAMP,
            provenance VARCHAR, source_endpoint VARCHAR, collector_version VARCHAR,
            payload_json VARCHAR, payload_sha256 VARCHAR, correction_parent_id VARCHAR,
            source_row_key VARCHAR, effective_from TIMESTAMP, effective_to TIMESTAMP, reason VARCHAR
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS vault_permission_sources (
            observation_id VARCHAR, source_sha256 VARCHAR, source_path VARCHAR
        )
    """)
    connection.execute("ALTER TABLE vault_permission_observations ADD COLUMN IF NOT EXISTS max_deposit DOUBLE")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS vault_permission_errors (
            vault_address VARCHAR, attempted_at TIMESTAMP, source_endpoint VARCHAR, error_type VARCHAR
        )
    """)


def build_permission_observation(vault: HyperliquidVault, info: VaultInfo | None) -> PermissionObservation:
    """Capture the successful response before portfolio processing starts.

    The client retains trimmed raw flags and their receipt clock. Test doubles
    without this envelope use the immediately returned parsed response instead.

    :param vault: Client that just fetched ``vaultDetails``.
    :param info: Parsed response, including nullable flags and policy inputs.
    :return: New immutable coherent observation, even without price history.
    """
    received_at = getattr(vault, "permission_received_at", None) or native_datetime_utc_now()
    payload = getattr(vault, "permission_payload", None)
    if payload is None:
        if info is None:
            msg = "No successful permission response to record"
            raise ValueError(msg)
        payload = {"isClosed": info.is_closed, "allowDeposits": info.allow_deposits, "relationship": {"type": info.relationship_type}, "leaderFraction": info.leader_fraction}
    for name in ("isClosed", "allowDeposits"):
        if payload.get(name) is not None and type(payload[name]) is not bool:
            msg = f"Invalid Hyperliquid permission field: {name}"
            raise ValueError(msg)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    leader_fraction = float(payload["leaderFraction"]) if payload.get("leaderFraction") is not None else None
    relationship_type = (payload.get("relationship") or {}).get("type")
    policy = classify_hyperliquid_vault_deposit(payload.get("isClosed"), payload.get("allowDeposits"), relationship_type or "normal", leader_fraction)
    return PermissionObservation(
        observation_id=uuid.uuid4().hex,
        vault_address=vault.vault_address.lower(),
        permission_observed_at=received_at,
        written_at=native_datetime_utc_now(),
        is_closed=payload.get("isClosed"),
        allow_deposits=payload.get("allowDeposits"),
        relationship_type=relationship_type,
        leader_fraction=leader_fraction,
        max_deposit=float(policy.max_deposit) if policy.max_deposit is not None else None,
        capacity_observed_at=received_at if payload.get("leaderFraction") is not None else None,
        provenance="observed" if payload.get("isClosed") is not None and payload.get("allowDeposits") is not None else "observed_unknown",
        payload_json=encoded,
        payload_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
    )


def append_permission_observation(connection: duckdb.DuckDBPyConnection, observation: PermissionObservation, *, new_fetch: bool = False) -> None:
    """Append once by observation ID, refusing a conflicting reuse of an ID.

    Call under the database owner's writer lock. New fetches use new IDs;
    persistence retries of the same observation are idempotent.

    :param connection: Caller-owned cursor under a serialised writer.
    :param observation: Complete response or migration annotation.
    :param new_fetch: Fresh collector UUID; skip an unnecessary full-table retry lookup.
    :return: ``None``.
    """
    values = asdict(observation)
    values["vault_address"] = str(values["vault_address"]).lower()
    for name in ("permission_observed_at", "written_at", "evidence_available_at", "capacity_observed_at", "effective_from", "effective_to"):
        if values[name] is not None and values[name].tzinfo is not None:
            msg = f"Expected naive UTC {name}"
            raise ValueError(msg)
    old = None if new_fetch else connection.execute("SELECT * FROM vault_permission_observations WHERE observation_id = ?", [observation.observation_id]).fetchone()
    row = tuple(values.values())
    if old is not None:
        if old != row:
            msg = "Conflicting permission observation ID"
            raise ValueError(msg)
        return
    columns = ",".join(values)
    connection.execute(f"INSERT INTO vault_permission_observations ({columns}) VALUES ({','.join('?' for _ in row)})", row)


def read_permission_observations(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Read exact observations and uncertainty boundaries without resampling.

    Inferred legacy price clocks remain distinguished by their provenance;
    migration/publication clocks never substitute for observation clocks.

    :param connection: Open scanner database, optionally read-only.
    :return: Frame with the columns of :class:`PermissionObservation`.
    """
    exists = connection.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'vault_permission_observations'").fetchone()[0]
    if not exists:
        return pd.DataFrame(columns=PermissionObservation.__dataclass_fields__)
    result = connection.execute("SELECT * FROM vault_permission_observations ORDER BY vault_address, permission_observed_at, observation_id").df()
    if "max_deposit" not in result:
        result["max_deposit"] = float("nan")
    return result


def select_permission_state(observations: pd.DataFrame, decisions: pd.DataFrame, frequency: str | None = None, *, legacy_max_age: datetime.timedelta = datetime.timedelta(days=2)) -> pd.DataFrame:  # noqa: PLR0914
    """Select coherent permission snapshots available at each decision.

    Round availability upwards before selection when a decision frequency is
    supplied. Retrospective uncertainty intervals override earlier open state
    irrespective of migration publication time. Explicit unknown responses
    remain in selection, preventing per-field filling across source snapshots.
    When no eligible genuine snapshot exists, recovered backup flags may use
    their price clock with ``legacy_price_timestamp`` provenance. They supply
    recorded leader shares and policy caps without requiring a capacity clock,
    and never override a genuine unknown response.

    :param observations: Exact observation table with nullable flags and clocks.
    :param decisions: Frame with ``vault_address`` (string) and ``timestamp`` (naive UTC).
    :param frequency: Optional Pandas decision frequency such as ``4h``.
    :param legacy_max_age: Maximum carry-forward age of an inferred backup flag, measured from its original price clock.
    :return: Decision frame plus snapshot values, provenance and original clocks.
    """
    output = decisions.reset_index(drop=True).copy()
    output["_decision_order"] = range(len(output))
    # Arrow sidecars use microseconds while decision grids commonly use
    # nanoseconds. Pandas as-of joins require identical datetime units.
    output["timestamp"] = pd.to_datetime(output["timestamp"]).astype("datetime64[ns]")
    fields = ["observation_id", "permission_observed_at", "evidence_available_at", "is_closed", "allow_deposits", "relationship_type", "leader_fraction", "max_deposit", "capacity_observed_at", "provenance", "reason", "available_at"]
    for name in fields:
        output[name] = pd.NaT if name.endswith("_at") else None
    if observations.empty or output.empty:
        return output.drop(columns="_decision_order")
    snapshots = observations[observations["record_kind"] == "observation"].copy()
    if "max_deposit" not in snapshots:
        snapshots["max_deposit"] = float("nan")
    for name in ("permission_observed_at", "capacity_observed_at", "evidence_available_at"):
        snapshots[name] = pd.to_datetime(snapshots[name]).astype("datetime64[ns]")
    snapshots["available_at"] = pd.to_datetime(snapshots["permission_observed_at"]).astype("datetime64[ns]")
    bounded = snapshots["provenance"] == "legacy_closure_bounded"
    snapshots.loc[bounded, "available_at"] = pd.to_datetime(snapshots.loc[bounded, "evidence_available_at"]).astype("datetime64[ns]")
    snapshots = snapshots[snapshots["available_at"].notna() & snapshots["provenance"].isin(("observed", "observed_unknown", "restored", "legacy_closure_bounded", "legacy_price_timestamp"))]
    snapshots["is_closed"] = snapshots["is_closed"].astype("boolean")
    snapshots["allow_deposits"] = snapshots["allow_deposits"].astype("boolean")
    archive_closures = snapshots[snapshots["provenance"].eq("legacy_closure_bounded")].copy()
    inferred = snapshots[snapshots["provenance"].eq("legacy_price_timestamp")].copy()
    # At identical source clocks the finer scanner has priority over the daily
    # compatibility sample, matching the price export's source precedence.
    inferred["_source_priority"] = inferred["source_endpoint"].eq("archive price rows/vault_high_freq_prices").astype(int)
    inferred = inferred.sort_values(["available_at", "_source_priority", "observation_id"]).drop_duplicates(["vault_address", "available_at"], keep="last")
    inferred["_original_available_at"] = inferred["available_at"]
    if frequency:
        inferred["available_at"] = inferred["available_at"].dt.ceil(frequency)
    inferred = inferred.sort_values(["available_at", "_original_available_at", "observation_id"])
    inferred_groups = dict(iter(inferred.groupby("vault_address", sort=False)))
    snapshots = snapshots[~snapshots["provenance"].isin(("legacy_closure_bounded", "legacy_price_timestamp"))]
    # Equal receipt clocks with conflicting coherent inputs have no reliable
    # precedence. Preserve both raw records but select an explicit unknown.
    snapshot_fields = ["is_closed", "allow_deposits", "relationship_type", "leader_fraction", "max_deposit", "capacity_observed_at"]
    snapshots["_state_hash"] = pd.util.hash_pandas_object(snapshots[snapshot_fields], index=False)
    conflicting = snapshots.groupby(["vault_address", "available_at"])["_state_hash"].transform("nunique").gt(1)
    snapshots.loc[conflicting, snapshot_fields] = None
    snapshots.loc[conflicting, "provenance"] = "observed_unknown"
    snapshots.loc[conflicting, "reason"] = "Conflicting permission snapshots at the same receipt time"
    snapshots["_original_available_at"] = snapshots["available_at"]
    if frequency:
        snapshots["available_at"] = snapshots["available_at"].dt.ceil(frequency)
    snapshots = snapshots.sort_values(["available_at", "_original_available_at", "observation_id"])
    if frequency:
        archive_closures["available_at"] = archive_closures["available_at"].dt.ceil(frequency)
    archive_closures = archive_closures.sort_values(["available_at", "observation_id"])
    archive_groups = dict(iter(archive_closures.groupby("vault_address", sort=False)))
    snapshot_groups = dict(iter(snapshots.groupby("vault_address", sort=False)))
    boundary_groups = dict(iter(observations[observations["record_kind"] == "uncertainty_boundary"].groupby("vault_address", sort=False)))
    parts = []
    for address, group in output.groupby("vault_address", sort=False):
        group = group.sort_values("timestamp").reset_index(drop=True)
        candidates = snapshot_groups.get(address, snapshots.iloc[:0])
        if not candidates.empty:
            group = pd.merge_asof(group.drop(columns=fields).sort_values("timestamp"), candidates[fields], left_on="timestamp", right_on="available_at", direction="backward")
        boundaries = boundary_groups.get(address, observations.iloc[:0])
        for boundary in boundaries.itertuples():
            start = pd.Timestamp(boundary.effective_from)
            end = pd.Timestamp(boundary.effective_to) if pd.notna(boundary.effective_to) else pd.Timestamp.max
            in_gap = (group["timestamp"] >= start) & (group["timestamp"] < end)
            measured = pd.to_datetime(group["permission_observed_at"])
            rounded = measured.dt.ceil(frequency) if frequency else measured
            fresh = rounded.notna() & (rounded >= start) & (rounded <= group["timestamp"])
            deny_only = group["provenance"].eq("legacy_closure_bounded") & (group["is_closed"].eq(True) | group["allow_deposits"].eq(False))
            mask = in_gap & ~fresh & ~deny_only
            for name in fields:
                group.loc[mask, name] = pd.NaT if name.endswith("_at") else None
            group.loc[mask, "provenance"] = "corrupted_unknown"
            group.loc[mask, "reason"] = boundary.reason
        closures = archive_groups.get(address, archive_closures.iloc[:0])
        fallback = inferred_groups.get(address, inferred.iloc[:0])
        if not fallback.empty:
            recovered = pd.merge_asof(group[["timestamp"]].sort_values("timestamp"), fallback[fields], left_on="timestamp", right_on="available_at", direction="backward")
            missing = group["observation_id"].isna().to_numpy() & recovered["observation_id"].notna().to_numpy()
            missing &= ((group["timestamp"] - recovered["permission_observed_at"]) <= pd.Timedelta(legacy_max_age)).to_numpy()
            for boundary in boundaries.itertuples():
                start = pd.Timestamp(boundary.effective_from)
                end = pd.Timestamp(boundary.effective_to) if pd.notna(boundary.effective_to) else pd.Timestamp.max
                # Fallback can recover a flag inside a gap, but an older flag
                # cannot silently bridge a gap with no recoverable evidence.
                in_gap = (group["timestamp"] >= start) & (group["timestamp"] < end)
                missing &= ~(in_gap & (recovered["available_at"] < start)).to_numpy()
            for name in fields:
                group.loc[missing, name] = recovered.loc[missing, name].to_numpy()
        if not closures.empty:
            archived = pd.merge_asof(group[["timestamp"]].sort_values("timestamp"), closures[fields], left_on="timestamp", right_on="available_at", direction="backward")
            missing = group["observation_id"].isna().to_numpy() & archived["observation_id"].notna().to_numpy()
            for name in fields:
                group.loc[missing, name] = archived.loc[missing, name].to_numpy()
        parts.append(group)
    result = pd.concat(parts).sort_values("_decision_order").drop(columns="_decision_order").reset_index(drop=True)
    return result


def project_permission_prices(prices: pd.DataFrame, observations: pd.DataFrame, time_column: str) -> pd.DataFrame:
    """Attach a point-in-time convenience projection without creating prices.

    The independent sidecar is authoritative when a response arrives after
    the latest price or when several responses share one price timestamp.

    Archived policy inputs use the last recorded price-row snapshot. Actual
    responses, including explicit unknowns, take precedence. Publication never
    supplies a new observation clock, and missing clocks do not erase values.

    :param prices: Scanner price frame with vault identity and source time.
    :param observations: Independent source observations and migration intervals.
    :param time_column: ``date`` or ``timestamp`` in the price frame.
    :return: Frame with nullable flags, retained policy inputs and original clocks.
    """
    states = select_permission_state(observations, prices[["vault_address", time_column]].rename(columns={time_column: "timestamp"}))
    result = prices.reset_index(drop=True).copy()
    policy_fields = ("leader_fraction", "max_deposit")
    for name in policy_fields:
        if name not in result:
            result[name] = float("nan")
    ordered = result.sort_values(["vault_address", time_column])
    anchors = ordered[list(policy_fields)].notna().any(axis=1)
    source_rows = pd.Series(ordered.index, index=ordered.index).where(anchors).groupby(ordered["vault_address"]).ffill()
    genuine = states["provenance"].isin(("observed", "observed_unknown", "restored"))
    use_response = genuine | source_rows.reindex(result.index).isna()
    for name in policy_fields:
        archived = source_rows.map(result[name]).reindex(result.index)
        result[name] = states[name].where(use_response, archived)
    for name in ("is_closed", "allow_deposits", "relationship_type", "capacity_observed_at", "permission_observed_at", "evidence_available_at", "provenance", "observation_id"):
        result[name] = states[name].values
    return result


def export_permission_history(database_paths: list[Path], destination: Path, *, observations: pd.DataFrame | None = None) -> pd.DataFrame:
    """Export separate source observations from closed scanner databases.

    Repeated backup copies deduplicate only identical observation IDs. A reused
    ID with different contents is a hard error instead of silent precedence.

    :param database_paths: Existing daily/HF scanner files opened read-only.
    :param destination: Sidecar Parquet path; replaced atomically.
    :param observations: Already-read observations from active scanner owners; avoids reopening with a different DuckDB configuration.
    :return: Combined exact observation frame, including explicit uncertainty.
    """
    parts = [observations] if observations is not None else []
    for path in database_paths if observations is None else []:
        if not path.exists():
            continue
        connection = duckdb.connect(str(path), read_only=True)
        try:
            parts.append(read_permission_observations(connection))
        finally:
            connection.close()
    result = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=PermissionObservation.__dataclass_fields__)
    if "max_deposit" not in result:
        result["max_deposit"] = float("nan")
    unique = result.drop_duplicates()
    if unique["observation_id"].duplicated().any():
        msg = "Conflicting permission sidecar observation IDs"
        raise ValueError(msg)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        timestamp_fields = {"permission_observed_at", "written_at", "evidence_available_at", "capacity_observed_at", "effective_from", "effective_to"}
        field_types = {"is_closed": pa.bool_(), "allow_deposits": pa.bool_(), "leader_fraction": pa.float64(), "max_deposit": pa.float64()}
        schema = pa.schema([(name, pa.timestamp("us") if name in timestamp_fields else field_types.get(name, pa.string())) for name in PermissionObservation.__dataclass_fields__])
        pq.write_table(pa.Table.from_pandas(unique, schema=schema, preserve_index=False), temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return unique
