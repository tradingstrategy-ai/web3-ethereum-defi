"""Core3 project scanner with DuckDB storage.

Orchestrates fetching data from the Core3 API and storing it in a
:py:class:`~eth_defi.core3.database.Core3Database`. Supports parallel
fetching with ``joblib.Parallel`` and incremental sync using watermarks.

Example usage::

    from pathlib import Path
    from eth_defi.core3.session import create_core3_session
    from eth_defi.core3.scanner import scan_projects

    session = create_core3_session()
    db = scan_projects(session=session, limit=10)
    try:
        df = db.get_latest_project_snapshots()
        print(df)
    finally:
        db.close()
"""

import datetime
import logging
import time
from collections.abc import Callable
from pathlib import Path

import requests
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.core3.constants import CORE3_DATABASE_PATH, INDEX_SLUG, SECTIONS
from eth_defi.core3.database import Core3Database
from eth_defi.core3.api import (
    fetch_index_pol_history,
    fetch_index_pol_history_incremental,
    fetch_pol_category_history,
    fetch_pol_category_history_incremental,
    fetch_pol_history,
    fetch_pol_history_incremental,
    fetch_project_detail,
    fetch_project_list,
    fetch_section_detail,
)
from eth_defi.core3.session import Core3Session

logger = logging.getLogger(__name__)


def _safe_fetch(slug: str, endpoint: str, fn: Callable):
    """Call *fn* and return the result, or ``None`` on recoverable errors.

    401/403 errors are re-raised (auth / Cloudflare block).
    All other HTTP, network, and parse errors are logged and swallowed
    so the scan can continue with the remaining endpoints.

    :param slug:
        Project slug (for log messages).
    :param endpoint:
        Human-readable endpoint name (for log messages).
    :param fn:
        Zero-argument callable that performs the fetch (and optional DB write).
    :return:
        Whatever *fn* returns, or ``None`` on error.
    """
    try:
        return fn()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403):
            raise
        elif e.response is not None and e.response.status_code == 404:
            logger.warning("Project %s: %s not found, skipping", slug, endpoint)
        else:
            status = e.response.status_code if e.response is not None else "unknown"
            logger.error("Project %s: %s HTTP %s, skipping", slug, endpoint, status)
    except (requests.Timeout, requests.ConnectionError) as e:
        logger.error("Project %s: %s network error (%s), skipping", slug, endpoint, type(e).__name__)
    except requests.RequestException as e:
        logger.error("Project %s: %s request failed (%s), skipping", slug, endpoint, e)
    except (ValueError, KeyError) as e:
        logger.error("Project %s: %s bad response (%s), skipping", slug, endpoint, e)
    return None


def _sync_time_series(
    db: Core3Database,
    session: Core3Session,
    slug: str,
    data_type: str,
    fetch_backfill: Callable,
    fetch_incremental: Callable,
    insert_fn: Callable,
    fetched_at: datetime.datetime,
) -> int:
    """Two-phase sync helper for PoL time-series data.

    Phase 1 (backfill): if no sync state exists or ``backfill_done`` is
    ``FALSE``, calls the chart endpoint for all-time data.

    Phase 2 (incremental): if ``backfill_done`` is ``TRUE``, calls the
    ranged history endpoint from the last known timestamp to now.

    :param db:
        Core3 database.
    :param session:
        Core3 API session.
    :param slug:
        Project slug (or ``INDEX_SLUG``).
    :param data_type:
        Sync state key (e.g. ``'pol_daily'``).
    :param fetch_backfill:
        Callable for the full chart endpoint. Signature: ``() -> list[dict]``.
    :param fetch_incremental:
        Callable for the ranged endpoint. Signature: ``(from_ts, to_ts) -> list[dict]``.
    :param insert_fn:
        Database insert function. Signature: ``(slug, points, fetched_at) -> int``.
    :param fetched_at:
        Timestamp of the current scan cycle.
    :return:
        Number of new rows inserted.
    """
    state = db.get_sync_state(slug, data_type)
    now_ts = int(time.time())

    if state is None or not state["backfill_done"]:
        points = fetch_backfill()
    else:
        from_ts = state["last_ts"] or 0
        points = fetch_incremental(from_ts, now_ts)

    new_count = insert_fn(slug, points, fetched_at)

    last_ts = max((p["timestamp"] for p in points), default=None) if points else (state["last_ts"] if state else None)
    db.update_sync_state(slug, data_type, last_ts, backfill_done=True)

    return new_count


