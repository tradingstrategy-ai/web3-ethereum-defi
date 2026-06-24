#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Multi-chain vault analysis + safe JSON export.

Features:
- Performs lifetime metric analysis for all available chains.
- Filters and formats results for the top-performing vaults.
- Safely exports to JSON with NaN/Inf -> null sanitization.
- Normalizes column keys into snake_case.
- Uses column-wise .map(parse_value) to comply with modern pandas.
- Uses allow_nan=False to guarantee strict JSON validity.

To test out:

.. code-block:: shell

    OUTPUT_JSON=/tmp/top-vaults.json python scripts/erc-4626/vault-analysis-json.py

To test out Pandas warning issues in calculate_lifetime_metrics(), enable strict warnings:

.. code-block:: shell
    PYTHONWARNINGS="error::RuntimeWarning" python scripts/erc-4626/vault-analysis-json.py
"""

import os
import json
import math
import pandas as pd
import datetime
from dataclasses import dataclass
from pathlib import Path

from atomicwrites import atomic_write

from eth_defi.compat import native_datetime_utc_now
from eth_defi.core3.constants import CORE3_DATABASE_PATH
from eth_defi.core3.database import Core3Database
from eth_defi.core3.vault_protocol import build_core3_protocols_for_export
from eth_defi.feed.database import DEFAULT_VAULT_POST_DATABASE, VaultPostDatabase
from eth_defi.token import is_stablecoin_like
from eth_defi.vault.curator_export import build_curators_for_export
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.version_info import VersionInfo

# Import core TradingStrategy / eth_defi modules
from eth_defi.vault.base import VaultSpec  # noqa: F401
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.research.vault_metrics import (
    VaultMetricsExport,
    calculate_lifetime_metrics,
    export_lifetime_row,
    cross_check_data,
    calculate_hourly_returns_for_all_vaults,
    slugify_protocol,
)

# --------------------------------------------------------------------
# Configuration via environment variables (scalar tunables)
# --------------------------------------------------------------------
MONTHS = int(os.getenv("MONTHS", "3"))  # Time window in months
EVENT_THRESHOLD = int(os.getenv("EVENT_THRESHOLD", "5"))  # Min event count
MAX_ANNUALISED_RETURN = float(os.getenv("MAX_ANNUALISED_RETURN", "4.0"))  # Cap annualized return at 400%
THRESHOLD_TVL = float(os.getenv("MIN_TVL", "5000"))  # Minimum TVL filter
TOP_PER_CHAIN = int(os.getenv("TOP_PER_CHAIN", "99999"))  # Top N vaults per chain


#: Default output filename when no ``OUTPUT_JSON`` override is supplied
#: and no ``output_path`` is passed to :py:func:`main`.
DEFAULT_OUTPUT_FILENAME = "stablecoin-vault-metrics.json"

STICKY_EXPORT_STATE_SCHEMA_VERSION = 1
STICKY_STALE_WARNING_AGE_DAYS_DEFAULT = 14
BLACKLISTED_RISK_LABEL = VaultTechnicalRisk.blacklisted.get_risk_level_name()


@dataclass(slots=True)
class StickyExportStats:
    """Counters emitted by sticky vault export state processing.

    The counters are intentionally small and line-oriented, because this
    script is often run from cron and pipeline logs are the first place
    operators diagnose data gaps.
    """

    #: State entries read before this run.
    loaded_state_entries: int = 0

    #: Rows passing the current peak TVL export filter.
    current_filter_passed: int = 0

    #: Current-filter rows that created a new sticky state entry.
    sticky_additions: int = 0

    #: Rows replayed from ``last_exported_record``.
    sticky_fallback_exports: int = 0

    #: Current rows that could not replace the stored fallback record.
    current_row_structural_fallbacks: int = 0

    #: Rows skipped because of structural suppression.
    structurally_suppressed_vaults: int = 0

    #: Rows carrying stale warning annotations.
    stale_warning_vaults: int = 0

    #: Exported sticky rows whose protocol slug no longer resolves in Core3.
    missing_protocol_slugs: int = 0

    #: Exported sticky rows whose curator slug no longer resolves in curator export.
    missing_curator_slugs: int = 0

    #: Previous successful run's current-filter row count.
    previous_current_filter_count: int | None = None


@dataclass(slots=True)
class StickyExportResult:
    """Result of applying sticky vault export state."""

    #: Final exported vault rows.
    vaults: list[dict]

    #: Mutated state ready to be written after output JSON succeeds.
    state: dict

    #: Operator-facing counters.
    stats: StickyExportStats


@dataclass(slots=True)
class CurrentVaultRow:
    """Normalised current dataframe row for sticky export processing."""

    #: Canonical state key, ``chain_id-lowercase_address``.
    key: str

    #: JSON export row produced by :py:func:`export_lifetime_row`.
    record: dict

    #: Whether this current row can safely replace ``last_exported_record``.
    export_safe: bool

    #: Whether the row timestamp is inside the freshness warning window.
    fresh: bool


def _resolve_defaults_from_env() -> dict:
    """Read env-var defaults for manual invocation.

    Returns a dict of path defaults that the ``__main__`` entrypoint
    passes into :py:func:`main`. Keeping this in a function (rather than
    at module import time) means env vars are re-read on every call and
    can be set by the caller just before invocation.

    :return:
        Dict with keys ``data_dir``, ``vault_db_path``, ``parquet_path``,
        ``output_path`` suitable for splatting into :py:func:`main`.
    """
    data_dir = Path(os.getenv("DATA_DIR", str(get_pipeline_data_dir()))).expanduser()
    env_output_json = os.getenv("OUTPUT_JSON")
    output_path = Path(env_output_json).expanduser() if env_output_json else data_dir / DEFAULT_OUTPUT_FILENAME
    return {
        "data_dir": data_dir,
        "vault_db_path": data_dir / "vault-metadata-db.pickle",
        "parquet_path": data_dir / "cleaned-vault-prices-1h.parquet",
        "output_path": output_path,
    }


def parse_env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    Accepts common truthy and falsy spellings used in deployment
    configuration. Unknown values fail closed with a clear error so a
    misspelt safety switch does not silently do the wrong thing.

    :param name:
        Environment variable name.
    :param default:
        Value to return when the variable is absent.
    :return:
        Parsed boolean.
    """
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {value!r}")


