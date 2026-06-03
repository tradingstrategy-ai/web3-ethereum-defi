"""Standalone DuckDB crash reproducer — no API needed.

Simulates the Core3 scanner's data volume and threading patterns
to isolate what causes the SIGSEGV (exit code 139) on Python 3.14 + DuckDB 1.5.

Run scenarios one at a time to identify the trigger:

    python scripts/core3/reproduce-duckdb-crash.py <scenario>

Scenarios:
    1  Sequential writes only, no threads, no tqdm, with CHECKPOINT every 100
    2  Sequential writes + tqdm, no threads, with CHECKPOINT every 100
    3  ThreadPoolExecutor (no DB in threads) + DB writes on main, no tqdm, CHECKPOINT every 100
    4  ThreadPoolExecutor + tqdm + DB writes on main, CHECKPOINT every 100
    5  ThreadPoolExecutor + tqdm + DB writes on main, reconnect() every 100
    6  ThreadPoolExecutor + tqdm + DB writes on main, NO checkpoint at all
    7  Sequential writes, no threads, no tqdm, no intermediate checkpoint, close at end
    8  Like 4, but with wal_autocheckpoint disabled (1TB)
    9  Like 4, but with a requests Session + LimiterAdapter (no real HTTP)
    10 Like 4, but with real HTTP calls to httpbin.org
    11 Like 4, but with 700 points/project + network-like delays
    12 INTERLEAVED: DB writes while threads do malloc-heavy work (simulates real scanner)
    13 TWO-PHASE: fetch ALL into memory first, then write to DB (DB open during fetch)
    14 TWO-PHASE + LATE DB OPEN: DB connection opened ONLY during write phase
    15 INTERLEAVED + DuckDB threads=1: reduce internal thread count
"""

import datetime
import faulthandler
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

faulthandler.enable()

# ---------------------------------------------------------------------------
# Configuration — matches real Core3 scanner data volume
# ---------------------------------------------------------------------------
NUM_PROJECTS = 150  # crash happens at ~100 in real scanner
POINTS_PER_PROJECT = 200  # reduced from 700; still pushes WAL past 16 MiB
CATEGORY_POINTS = 200  # reduced from 700
CHECKPOINT_EVERY = 100  # chunk boundary
MAX_WORKERS = 8

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_db(path: Path, disable_autocheckpoint: bool = False):
    """Create a DuckDB database with Core3-like schema."""
    import duckdb

    con = duckdb.connect(str(path))
    if disable_autocheckpoint:
        con.execute("SET wal_autocheckpoint = '1TB'")
    con.execute("""
        CREATE TABLE IF NOT EXISTS project_snapshots (
            slug VARCHAR NOT NULL,
            fetched_at TIMESTAMP NOT NULL,
            name VARCHAR,
            rank INTEGER,
            pol_score DOUBLE,
            payload VARCHAR NOT NULL,
            PRIMARY KEY (slug, fetched_at)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pol_daily (
            slug VARCHAR NOT NULL,
            ts TIMESTAMP NOT NULL,
            pol_score DOUBLE NOT NULL,
            fetched_at TIMESTAMP NOT NULL,
            PRIMARY KEY (slug, ts)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pol_category_daily (
            slug VARCHAR NOT NULL,
            ts TIMESTAMP NOT NULL,
            security_score DOUBLE,
            financial_score DOUBLE,
            operational_score DOUBLE,
            reputational_score DOUBLE,
            regulatory_score DOUBLE,
            fetched_at TIMESTAMP NOT NULL,
            PRIMARY KEY (slug, ts)
        )
    """)
    return con


def make_fake_project(slug: str) -> dict:
    """Generate fake project data matching real scanner volume."""
    fetched_at = datetime.datetime(2025, 6, 1, 0, 0, 0)
    detail = {"slug": slug, "name": f"Project {slug}", "rank": 1, "pol": {"score": 42.0}}
    pol_points = [(slug, datetime.datetime(2023, 1, 1) + datetime.timedelta(days=i), 42.0 + i * 0.01, fetched_at) for i in range(POINTS_PER_PROJECT)]
    cat_points = [(slug, datetime.datetime(2023, 1, 1) + datetime.timedelta(days=i), 10.0, 20.0, 30.0, 40.0, 50.0, fetched_at) for i in range(CATEGORY_POINTS)]
    return {
        "slug": slug,
        "fetched_at": fetched_at,
        "detail": detail,
        "pol_points": pol_points,
        "cat_points": cat_points,
    }


