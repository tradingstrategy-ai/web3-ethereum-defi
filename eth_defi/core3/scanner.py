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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from tqdm.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.core3.constants import CORE3_DATABASE_PATH, INDEX_SLUG, SECTIONS
from eth_defi.core3.database import Core3Database
from eth_defi.core3.session import (
    Core3Session,
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

logger = logging.getLogger(__name__)


def _sync_time_series(
    db: Core3Database,
    session: Core3Session,
    slug: str,
    data_type: str,
    fetch_backfill,
    fetch_incremental,
    insert_fn,
    fetched_at: datetime.datetime,
    timeout: float,
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
    :param timeout:
        HTTP request timeout.
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


def _fetch_project_data(
    session: Core3Session,
    slug: str,
    fetch_pol: bool,
    fetch_categories: bool,
    fetch_sections_flag: bool,
    timeout: float,
) -> dict:
    """Worker function to fetch all API data for a single project.

    Runs in a thread pool — only does HTTP fetching, no database writes.
    Each endpoint call is wrapped in its own try/except so that a failure
    on one endpoint does not prevent the others from succeeding.

    :param session:
        Core3 API session.
    :param slug:
        Project slug.
    :param fetch_pol:
        Whether to fetch PoL history.
    :param fetch_categories:
        Whether to fetch category PoL history.
    :param fetch_sections_flag:
        Whether to fetch section detail endpoints.
    :param timeout:
        HTTP request timeout.
    :return:
        Dict with fetched data keyed by endpoint type.
    """
    data = {"slug": slug, "detail": None, "pol_points": None, "category_points": None, "sections": {}}

    # 1. Project detail
    try:
        data["detail"] = fetch_project_detail(session, slug, timeout=timeout)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403):
            raise
        elif e.response is not None and e.response.status_code == 404:
            logger.warning("Project %s: detail endpoint not found, skipping", slug)
        else:
            status = e.response.status_code if e.response is not None else "unknown"
            logger.error("Project %s: detail HTTP %s, skipping", slug, status)
    except (requests.Timeout, requests.ConnectionError) as e:
        logger.error("Project %s: detail network error (%s), skipping", slug, type(e).__name__)
    except requests.RequestException as e:
        logger.error("Project %s: detail request failed (%s), skipping", slug, e)
    except (ValueError, KeyError) as e:
        logger.error("Project %s: detail bad response (%s), skipping", slug, e)

    # 2. PoL daily history
    if fetch_pol:
        try:
            data["pol_points"] = fetch_pol_history(session, slug, timeout=timeout)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                raise
            elif e.response is not None and e.response.status_code == 404:
                logger.warning("Project %s: PoL history not found, skipping", slug)
            else:
                status = e.response.status_code if e.response is not None else "unknown"
                logger.error("Project %s: PoL history HTTP %s, skipping", slug, status)
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.error("Project %s: PoL history network error (%s), skipping", slug, type(e).__name__)
        except requests.RequestException as e:
            logger.error("Project %s: PoL history request failed (%s), skipping", slug, e)
        except (ValueError, KeyError) as e:
            logger.error("Project %s: PoL history bad response (%s), skipping", slug, e)

    # 3. Category PoL daily history
    if fetch_categories:
        try:
            data["category_points"] = fetch_pol_category_history(session, slug, timeout=timeout)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                raise
            elif e.response is not None and e.response.status_code == 404:
                logger.warning("Project %s: category history not found, skipping", slug)
            else:
                status = e.response.status_code if e.response is not None else "unknown"
                logger.error("Project %s: category history HTTP %s, skipping", slug, status)
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.error("Project %s: category history network error (%s), skipping", slug, type(e).__name__)
        except requests.RequestException as e:
            logger.error("Project %s: category history request failed (%s), skipping", slug, e)
        except (ValueError, KeyError) as e:
            logger.error("Project %s: category history bad response (%s), skipping", slug, e)

    # 4. Section details
    if fetch_sections_flag:
        for section in SECTIONS:
            try:
                data["sections"][section] = fetch_section_detail(session, slug, section, timeout=timeout)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    raise
                elif e.response is not None and e.response.status_code == 404:
                    logger.warning("Project %s: %s section not found, skipping", slug, section)
                else:
                    status = e.response.status_code if e.response is not None else "unknown"
                    logger.error("Project %s: %s section HTTP %s, skipping", slug, section, status)
            except (requests.Timeout, requests.ConnectionError) as e:
                logger.error("Project %s: %s section network error (%s), skipping", slug, section, type(e).__name__)
            except requests.RequestException as e:
                logger.error("Project %s: %s section request failed (%s), skipping", slug, section, e)
            except (ValueError, KeyError) as e:
                logger.error("Project %s: %s section bad response (%s), skipping", slug, section, e)

    return data


def _store_project_data(
    db: Core3Database,
    data: dict,
    fetched_at: datetime.datetime,
) -> None:
    """Write fetched project data to the database (main thread only).

    :param db:
        Core3 database.
    :param data:
        Dict returned by :py:func:`_fetch_project_data`.
    :param fetched_at:
        Timestamp of the current scan cycle.
    """
    slug = data["slug"]

    if data["detail"] is not None:
        db.insert_project_snapshot(slug, fetched_at, data["detail"])

    if data["pol_points"] is not None:
        db.insert_pol_daily_points(slug, data["pol_points"], fetched_at)
        last_ts = max((p["timestamp"] for p in data["pol_points"]), default=None)
        db.update_sync_state(slug, "pol_daily", last_ts, backfill_done=True)

    if data["category_points"] is not None:
        db.insert_pol_category_daily_points(slug, data["category_points"], fetched_at)
        last_ts = max((p["timestamp"] for p in data["category_points"]), default=None)
        db.update_sync_state(slug, "pol_category_daily", last_ts, backfill_done=True)

    for section, raw in data["sections"].items():
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
    checkpoint_every: int = 100,
) -> Core3Database:
    """Scan all Core3 projects and store snapshots in DuckDB.

    This function:

    1. Fetches the project list from ``/v1/list`` to get all slugs
    2. For each slug (parallelised with ``ThreadPoolExecutor``):

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
    :param checkpoint_every:
        Flush WAL to disk every N projects. DuckDB CHECKPOINT cannot run
        safely while ``ThreadPoolExecutor`` threads are alive (heap
        corruption on Python 3.14 + DuckDB 1.5, see `duckdb#13904
        <https://github.com/duckdb/duckdb/issues/13904>`__), so work is
        processed in chunks — the executor exits and all threads join
        before each checkpoint.
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

    # Process projects in chunks with a two-phase approach per chunk:
    #   Phase 1 (fetch): parallel HTTP with ThreadPoolExecutor + tqdm
    #   Phase 2 (store): sequential DB writes, then reconnect
    #
    # DuckDB con.close() triggers an implicit CHECKPOINT which causes
    # heap corruption if ANY Python thread is alive — including tqdm's
    # persistent monitor thread (Python 3.14 + DuckDB 1.5, duckdb#13904).
    # By collecting fetch results first, we can ensure no threads exist
    # when we touch the database.
    chunks = [slugs[i : i + checkpoint_every] for i in range(0, len(slugs), checkpoint_every)]
    processed = 0

    for chunk_idx, chunk in enumerate(chunks):
        # Phase 1: fetch all data (threads + tqdm alive, no DB access)
        results = []
        desc = f"Fetching chunk {chunk_idx + 1}/{len(chunks)} ({max_workers} workers)"
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _fetch_project_data,
                        session,
                        slug,
                        fetch_pol=fetch_pol_history,
                        fetch_categories=fetch_category_history,
                        fetch_sections_flag=fetch_sections,
                        timeout=timeout,
                    ): slug
                    for slug in chunk
                }
                with tqdm(total=len(chunk), desc=desc) as progress:
                    for future in as_completed(futures):
                        results.append(future.result())
                        progress.update(1)
        else:
            for slug in tqdm(chunk, desc=desc):
                results.append(
                    _fetch_project_data(
                        session,
                        slug,
                        fetch_pol=fetch_pol_history,
                        fetch_categories=fetch_category_history,
                        fetch_sections_flag=fetch_sections,
                        timeout=timeout,
                    )
                )

        # Phase 2: write to DB and checkpoint (no threads alive)
        for data in results:
            _store_project_data(db, data, fetched_at)
        processed += len(results)
        db.reconnect()
        logger.info("Saved %d/%d projects", processed, len(slugs))

    # Index-level PoL history (single request, not parallelised)
    if fetch_index_pol:
        try:
            _sync_time_series(
                db,
                session,
                INDEX_SLUG,
                "pol_daily",
                fetch_backfill=lambda: fetch_index_pol_history(session, timeout=timeout),
                fetch_incremental=lambda f, t: fetch_index_pol_history_incremental(session, f, t, timeout=timeout),
                insert_fn=db.insert_pol_daily_points,
                fetched_at=fetched_at,
                timeout=timeout,
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                raise
            logger.error("Index PoL history failed: %s", e)
        except (requests.Timeout, requests.ConnectionError, requests.RequestException) as e:
            logger.error("Index PoL history failed: %s", e)
        except (ValueError, KeyError) as e:
            logger.error("Index PoL history bad response: %s", e)

    db.reconnect()
    logger.info(
        "Scan complete: %d projects, %d snapshots, %d PoL daily rows",
        db.get_project_count(),
        db.get_snapshot_count(),
        db.get_pol_daily_count(),
    )
    return db