def format_state_timestamp(ts: datetime.datetime) -> str:
    """Format a naive UTC timestamp for sticky export state.

    State files use ``Z`` suffixes for readability, while comparisons use
    naive UTC datetimes internally.

    :param ts:
        Naive UTC datetime.
    :return:
        ISO 8601 string with ``Z`` suffix.
    """
    assert ts.tzinfo is None, f"Expected naive UTC datetime, got {ts!r}"
    return ts.replace(microsecond=0).isoformat() + "Z"


def normalise_datetime_to_naive_utc(value) -> datetime.datetime | None:
    """Normalise timestamp-like values to naive UTC datetimes.

    Tz-aware Pandas and Python timestamps are converted to UTC first and
    only then stripped of tzinfo. This avoids both ``TypeError`` when
    comparing aware and naive values, and silent wall-clock shifts from
    dropping tzinfo before conversion.

    :param value:
        ``datetime``, ``pd.Timestamp``, ISO string, or null-like value.
    :return:
        Naive UTC datetime, or ``None`` for null-like values.
    """
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        timestamp = value
    elif isinstance(value, datetime.datetime):
        timestamp = pd.Timestamp(value)
    elif isinstance(value, str):
        timestamp = pd.Timestamp(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported timestamp value: {value!r}")

    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)

    return timestamp.to_pydatetime().replace(tzinfo=None)


def make_vault_export_state_key(chain_id: int | str, address: str) -> str:
    """Create a canonical sticky export state key.

    State, dataframe rows, exported rows, and deduplication all use the
    same ``chain_id-lowercase_address`` shape.

    :param chain_id:
        Numeric chain id.
    :param address:
        EVM address, possibly checksummed.
    :return:
        Canonical state key.
    """
    if chain_id is None or address is None:
        raise ValueError("Both chain_id and address are required")
    address_text = str(address).strip()
    if not address_text:
        raise ValueError("Address cannot be empty")
    return f"{int(chain_id)}-{address_text.lower()}"


def make_vault_export_state_key_from_record(record: dict) -> str:
    """Create a sticky export state key from an exported vault record.

    :param record:
        Exported vault JSON row.
    :return:
        Canonical state key.
    """
    chain_id = record.get("chain_id")
    address = record.get("address")
    if chain_id is None or address is None:
        vault_id = record.get("id")
        if isinstance(vault_id, str) and "-" in vault_id:
            chain_id_text, address = vault_id.split("-", 1)
            chain_id = int(chain_id_text)
    return make_vault_export_state_key(chain_id, address)