def write_project_to_db(con, lock, project: dict):
    """Write one project's data to DuckDB (main thread)."""
    slug = project["slug"]
    detail = project["detail"]
    fetched_at = project["fetched_at"]

    with lock:
        con.execute(
            "INSERT INTO project_snapshots VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            [slug, fetched_at, detail["name"], detail["rank"], detail["pol"]["score"], json.dumps(detail)],
        )
        con.executemany(
            "INSERT INTO pol_daily VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            project["pol_points"],
        )
        con.executemany(
            "INSERT INTO pol_category_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            project["cat_points"],
        )


def fake_fetch(_slug: str) -> dict:
    """Simulate HTTP fetch — no DB access."""
    return make_fake_project(_slug)


def report_threads(label: str):
    """Print all alive threads."""
    threads = [t for t in threading.enumerate() if t is not threading.main_thread()]
    print(f"  [{label}] Non-main threads: {len(threads)} — {[t.name for t in threads]}")


def checkpoint(con, lock):
    """Explicit CHECKPOINT."""
    with lock:
        con.execute("CHECKPOINT")


def reconnect(path: Path, con, lock, disable_autocheckpoint: bool = False):
    """Close and reopen connection."""
    import duckdb

    with lock:
        con.close()
    new_con = duckdb.connect(str(path))
    if disable_autocheckpoint:
        new_con.execute("SET wal_autocheckpoint = '1TB'")
    return new_con


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_1(db_path: Path):
    """Sequential writes only, no threads, no tqdm, CHECKPOINT every 100."""
    print("Scenario 1: sequential + CHECKPOINT")
    con = create_db(db_path)
    lock = threading.Lock()
    for i in range(NUM_PROJECTS):
        project = make_fake_project(f"project-{i}")
        write_project_to_db(con, lock, project)
        if (i + 1) % CHECKPOINT_EVERY == 0:
            report_threads(f"before checkpoint at {i + 1}")
            checkpoint(con, lock)
            print(f"  Checkpoint at {i + 1} OK")
    con.close()
    print("  PASSED")


def scenario_2(db_path: Path):
    """Sequential writes + tqdm, no threads, CHECKPOINT every 100."""
    from tqdm.auto import tqdm

    print("Scenario 2: sequential + tqdm + CHECKPOINT")
    con = create_db(db_path)
    lock = threading.Lock()
    for i in tqdm(range(NUM_PROJECTS), desc="Scenario 2"):
        project = make_fake_project(f"project-{i}")
        write_project_to_db(con, lock, project)
        if (i + 1) % CHECKPOINT_EVERY == 0:
            report_threads(f"before checkpoint at {i + 1}")
            checkpoint(con, lock)
            print(f"  Checkpoint at {i + 1} OK")
    con.close()
    print("  PASSED")


def scenario_3(db_path: Path):
    """ThreadPoolExecutor + DB writes on main, no tqdm, CHECKPOINT every 100."""
    print("Scenario 3: ThreadPoolExecutor + CHECKPOINT, no tqdm")
    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    for chunk_idx, chunk in enumerate(chunks):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fake_fetch, s): s for s in chunk}
            for future in as_completed(futures):
                write_project_to_db(con, lock, future.result())

        report_threads(f"after executor exit, chunk {chunk_idx}")
        checkpoint(con, lock)
        print(f"  Checkpoint after chunk {chunk_idx} OK")

    con.close()
    print("  PASSED")


def scenario_4(db_path: Path):
    """ThreadPoolExecutor + tqdm + DB writes on main, CHECKPOINT every 100."""
    from tqdm.auto import tqdm

    print("Scenario 4: ThreadPoolExecutor + tqdm + CHECKPOINT")
    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    with tqdm(total=NUM_PROJECTS, desc="Scenario 4") as progress:
        for chunk_idx, chunk in enumerate(chunks):
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fake_fetch, s): s for s in chunk}
                for future in as_completed(futures):
                    write_project_to_db(con, lock, future.result())
                    progress.update(1)

            report_threads(f"after executor exit, chunk {chunk_idx}")
            checkpoint(con, lock)
            print(f"  Checkpoint after chunk {chunk_idx} OK")

    con.close()
    print("  PASSED")