def _process_project(
    session: Core3Session,
    db: Core3Database,
    slug: str,
    fetched_at: datetime.datetime,
    fetch_pol: bool,
    fetch_categories: bool,
    fetch_sections_flag: bool,
    timeout: float,
) -> None:
    """Worker function to process a single project.

    Each endpoint call is wrapped in :func:`_safe_fetch` so that a failure
    on one endpoint does not prevent the others from succeeding.

    :param session:
        Core3 API session.
    :param db:
        Core3 database.
    :param slug:
        Project slug.
    :param fetched_at:
        Timestamp of the current scan cycle.
    :param fetch_pol:
        Whether to fetch PoL history.
    :param fetch_categories:
        Whether to fetch category PoL history.
    :param fetch_sections_flag:
        Whether to fetch section detail endpoints.
    :param timeout:
        HTTP request timeout.
    """
    # 1. Project detail snapshot
    raw = _safe_fetch(slug, "detail", lambda: fetch_project_detail(session, slug, timeout=timeout))
    if raw is not None:
        db.insert_project_snapshot(slug, fetched_at, raw)

    # 2. PoL daily history
    if fetch_pol:
        _safe_fetch(
            slug,
            "PoL history",
            lambda: _sync_time_series(
                db,
                session,
                slug,
                "pol_daily",
                fetch_backfill=lambda: fetch_pol_history(session, slug, timeout=timeout),
                fetch_incremental=lambda f, t: fetch_pol_history_incremental(session, slug, f, t, timeout=timeout),
                insert_fn=db.insert_pol_daily_points,
                fetched_at=fetched_at,
            ),
        )

    # 3. Category PoL daily history
    if fetch_categories:
        _safe_fetch(
            slug,
            "category history",
            lambda: _sync_time_series(
                db,
                session,
                slug,
                "pol_category_daily",
                fetch_backfill=lambda: fetch_pol_category_history(session, slug, timeout=timeout),
                fetch_incremental=lambda f, t: fetch_pol_category_history_incremental(session, slug, f, t, timeout=timeout),
                insert_fn=db.insert_pol_category_daily_points,
                fetched_at=fetched_at,
            ),
        )

    # 4. Section snapshots
    if fetch_sections_flag:
        for section in SECTIONS:
            raw = _safe_fetch(slug, f"{section} section", lambda s=section: fetch_section_detail(session, slug, s, timeout=timeout))
            if raw is not None:
                db.insert_section_snapshot(slug, section, fetched_at, raw)


def scan_projects(
    session: Core3Session,
    db_path: Path = CORE3_DATABASE_PATH,
    fetch_sections: bool = False,
    fetch_pol_history: bool = True,
    fetch_category_history: bool = True,
    fetch_index_pol: bool = True,
    limit: int | None = None,
    max_workers: int = 8,
    timeout: float = 30.0,
) -> Core3Database:
    """Scan all Core3 projects and store snapshots in DuckDB.

    This function:

    1. Fetches the project list from ``/v1/list`` to get all slugs
    2. For each slug (parallelised with ``joblib``):

       a. Fetches ``/v1/{slug}`` and inserts into ``project_snapshots``
       b. Optionally fetches PoL history and inserts into ``pol_daily``
       c. Optionally fetches category history and inserts into ``pol_category_daily``
       d. Optionally fetches section endpoints and inserts into ``section_snapshots``

    3. Optionally fetches index-level PoL history

    :param session:
        Core3 API session. Use :py:func:`~eth_defi.core3.session.create_core3_session`
        to create one.
    :param db_path:
        Path to the DuckDB database file.
    :param fetch_sections:
        If ``True``, also fetch section detail endpoints (security, financial, etc.).
        This is slower (5 extra API calls per project).
    :param fetch_pol_history:
        If ``True``, fetch PoL daily history for each project.
    :param fetch_category_history:
        If ``True``, fetch category PoL breakdown history for each project.
    :param fetch_index_pol:
        If ``True``, fetch the aggregate index-level PoL history.
    :param limit:
        Limit the number of projects to scan. For testing only.
    :param max_workers:
        Maximum number of parallel workers for fetching project data.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        :py:class:`~eth_defi.core3.database.Core3Database` instance with
        the newly inserted data. Caller must call ``close()`` when done.
    """
    fetched_at = native_datetime_utc_now()

    db = Core3Database(db_path)

    # Fetch project list — hard fail if this fails (no meaningful scan without it)
    project_list = fetch_project_list(session, timeout=timeout)
    slugs = [p["slug"] for p in project_list]
    logger.info("Fetched %d projects from Core3 API", len(slugs))

    if limit is not None:
        slugs = slugs[:limit]
        logger.info("Limited to %d projects", len(slugs))

    # Parallel per-project processing
    desc = f"Scanning Core3 projects ({max_workers} workers)"
    Parallel(n_jobs=max_workers, backend="threading")(
        delayed(_process_project)(
            session,
            db,
            slug,
            fetched_at,
            fetch_pol=fetch_pol_history,
            fetch_categories=fetch_category_history,
            fetch_sections_flag=fetch_sections,
            timeout=timeout,
        )
        for slug in tqdm(slugs, desc=desc)
    )

    # Index-level PoL history (single request, not parallelised)
    if fetch_index_pol:
        _safe_fetch(
            INDEX_SLUG,
            "index PoL history",
            lambda: _sync_time_series(
                db,
                session,
                INDEX_SLUG,
                "pol_daily",
                fetch_backfill=lambda: fetch_index_pol_history(session, timeout=timeout),
                fetch_incremental=lambda f, t: fetch_index_pol_history_incremental(session, f, t, timeout=timeout),
                insert_fn=db.insert_pol_daily_points,
                fetched_at=fetched_at,
            ),
        )

    db.save()
    logger.info(
        "Scan complete: %d projects, %d snapshots, %d PoL daily rows",
        db.get_project_count(),
        db.get_snapshot_count(),
        db.get_pol_daily_count(),
    )
    return db
