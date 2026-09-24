"""Low-TVL vault metrics freshness gate.

Vault metrics calculation is expensive per vault, and most vaults are tiny:
roughly three quarters of stablecoin-denominated vaults hold less than
USD 5,000. This module implements the freshness gate that recalculates
low-TVL vaults only every :data:`LOW_TVL_METRICS_MAX_AGE` while keeping every
export-relevant vault fresh on every run.

A vault is **low TVL** when its current TVL is below the hardcoded
family threshold (:data:`LOW_TVL_THRESHOLD_USD` /
:data:`LOW_TVL_THRESHOLD_BTC` / :data:`LOW_TVL_THRESHOLD_ETH`). Thresholds
and the max age are deliberately hardcoded module constants, not env vars.

The gate state lives in a small per-bundle JSON file
(``vault-metrics-state.json``, ``crypto-vaults/crypto-vault-metrics-state.json``)
because the export JSONs only contain vaults that passed the export filter:
the never-exported low-TVL vaults are the bulk of the savings and would
otherwise be due on every run.

The due rules are evaluated from one cheap ``groupby("id")`` pass over the
raw price frame, before any metrics work:

1. no state entry (new vault), or
2. current TVL is null/non-finite (safe default), or
3. current TVL >= the family freshness threshold (promotion is immediate), or
4. peak TVL >= the per-vault **export** threshold (the vault can enter or
   re-enter the export, so its row must stay fresh), or
5. ``metrics_updated_at`` plus a deterministic per-vault stagger offset is
   older than :data:`LOW_TVL_METRICS_MAX_AGE`.

Everything else is skipped. Without the stagger, the first full run would
give every low-TVL vault the same timestamp and three days later the entire
cohort would become due in one giant spike run; the offset spreads expiry
evenly across the window with no extra state.

The freshness thresholds are intentionally independent of the export
thresholds (``MIN_TVL`` with per-protocol overrides, crypto
``CRYPTO_VAULTS_MIN_TVL_USD`` converted at guideline rates): the two
mechanisms answer different questions, and due rule 4 uses the real
per-vault export threshold, not the freshness constant.
"""

import datetime
import gc
import hashlib
import json
import logging
import math
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
from atomicwrites import atomic_write

logger = logging.getLogger(__name__)

#: Current TVL below this USD value marks a stablecoin/USD-family vault low TVL.
LOW_TVL_THRESHOLD_USD = 5_000.0

#: Current TVL below this BTC value marks a BTC-family vault low TVL.
LOW_TVL_THRESHOLD_BTC = 0.1

#: Current TVL below this ETH value marks an ETH-family vault low TVL.
LOW_TVL_THRESHOLD_ETH = 2.5

#: Low-TVL vaults are recalculated when their last metrics update is older
#: than this. High-TVL and export-relevant vaults are always recalculated.
LOW_TVL_METRICS_MAX_AGE = datetime.timedelta(days=3)

#: Schema version of the metrics freshness state document.
VAULT_METRICS_STATE_SCHEMA_VERSION = 1

#: Default freshness state filename for the stablecoin bundle.
VAULT_METRICS_STATE_FILENAME = "vault-metrics-state.json"

#: Freshness state filename for the crypto bundle, stored in its bundle directory.
CRYPTO_METRICS_STATE_FILENAME = "crypto-vault-metrics-state.json"

#: Freshness thresholds in family units, keyed by ``DenominationFamily`` value.
#: Unknown or unsupported families fall back to the USD threshold, which only
#: affects how eagerly they are skipped; due rules 1, 2, 4 and a missing
#: ``metrics_updated_at`` still force recomputation.
LOW_TVL_THRESHOLDS_BY_FAMILY: dict[str, float] = {
    "stablecoin": LOW_TVL_THRESHOLD_USD,
    "eth": LOW_TVL_THRESHOLD_ETH,
    "btc": LOW_TVL_THRESHOLD_BTC,
}


def free_memory() -> None:
    """Release freed buffers back to the operating system.

    ``gc.collect()`` alone is not enough under pandas 3, where string
    column buffers belong to Arrow's allocator: memory is freed logically
    but can stay in the allocator and keep RSS high.
    ``pa.default_memory_pool().release_unused()`` returns those buffers to
    the OS. Call after deleting a large frame.

    See `pyarrow.MemoryPool.release_unused
    <https://arrow.apache.org/docs/python/generated/pyarrow.MemoryPool.html#pyarrow.MemoryPool.release_unused>`__.
    """
    gc.collect()
    pa.default_memory_pool().release_unused()