def scenario_5(db_path: Path):
    """ThreadPoolExecutor + tqdm + DB writes on main, reconnect() every 100."""
    from tqdm.auto import tqdm

    print("Scenario 5: ThreadPoolExecutor + tqdm + reconnect()")
    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    with tqdm(total=NUM_PROJECTS, desc="Scenario 5") as progress:
        for chunk_idx, chunk in enumerate(chunks):
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fake_fetch, s): s for s in chunk}
                for future in as_completed(futures):
                    write_project_to_db(con, lock, future.result())
                    progress.update(1)

            report_threads(f"after executor exit, chunk {chunk_idx}")
            con = reconnect(db_path, con, lock)
            print(f"  Reconnect after chunk {chunk_idx} OK")

    con.close()
    print("  PASSED")


def scenario_6(db_path: Path):
    """ThreadPoolExecutor + tqdm + DB writes on main, NO checkpoint at all."""
    from tqdm.auto import tqdm

    print("Scenario 6: ThreadPoolExecutor + tqdm + NO checkpoint")
    con = create_db(db_path, disable_autocheckpoint=True)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]

    with tqdm(total=NUM_PROJECTS, desc="Scenario 6") as progress:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fake_fetch, s): s for s in slugs}
            for future in as_completed(futures):
                write_project_to_db(con, lock, future.result())
                progress.update(1)

    report_threads("after everything")
    # Don't checkpoint, don't close cleanly — just exit
    print("  PASSED (no checkpoint, no close)")


def scenario_7(db_path: Path):
    """Sequential writes, no threads, no tqdm, no intermediate checkpoint, close at end."""
    print("Scenario 7: sequential, no threads, no tqdm, close at end only")
    con = create_db(db_path)
    lock = threading.Lock()
    for i in range(NUM_PROJECTS):
        project = make_fake_project(f"project-{i}")
        write_project_to_db(con, lock, project)
        if (i + 1) % 50 == 0:
            print(f"  Written {i + 1}/{NUM_PROJECTS}")
    report_threads("before close")
    con.close()
    print("  PASSED")


def scenario_8(db_path: Path):
    """Like scenario 4, but with wal_autocheckpoint disabled."""
    from tqdm.auto import tqdm

    print("Scenario 8: ThreadPoolExecutor + tqdm + CHECKPOINT + autocheckpoint=1TB")
    con = create_db(db_path, disable_autocheckpoint=True)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    with tqdm(total=NUM_PROJECTS, desc="Scenario 8") as progress:
        for chunk_idx, chunk in enumerate(chunks):
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fake_fetch, s): s for s in chunk}
                for future in as_completed(futures):
                    write_project_to_db(con, lock, future.result())
                    progress.update(1)

            report_threads(f"after executor exit, chunk {chunk_idx}")
            checkpoint(con, lock)
            print(f"  Checkpoint after chunk {chunk_idx} OK")

    con.close()
    print("  PASSED")


# ---------------------------------------------------------------------------


def scenario_9(db_path: Path):
    """Like scenario 4, but with a real requests Session + LimiterAdapter."""
    from tqdm.auto import tqdm
    from pyrate_limiter import MemoryQueueBucket
    from requests import Session
    from requests_ratelimiter import LimiterAdapter

    print("Scenario 9: ThreadPoolExecutor + tqdm + CHECKPOINT + real requests Session")

    # Create a session matching create_core3_session() config
    session = Session()
    adapter = LimiterAdapter(
        per_second=50,  # fast, we're not hitting real endpoints
        pool_connections=32,
        pool_maxsize=32,
        bucket_class=MemoryQueueBucket,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    def fetch_with_session(slug):
        # Don't actually make HTTP calls, but use the session object
        # to keep its connection pool alive
        return make_fake_project(slug)

    with tqdm(total=NUM_PROJECTS, desc="Scenario 9") as progress:
        for chunk_idx, chunk in enumerate(chunks):
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fetch_with_session, s): s for s in chunk}
                for future in as_completed(futures):
                    write_project_to_db(con, lock, future.result())
                    progress.update(1)

            report_threads(f"after executor exit, chunk {chunk_idx}")
            checkpoint(con, lock)
            print(f"  Checkpoint after chunk {chunk_idx} OK")

    session.close()
    con.close()
    print("  PASSED")