def resolve_sticky_export_state_path(data_dir: Path, output_path: Path) -> Path:
    """Resolve the output-namespaced sticky export state path.

    ``VAULT_EXPORT_STATE_PATH`` is an explicit override. Otherwise the
    output filename stem is included so manual standalone runs do not
    mutate production ``top_vaults_by_chain`` state.

    :param data_dir:
        Pipeline data directory.
    :param output_path:
        Export JSON path.
    :return:
        State file path.
    """
    env_path = os.getenv("VAULT_EXPORT_STATE_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return data_dir / f"vault-export-state-{output_path.stem}.json"


def make_empty_sticky_export_state(output_stem: str, now: datetime.datetime) -> dict:
    """Create an empty sticky export state document.

    :param output_stem:
        Output filename stem this state belongs to.
    :param now:
        Current naive UTC datetime.
    :return:
        Empty state mapping.
    """
    return {
        "schema_version": STICKY_EXPORT_STATE_SCHEMA_VERSION,
        "output_stem": output_stem,
        "updated_at": format_state_timestamp(now),
        "last_current_filter_count": 0,
        "vaults": {},
    }


def validate_sticky_export_state(state: dict) -> None:
    """Validate sticky export state structure.

    :param state:
        Decoded state JSON.
    """
    if not isinstance(state, dict):
        raise ValueError("Sticky export state must be a JSON object")
    if state.get("schema_version") != STICKY_EXPORT_STATE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported sticky export state schema version: {state.get('schema_version')!r}")
    if not isinstance(state.get("vaults"), dict):
        raise ValueError("Sticky export state must contain a vaults object")
    if "last_current_filter_count" in state and not isinstance(state["last_current_filter_count"], int):
        raise ValueError("Sticky export state last_current_filter_count must be an integer")
    for key, entry in state["vaults"].items():
        if not isinstance(key, str) or "-" not in key:
            raise ValueError(f"Invalid sticky export state vault key: {key!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid sticky export state entry for {key}: expected object")
        status = entry.get("status")
        if status not in {"active", "suppressed"}:
            raise ValueError(f"Invalid sticky export state status for {key}: {status!r}")


def load_sticky_export_state(path: Path, output_stem: str, now: datetime.datetime) -> dict:
    """Load sticky export state or create a new empty state.

    Corrupt existing state raises instead of being reset, because the
    sticky qualification history is production state.

    :param path:
        State file path.
    :param output_stem:
        Output filename stem.
    :param now:
        Current naive UTC datetime.
    :return:
        Loaded state mapping.
    """
    if not path.exists():
        return make_empty_sticky_export_state(output_stem, now)
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    validate_sticky_export_state(state)
    return state


def save_sticky_export_state(state: dict, path: Path) -> None:
    """Atomically write sticky export state.

    :param state:
        State mapping.
    :param path:
        Destination path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True, encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, allow_nan=False)


def find_non_serializable_paths(obj, path=None, results=None):
    """
    Recursively traverses a Python object (dict or list) and collects paths to non-serializable values or invalid keys.

    Args:
        obj: The object to check (dict, list, or nested combination).
        path: Current path (list of keys/indices; internal use).
        results: List to collect issues (internal use).

    Returns:
        List of tuples: (path_list, issue_description) for each problem found.
        Empty list if everything is serializable.
    """
    if path is None:
        path = []
    if results is None:
        results = []

    # Valid primitive types
    if isinstance(obj, (str, int, float, bool, type(None))):
        return results

    # Handle lists: recurse on each element
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_path = path + [i]
            find_non_serializable_paths(item, new_path, results)

    # Handle dicts: check keys are strings, then recurse on values
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                results.append((path + [key], f"Non-string key: {type(key).__name__}"))
            new_path = path + [key]
            find_non_serializable_paths(value, new_path, results)

    # Anything else is non-serializable
    else:
        results.append((path, f"Non-serializable type: {type(obj).__name__}"))

    return results


def is_blacklisted_risk_value(value) -> bool:
    """Check if a row or exported record risk means hard blacklist.

    Exported records serialise :py:attr:`VaultTechnicalRisk.blacklisted` as
    ``"Blacklisted"`` through ``get_risk_level_name()``. Current dataframe
    rows may still carry the enum before export.

    :param value:
        Risk enum or serialised risk value.
    :return:
        ``True`` if the value is the exact blacklist marker.
    """
    if value is VaultTechnicalRisk.blacklisted:
        return True
    if value == VaultTechnicalRisk.blacklisted.value:
        return True
    return value == BLACKLISTED_RISK_LABEL


def is_blacklisted_record(record: dict) -> bool:
    """Check if an exported vault row is blacklisted.

    :param record:
        Exported vault row.
    :return:
        ``True`` when the row carries the hard blacklist risk.
    """
    return is_blacklisted_risk_value(record.get("risk"))


def is_export_record_key_safe(record: dict) -> tuple[bool, str | None]:
    """Check if a stored exported row has the key fields needed for replay.

    :param record:
        Exported vault row.
    :return:
        ``(safe, reason)``.
    """
    try:
        make_vault_export_state_key_from_record(record)
    except (TypeError, ValueError) as e:
        return False, f"invalid_key_fields:{e}"
    return True, None


def is_current_record_export_safe(record: dict) -> tuple[bool, str | None]:
    """Check if a current row can replace the stored export record.

    Missing non-key fields are treated as a transient metadata gap for
    sticky vaults. The caller can still replay a previous
    ``last_exported_record``.

    :param record:
        Exported vault row.
    :return:
        ``(safe, reason)``.
    """
    safe, reason = is_export_record_key_safe(record)
    if not safe:
        return safe, reason
    for key in ("name", "protocol_slug"):
        if not record.get(key):
            return False, f"missing_{key}"
    return True, None


def is_row_fresh(record: dict, now: datetime.datetime, stale_warning_age_days: int) -> bool:
    """Check if an exported current row is inside the freshness warning window.

    :param record:
        Exported current row.
    :param now:
        Current naive UTC datetime.
    :param stale_warning_age_days:
        Warning age in days.
    :return:
        ``True`` when the row is fresh.
    """
    last_updated_at = normalise_datetime_to_naive_utc(record.get("last_updated_at"))
    if last_updated_at is None:
        return False
    return last_updated_at >= now - datetime.timedelta(days=stale_warning_age_days)


def copy_export_record(record: dict) -> dict:
    """Create a detached copy of an exported row.

    :param record:
        Exported row.
    :return:
        JSON-round-tripped copy.
    """
    return json.loads(json.dumps(record, allow_nan=False))


def make_current_vault_row(row: pd.Series, now: datetime.datetime, stale_warning_age_days: int) -> CurrentVaultRow:
    """Normalise one metrics dataframe row for sticky processing.

    :param row:
        Lifetime metrics dataframe row.
    :param now:
        Current naive UTC datetime.
    :param stale_warning_age_days:
        Warning age in days.
    :return:
        Normalised current row wrapper.
    """
    record = export_lifetime_row(row)
    key = make_vault_export_state_key_from_record(record)
    export_safe, _reason = is_current_record_export_safe(record)
    fresh = is_row_fresh(record, now, stale_warning_age_days)
    return CurrentVaultRow(
        key=key,
        record=record,
        export_safe=export_safe,
        fresh=fresh,
    )


def annotate_current_record(record: dict, state_entry: dict | None, fresh: bool) -> dict:
    """Annotate an exported current row.

    :param record:
        Exported row.
    :param state_entry:
        Previous or current state entry.
    :param fresh:
        Whether the row is fresh.
    :return:
        Annotated copy.
    """
    annotated = copy_export_record(record)
    if state_entry:
        annotated["first_qualified_at"] = state_entry.get("first_qualified_at")
        annotated["last_qualified_at"] = state_entry.get("last_qualified_at")
    if not fresh:
        annotated["sticky_export"] = False
        annotated["stale_export"] = False
        annotated["stale_current_row"] = True
        annotated["risk_possibly_stale"] = True
    return annotated


def annotate_sticky_record(record: dict, state_entry: dict, fresh: bool) -> dict:
    """Annotate a sticky current-row export.

    :param record:
        Exported current row.
    :param state_entry:
        Sticky state entry.
    :param fresh:
        Whether the row is fresh.
    :return:
        Annotated copy.
    """
    annotated = copy_export_record(record)
    annotated["sticky_export"] = True
    annotated["sticky_reason"] = "previously_passed_filter"
    annotated["stale_export"] = False
    annotated["first_qualified_at"] = state_entry.get("first_qualified_at")
    annotated["last_qualified_at"] = state_entry.get("last_qualified_at")
    if not fresh:
        annotated["stale_current_row"] = True
        annotated["risk_possibly_stale"] = True
    return annotated


def annotate_fallback_record(record: dict, state_entry: dict, fallback_reason: str | None = None) -> dict:
    """Annotate a stale fallback export.

    :param record:
        Stored ``last_exported_record``.
    :param state_entry:
        Sticky state entry.
    :param fallback_reason:
        Optional reason for fallback.
    :return:
        Annotated copy.
    """
    annotated = copy_export_record(record)
    annotated["sticky_export"] = True
    annotated["sticky_reason"] = "previously_passed_filter"
    annotated["stale_export"] = True
    annotated["stale_since"] = state_entry.get("stale_since")
    annotated["risk_possibly_stale"] = True
    annotated["first_qualified_at"] = state_entry.get("first_qualified_at")
    annotated["last_qualified_at"] = state_entry.get("last_qualified_at")
    if fallback_reason:
        annotated["fallback_reason"] = fallback_reason
    return annotated


def make_state_entry_from_current_row(
    current_row: CurrentVaultRow,
    now_text: str,
    threshold_tvl: float,
    existing_entry: dict | None = None,
) -> dict:
    """Create or update sticky state for an exported current row.

    :param current_row:
        Current row wrapper.
    :param now_text:
        Current timestamp formatted for state.
    :param threshold_tvl:
        Active ``MIN_TVL`` threshold.
    :param existing_entry:
        Previous state entry.
    :return:
        Updated state entry.
    """
    entry = dict(existing_entry or {})
    entry["chain_id"] = int(current_row.record["chain_id"])
    entry["address"] = str(current_row.record["address"]).lower()
    entry["status"] = "active"
    entry.pop("suppression_reason", None)
    entry.pop("suppressed_at", None)
    entry.setdefault("first_qualified_at", now_text)
    entry["last_exported_at"] = now_text
    entry["qualification"] = {
        "min_tvl": threshold_tvl,
        "peak_nav": current_row.record.get("peak_nav"),
    }
    if current_row.fresh:
        entry["last_fresh_row_at"] = now_text
        entry["stale_since"] = None
    entry["last_exported_record"] = copy_export_record(current_row.record)
    return entry


def mark_state_entry_suppressed(
    state: dict,
    key: str,
    reason: str,
    now_text: str,
    current_row: CurrentVaultRow | None = None,
    threshold_tvl: float | None = None,
) -> None:
    """Persist structural suppression for a vault.

    :param state:
        Sticky export state.
    :param key:
        Canonical vault key.
    :param reason:
        Suppression reason.
    :param now_text:
        Current timestamp formatted for state.
    :param current_row:
        Current row, if available.
    :param threshold_tvl:
        Active ``MIN_TVL`` threshold, if the current row qualified.
    """
    entry = dict(state["vaults"].get(key, {}))
    if current_row is not None:
        entry.setdefault("chain_id", int(current_row.record["chain_id"]))
        entry.setdefault("address", str(current_row.record["address"]).lower())
        entry.setdefault("first_qualified_at", now_text)
        entry.setdefault("last_qualified_at", now_text)
        entry.setdefault(
            "qualification",
            {
                "min_tvl": threshold_tvl,
                "peak_nav": current_row.record.get("peak_nav"),
            },
        )
    entry["status"] = "suppressed"
    entry["suppression_reason"] = reason
    entry["suppressed_at"] = now_text
    state["vaults"][key] = entry


def add_exported_vault(vaults_by_key: dict[str, tuple[int, dict]], key: str, priority: int, record: dict) -> None:
    """Add a vault export row with deterministic deduplication.

    Higher priority wins. The priority rule is a safety net for malformed
    duplicate inputs, because normal current/sticky paths are intended to
    be mutually exclusive.

    :param vaults_by_key:
        Accumulator keyed by canonical vault key.
    :param key:
        Canonical vault key.
    :param priority:
        Priority number, higher wins.
    :param record:
        Export row.
    """
    existing = vaults_by_key.get(key)
    if existing is None or priority > existing[0]:
        vaults_by_key[key] = (priority, record)


def apply_sticky_export_state(
    lifetime_data_df: pd.DataFrame,
    state: dict,
    *,
    now: datetime.datetime,
    threshold_tvl: float,
    stale_warning_age_days: int,
) -> StickyExportResult:
    """Apply append-biased sticky export state to lifetime metrics.

    Current rows passing the peak TVL filter qualify a vault forever.
    Later missing, stale, below-threshold, or structurally incomplete rows
    do not make a previously qualified vault disappear unless exact
    blacklist/invalid-fallback suppression applies.

    :param lifetime_data_df:
        Calculated lifetime metrics.
    :param state:
        Mutable sticky export state.
    :param now:
        Current naive UTC datetime.
    :param threshold_tvl:
        Active peak TVL export threshold.
    :param stale_warning_age_days:
        Warning age in days.
    :return:
        Final vault rows, mutated state, and counters.
    """
    validate_sticky_export_state(state)
    now_text = format_state_timestamp(now)
    state_vaults = state["vaults"]
    stats = StickyExportStats(
        loaded_state_entries=len(state_vaults),
        previous_current_filter_count=state.get("last_current_filter_count"),
    )
    current_rows: dict[str, CurrentVaultRow] = {}
    unsafe_current_row_keys: set[str] = set()

    for _, row in lifetime_data_df.iterrows():
        try:
            current_row = make_current_vault_row(row, now, stale_warning_age_days)
        except (TypeError, ValueError) as e:
            print(f"Skipping metrics row with invalid vault identity: {e}")
            continue
        current_rows[current_row.key] = current_row
        if not current_row.export_safe:
            unsafe_current_row_keys.add(current_row.key)

    current_filter_rows = [current_row for current_row in current_rows.values() if current_row.record.get("peak_nav") is not None and current_row.record["peak_nav"] >= threshold_tvl]
    stats.current_filter_passed = len(current_filter_rows)

    current_filter_keys = set()
    vaults_by_key: dict[str, tuple[int, dict]] = {}

    for current_row in current_filter_rows:
        key = current_row.key
        current_filter_keys.add(key)
        existing_entry = state_vaults.get(key)

        if is_blacklisted_record(current_row.record):
            mark_state_entry_suppressed(state, key, "current_blacklisted_record", now_text, current_row, threshold_tvl)
            stats.structurally_suppressed_vaults += 1
            continue

        if existing_entry and existing_entry.get("status") == "suppressed" and not current_row.export_safe:
            stats.structurally_suppressed_vaults += 1
            continue

        if not current_row.export_safe:
            if existing_entry and existing_entry.get("status") == "active" and existing_entry.get("last_exported_record"):
                stats.current_row_structural_fallbacks += 1
                unsafe_current_row_keys.add(key)
            else:
                stats.current_row_structural_fallbacks += 1
                continue
        else:
            is_new_entry = existing_entry is None
            entry = make_state_entry_from_current_row(current_row, now_text, threshold_tvl, existing_entry)
            entry["last_qualified_at"] = now_text
            state_vaults[key] = entry
            annotated = annotate_current_record(current_row.record, entry, current_row.fresh)
            if not current_row.fresh:
                stats.stale_warning_vaults += 1
            if is_new_entry:
                stats.sticky_additions += 1
            add_exported_vault(vaults_by_key, key, 30, annotated)

    for key, entry in list(state_vaults.items()):
        if entry.get("status") != "active":
            continue
        if key in current_filter_keys and key in vaults_by_key:
            continue
        current_row = current_rows.get(key)
        fallback_reason = None
        if current_row and is_blacklisted_record(current_row.record):
            mark_state_entry_suppressed(state, key, "current_blacklisted_record", now_text, current_row, threshold_tvl)
            stats.structurally_suppressed_vaults += 1
            vaults_by_key.pop(key, None)
            continue

        if current_row and current_row.export_safe:
            entry = make_state_entry_from_current_row(current_row, now_text, threshold_tvl, entry)
            state_vaults[key] = entry
            annotated = annotate_sticky_record(current_row.record, entry, current_row.fresh)
            if not current_row.fresh:
                stats.stale_warning_vaults += 1
            add_exported_vault(vaults_by_key, key, 20 if current_row.fresh else 10, annotated)
            continue

        if current_row and not current_row.export_safe:
            fallback_reason = "current_row_structurally_unsafe"
            if key not in unsafe_current_row_keys:
                stats.current_row_structural_fallbacks += 1

        fallback_record = entry.get("last_exported_record")
        fallback_safe = isinstance(fallback_record, dict) and bool(fallback_record)
        if fallback_safe:
            fallback_safe, _reason = is_export_record_key_safe(fallback_record)

        if not fallback_safe:
            mark_state_entry_suppressed(state, key, "invalid_last_exported_record", now_text)
            stats.structurally_suppressed_vaults += 1
            vaults_by_key.pop(key, None)
            continue

        if is_blacklisted_record(fallback_record):
            mark_state_entry_suppressed(state, key, "stale_blacklisted_record", now_text)
            stats.structurally_suppressed_vaults += 1
            vaults_by_key.pop(key, None)
            continue

        if entry.get("stale_since") is None:
            entry["stale_since"] = now_text
        entry["last_exported_at"] = now_text
        annotated = annotate_fallback_record(fallback_record, entry, fallback_reason)
        stats.sticky_fallback_exports += 1
        stats.stale_warning_vaults += 1
        add_exported_vault(vaults_by_key, key, 5, annotated)

    state["last_current_filter_count"] = stats.current_filter_passed
    state["updated_at"] = now_text
    return StickyExportResult(
        vaults=[record for _, record in vaults_by_key.values()],
        state=state,
        stats=stats,
    )


def validate_strict_json_serialisable(obj: dict) -> None:
    """Validate JSON serialisability with strict NaN handling.

    :param obj:
        Object to validate.
    """
    results = find_non_serializable_paths(obj)
    if results:
        print("Found non-serializable values in output data:")
        for path, issue in results:
            path_str = " -> ".join(str(p) for p in path)
            print(f" - Path: {path_str}: {issue}")
        raise ValueError("Non-serializable values found; aborting JSON export.")
    json.dumps(obj, ensure_ascii=False, allow_nan=False)


def main(
    data_dir: Path | None = None,
    vault_db_path: Path | None = None,
    parquet_path: Path | None = None,
    output_path: Path | None = None,
    core3_db_path: Path | None = None,
    feed_db_path: Path | None = None,
):
    """Main execution function for vault analysis and JSON export.

    All four arguments are independently overridable. When a path
    argument is ``None``, it is derived from ``data_dir`` so that a
    caller passing only ``data_dir`` reads *and* writes under that
    directory consistently — never a mix of ``data_dir`` for reads and
    ``~/.tradingstrategy/vaults`` for writes.

    :param data_dir:
        Pipeline data directory. When ``None``, falls back to the
        ``DATA_DIR`` env var (default ``~/.tradingstrategy/vaults``).
        Acts as the anchor for both ``parquet_path`` and ``output_path``
        defaults when those are also ``None``.

    :param vault_db_path:
        Path to the vault metadata pickle. When ``None``,
        :py:meth:`VaultDatabase.read` uses
        :py:data:`eth_defi.vault.vaultdb.DEFAULT_VAULT_DATABASE`.

    :param parquet_path:
        Path to the cleaned vault prices parquet. When ``None``,
        defaults to ``data_dir / "cleaned-vault-prices-1h.parquet"``.

    :param output_path:
        Destination JSON path. When ``None``, defaults to
        ``data_dir / DEFAULT_OUTPUT_FILENAME``. The ``OUTPUT_JSON`` env
        var is honoured by :py:func:`_resolve_defaults_from_env` in the
        ``__main__`` entrypoint, not by :py:func:`main` itself, so
        in-process callers get deterministic path anchoring with no env
        var surprises.

    :param core3_db_path:
        Path to the Core3 risk intelligence DuckDB database.
        When ``None``, resolved from ``CORE3_DATABASE_PATH`` env var,
        then the default constant. The database is only opened if the
        resolved file exists on disk.

    :param feed_db_path:
        Path to the vault post feed DuckDB database.
        When ``None``, resolved from ``FEED_DB_PATH`` env var,
        falling back to ``DB_PATH`` (used by the feed collector),
        then :py:data:`~eth_defi.feed.database.DEFAULT_VAULT_POST_DATABASE`.
        The database is only opened if the resolved file exists on disk.
    """
    defaults = _resolve_defaults_from_env()
    if data_dir is None:
        data_dir = defaults["data_dir"]
    if vault_db_path is None:
        vault_db_path = defaults["vault_db_path"]
    if parquet_path is None:
        parquet_path = defaults["parquet_path"]
    if output_path is None:
        output_path = defaults["output_path"]

    data_dir = Path(data_dir)
    vault_db_path = Path(vault_db_path)
    parquet_path = Path(parquet_path)
    output_path = Path(output_path)

    sticky_export_enabled = not parse_env_bool("DISABLE_STICKY_VAULT_EXPORT", default=False)
    now = native_datetime_utc_now()
    sticky_state_path = None
    sticky_state = None
    stale_warning_age_days = STICKY_STALE_WARNING_AGE_DAYS_DEFAULT

    if sticky_export_enabled:
        sticky_state_path = resolve_sticky_export_state_path(data_dir, output_path)
        stale_warning_age_days = int(os.getenv("STICKY_STALE_WARNING_AGE_DAYS", str(STICKY_STALE_WARNING_AGE_DAYS_DEFAULT)))
        sticky_state = load_sticky_export_state(sticky_state_path, output_path.stem, now)
    else:
        print("Sticky vault export state disabled with DISABLE_STICKY_VAULT_EXPORT=true")

    # --------------------------------------------------------------------
    # Step 2: Load database and parquet price data
    # --------------------------------------------------------------------
    vault_db = VaultDatabase.read(vault_db_path)
    prices_df = pd.read_parquet(parquet_path)
    chains = prices_df["chain"].unique()

    print(f"Loaded {len(vault_db):,} vault metadata entries and {len(prices_df):,} price rows across {len(chains):,} chains from {prices_df.index.min()} to {prices_df.index.max()}; price columns: {len(prices_df.columns):,}")

    # sample_vault = next(iter(vault_db.values()))
    # print("We have vault metadata keys: ", ", ".join(c for c in sample_vault.keys()))
    # display(pd.Series(sample_vault))

    errors = cross_check_data(
        vault_db,
        prices_df,
    )
    assert errors == 0, f"Data Cross-check found: {errors} errors"

    usd_vaults = [v for v in vault_db.values() if is_stablecoin_like(v["Denomination"])]
    print(f"The report covers {len(usd_vaults):,} stablecoin-denominated vaults out of {len(vault_db):,} total vaults")

    # Build chain-address strings for vaults we are interested in.
    # Remove Silo vaults that cause havoc after xUSD incident.
    allowed_vault_ids = (str(v["_detection_data"].chain) + "-" + v["_detection_data"].address for v in usd_vaults)

    # Filter out prices to contain only data for vaults we are interested in
    prices_df = prices_df.loc[prices_df["id"].isin(allowed_vault_ids)]
    print(f"Filtered stablecoin-denominated price data has {len(prices_df):,} rows")

    returns_df = calculate_hourly_returns_for_all_vaults(prices_df)

    # Build Core3 protocol-level risk data up front, so it can be attached
    # both per-vault (compact ``core3`` summary inside each vault record) and
    # at the top level of the export (full ``core3_protocols`` dict).
    # Core3 records are keyed by our internal protocol slug.
    core3_protocols = {}
    if core3_db_path is None:
        db_path_env = os.getenv("CORE3_DATABASE_PATH")
        if db_path_env:
            core3_db_path = Path(db_path_env).expanduser()
        else:
            core3_db_path = CORE3_DATABASE_PATH
    if core3_db_path.exists():
        core3_db = Core3Database(core3_db_path)
        try:
            print(f"Opened Core3 risk database at {core3_db_path} with {core3_db.get_project_count()} projects")
            # Derive protocol slugs directly from vault metadata (slugify_vaults
            # has not run yet at this point, so compute the slug from "Protocol").
            all_protocol_slugs = {slugify_protocol(v["Protocol"]) for v in vault_db.values()}
            core3_protocols = build_core3_protocols_for_export(core3_db, all_protocol_slugs)
        finally:
            core3_db.close()

    lifetime_data_df = calculate_lifetime_metrics(returns_df, vault_db, core3_protocols=core3_protocols)

    print(f"Calculated lifetime metrics for {len(lifetime_data_df):,} vaults with {len(lifetime_data_df.columns):,} columns")

    # Don't export all crappy vaults to keep the data more compact
    # Use peak TVL so we will export old vaults too which were popular in the past
    filtered_lifetime_data_df = lifetime_data_df[lifetime_data_df["peak_nav"] >= THRESHOLD_TVL]

    sticky_result = None
    if sticky_export_enabled:
        assert sticky_state is not None
        sticky_result = apply_sticky_export_state(
            lifetime_data_df,
            sticky_state,
            now=now,
            threshold_tvl=THRESHOLD_TVL,
            stale_warning_age_days=stale_warning_age_days,
        )
        vaults = sticky_result.vaults
    else:
        # 5️⃣ Convert DataFrame → list of dicts
        vaults = [export_lifetime_row(r) for _, r in filtered_lifetime_data_df.iterrows()]

    # 6️⃣ Restrict the top-level Core3 protocol risk data to protocols that
    # actually survived the export filter. Core3 data is per-protocol (not
    # per-vault), so it lives at the top level keyed by our protocol slugs;
    # the full dict was built up front (above) and also fed per-vault.
    exported_slugs = {v.get("protocol_slug") for v in vaults if v.get("protocol_slug")}
    exported_slugs.discard(None)
    core3_protocols = {slug: record for slug, record in core3_protocols.items() if slug in exported_slugs}

    # 6b. Build curator metadata and recent feed entries for the export.
    # Curator data is per-curator (not per-vault), keyed by curator slug.
    curators_export = {}
    unique_curator_slugs = {v.get("curator_slug") for v in vaults if v.get("curator_slug")}
    unique_curator_slugs.discard(None)
    public_url = os.getenv("R2_VAULT_METADATA_PUBLIC_URL", "")

    if feed_db_path is None:
        feed_db_path_env = os.getenv("FEED_DB_PATH") or os.getenv("DB_PATH")
        if feed_db_path_env:
            feed_db_path = Path(feed_db_path_env).expanduser()
        else:
            feed_db_path = DEFAULT_VAULT_POST_DATABASE

    if feed_db_path.exists():
        feed_db = VaultPostDatabase(feed_db_path)
        try:
            print(f"Opened feed database at {feed_db_path}")
            curators_export = build_curators_for_export(
                unique_curator_slugs,
                feed_db=feed_db,
                public_url=public_url,
            )
        finally:
            feed_db.close()
    else:
        curators_export = build_curators_for_export(
            unique_curator_slugs,
            feed_db=None,
            public_url=public_url,
        )

    if sticky_result is not None:
        sticky_protocol_slugs = {v.get("protocol_slug") for v in vaults if v.get("sticky_export") and v.get("protocol_slug")}
        sticky_curator_slugs = {v.get("curator_slug") for v in vaults if v.get("sticky_export") and v.get("curator_slug")}
        sticky_result.stats.missing_protocol_slugs = len(sticky_protocol_slugs - set(core3_protocols.keys()))
        sticky_result.stats.missing_curator_slugs = len(sticky_curator_slugs - set(curators_export.keys()))

    print(f"Built curator export for {len(curators_export)} curators")

    # 7️⃣ Add metadata and deep sanitize.
    # The git version stamp identifies which exporter build produced the file,
    # so stale-deployment issues are diagnosable from the JSON alone.
    version_info = VersionInfo.read_docker_version()
    output_data: VaultMetricsExport = {
        "generated_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": {
            "version": version_info.as_dict(),
        },
        "core3_protocols": core3_protocols,
        "curators": curators_export,
        "vaults": vaults,
    }

    validate_strict_json_serialisable(output_data)
    if sticky_result is not None:
        validate_strict_json_serialisable(sticky_result.state)

    # 7️⃣ Write to JSON file (strict mode)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(output_path), mode="w", overwrite=True, encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False, allow_nan=False)

    if sticky_result is not None:
        assert sticky_state_path is not None
        save_sticky_export_state(sticky_result.state, sticky_state_path)
        print(f"Sticky export state: loaded {sticky_result.stats.loaded_state_entries:,} vault entries from {sticky_state_path}")
        print(f"Current filter passed: {sticky_result.stats.current_filter_passed:,}")
        if sticky_result.stats.previous_current_filter_count is not None and sticky_result.stats.previous_current_filter_count > 0:
            previous_count = sticky_result.stats.previous_current_filter_count
            current_count = sticky_result.stats.current_filter_passed
            if current_count < previous_count * 0.8:
                print(f"WARNING: current filter rows dropped from {previous_count:,} to {current_count:,}")
        print(f"Sticky additions: {sticky_result.stats.sticky_additions:,}")
        print(f"Sticky fallback exports: {sticky_result.stats.sticky_fallback_exports:,}")
        print(f"Current-row structural fallbacks: {sticky_result.stats.current_row_structural_fallbacks:,}")
        print(f"Structurally suppressed vaults: {sticky_result.stats.structurally_suppressed_vaults:,}")
        print(f"Stale warning vaults: {sticky_result.stats.stale_warning_vaults:,}")
        print(f"Missing protocol slugs for sticky rows: {sticky_result.stats.missing_protocol_slugs:,}")
        print(f"Missing curator slugs for sticky rows: {sticky_result.stats.missing_curator_slugs:,}")

    print(f"Exported {len(vaults):,} vault rows to {output_path}")


if __name__ == "__main__":
    main()
