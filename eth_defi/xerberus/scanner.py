"""Xerberus risk scanner with DuckDB storage.

Orchestrates fetching registry scores and optional vault lists from the
Xerberus public REST API and storing them in
:py:class:`~eth_defi.xerberus.database.XerberusDatabase`.

HTTP is serial and paced (see session rate limits). Do not fan out parallel
authenticated calls under the public 10 requests / 60 s quota.

Example::

    import os

    from eth_defi.xerberus.session import create_xerberus_session
    from eth_defi.xerberus.scanner import scan_xerberus

    # Pass api_email explicitly when known; never invent the registered email.
    session = create_xerberus_session(
        api_key=os.environ["XERBERUS_API_KEY"],
        api_email=os.environ["XERBERUS_API_EMAIL"],
    )
    db = scan_xerberus(session=session)
    try:
        print(db.get_entity_counts())
    finally:
        db.close()
"""

import datetime
import logging
from pathlib import Path

import requests

from eth_defi.compat import native_datetime_utc_now
from eth_defi.xerberus.api import (
    fetch_healthcheck,
    fetch_registry_scores,
    fetch_vault_list,
    fetch_vault_report_url,
)
from eth_defi.xerberus.constants import (
    XERBERUS_DATABASE_PATH,
    XERBERUS_DEFAULT_REPORT_LIMIT,
    XERBERUS_DEFAULT_TIMEOUT,
    XERBERUS_VAULT_PLATFORMS,
)
from eth_defi.xerberus.database import XerberusDatabase
from eth_defi.xerberus.errors import XerberusAPIError
from eth_defi.xerberus.session import XerberusSession

logger = logging.getLogger(__name__)


def scan_xerberus(
    session: XerberusSession,
    db_path: Path = XERBERUS_DATABASE_PATH,
    fetch_vault_lists: bool = True,
    fetch_reports: bool = True,
    report_limit: int = XERBERUS_DEFAULT_REPORT_LIMIT,
    registry_types: tuple[str, ...] = ("pool", "protocol"),
    timeout: float = XERBERUS_DEFAULT_TIMEOUT,
    dry_run: bool = False,
) -> XerberusDatabase | None:
    """Scan Xerberus registry (and optional vault lists) into DuckDB.

    1. Soft pre-flight registry healthcheck
    2. Fetch registry scores for pool + protocol
    3. Insert snapshots and upsert ``score_daily``
    4. Optionally fetch per-platform vault lists
    5. Optionally backfill a capped number of report URLs

    Caller must :py:meth:`~eth_defi.xerberus.database.XerberusDatabase.close`
    the returned database (unless ``dry_run``).

    :param session:
        Authenticated Xerberus session.
    :param db_path:
        DuckDB path.
    :param fetch_vault_lists:
        When ``True``, poll ``/vault/list`` for each known platform.
    :param fetch_reports:
        When ``True``, fetch dendrogram URLs for a capped set of pools.
    :param report_limit:
        Max report download calls this run.
    :param registry_types:
        Entity types passed to ``/registry/scores``.
    :param timeout:
        HTTP timeout seconds.
    :param dry_run:
        When ``True``, log the plan and return ``None`` without writing.
    :return:
        Open :class:`XerberusDatabase`, or ``None`` on dry run.
    """
    if dry_run:
        logger.info(
            "DRY_RUN Xerberus scan: registry_types=%s fetch_vault_list=%s platforms=%s fetch_reports=%s report_limit=%s db_path=%s",
            registry_types,
            fetch_vault_lists,
            XERBERUS_VAULT_PLATFORMS if fetch_vault_lists else (),
            fetch_reports,
            report_limit,
            db_path,
        )
        return None

    # Soft pre-flight
    try:
        health = fetch_healthcheck(session, group="registry", timeout=timeout)
        logger.info("Xerberus registry healthcheck: %s", health.strip()[:80])
    except (requests.RequestException, XerberusAPIError) as e:
        logger.warning("Xerberus registry healthcheck failed (continuing): %s", e)

    fetched_at = native_datetime_utc_now()
    db = XerberusDatabase(db_path)

    try:
        entities = fetch_registry_scores(session, types=registry_types, timeout=timeout)
        inserted = db.insert_registry_snapshot_batch(entities, fetched_at)
        logger.info("Inserted %d registry snapshot rows from %d entities", inserted, len(entities))

        daily_points: list[dict] = []
        for raw in entities:
            entity_type = raw.get("type")
            entity_id = raw.get("id")
            if not entity_type or not entity_id:
                continue
            score = raw.get("score")
            if score is None:
                continue
            chain_id = raw.get("chainId")
            address = raw.get("address")
            daily_points.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "day": fetched_at.date(),
                    "score": score,
                    "fetched_at": fetched_at,
                    "chain_id": int(chain_id) if chain_id is not None else None,
                    "address": address.lower() if isinstance(address, str) else None,
                }
            )
        daily_count = db.upsert_score_daily_points(daily_points)
        logger.info("Upserted %d score_daily rows", daily_count)
        db.update_sync_state("registry", meta={"entities": len(entities), "inserted": inserted, "score_daily": daily_count})

        if fetch_vault_lists:
            for platform in XERBERUS_VAULT_PLATFORMS:
                try:
                    vaults = fetch_vault_list(session, platform=platform, timeout=timeout)
                    n = db.insert_vault_list_snapshot_batch(platform, vaults, fetched_at)
                    logger.info("Platform %s: inserted %d vault list rows", platform, n)
                    db.update_sync_state(f"vault_list:{platform}", meta={"count": n})
                except (requests.RequestException, XerberusAPIError) as e:
                    logger.warning("Vault list for platform %s failed (continuing): %s", platform, e)

        if fetch_reports and report_limit > 0:
            _backfill_reports(session, db, fetched_at=fetched_at, limit=report_limit, timeout=timeout)

        db.save()
        counts = db.get_entity_counts()
        logger.info("Xerberus scan complete: %s", counts)
        return db
    except Exception:
        db.close()
        raise