def scenario_10(db_path: Path):
    """Like scenario 4, but threads make REAL HTTP requests (to localhost/invalid)."""
    from tqdm.auto import tqdm
    from pyrate_limiter import MemoryQueueBucket
    from requests import Session
    from requests_ratelimiter import LimiterAdapter

    print("Scenario 10: ThreadPoolExecutor + tqdm + CHECKPOINT + real HTTP calls")

    session = Session()
    adapter = LimiterAdapter(
        per_second=50,
        pool_connections=32,
        pool_maxsize=32,
        bucket_class=MemoryQueueBucket,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    def fetch_with_real_http(slug):
        # Make a real HTTP request that fails fast
        try:
            session.get("https://httpbin.org/get", timeout=2)
        except Exception:
            pass
        return make_fake_project(slug)

    with tqdm(total=NUM_PROJECTS, desc="Scenario 10") as progress:
        for chunk_idx, chunk in enumerate(chunks):
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fetch_with_real_http, s): s for s in chunk}
                for future in as_completed(futures):
                    write_project_to_db(con, lock, future.result())
                    progress.update(1)

            report_threads(f"after executor exit, chunk {chunk_idx}")
            checkpoint(con, lock)
            print(f"  Checkpoint after chunk {chunk_idx} OK")

    session.close()
    con.close()
    print("  PASSED")


def scenario_11(db_path: Path):
    """Like scenario 4, but with REAL data volume (700 points per project) and network-like delays."""
    from tqdm.auto import tqdm

    print("Scenario 11: ThreadPoolExecutor + tqdm + CHECKPOINT + 700 points/project + delays")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    import random

    def fetch_with_delay(slug):
        time.sleep(random.uniform(0.2, 2.0))  # simulate real network latency
        return make_fake_project(slug)

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    with tqdm(total=NUM_PROJECTS, desc="Scenario 11") as progress:
        for chunk_idx, chunk in enumerate(chunks):
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fetch_with_delay, s): s for s in chunk}
                for future in as_completed(futures):
                    write_project_to_db(con, lock, future.result())
                    progress.update(1)

            report_threads(f"after executor exit, chunk {chunk_idx}")
            checkpoint(con, lock)
            print(f"  Checkpoint after chunk {chunk_idx} OK")

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def malloc_heavy_fetch(slug):
    """Simulate the malloc pressure of real HTTP + SSL + JSON parsing.

    SSL context creation does heavy malloc (like real HTTPS requests).
    JSON encode/decode churns the allocator concurrently with DuckDB's
    internal threads — reproduces the heap corruption on macOS.
    """
    import random
    import ssl

    ctx = ssl.create_default_context()
    big_data = {f"key_{i}": f"value_{i}" * random.randint(10, 100) for i in range(200)}
    payload = json.dumps(big_data)
    parsed = json.loads(payload)
    for _ in range(50):
        _ = json.dumps(parsed)
        _ = json.loads(payload)
    time.sleep(random.uniform(0.1, 0.5))
    return make_fake_project(slug)


