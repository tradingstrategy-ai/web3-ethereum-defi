"""Resumable Neutral Trade directory, backfill and live collection.

All modes share one page reader and transactional, single-owner DuckDB writer.
API contracts: https://api.neutral.trade/openapi.json.
"""

import datetime
import hashlib
import json
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import duckdb
from filelock import FileLock
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.neutral_trade.api import NeutralTradeAPIError, NeutralTradeClient, NeutralTradeVault

# ruff: noqa: EM101
logger = logging.getLogger(__name__)

#: Bounds untrusted integer strings while accommodating EVM uint256 amounts.
MAX_RAW_AMOUNT_DIGITS = 128


@dataclass(slots=True)
class ScanResult:
    """Observe completed work separately from incomplete maintenance."""

    #: Available catalogue records, before explicit selection.
    leads: int = 0
    #: Committed history pages.
    pages: int = 0
    #: Operations which failed and remain due.
    failures: int = 0
    #: True when the page/time budget leaves work for a later invocation.
    pending: bool = False


class NeutralTradeDatabase:
    """Own one private source archive and exclusive source writer lock.

    Unconstrained tables avoid DuckDB ART-index corruption. Raw observations
    and progress are committed together, preserving all unmatched history.
    Workers never access this connection.

    :param path: Explicit private source archive, not a public dataset.
    """

    def __init__(self, path: Path) -> None:
        """Acquire ownership before opening the file-backed database.

        The entry-point must already hold lifecycle/pipeline locks where needed.

        :param path: File-backed source database.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._owner = threading.get_ident()
        self._lock = FileLock(str(path) + ".lock", timeout=0)
        self._lock.acquire()
        try:
            self.con = duckdb.connect(str(path))
            self.con.execute("SET wal_autocheckpoint='1TB'")
            self.con.execute("CREATE TABLE IF NOT EXISTS leads(chain VARCHAR, address VARCHAR, raw VARCHAR, metrics VARCHAR, first_seen TIMESTAMP, last_seen TIMESTAMP, skip_reason VARCHAR)")
            self.con.execute("CREATE TABLE IF NOT EXISTS observations(chain VARCHAR, address VARCHAR, endpoint VARCHAR, observation_key VARCHAR, timestamp TIMESTAMP, raw VARCHAR, envelope VARCHAR, written_at TIMESTAMP)")
            self.con.execute("CREATE TABLE IF NOT EXISTS history_sync(chain VARCHAR, address VARCHAR, endpoint VARCHAR, cursor VARCHAR, start_date DATE, end_date DATE, complete BOOLEAN, last_success TIMESTAMP, error VARCHAR)")
            self.con.execute("ALTER TABLE history_sync ADD COLUMN IF NOT EXISTS visited VARCHAR DEFAULT '[]'")
        except (duckdb.Error, OSError):
            self._lock.release()
            raise

    def __enter__(self) -> Self:
        """Return the source archive under its held writer lock.

        All calls must remain in the creating thread.

        :return: This archive.
        """
        return self

    def __exit__(self, *_: object) -> None:
        """Close/checkpoint before releasing the source lock.

        Persisted page progress survives normal closure and process restart.

        :return: None.
        """
        try:
            self.con.close()
        finally:
            self._lock.release()

    def _check_owner(self) -> None:
        """Reject accidental worker-thread database access.

        DuckDB writes and progress updates belong to one owner thread.

        :return: None.
        """
        assert threading.get_ident() == self._owner, "Database access must stay in the owner thread"

    def save_lead(self, vault: NeutralTradeVault, metrics: dict | None = None) -> None:
        """Upsert current metadata without deleting disappeared leads.

        WSOL retains only a minimal exclusion audit, with no detailed data.
        A metadata refresh preserves previous metrics until replaced.

        :param vault: Available directory entry.
        :param metrics: Successful metrics envelope, or no refresh.
        :return: None.
        """
        self._check_owner()
        now = native_datetime_utc_now()
        previous = self.con.execute("SELECT first_seen, metrics FROM leads WHERE chain=? AND address=?", [vault.chain, vault.address]).fetchone()
        raw = {"bundleKey": vault.address, "chain": vault.chain, "visible": vault.public} if vault.skip_reason else vault.raw
        metric_json = None if vault.skip_reason else json.dumps(metrics) if metrics is not None else previous[1] if previous else None
        self.con.execute("BEGIN")
        try:
            self.con.execute("DELETE FROM leads WHERE chain=? AND address=?", [vault.chain, vault.address])
            self.con.execute("INSERT INTO leads VALUES (?,?,?,?,?,?,?)", [vault.chain, vault.address, json.dumps(raw), metric_json, previous[0] if previous else now, now, vault.skip_reason])
            self.con.execute("COMMIT")
        except duckdb.Error:
            self.con.execute("ROLLBACK")
            raise

    def prepare_history(self, vault: NeutralTradeVault, endpoint: str, *, start_date: datetime.date | None, end_date: datetime.date, refresh: bool = False) -> bool:
        """Resume incomplete work or open a bounded incremental date range.

        Completed histories refresh daily with a seven-day correction overlap;
        new leads and explicit refresh/range requests are immediately due.

        :param vault: Eligible source identity.
        :param endpoint: Canonical history endpoint.
        :param start_date: Explicit inclusive start, or full initial history.
        :param end_date: Fixed inclusive end of this invocation's range.
        :param refresh: Start a deliberate full-range refresh.
        :return: Whether a page is due.
        """
        self._check_owner()
        row = self.con.execute("SELECT complete,last_success,end_date FROM history_sync WHERE chain=? AND address=? AND endpoint=?", [vault.chain, vault.address, endpoint]).fetchone()
        if row and row[2] > end_date:
            return False  # Fixed-scope onboarding must never rewind newer coverage.
        if row and not row[0] and not refresh:
            return True
        if row and not refresh and start_date is None and row[1] and native_datetime_utc_now() - row[1] < datetime.timedelta(hours=24):
            return False
        beginning = start_date
        if row and not refresh and beginning is None:
            beginning = row[2] - datetime.timedelta(days=7)
        self.con.execute("DELETE FROM history_sync WHERE chain=? AND address=? AND endpoint=?", [vault.chain, vault.address, endpoint])
        self.con.execute("INSERT INTO history_sync VALUES (?,?,?,NULL,?,?,false,NULL,NULL,'[]')", [vault.chain, vault.address, endpoint, beginning, end_date])
        return True

    def history_request(self, vault: NeutralTradeVault, endpoint: str) -> tuple[str | None, datetime.date | None, datetime.date]:
        """Read the current persisted page position.

        The range stays fixed across resumptions; its cursor is opaque.

        :param vault: Source identity.
        :param endpoint: Canonical endpoint.
        :return: Cursor, start date and end date.
        """
        self._check_owner()
        return self.con.execute("SELECT cursor,start_date,end_date FROM history_sync WHERE chain=? AND address=? AND endpoint=?", [vault.chain, vault.address, endpoint]).fetchone()

    def save_page(self, vault: NeutralTradeVault, endpoint: str, page: dict) -> bool:
        """Atomically store one validated page and its continuation cursor.

        Full source JSON preserves exact values and provenance. Corrections
        replace the same event key; shortened responses never delete history.

        :param vault: Source identity.
        :param endpoint: Canonical endpoint.
        :param page: Original validated response envelope.
        :return: True when this range reached its terminal cursor.
        """
        self._check_owner()
        data = page["data"]
        cursor = data["nextCursor"]
        previous_cursor, _, _ = self.history_request(vault, endpoint)
        visited = json.loads(self.con.execute("SELECT visited FROM history_sync WHERE chain=? AND address=? AND endpoint=?", [vault.chain, vault.address, endpoint]).fetchone()[0])
        if cursor is not None and (cursor == previous_cursor or cursor in visited):
            raise NeutralTradeAPIError("History cursor failed to advance")
        rows = data["series" if endpoint == "share-price" else "snapshots"]
        if cursor is not None and not rows:
            raise NeutralTradeAPIError("Empty page has a continuation cursor")
        now = native_datetime_utc_now()
        envelope = json.dumps({k: v for k, v in page.items() if k != "data"})
        self.con.execute("BEGIN")
        try:
            for raw in rows:
                amount_fields = ("closePps", "closeTvlToken", "closeTotalShares") if endpoint == "snapshots" else ("pps", "totalEquity", "totalShares")
                if any(not isinstance(raw.get(field), str) or not raw[field].isascii() or not raw[field].isdecimal() or len(raw[field]) > MAX_RAW_AMOUNT_DIGITS for field in amount_fields):
                    raise NeutralTradeAPIError("Invalid lossless amount string in history page")
                if not isinstance(raw.get("isLegacy"), bool):
                    raise NeutralTradeAPIError("Missing history provenance")
                if endpoint == "snapshots":
                    timestamp = datetime.datetime.combine(datetime.date.fromisoformat(raw["date"]), datetime.time(23, 59, 59))
                    key = raw["date"]
                else:
                    timestamp = datetime.datetime.fromtimestamp(raw["blockTime"], datetime.UTC).replace(tzinfo=None)
                    # Signature/event/slot/time form the available immutable source discriminator.
                    key = hashlib.sha256(json.dumps([raw.get("signature"), raw.get("slot"), raw["blockTime"], raw.get("sourceEvent"), raw.get("isLegacy")]).encode()).hexdigest()
                self.con.execute("DELETE FROM observations WHERE chain=? AND address=? AND endpoint=? AND observation_key=?", [vault.chain, vault.address, endpoint, key])
                self.con.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)", [vault.chain, vault.address, endpoint, key, timestamp, json.dumps(raw), envelope, now])
            self.con.execute("UPDATE history_sync SET cursor=?,complete=?,last_success=?,error=NULL,visited=? WHERE chain=? AND address=? AND endpoint=?", [cursor, cursor is None, now, json.dumps(visited + ([cursor] if cursor else [])), vault.chain, vault.address, endpoint])
            self.con.execute("COMMIT")
        except (duckdb.Error, NeutralTradeAPIError, ValueError, KeyError, TypeError):
            self.con.execute("ROLLBACK")
            raise
        return cursor is None

    def record_error(self, vault: NeutralTradeVault, endpoint: str, error: str) -> None:
        """Keep failed work due without changing cursor/coverage.

        Only sanitised client diagnostics should be supplied by callers.

        :param vault: Source identity.
        :param endpoint: Failed canonical endpoint.
        :param error: Safe diagnostic text.
        :return: None.
        """
        self._check_owner()
        self.con.execute("BEGIN")
        try:
            self.con.execute("UPDATE history_sync SET error=? WHERE chain=? AND address=? AND endpoint=?", [error, vault.chain, vault.address, endpoint])
            if "HTTP 400" in error or "HTTP 422" in error:
                self.con.execute("UPDATE history_sync SET cursor=NULL,visited='[]' WHERE chain=? AND address=? AND endpoint=?", [vault.chain, vault.address, endpoint])
            self.con.execute("COMMIT")
        except duckdb.Error:
            self.con.execute("ROLLBACK")
            raise

    def iter_leads(self) -> Iterator[dict]:
        """Yield retained source metadata, including unavailable decisions.

        This is a private archive API; public exporters apply admission checks.

        :return: Lead dictionaries with raw configuration and metrics.
        """
        self._check_owner()
        for chain, address, raw, metrics, first, last, skip in self.con.execute("SELECT * FROM leads ORDER BY chain,address").fetchall():
            first_price = self.con.execute("SELECT min(timestamp) FROM observations WHERE chain=? AND address=? AND json_extract_string(raw, '$.isLegacy')='false'", [chain, address]).fetchone()[0]
            yield {"first_price": first_price, "chain": chain, "address": address, "raw": json.loads(raw), "metrics": json.loads(metrics) if metrics else None, "first_seen": first, "last_seen": last, "skip_reason": skip}


def fetch_history_page(api_key: str, vault: NeutralTradeVault, endpoint: str, position: tuple) -> tuple[NeutralTradeVault, str, dict | None, str | None]:
    """Fetch one page in an isolated worker session without database access.

    Workers return parsed values; the owner commits pages as results arrive.

    :param api_key: Partner credential, never logged.
    :param vault: Eligible source identity.
    :param endpoint: Canonical endpoint.
    :param position: Cursor and fixed date bounds from persisted state.
    :return: Identity, endpoint, page or sanitised error.
    """
    cursor, start, end = position
    try:
        with NeutralTradeClient(api_key) as client:
            page = next(client.fetch_history_pages(vault, endpoint, max_pages=1, page_size=100, start_date=start, end_date=end, cursor=cursor))
        return vault, endpoint, page, None
    except NeutralTradeAPIError as exc:
        return vault, endpoint, None, str(exc)


def run_scan(db: NeutralTradeDatabase, api_key: str, *, max_workers: int = 2, page_budget: int = 100, time_budget: float = 300, vault_ids: set[str] | None = None, start_date: datetime.date | None = None, end_date: datetime.date | None = None, refresh: bool = False) -> ScanResult:
    """Discover leads, refresh current metadata and resume bounded history.

    Manual/full and scheduled/incremental modes use this same routine. Daily
    histories use independent eligibility; current metrics refresh every run.

    :param db: Owner-thread private database under its source lock.
    :param api_key: Partner credential.
    :param max_workers: Threaded page-reader concurrency.
    :param page_budget: Maximum committed/attempted pages this invocation.
    :param time_budget: Time before stopping new page dispatches.
    :param vault_ids: Optional exact source IDs; exclusions still apply.
    :param start_date: Optional inclusive historical start.
    :param end_date: Optional fixed end, default today in UTC.
    :param refresh: Reopen completed histories for a full refresh.
    :return: Counts distinguishing failures, pending work and completion.
    """
    assert max_workers > 0 and page_budget > 0 and time_budget > 0
    end_date = end_date or native_datetime_utc_now().date()
    assert start_date is None or start_date <= end_date
    deadline = time.monotonic() + time_budget
    result = ScanResult()
    pending = []
    with NeutralTradeClient(api_key) as client:
        client.fetch_status()
        leads = list(client.fetch_vaults())
        result.leads = len(leads)
        for vault in tqdm(leads, desc="Neutral Trade leads"):
            if vault_ids and vault.address not in vault_ids:
                continue
            db.save_lead(vault)
            if vault.skip_reason or not vault.public:
                continue
            if time.monotonic() >= deadline:
                result.pending = True
                continue
            try:
                db.save_lead(vault, client.fetch_metrics(vault))
            except NeutralTradeAPIError as exc:
                logger.warning("Neutral Trade current metrics deferred for %s: %s", vault.address, exc)
                result.failures += 1
            for endpoint in ("share-price", "snapshots"):
                if db.prepare_history(vault, endpoint, start_date=start_date, end_date=end_date, refresh=refresh):
                    pending.append((vault, endpoint))
    with tqdm(total=page_budget, desc="Neutral Trade history pages") as progress:
        while pending and result.pages < page_budget and time.monotonic() < deadline:
            batch, pending = pending[: min(max_workers, page_budget - result.pages)], pending[min(max_workers, page_budget - result.pages) :]
            positions = [(v, ep, db.history_request(v, ep)) for v, ep in batch]
            outputs = Parallel(n_jobs=max_workers, backend="threading", return_as="generator_unordered")(delayed(fetch_history_page)(api_key, v, ep, position) for v, ep, position in positions)
            for vault, endpoint, page, error in outputs:
                result.pages += 1
                progress.update(1)
                if error:
                    db.record_error(vault, endpoint, error)
                    result.failures += 1
                    logger.warning("Neutral Trade historical read deferred: %s", endpoint)
                    continue
                try:
                    complete = db.save_page(vault, endpoint, page)
                except (NeutralTradeAPIError, ValueError, KeyError, TypeError) as exc:
                    db.record_error(vault, endpoint, "Invalid source page")
                    result.failures += 1
                    reason = str(exc) if isinstance(exc, NeutralTradeAPIError) else type(exc).__name__
                    logger.warning("Neutral Trade invalid history page deferred for %s (%s): %s", vault.address, endpoint, reason)
                    continue
                if not complete:
                    pending.append((vault, endpoint))
    result.pending = result.pending or bool(pending)
    logger.info("Neutral Trade: leads=%s pages=%s failures=%s pending=%s", result.leads, result.pages, result.failures, result.pending)
    return result