def low_tvl_threshold_for_family(family: str | None) -> float:
    """Resolve the low-TVL freshness threshold for one denomination family.

    Unknown or unsupported families deliberately fall back to the USD
    threshold: the fallback only affects how eagerly such a vault is
    skipped, and every unsafe case (no state entry, null TVL, export
    relevance, missing timestamp) still forces recomputation through the
    other due rules.

    :param family:
        ``DenominationFamily`` value (``stablecoin``, ``eth``, ``btc``), or
        ``None``/unknown for a safe USD fallback.
    :return:
        Freshness threshold in the family unit.
    """
    return LOW_TVL_THRESHOLDS_BY_FAMILY.get(family or "", LOW_TVL_THRESHOLD_USD)


def resolve_metrics_state_path(data_dir: Path) -> Path:
    """Resolve the metrics freshness state path for the stablecoin bundle.

    ``VAULT_METRICS_STATE_PATH`` is an explicit override, following the
    ``VAULT_EXPORT_STATE_PATH`` precedent. Otherwise the state file lives
    under the pipeline data directory. The crypto bundle does not use this
    helper: its state file is always derived from the bundle's sticky-state
    path, so one env override can never point both bundles at the same file.

    :param data_dir:
        Pipeline data directory.
    :return:
        State file path.
    """
    env_path = os.getenv("VAULT_METRICS_STATE_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return data_dir / VAULT_METRICS_STATE_FILENAME


def make_empty_metrics_state(now: datetime.datetime) -> dict:
    """Create an empty freshness state document.

    Used on first deploy, after state loss, and when quarantining a corrupt
    state file; all three cases intentionally degrade to one full
    recomputation run.

    :param now:
        Current naive UTC datetime.
    :return:
        Empty state mapping.
    """
    return {
        "schema_version": VAULT_METRICS_STATE_SCHEMA_VERSION,
        "updated_at": now.isoformat(),
        "vaults": {},
    }


def load_metrics_state(path: Path, now: datetime.datetime) -> dict:
    """Load freshness state, self-healing from a corrupt file.

    A corrupt state file (malformed JSON, wrong schema version, wrong
    structure) is moved aside with a ``.corrupt-<timestamp>`` suffix and an
    empty state is returned: one full recompute follows and the gate applies
    from the next run.

    :param path:
        State file path.
    :param now:
        Current naive UTC datetime.
    :return:
        Loaded state mapping.
    """
    if not path.exists():
        return make_empty_metrics_state(now)
    try:
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            message = "Metrics state must be a JSON object"
            raise ValueError(message)
        if state.get("schema_version") != VAULT_METRICS_STATE_SCHEMA_VERSION:
            message = f"Unsupported metrics state schema version: {state.get('schema_version')!r}"
            raise ValueError(message)
        if not isinstance(state.get("vaults"), dict):
            message = "Metrics state must contain a vaults object"
            raise ValueError(message)
        if any(not isinstance(entry, dict) for entry in state["vaults"].values()):
            message = "Metrics state vault entries must be objects"
            raise ValueError(message)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        quarantine_path = path.with_suffix(f".corrupt-{now.strftime('%Y%m%d%H%M%S')}")
        logger.warning("Metrics state at %s is corrupt (%s); moving it aside to %s and starting empty", path, e, quarantine_path)
        path.replace(quarantine_path)
        return make_empty_metrics_state(now)
    return state


def save_metrics_state(state: dict, path: Path) -> None:
    """Atomically write freshness state.

    The write goes through the same ``atomicwrites`` pattern as the sticky
    export state, so a crash mid-write never leaves a truncated state file
    behind.

    :param state:
        State mapping.
    :param path:
        Destination path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True, encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, allow_nan=False)


def _finite_or_none(value: object) -> float | None:
    """Convert one observed TVL value to a finite float or ``None``.

    Missing, null, non-numeric and non-finite (NaN, infinity) observations
    all become ``None`` so the due rules can treat them with their safe
    default. Used for both fresh price-frame observations and values read
    back from the state file.

    :param value:
        Raw observation (numpy scalar, pandas NA, float, int, ``None``).
    :return:
        Finite float, or ``None`` when no finite value exists.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def compute_vault_tvl_observations(prices_df: pd.DataFrame) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """Cheap per-vault current and peak TVL from raw price rows.

    One ``groupby("id")`` pass over the price frame: the last non-null
    ``total_assets`` observation is the current TVL and the maximum is the
    peak TVL. No metrics calculation is needed for the due/skip decision.
    The frame may be unfiltered (every denomination): the lookups simply
    cover every vault in it, and callers restrict the due/skip decision to
    their own vault set afterwards. When the ``total_assets`` column is
    missing, empty lookups are returned so every vault falls back to the
    safe "due" default.

    :param prices_df:
        Vault price rows with ``id`` and ``total_assets`` columns.
    :return:
        ``(current_tvl_by_id, peak_tvl_by_id)`` with finite floats or
        ``None`` per vault.
    """
    if "total_assets" not in prices_df.columns:
        return {}, {}
    grouped = prices_df.groupby("id")["total_assets"]
    current_by_id = {str(key): _finite_or_none(value) for key, value in grouped.last().items()}
    peak_by_id = {str(key): _finite_or_none(value) for key, value in grouped.max().items()}
    return current_by_id, peak_by_id


def _stagger_offset(vault_id: str) -> datetime.timedelta:
    """Deterministic per-vault expiry offset spreading the low-TVL cohort.

    Uses SHA-256 instead of the built-in ``hash()`` because ``hash()`` is
    randomised per process (``PYTHONHASHSEED``) and the offset must be stable
    across scanner restarts.

    :param vault_id:
        Canonical vault id (``<chain>-<address>``).
    :return:
        Offset in ``[0, LOW_TVL_METRICS_MAX_AGE)``.
    """
    digest = hashlib.sha256(vault_id.encode("utf-8")).hexdigest()
    offset_seconds = int(digest[:8], 16) % int(LOW_TVL_METRICS_MAX_AGE.total_seconds())
    return datetime.timedelta(seconds=offset_seconds)


def _parse_metrics_updated_at(value: object) -> datetime.datetime | None:
    """Parse one stored ``metrics_updated_at`` timestamp.

    Missing or malformed values, and values with a timezone offset (which
    are converted to naive UTC), are handled without raising: a malformed
    timestamp simply makes the vault due.

    :param value:
        Raw state value.
    :return:
        Naive UTC datetime, or ``None`` for missing/malformed values.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def partition_due_vault_ids(  # noqa: PLR0917
    seen_ids: set[str],
    state: dict,
    current_tvl_by_id: dict[str, float | None],
    peak_tvl_by_id: dict[str, float | None],
    family_by_id: dict[str, str],
    export_threshold_by_id: dict[str, float],
    now: datetime.datetime,
    patchable_ids: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Split vaults into due (recalculate) and skipped (keep previous record).

    See the module docstring for the due rules. The optional
    ``patchable_ids`` precondition is used by the crypto bundle, which has no
    sticky record replay: a vault may only be skipped when a validated
    previous export record exists to patch into the output. The stablecoin
    bundle passes ``None`` because its sticky export state replays skipped
    vaults automatically.

    :param seen_ids:
        Vault ids present in the current price data.
    :param state:
        Loaded freshness state mapping.
    :param current_tvl_by_id:
        Current TVL per vault from :func:`compute_vault_tvl_observations`.
    :param peak_tvl_by_id:
        Peak TVL per vault from :func:`compute_vault_tvl_observations`.
    :param family_by_id:
        ``DenominationFamily`` value per vault.
    :param export_threshold_by_id:
        Per-vault export threshold (with protocol overrides) in the vault's
        denomination unit.
    :param now:
        Current naive UTC datetime.
    :param patchable_ids:
        Optional set of vault ids with a validated previous record to patch.
    :return:
        ``(due_ids, skipped_ids)`` partitioning ``seen_ids``.
    """
    state_vaults = state.get("vaults", {})
    due_ids: set[str] = set()
    skipped_ids: set[str] = set()

    for vault_id in seen_ids:
        entry = state_vaults.get(vault_id)
        current_tvl = _finite_or_none(current_tvl_by_id.get(vault_id))
        peak_tvl = _finite_or_none(peak_tvl_by_id.get(vault_id))
        family = family_by_id.get(vault_id)
        export_threshold = export_threshold_by_id.get(vault_id, LOW_TVL_THRESHOLD_USD)

        due = False
        if entry is None:
            due = True
        elif patchable_ids is not None and vault_id not in patchable_ids:
            due = True
        elif current_tvl is None:
            due = True
        elif current_tvl >= low_tvl_threshold_for_family(family):
            due = True
        elif peak_tvl is not None and peak_tvl >= export_threshold:
            due = True
        else:
            updated_at = _parse_metrics_updated_at(entry.get("metrics_updated_at"))
            if updated_at is None or updated_at > now:
                due = True
            else:
                effective_age = now - updated_at - _stagger_offset(vault_id)
                due = effective_age > LOW_TVL_METRICS_MAX_AGE

        if due:
            due_ids.add(vault_id)
        else:
            skipped_ids.add(vault_id)

    return due_ids, skipped_ids


def refresh_metrics_state(  # noqa: PLR0917
    state: dict,
    seen_ids: set[str],
    computed_ids: set[str],
    current_tvl_by_id: dict[str, float | None],
    peak_tvl_by_id: dict[str, float | None],
    family_by_id: dict[str, str],
    now: datetime.datetime,
) -> dict:
    """Refresh the freshness state after one metrics run.

    ``metrics_updated_at`` advances **only** for vault ids actually returned
    in the metrics frame: a vault that failed metric calculation stays due
    next run instead of being stamped fresh for three days. The cheap TVL
    observations are refreshed for every vault seen in the price data, and
    vaults no longer present in the price data are dropped.

    :param state:
        Mutable loaded state mapping.
    :param seen_ids:
        Vault ids present in the current price data.
    :param computed_ids:
        Vault ids actually returned by the metrics calculation.
    :param current_tvl_by_id:
        Current TVL per vault.
    :param peak_tvl_by_id:
        Peak TVL per vault.
    :param family_by_id:
        ``DenominationFamily`` value per vault.
    :param now:
        Current naive UTC datetime.
    :return:
        The mutated state mapping.
    """
    previous_vaults = state.get("vaults", {})
    vaults: dict[str, dict] = {}
    for vault_id in seen_ids:
        previous_entry = previous_vaults.get(vault_id) or {}
        if vault_id in computed_ids:
            metrics_updated_at = now.isoformat()
        else:
            metrics_updated_at = previous_entry.get("metrics_updated_at")
        vaults[vault_id] = {
            "metrics_updated_at": metrics_updated_at,
            "current_tvl": _finite_or_none(current_tvl_by_id.get(vault_id)),
            "peak_tvl": _finite_or_none(peak_tvl_by_id.get(vault_id)),
            "denomination_family": family_by_id.get(vault_id),
        }
    state["vaults"] = vaults
    state["updated_at"] = now.isoformat()
    return state


def clear_period_rankings(record: dict) -> dict:
    """Clear ranking fields on a replayed or patched export record.

    Rankings are computed over the vaults that were recalculated in the same
    run, so a record replayed from an earlier run carries ranks from a
    different vault universe and would produce duplicate, non-comparable
    curator ranks. Cleared in place; skipped vaults show no current ranks.

    :param record:
        Exported vault JSON row with a ``period_results`` list.
    :return:
        The same record, mutated.
    """
    period_results = record.get("period_results")
    if isinstance(period_results, list):
        record["period_results"] = [
            {
                **period,
                "ranking_overall": None,
                "ranking_chain": None,
                "ranking_protocol": None,
                "ranking_curator": None,
            }
            if isinstance(period, dict)
            else period
            for period in period_results
        ]
    return record


def load_valid_previous_crypto_records(
    metadata_path: Path,
    *,
    schema_version: int,
    whitelist_sha256: str,
) -> dict[str, dict]:
    """Load previous crypto-bundle records that are safe to patch.

    A previous record may only be re-attached when the whole document is
    compatible with the current process: matching ``schema_version`` and
    ``denomination_whitelist_sha256``. A missing, unreadable or mismatched
    document yields an empty mapping so every affected vault is recomputed.
    Per-record family and threshold validation stays at the call site, which
    owns the vault metadata and threshold inputs.

    :param metadata_path:
        Previous ``crypto-vault-metadata.json`` path, read before it is
        overwritten.
    :param schema_version:
        Current ``CRYPTO_VAULTS_SCHEMA_VERSION``.
    :param whitelist_sha256:
        Current :func:`get_denomination_whitelist_digest` value.
    :return:
        Mapping of vault id to previous record; empty when the document
        cannot be trusted.
    """
    if not metadata_path.exists():
        return {}
    try:
        document = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read previous crypto metadata at %s (%s); affected vaults will be recomputed", metadata_path, e)
        return {}
    if not isinstance(document, dict):
        return {}
    if document.get("schema_version") != schema_version:
        return {}
    if document.get("denomination_whitelist_sha256") != whitelist_sha256:
        return {}
    vaults = document.get("vaults")
    if not isinstance(vaults, list):
        return {}
    records: dict[str, dict] = {}
    for record in vaults:
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records[record["id"]] = record
    return records