def scenario_12(db_path: Path):
    """INTERLEAVED: DB writes on main while threads do malloc-heavy SSL/HTTP work.

    This matches the real scanner pattern: as_completed() loop writes to DuckDB
    on the main thread while OTHER executor threads are still running, doing
    malloc-heavy SSL + JSON work. DuckDB also spawns ~22 internal C++ threads.
    All threads share the same macOS malloc arenas → heap corruption.
    """
    from tqdm.auto import tqdm

    print("Scenario 12: INTERLEAVED — DB writes while threads do malloc-heavy work")
    print("  (This scenario attempts to reproduce the real crash)")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]

    # Single executor for all projects — DB writes interleaved with fetches
    with tqdm(total=NUM_PROJECTS, desc="Scenario 12") as progress:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(malloc_heavy_fetch, s): s for s in slugs}
            for future in as_completed(futures):
                # Write to DB while other threads still running
                write_project_to_db(con, lock, future.result())
                progress.update(1)

    report_threads("after everything")
    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_13(db_path: Path):
    """TWO-PHASE: fetch ALL into memory first, then write to DB with no threads alive.

    DB connection kept open during fetch phase (DuckDB's internal threads present).
    """
    from tqdm.auto import tqdm

    print("Scenario 13: TWO-PHASE — DB open during fetch (DuckDB threads present)")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    for chunk_idx, chunk in enumerate(chunks):
        # Phase 1: fetch all (threads alive, DuckDB connection open but idle)
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(malloc_heavy_fetch, s): s for s in chunk}
            with tqdm(total=len(chunk), desc=f"Fetch chunk {chunk_idx + 1}/{len(chunks)}") as progress:
                for future in as_completed(futures):
                    results.append(future.result())
                    progress.update(1)

        # Phase 2: write to DB (no threads alive except tqdm daemon)
        for project in results:
            write_project_to_db(con, lock, project)
        report_threads(f"before checkpoint, chunk {chunk_idx}")
        checkpoint(con, lock)
        print(f"  Checkpoint after chunk {chunk_idx} OK")

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_14(db_path: Path):
    """TWO-PHASE + LATE DB OPEN: DB connection opened ONLY during write phase.

    DuckDB's 22 internal threads don't exist during malloc-heavy fetch phase.
    """
    from tqdm.auto import tqdm

    print("Scenario 14: TWO-PHASE + LATE DB OPEN — DB closed during fetch")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    for chunk_idx, chunk in enumerate(chunks):
        # Phase 1: fetch all (NO DuckDB connection — no internal threads)
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(malloc_heavy_fetch, s): s for s in chunk}
            with tqdm(total=len(chunk), desc=f"Fetch chunk {chunk_idx + 1}/{len(chunks)}") as progress:
                for future in as_completed(futures):
                    results.append(future.result())
                    progress.update(1)
        # executor exited, all threads joined

        # Phase 2: open DB, write, close (no fetch threads, no DuckDB threads during fetch)
        con = create_db(db_path)
        lock = threading.Lock()
        for project in results:
            write_project_to_db(con, lock, project)
        report_threads(f"before close, chunk {chunk_idx}")
        con.close()
        print(f"  Close after chunk {chunk_idx} OK")

    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_15(db_path: Path):
    """Like scenario 12, but with DuckDB threads=1."""
    from tqdm.auto import tqdm

    print("Scenario 15: INTERLEAVED + DuckDB threads=1")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    import duckdb

    con = duckdb.connect(str(db_path))
    con.execute("SET threads = 1")
    con.execute("SET wal_autocheckpoint = '1TB'")
    # Create schema
    con.execute("""CREATE TABLE IF NOT EXISTS project_snapshots (
        slug VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL, name VARCHAR,
        rank INTEGER, pol_score DOUBLE, payload VARCHAR NOT NULL,
        PRIMARY KEY (slug, fetched_at))""")
    con.execute("""CREATE TABLE IF NOT EXISTS pol_daily (
        slug VARCHAR NOT NULL, ts TIMESTAMP NOT NULL, pol_score DOUBLE NOT NULL,
        fetched_at TIMESTAMP NOT NULL, PRIMARY KEY (slug, ts))""")
    con.execute("""CREATE TABLE IF NOT EXISTS pol_category_daily (
        slug VARCHAR NOT NULL, ts TIMESTAMP NOT NULL,
        security_score DOUBLE, financial_score DOUBLE, operational_score DOUBLE,
        reputational_score DOUBLE, regulatory_score DOUBLE,
        fetched_at TIMESTAMP NOT NULL, PRIMARY KEY (slug, ts))""")

    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]

    with tqdm(total=NUM_PROJECTS, desc="Scenario 15") as progress:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(malloc_heavy_fetch, s): s for s in slugs}
            for future in as_completed(futures):
                write_project_to_db(con, lock, future.result())
                progress.update(1)

    report_threads("after everything")
    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_16(db_path: Path):
    """TWO-PHASE + LATE DB OPEN + NO TQDM: isolate tqdm as a factor.

    Same as scenario 14 but without tqdm. If this passes, tqdm's
    persistent monitor thread is interacting with DuckDB to trigger the crash.
    """
    print("Scenario 16: TWO-PHASE + LATE DB OPEN + NO TQDM")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    for chunk_idx, chunk in enumerate(chunks):
        # Phase 1: fetch all (NO DuckDB, NO tqdm)
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(malloc_heavy_fetch, s): s for s in chunk}
            for future in as_completed(futures):
                results.append(future.result())
        print(f"  Fetched chunk {chunk_idx + 1}/{len(chunks)}: {len(results)} projects")

        # Phase 2: open DB, write, close
        con = create_db(db_path)
        lock = threading.Lock()
        for project in results:
            write_project_to_db(con, lock, project)
        report_threads(f"before close, chunk {chunk_idx}")
        con.close()
        print(f"  Close after chunk {chunk_idx} OK")

    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_17(db_path: Path):
    """SEQUENTIAL malloc-heavy fetch + DuckDB writes. No thread pool at all.

    If this passes, multi-threaded malloc_heavy_fetch is required for the crash.
    """
    from tqdm.auto import tqdm

    print("Scenario 17: SEQUENTIAL malloc-heavy + DuckDB (no ThreadPoolExecutor)")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]

    for slug in tqdm(slugs, desc="Scenario 17"):
        project = malloc_heavy_fetch(slug)
        write_project_to_db(con, lock, project)

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_18(db_path: Path):
    """ProcessPoolExecutor for fetch, DuckDB writes on main thread.

    Each worker process has its own heap — no cross-process memory corruption.
    """
    from tqdm.auto import tqdm
    from concurrent.futures import ProcessPoolExecutor

    print("Scenario 18: ProcessPoolExecutor + DuckDB on main (separate heaps)")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]
    chunks = [slugs[i : i + CHECKPOINT_EVERY] for i in range(0, len(slugs), CHECKPOINT_EVERY)]

    for chunk_idx, chunk in enumerate(chunks):
        results = []
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(malloc_heavy_fetch, s): s for s in chunk}
            with tqdm(total=len(chunk), desc=f"Fetch chunk {chunk_idx + 1}/{len(chunks)}") as progress:
                for future in as_completed(futures):
                    results.append(future.result())
                    progress.update(1)

        for project in results:
            write_project_to_db(con, lock, project)
        report_threads(f"before reconnect, chunk {chunk_idx}")
        con.close()
        con = create_db(db_path)
        print(f"  Reconnect after chunk {chunk_idx} OK")

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_19(db_path: Path):
    """Sequential, JSON churn only (no ssl.create_default_context), + DuckDB writes.

    Isolates whether SSL context creation is required for the crash.
    """
    from tqdm.auto import tqdm

    print("Scenario 19: SEQUENTIAL JSON-only churn + DuckDB (no SSL)")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    import random

    def json_only_fetch(slug):
        big_data = {f"key_{i}": f"value_{i}" * random.randint(10, 100) for i in range(200)}
        payload = json.dumps(big_data)
        parsed = json.loads(payload)
        for _ in range(50):
            _ = json.dumps(parsed)
            _ = json.loads(payload)
        time.sleep(random.uniform(0.1, 0.5))
        return make_fake_project(slug)

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]

    for slug in tqdm(slugs, desc="Scenario 19"):
        project = json_only_fetch(slug)
        write_project_to_db(con, lock, project)

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_20(db_path: Path):
    """Sequential, SSL only (no JSON churn), + DuckDB writes.

    Isolates whether SSL alone is sufficient for the crash.
    """
    from tqdm.auto import tqdm
    import ssl

    print("Scenario 20: SEQUENTIAL SSL-only + DuckDB (no JSON churn)")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    def ssl_only_fetch(slug):
        ctx = ssl.create_default_context()
        time.sleep(random.uniform(0.1, 0.5))
        return make_fake_project(slug)

    import random

    con = create_db(db_path)
    lock = threading.Lock()
    slugs = [f"project-{i}" for i in range(NUM_PROJECTS)]

    for slug in tqdm(slugs, desc="Scenario 20"):
        project = ssl_only_fetch(slug)
        write_project_to_db(con, lock, project)

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_21(db_path: Path):
    """Pure DuckDB writes only, 700 points/project, no fetch, no JSON, no SSL.

    Uses DEFAULT wal_autocheckpoint (16 MiB). Crashes because the implicit
    CHECKPOINT races with tqdm's monitor thread.
    """
    from tqdm.auto import tqdm

    print("Scenario 21: PURE DuckDB writes only, 700 points/project, DEFAULT autocheckpoint")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    con = create_db(db_path)  # default wal_autocheckpoint=16MiB
    lock = threading.Lock()

    for i in tqdm(range(NUM_PROJECTS), desc="Scenario 21"):
        project = make_fake_project(f"project-{i}")
        write_project_to_db(con, lock, project)

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_22(db_path: Path):
    """JSON churn first (all projects), THEN DuckDB writes (all projects).

    Fully separate phases: if this passes, the interleaving matters.
    If this crashes, the heap is poisoned by prior JSON churn.
    """
    from tqdm.auto import tqdm
    import random

    print("Scenario 22: JSON churn ALL first, then DuckDB writes ALL")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    # Phase 1: all JSON churn, no DuckDB
    projects = []
    for slug in tqdm([f"project-{i}" for i in range(NUM_PROJECTS)], desc="Phase 1: JSON churn"):
        big_data = {f"key_{i}": f"value_{i}" * random.randint(10, 100) for i in range(200)}
        payload = json.dumps(big_data)
        parsed = json.loads(payload)
        for _ in range(50):
            _ = json.dumps(parsed)
            _ = json.loads(payload)
        projects.append(make_fake_project(slug))

    # Phase 2: all DuckDB writes
    con = create_db(db_path)
    lock = threading.Lock()
    for project in tqdm(projects, desc="Phase 2: DB writes"):
        write_project_to_db(con, lock, project)

    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_23(db_path: Path):
    """Like scenario 21, but with wal_autocheckpoint DISABLED (1TB).

    If this passes while 21 crashes, the root cause is:
    DuckDB's implicit wal_autocheckpoint + tqdm's daemon thread = heap corruption.
    """
    from tqdm.auto import tqdm

    print("Scenario 23: PURE DuckDB writes, 700 points, autocheckpoint=1TB")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    con = create_db(db_path, disable_autocheckpoint=True)
    lock = threading.Lock()

    for i in tqdm(range(NUM_PROJECTS), desc="Scenario 23"):
        project = make_fake_project(f"project-{i}")
        write_project_to_db(con, lock, project)

    report_threads("before close")
    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