def _backfill_reports(
    session: XerberusSession,
    db: XerberusDatabase,
    *,
    fetched_at: datetime.datetime,
    limit: int,
    timeout: float,
) -> None:
    """Fetch a capped number of dendrogram report URLs for registry pools."""
    pools = db.get_latest_registry_entities(entity_type="pool")
    if len(pools) == 0:
        return

    fetched = 0
    for _, row in pools.iterrows():
        if fetched >= limit:
            break
        chain_id = row.get("chain_id")
        address = row.get("address")
        entity_id = row.get("entity_id")
        if chain_id is None or not address:
            continue
        existing = db.get_report_url(int(chain_id), str(address))
        if existing:
            continue
        # Prefer constructible app URL when we have entity_id
        if entity_id and str(entity_id).startswith("pool_"):
            url = f"https://app.xerberus.io/pool/dendrogram/{entity_id}"
            db.upsert_report_url(int(chain_id), str(address), url, fetched_at, entity_id=str(entity_id))
            fetched += 1
            continue
        try:
            url = fetch_vault_report_url(session, str(address), timeout=timeout)
            db.upsert_report_url(
                int(chain_id),
                str(address),
                url,
                fetched_at,
                entity_id=str(entity_id) if entity_id else None,
                error=None if url else "not_found",
            )
            fetched += 1
        except (requests.RequestException, XerberusAPIError) as e:
            db.upsert_report_url(
                int(chain_id),
                str(address),
                None,
                fetched_at,
                entity_id=str(entity_id) if entity_id else None,
                error=str(e),
            )
            fetched += 1
            logger.warning("Report fetch failed for %s: %s", address, e)

    db.update_sync_state("reports", meta={"fetched": fetched})
    logger.info("Report backfill: %d URLs processed", fetched)
