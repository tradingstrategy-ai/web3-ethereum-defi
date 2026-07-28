"""Export helpers for Xerberus risk data into vault metrics JSON.

Builds:

- Preloaded pool lookup map for O(1) per-vault resolve
- Compact :class:`XerberusVaultSection` on each vault row
- Top-level :class:`XerberusProtocolExportRecord` map (like ``core3_protocols``)
"""

import datetime
import logging
from collections.abc import Iterable
from typing import Literal, TypedDict

from eth_defi.xerberus.constants import (
    XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS,
    XERBERUS_SCORE_SCALE,
)
from eth_defi.xerberus.database import XerberusDatabase
from eth_defi.xerberus.mappings import XERBERUS_PROTOCOL_MAPPINGS

logger = logging.getLogger(__name__)


class XerberusPoolLookupRow(TypedDict):
    """Preloaded per-vault score row for O(1) resolve during metrics export."""

    #: EIP-155 chain id.
    chain_id: int

    #: Lowercased EVM vault address.
    address: str

    #: Composite score 0–100, or None if unscored.
    score: float | None

    #: Xerberus entity id (registry pool id or vault_list vault_id).
    entity_id: str

    #: Display name; never used as join key.
    name: str | None

    #: ISO 8601 snapshot timestamp.
    fetched_at: str

    #: Which table supplied this row.
    source: Literal["registry", "vault_list"]

    #: Optional dendrogram deep link when known.
    report_url: str | None


class XerberusVaultSection(TypedDict):
    """Compact per-vault Xerberus risk summary on each vault metrics row."""

    #: Composite score 0–100, or None.
    score: float | None

    #: Always :py:data:`~eth_defi.xerberus.constants.XERBERUS_SCORE_SCALE`.
    score_scale: str

    #: ``pool`` when vault-specific; ``protocol`` on fallback.
    entity_type: Literal["pool", "protocol"]

    #: Xerberus entity id.
    entity_id: str

    #: Display name.
    name: str | None

    #: Our protocol slug when ``entity_type`` is ``protocol``.
    protocol_slug: str | None

    #: Dendrogram report URL when known.
    report_url: str | None

    #: ISO 8601 snapshot timestamp.
    fetched_at: str


class XerberusProtocolExportRecord(TypedDict):
    """Top-level protocol metadata in ``xerberus_protocols`` (like Core3)."""

    #: Our vault protocol slug (same as map key).
    protocol_slug: str

    #: Xerberus registry protocol id.
    entity_id: str

    #: Protocol display name.
    name: str | None

    #: Composite score 0–100, or None.
    score: float | None

    #: Always :py:data:`~eth_defi.xerberus.constants.XERBERUS_SCORE_SCALE`.
    score_scale: str

    #: ISO 8601 snapshot timestamp.
    fetched_at: str


class XerberusExportStats(TypedDict):
    """Optional coverage summary for the exported vault list."""

    #: Number of vaults considered.
    total_vaults: int

    #: Vaults with a pool-level score.
    pool_matches: int

    #: Vaults that fell back to protocol score only.
    protocol_fallbacks: int

    #: Vaults with no Xerberus section.
    unmatched: int

    #: Percentage of vaults with any Xerberus match (pool or protocol).
    coverage_pct: float


def _format_fetched_at(value: object) -> str:
    """Serialise a timestamp to ISO 8601 string."""
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().isoformat()
    return str(value)


def build_xerberus_pool_lookup(
    db: XerberusDatabase,
    max_age_days: int | None = XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS,
) -> dict[tuple[int, str], XerberusPoolLookupRow]:
    """Build a ``(chain_id, address)`` → score map for per-vault export.

    Merge precedence:

    1. Registry pool with non-null score always wins.
    2. Vault list is used only when the key is missing or registry score is null.

    :param db:
        Open Xerberus database (preferably read-only).
    :param max_age_days:
        Drop snapshots older than this many days.
    :return:
        Lookup dict keyed by ``(chain_id, address_lower)``.
    """
    lookup: dict[tuple[int, str], XerberusPoolLookupRow] = {}

    registry_pools = db.get_latest_registry_entities(entity_type="pool", max_age_days=max_age_days)
    for _, row in registry_pools.iterrows():
        chain_id = row.get("chain_id")
        address = row.get("address")
        if chain_id is None or not address:
            continue
        key = (int(chain_id), str(address).lower())
        entity_id = str(row["entity_id"])
        # Per-row lookup is fine: Xerberus DuckDB is local, small, and typically
        # memory-backed for this export path (not a remote round-trip).
        report_url = db.get_report_url(int(chain_id), str(address))
        if report_url is None and entity_id.startswith("pool_"):
            report_url = f"https://app.xerberus.io/pool/dendrogram/{entity_id}"
        lookup[key] = {
            "chain_id": int(chain_id),
            "address": str(address).lower(),
            "score": float(row["score"]) if row.get("score") is not None else None,
            "entity_id": entity_id,
            "name": row.get("name"),
            "fetched_at": _format_fetched_at(row["fetched_at"]),
            "source": "registry",
            "report_url": report_url,
        }

    vault_list = db.get_latest_vault_list(max_age_days=max_age_days)
    for _, row in vault_list.iterrows():
        chain_id = row.get("chain_id")
        address = row.get("address")
        if chain_id is None or not address:
            continue
        key = (int(chain_id), str(address).lower())
        existing = lookup.get(key)
        score = float(row["score"]) if row.get("score") is not None else None
        if existing is None or (existing.get("score") is None and score is not None):
            # Same local DuckDB note as above: per-row get_report_url is cheap here.
            lookup[key] = {
                "chain_id": int(chain_id),
                "address": str(address).lower(),
                "score": score,
                "entity_id": str(row["vault_id"]),
                "name": row.get("name"),
                "fetched_at": _format_fetched_at(row["fetched_at"]),
                "source": "vault_list",
                "report_url": existing.get("report_url") if existing else db.get_report_url(int(chain_id), str(address)),
            }

    logger.info("Built Xerberus pool lookup with %d keys", len(lookup))
    return lookup