def scenario_24(db_path: Path):
    """Like scenario 21 (700 points, default autocheckpoint) but NO tqdm.

    If this passes while 21 crashes, tqdm's monitor thread is required.
    """
    print("Scenario 24: PURE DuckDB writes, 700 points, DEFAULT autocheckpoint, NO tqdm")

    global POINTS_PER_PROJECT, CATEGORY_POINTS
    old_pp, old_cp = POINTS_PER_PROJECT, CATEGORY_POINTS
    POINTS_PER_PROJECT = 700
    CATEGORY_POINTS = 700

    con = create_db(db_path)  # default wal_autocheckpoint
    lock = threading.Lock()

    for i in range(NUM_PROJECTS):
        project = make_fake_project(f"project-{i}")
        write_project_to_db(con, lock, project)
        if (i + 1) % 50 == 0:
            print(f"  Written {i + 1}/{NUM_PROJECTS}")

    report_threads("before close")
    con.close()
    POINTS_PER_PROJECT, CATEGORY_POINTS = old_pp, old_cp
    print("  PASSED")


SCENARIOS = {
    "1": scenario_1,
    "2": scenario_2,
    "3": scenario_3,
    "4": scenario_4,
    "5": scenario_5,
    "6": scenario_6,
    "7": scenario_7,
    "8": scenario_8,
    "9": scenario_9,
    "10": scenario_10,
    "11": scenario_11,
    "12": scenario_12,
    "13": scenario_13,
    "14": scenario_14,
    "15": scenario_15,
    "16": scenario_16,
    "17": scenario_17,
    "18": scenario_18,
    "19": scenario_19,
    "20": scenario_20,
    "21": scenario_21,
    "22": scenario_22,
    "23": scenario_23,
    "24": scenario_24,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in SCENARIOS:
        print(__doc__)
        print("Available scenarios:", ", ".join(sorted(SCENARIOS.keys())))
        sys.exit(1)

    scenario_id = sys.argv[1]
    print(f"\nPython {sys.version}")

    import duckdb

    print(f"DuckDB {duckdb.__version__}")
    print(f"GIL enabled: {sys._is_gil_enabled() if hasattr(sys, '_is_gil_enabled') else 'N/A'}")
    print(f"Projects: {NUM_PROJECTS}, points/project: {POINTS_PER_PROJECT}+{CATEGORY_POINTS}")
    print(f"Checkpoint every: {CHECKPOINT_EVERY}, workers: {MAX_WORKERS}")
    print()

    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test.duckdb"

    SCENARIOS[scenario_id](db_path)


if __name__ == "__main__":
    main()