def build_xerberus_protocols_for_export(
    db: XerberusDatabase,
    protocol_slugs: Iterable[str],
    max_age_days: int | None = XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS,
) -> dict[str, XerberusProtocolExportRecord]:
    """Build protocol-slug-keyed Xerberus records for top-level JSON export.

    :param db:
        Open Xerberus database.
    :param protocol_slugs:
        Our vault protocol slugs to look up.
    :param max_age_days:
        Max snapshot age.
    :return:
        Dict mapping our protocol slug to export records. Unmapped / missing
        / stale slugs are excluded.
    """
    result: dict[str, XerberusProtocolExportRecord] = {}
    protocol_slugs = set(protocol_slugs)

    for slug in protocol_slugs:
        xerberus_id = XERBERUS_PROTOCOL_MAPPINGS.get(slug)
        if not xerberus_id:
            continue
        row = db.get_latest_protocol_by_id(xerberus_id, max_age_days=max_age_days)
        if row is None:
            continue
        result[slug] = {
            "protocol_slug": slug,
            "entity_id": row["entity_id"],
            "name": row.get("name"),
            "score": float(row["score"]) if row.get("score") is not None else None,
            "score_scale": XERBERUS_SCORE_SCALE,
            "fetched_at": _format_fetched_at(row["fetched_at"]),
        }

    logger.info("Built Xerberus protocol export for %d / %d slugs", len(result), len(protocol_slugs))
    return result


def resolve_xerberus_vault_section(
    chain_id: int,
    address: str,
    protocol_slug: str | None,
    pools: dict[tuple[int, str], XerberusPoolLookupRow],
    protocols: dict[str, XerberusProtocolExportRecord],
) -> XerberusVaultSection | None:
    """Resolve compact per-vault Xerberus section using canonical lookup order.

    1. Pool map hit with non-null score (or any pool hit with entity id)
    2. Protocol fallback via ``protocols[protocol_slug]``

    :param chain_id:
        Vault chain id.
    :param address:
        Vault address (any case).
    :param protocol_slug:
        Our protocol slug for fallback.
    :param pools:
        Preloaded pool lookup from :func:`build_xerberus_pool_lookup`.
    :param protocols:
        Preloaded protocol export map.
    :return:
        Compact section, or ``None`` if nothing matched.
    """
    key = (int(chain_id), str(address).lower())
    pool = pools.get(key)
    if pool is not None and pool.get("score") is not None:
        return {
            "score": pool["score"],
            "score_scale": XERBERUS_SCORE_SCALE,
            "entity_type": "pool",
            "entity_id": pool["entity_id"],
            "name": pool.get("name"),
            "protocol_slug": None,
            "report_url": pool.get("report_url"),
            "fetched_at": pool["fetched_at"],
        }

    # Pool row exists but null score — still prefer pool identity if no protocol score
    if protocol_slug and protocol_slug in protocols:
        proto = protocols[protocol_slug]
        if proto.get("score") is not None or pool is None:
            return {
                "score": proto.get("score"),
                "score_scale": XERBERUS_SCORE_SCALE,
                "entity_type": "protocol",
                "entity_id": proto["entity_id"],
                "name": proto.get("name"),
                "protocol_slug": protocol_slug,
                "report_url": None,
                "fetched_at": proto["fetched_at"],
            }

    if pool is not None:
        return {
            "score": pool.get("score"),
            "score_scale": XERBERUS_SCORE_SCALE,
            "entity_type": "pool",
            "entity_id": pool["entity_id"],
            "name": pool.get("name"),
            "protocol_slug": None,
            "report_url": pool.get("report_url"),
            "fetched_at": pool["fetched_at"],
        }

    return None


def compute_xerberus_export_stats(
    vaults: list[dict],
) -> XerberusExportStats:
    """Compute coverage stats from exported vault records.

    :param vaults:
        Vault metric dicts that may contain a ``xerberus`` key.
    :return:
        Coverage statistics.
    """
    total = len(vaults)
    pool_matches = 0
    protocol_fallbacks = 0
    for vault in vaults:
        section = vault.get("xerberus")
        if not section:
            continue
        if section.get("entity_type") == "pool":
            pool_matches += 1
        elif section.get("entity_type") == "protocol":
            protocol_fallbacks += 1
    matched = pool_matches + protocol_fallbacks
    unmatched = total - matched
    coverage = (matched / total * 100.0) if total else 0.0
    return {
        "total_vaults": total,
        "pool_matches": pool_matches,
        "protocol_fallbacks": protocol_fallbacks,
        "unmatched": unmatched,
        "coverage_pct": round(coverage, 2),
    }
