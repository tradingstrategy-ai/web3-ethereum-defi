"""DuckDB persistence for Hyperliquid account trading data.

Stores fills, funding payments, and ledger updates for a whitelisted set of
accounts (vaults or normal addresses). Incremental sync accumulates data
beyond the 10K fill API limit by fetching only new records on each run.

The sync is crash-resumeable: partial batches are safely re-inserted on
restart via ``INSERT OR IGNORE`` on natural primary keys.

Schema
------

Five tables:

- ``accounts`` -- whitelisted addresses to track
- ``fills`` -- individual trade fills from ``userFillsByTime``
- ``funding`` -- funding payments from ``userFunding``
- ``ledger`` -- deposit/withdrawal events from ``userNonFundingLedgerUpdates``
- ``sync_state`` -- per-account watermarks for incremental sync

Storage location
----------------

Default: ``~/.tradingstrategy/hyperliquid/trade-history.duckdb``

Example::

    from pathlib import Path
    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase

    session = create_hyperliquid_session()
    db = HyperliquidTradeHistoryDatabase(Path("/tmp/trade-history.duckdb"))

    db.add_account("0x1e37a337ed460039d1b15bd3bc489de789768d5e", label="Growi HF")
    db.sync_account(session, "0x1e37a337ed460039d1b15bd3bc489de789768d5e")

    fills = db.get_fills("0x1e37a337ed460039d1b15bd3bc489de789768d5e")
    print(f"Stored {len(fills)} fills")

    db.close()
"""

import datetime
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path

import duckdb
from eth_typing import HexAddress
from tqdm import tqdm as tqdm_std
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.position import Fill
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.hyperliquid.trade_history import FundingPayment

logger = logging.getLogger(__name__)

#: Default DuckDB path for trade history
DEFAULT_TRADE_HISTORY_DB_PATH = Path("~/.tradingstrategy/hyperliquid/trade-history.duckdb").expanduser()

#: Maximum records per API request for fills and ledger
MAX_PER_REQUEST = 2000

#: Maximum records per API request for funding
MAX_FUNDING_PER_REQUEST = 500


def _format_count(n: int) -> str:
    """Format an event count with k/M suffix for compact display.

    :param n:
        Event count.
    :return:
        Formatted string, e.g. ``"42"``, ``"1.5k"``, ``"2.3M"``.
    """
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ANSI colour codes for progress bar readability
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _colour_desc(data_type: str, label: str) -> str:
    """Build a coloured progress bar description.

    Data type in cyan, account label in bold white.
    """
    return f"{_CYAN}{data_type}{_RESET} {_BOLD}{label}{_RESET}"


def _colour_postfix(**kwargs: str) -> str:
    """Build a coloured postfix string.

    Keys are dim, values are green, separated by dim commas.
    """
    parts = [f"{_DIM}{k}={_RESET}{_GREEN}{v}{_RESET}" for k, v in kwargs.items()]
    return f"{_DIM},{_RESET} ".join(parts)


class HyperliquidTradeHistoryDatabase:
    """DuckDB database for storing Hyperliquid account trading data.

    Stores fills, funding payments, and ledger updates for whitelisted
    accounts. Supports incremental sync that accumulates data beyond
    the 10K fill API limit.

    The database is crash-resumeable: interrupted syncs can be safely
    re-run without data loss or duplicates.

    Thread safety: all database operations are protected by an internal
    lock. Multiple threads can call sync methods concurrently -- the
    API calls run in parallel while database writes are serialised.
    """

    def __init__(self, path: Path):
        """Initialise the database connection.

        :param path:
            Path to the DuckDB file. Parent directories are created if needed.
        """
        assert isinstance(path, Path), f"Expected Path, got {type(path)}"
        assert not path.is_dir(), f"Expected file path, got directory: {path}"

        path.parent.mkdir(parents=True, exist_ok=True)

        self.path = path
        self.con = duckdb.connect(str(path))
        self._db_lock = threading.Lock()
        self._init_schema()

    def __del__(self):
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def close(self):
        """Close the database connection."""
        if self.con is not None:
            self.con.close()
            self.con = None

    def save(self):
        """Force a checkpoint to ensure data is persisted to disk."""
        with self._db_lock:
            self.con.execute("CHECKPOINT")

    def _init_schema(self):
        """Create tables if they don't exist."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                address VARCHAR PRIMARY KEY,
                label VARCHAR,
                is_vault BOOLEAN NOT NULL DEFAULT TRUE,
                added_at BIGINT NOT NULL
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS fills (
                address VARCHAR NOT NULL,
                trade_id BIGINT NOT NULL,
                ts BIGINT NOT NULL,
                coin VARCHAR NOT NULL,
                side TINYINT NOT NULL,
                sz FLOAT NOT NULL,
                px FLOAT NOT NULL,
                closed_pnl FLOAT,
                start_position FLOAT,
                fee FLOAT,
                oid BIGINT,
                PRIMARY KEY (address, trade_id)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS funding (
                address VARCHAR NOT NULL,
                ts BIGINT NOT NULL,
                coin VARCHAR NOT NULL,
                usdc FLOAT NOT NULL,
                sz FLOAT,
                rate FLOAT,
                PRIMARY KEY (address, ts, coin)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                address VARCHAR NOT NULL,
                ts BIGINT NOT NULL,
                event_type VARCHAR NOT NULL,
                usdc FLOAT NOT NULL,
                vault VARCHAR,
                PRIMARY KEY (address, ts, event_type)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                address VARCHAR NOT NULL,
                data_type VARCHAR NOT NULL,
                oldest_ts BIGINT,
                newest_ts BIGINT,
                row_count INTEGER,
                last_synced BIGINT NOT NULL,
                PRIMARY KEY (address, data_type)
            )
        """)

    # ──────────────────────────────────────────────
    # Account management
    # ──────────────────────────────────────────────

    def add_account(
        self,
        address: HexAddress,
        label: str | None = None,
        is_vault: bool = True,
    ) -> None:
        """Add an account to the whitelist.

        Idempotent -- re-adding an existing account updates the label.

        :param address:
            Hyperliquid account address.
        :param label:
            Human-readable name (e.g. "Growi HF").
        :param is_vault:
            Whether this is a vault account.
        """
        now_ms = int(native_datetime_utc_now().timestamp() * 1000)
        with self._db_lock:
            self.con.execute(
                """
                INSERT INTO accounts (address, label, is_vault, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (address) DO UPDATE SET label = EXCLUDED.label, is_vault = EXCLUDED.is_vault
                """,
                [address.lower(), label, is_vault, now_ms],
            )
        logger.info("Added account %s (%s) to whitelist", address, label or "unlabelled")

    def remove_account(self, address: HexAddress, purge_data: bool = False) -> None:
        """Remove an account from the whitelist.

        :param address:
            Account address to remove.
        :param purge_data:
            If True, also delete all stored data for this account.
        """
        addr = address.lower()
        with self._db_lock:
            self.con.execute("DELETE FROM accounts WHERE address = ?", [addr])
            if purge_data:
                for table in ("fills", "funding", "ledger", "sync_state"):
                    self.con.execute(f"DELETE FROM {table} WHERE address = ?", [addr])
        if purge_data:
            logger.info("Purged all data for account %s", address)
        else:
            logger.info("Removed account %s from whitelist (data preserved)", address)

    def get_accounts(self) -> list[dict]:
        """Get all whitelisted accounts.

        :return:
            List of account dicts with address, label, is_vault, added_at.
        """
        with self._db_lock:
            result = self.con.execute("SELECT address, label, is_vault, added_at FROM accounts ORDER BY added_at").fetchall()
        return [{"address": r[0], "label": r[1], "is_vault": r[2], "added_at": r[3]} for r in result]

    # ──────────────────────────────────────────────
    # Sync: fills
    # ──────────────────────────────────────────────

    def sync_account_fills(
        self,
        session: HyperliquidSession,
        address: HexAddress,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        timeout: float = 30.0,
        progress: tqdm | None = None,
    ) -> int:
        """Fetch new fills since last sync and store them.

        Incremental: only fetches fills newer than the last stored timestamp.
        Uses ``INSERT OR IGNORE`` to handle overlapping batches safely.

        Proxy support is handled by the session's built-in proxy rotation
        via :py:meth:`~eth_defi.hyperliquid.session.HyperliquidSession.post_info`.

        :param session:
            Hyperliquid API session (with optional proxy configuration).
        :param address:
            Account address.
        :param start_time:
            Override start time (default: use sync_state or 1 year ago).
        :param end_time:
            Override end time (default: now).
        :param timeout:
            HTTP request timeout.
        :param progress:
            External tqdm bar to reuse. If None, creates and manages its own.
        :return:
            Number of new fills inserted.
        """
        addr = address.lower()

        if end_time is None:
            end_time = native_datetime_utc_now()
        end_ms = int(end_time.timestamp() * 1000)

        # Determine start time from sync state or default
        if start_time is None:
            state = self._get_sync_state_row(addr, "fills")
            if state and state["newest_ts"]:
                # Start from last known timestamp (overlap by 1ms to catch edge cases)
                start_ms = state["newest_ts"]
            else:
                # First run: go back 1 year
                start_ms = int((end_time - datetime.timedelta(days=365)).timestamp() * 1000)
        else:
            start_ms = int(start_time.timestamp() * 1000)

        logger.info("Syncing fills for %s from ts=%d to ts=%d", addr, start_ms, end_ms)

        total_inserted = 0
        total_fetched = 0
        batch_num = 0
        current_start_ms = start_ms

        if progress is False:
            progress = None
            own_bar = False
        elif progress is None:
            own_bar = True
            progress = tqdm(
                desc=_colour_desc("Fills", addr[:10]),
                unit="fill",
                leave=False,
                colour="cyan",
            )
        else:
            own_bar = False
            progress.n = 0
            progress.last_print_n = 0
            progress.unit = "fill"
            progress.set_postfix_str("")
            progress.set_description_str(_colour_desc("Fills", addr[:10]))

        try:
            while current_start_ms < end_ms:
                payload = {
                    "type": "userFillsByTime",
                    "user": addr,
                    "startTime": current_start_ms,
                    "endTime": end_ms,
                }

                response = session.post_info(payload, timeout=timeout)
                response.raise_for_status()
                raw_fills = response.json()

                if not raw_fills:
                    break

                batch_num += 1
                total_fetched += len(raw_fills)

                # Batch insert
                rows = []
                newest_batch_ts = None
                for raw in raw_fills:
                    ts = raw["time"]
                    tid = raw.get("tid")
                    if tid is None:
                        continue
                    rows.append(
                        (
                            addr,
                            tid,
                            ts,
                            raw["coin"],
                            0 if raw["side"] == "B" else 1,
                            float(raw["sz"]),
                            float(raw["px"]),
                            float(raw.get("closedPnl", 0)),
                            float(raw.get("startPosition", 0)),
                            float(raw.get("fee", 0)),
                            raw.get("oid"),
                        )
                    )
                    if newest_batch_ts is None or ts > newest_batch_ts:
                        newest_batch_ts = ts

                if rows:
                    inserted = self._insert_fills_batch(addr, rows)
                    total_inserted += inserted

                # Update sync state after each batch
                self._update_sync_state_fills(addr)

                if progress is not None:
                    progress.update(len(raw_fills))
                    progress.set_postfix_str(_colour_postfix(batch=str(batch_num), fetched=_format_count(total_fetched), inserted=_format_count(total_inserted)))

                # Paginate forward: API returns oldest first
                if newest_batch_ts is not None:
                    current_start_ms = newest_batch_ts + 1

                if len(raw_fills) < MAX_PER_REQUEST:
                    break
        finally:
            if own_bar and progress is not None:
                progress.close()

        logger.info("Synced %d new fills for %s (fetched %d)", total_inserted, addr, total_fetched)
        return total_inserted

    def _insert_fills_batch(self, address: str, rows: list[tuple]) -> int:
        """Insert a batch of fill rows, ignoring duplicates.

        :param address:
            Account address for per-address counting.
        :param rows:
            Tuples of fill data.
        :return:
            Number of rows actually inserted.
        """
        with self._db_lock:
            before = self.con.execute("SELECT COUNT(*) FROM fills WHERE address = ?", [address]).fetchone()[0]
            self.con.executemany(
                """
                INSERT OR IGNORE INTO fills (address, trade_id, ts, coin, side, sz, px, closed_pnl, start_position, fee, oid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            after = self.con.execute("SELECT COUNT(*) FROM fills WHERE address = ?", [address]).fetchone()[0]
        return after - before

    # ──────────────────────────────────────────────
    # Sync: funding
    # ──────────────────────────────────────────────

    def sync_account_funding(
        self,
        session: HyperliquidSession,
        address: HexAddress,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        timeout: float = 30.0,
        progress: tqdm | None = None,
    ) -> int:
        """Fetch new funding payments since last sync and store them.

        Proxy support is handled by the session's built-in proxy rotation
        via :py:meth:`~eth_defi.hyperliquid.session.HyperliquidSession.post_info`.

        :param session:
            Hyperliquid API session (with optional proxy configuration).
        :param address:
            Account address.
        :param start_time:
            Override start time.
        :param end_time:
            Override end time.
        :param timeout:
            HTTP request timeout.
        :param progress:
            External tqdm bar to reuse. If None, creates and manages its own.
        :return:
            Number of new funding payments inserted.
        """
        addr = address.lower()

        if end_time is None:
            end_time = native_datetime_utc_now()
        end_ms = int(end_time.timestamp() * 1000)

        if start_time is None:
            state = self._get_sync_state_row(addr, "funding")
            if state and state["newest_ts"]:
                start_ms = state["newest_ts"]
            else:
                start_ms = int((end_time - datetime.timedelta(days=365)).timestamp() * 1000)
        else:
            start_ms = int(start_time.timestamp() * 1000)

        logger.info("Syncing funding for %s from ts=%d to ts=%d", addr, start_ms, end_ms)

        total_inserted = 0
        total_fetched = 0
        batch_num = 0
        current_start_ms = start_ms

        if progress is False:
            progress = None
            own_bar = False
        elif progress is None:
            own_bar = True
            progress = tqdm(
                desc=_colour_desc("Funding", addr[:10]),
                unit="payment",
                leave=False,
                colour="cyan",
            )
        else:
            own_bar = False
            progress.n = 0
            progress.last_print_n = 0
            progress.unit = "payment"
            progress.set_postfix_str("")
            progress.set_description_str(_colour_desc("Funding", addr[:10]))

        try:
            while current_start_ms < end_ms:
                payload = {
                    "type": "userFunding",
                    "user": addr,
                    "startTime": current_start_ms,
                    "endTime": end_ms,
                }

                response = session.post_info(payload, timeout=timeout)
                response.raise_for_status()
                raw_funding = response.json()

                if not raw_funding:
                    break

                batch_num += 1
                total_fetched += len(raw_funding)

                rows = []
                newest_batch_ts = None
                for raw in raw_funding:
                    ts = raw["time"]
                    delta = raw.get("delta", raw)
                    rows.append(
                        (
                            addr,
                            ts,
                            delta["coin"],
                            float(delta.get("usdc", 0)),
                            float(delta.get("szi", 0)),
                            float(delta.get("fundingRate", 0)),
                        )
                    )
                    if newest_batch_ts is None or ts > newest_batch_ts:
                        newest_batch_ts = ts

                if rows:
                    inserted = self._insert_funding_batch(addr, rows)
                    total_inserted += inserted

                self._update_sync_state_funding(addr)

                if progress is not None:
                    progress.update(len(raw_funding))
                    progress.set_postfix_str(_colour_postfix(batch=str(batch_num), fetched=_format_count(total_fetched), inserted=_format_count(total_inserted)))

                # Paginate forward: API returns oldest first
                if newest_batch_ts is not None:
                    current_start_ms = newest_batch_ts + 1

                if len(raw_funding) < MAX_FUNDING_PER_REQUEST:
                    break
        finally:
            if own_bar and progress is not None:
                progress.close()

        logger.info("Synced %d new funding payments for %s (fetched %d)", total_inserted, addr, total_fetched)
        return total_inserted

    def _insert_funding_batch(self, address: str, rows: list[tuple]) -> int:
        """Insert a batch of funding rows, ignoring duplicates.

        :param address:
            Account address for per-address counting.
        :param rows:
            Tuples of funding data.
        :return:
            Number of rows actually inserted.
        """
        with self._db_lock:
            before = self.con.execute("SELECT COUNT(*) FROM funding WHERE address = ?", [address]).fetchone()[0]
            self.con.executemany(
                """
                INSERT OR IGNORE INTO funding (address, ts, coin, usdc, sz, rate)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            after = self.con.execute("SELECT COUNT(*) FROM funding WHERE address = ?", [address]).fetchone()[0]
        return after - before

    # ──────────────────────────────────────────────
    # Sync: ledger
    # ──────────────────────────────────────────────

    def sync_account_ledger(
        self,
        session: HyperliquidSession,
        address: HexAddress,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        timeout: float = 30.0,
        progress: tqdm | None = None,
    ) -> int:
        """Fetch new ledger events since last sync and store them.

        Proxy support is handled by the session's built-in proxy rotation
        via :py:meth:`~eth_defi.hyperliquid.session.HyperliquidSession.post_info`.

        :param session:
            Hyperliquid API session (with optional proxy configuration).
        :param address:
            Account address.
        :param start_time:
            Override start time.
        :param end_time:
            Override end time.
        :param timeout:
            HTTP request timeout.
        :param progress:
            External tqdm bar to reuse. If None, creates and manages its own.
        :return:
            Number of new ledger events inserted.
        """
        addr = address.lower()

        if end_time is None:
            end_time = native_datetime_utc_now()
        end_ms = int(end_time.timestamp() * 1000)

        if start_time is None:
            state = self._get_sync_state_row(addr, "ledger")
            if state and state["newest_ts"]:
                start_ms = state["newest_ts"]
            else:
                start_ms = int((end_time - datetime.timedelta(days=365)).timestamp() * 1000)
        else:
            start_ms = int(start_time.timestamp() * 1000)

        logger.info("Syncing ledger for %s from ts=%d to ts=%d", addr, start_ms, end_ms)

        total_inserted = 0
        total_fetched = 0
        batch_num = 0
        current_start_ms = start_ms

        if progress is False:
            progress = None
            own_bar = False
        elif progress is None:
            own_bar = True
            progress = tqdm(
                desc=_colour_desc("Ledger", addr[:10]),
                unit="event",
                leave=False,
                colour="cyan",
            )
        else:
            own_bar = False
            progress.n = 0
            progress.last_print_n = 0
            progress.unit = "event"
            progress.set_postfix_str("")
            progress.set_description_str(_colour_desc("Ledger", addr[:10]))

        try:
            while current_start_ms < end_ms:
                payload = {
                    "type": "userNonFundingLedgerUpdates",
                    "user": addr,
                    "startTime": current_start_ms,
                    "endTime": end_ms,
                }

                response = session.post_info(payload, timeout=timeout)
                response.raise_for_status()
                raw_updates = response.json()

                if not raw_updates:
                    break

                batch_num += 1
                total_fetched += len(raw_updates)

                rows = []
                newest_batch_ts = None
                for raw in raw_updates:
                    ts = raw["time"]
                    delta = raw.get("delta", {})
                    event_type = delta.get("type", "unknown")
                    usdc = float(delta.get("usdc", 0))
                    vault = delta.get("vault")

                    rows.append((addr, ts, event_type, usdc, vault))
                    if newest_batch_ts is None or ts > newest_batch_ts:
                        newest_batch_ts = ts

                if rows:
                    inserted = self._insert_ledger_batch(addr, rows)
                    total_inserted += inserted

                self._update_sync_state_ledger(addr)

                if progress is not None:
                    progress.update(len(raw_updates))
                    progress.set_postfix_str(_colour_postfix(batch=str(batch_num), fetched=_format_count(total_fetched), inserted=_format_count(total_inserted)))

                # Paginate forward: API returns oldest first
                if newest_batch_ts is not None:
                    current_start_ms = newest_batch_ts + 1

                if len(raw_updates) < MAX_PER_REQUEST:
                    break
        finally:
            if own_bar and progress is not None:
                progress.close()

        logger.info("Synced %d new ledger events for %s (fetched %d)", total_inserted, addr, total_fetched)
        return total_inserted

    def _insert_ledger_batch(self, address: str, rows: list[tuple]) -> int:
        """Insert a batch of ledger rows, ignoring duplicates.

        :param address:
            Account address for per-address counting.
        :param rows:
            Tuples of ledger data.
        :return:
            Number of rows actually inserted.
        """
        with self._db_lock:
            before = self.con.execute("SELECT COUNT(*) FROM ledger WHERE address = ?", [address]).fetchone()[0]
            self.con.executemany(
                """
                INSERT OR IGNORE INTO ledger (address, ts, event_type, usdc, vault)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            after = self.con.execute("SELECT COUNT(*) FROM ledger WHERE address = ?", [address]).fetchone()[0]
        return after - before

    # ──────────────────────────────────────────────
    # Sync: orchestrator
    # ──────────────────────────────────────────────

    def sync_account(
        self,
        session: HyperliquidSession,
        address: HexAddress,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
        timeout: float = 30.0,
        progress: tqdm | None = None,
        label: str | None = None,
    ) -> dict[str, int]:
        """Sync all data types for a single account.

        Each data type (fills, funding, ledger) is synced independently
        with its own sync_state watermark. Individual batch inserts use
        ``INSERT OR IGNORE`` for idempotent crash recovery.

        When ``progress`` is provided, it is reused across all three data
        types. The bar description updates as it moves through fills,
        funding, and ledger.

        Proxy support is handled by the session's built-in proxy rotation.
        In threaded mode, each worker should receive its own session clone
        via :py:meth:`~eth_defi.hyperliquid.session.HyperliquidSession.clone_for_worker`.

        :param session:
            Hyperliquid API session (with optional proxy configuration).
        :param address:
            Account address.
        :param start_time:
            Override start time.
        :param end_time:
            Override end time.
        :param timeout:
            HTTP request timeout.
        :param progress:
            External tqdm bar to reuse across data types (None for auto).
        :param label:
            Short display name for progress bars (falls back to address prefix).
        :return:
            Dict with counts: ``{"fills": N, "funding": N, "ledger": N}``.
        """
        addr = address.lower()
        short = label or addr[:10]
        logger.info("Syncing all data for account %s", addr)

        # When an external progress bar is provided (threaded mode), use it
        # as a 3-step tracker (fills → funding → ledger) with a visible
        # progress bar and ETA.  Sub-methods run without their own bars.
        if progress is not None:
            progress.n = 0
            progress.last_print_n = 0
            progress.total = 3
            progress.unit = "step"
            progress.set_postfix_str("")
            progress.set_description_str(_colour_desc("Fills", short))

        # Pass progress=False to suppress sub-method bar creation in threaded mode
        sub_progress = False if progress is not None else None

        fills_count = self.sync_account_fills(session, addr, start_time=start_time, end_time=end_time, timeout=timeout, progress=sub_progress)
        self.save()

        if progress is not None:
            progress.update(1)
            progress.set_description_str(_colour_desc("Funding", short))
            progress.set_postfix_str(_colour_postfix(fills=_format_count(fills_count)))

        funding_count = self.sync_account_funding(session, addr, start_time=start_time, end_time=end_time, timeout=timeout, progress=sub_progress)
        self.save()

        if progress is not None:
            progress.update(1)
            progress.set_description_str(_colour_desc("Ledger", short))
            progress.set_postfix_str(_colour_postfix(fills=_format_count(fills_count), funding=_format_count(funding_count)))

        ledger_count = self.sync_account_ledger(session, addr, start_time=start_time, end_time=end_time, timeout=timeout, progress=sub_progress)
        self.save()

        if progress is not None:
            progress.update(1)
            progress.set_postfix_str(_colour_postfix(fills=_format_count(fills_count), funding=_format_count(funding_count), ledger=_format_count(ledger_count)))

        result = {"fills": fills_count, "funding": funding_count, "ledger": ledger_count}
        logger.info("Sync complete for %s: %s", addr, result)
        return result

    def sync_all(
        self,
        session: HyperliquidSession,
        max_workers: int = 1,
        timeout: float = 30.0,
    ) -> dict[str, dict[str, int]]:
        """Sync all whitelisted accounts.

        When ``max_workers > 1``, accounts are synced in parallel using a
        thread pool. Each worker gets its own session clone via
        :py:meth:`~eth_defi.hyperliquid.session.HyperliquidSession.clone_for_worker`,
        which shares rate-limiter adapters and
        :py:class:`~eth_defi.event_reader.webshare.ProxyStateManager` but
        starts on a different proxy for load distribution across IPs.

        Proxy support is configured on the session itself via
        :py:meth:`~eth_defi.hyperliquid.session.HyperliquidSession.configure_rotator`
        or the ``rotator`` / ``proxy_urls`` parameter of
        :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

        Progress display:

        - Position 0: overall account progress
        - Positions 1..N: one bar per worker showing current data type

        :param session:
            Hyperliquid API session (with optional proxy configuration).
        :param max_workers:
            Number of parallel workers for concurrent API calls.
        :param timeout:
            HTTP request timeout.
        :return:
            Dict mapping address to sync counts.
        """
        accounts = self.get_accounts()
        if not accounts:
            return {}

        results = {}
        total_events = 0

        if max_workers <= 1:
            # Sequential path: use the session directly (proxy rotation is built in)
            overall = tqdm(
                accounts,
                desc="Syncing accounts",
                unit="account",
                colour="green",
            )
            for account in overall:
                addr = account["address"]
                label = account.get("label", addr[:10])
                overall.set_postfix_str(_colour_postfix(account=label, total=_format_count(total_events)))
                try:
                    result = self.sync_account(session, addr, timeout=timeout)
                    results[addr] = result
                    total_events += result.get("fills", 0) + result.get("funding", 0) + result.get("ledger", 0)
                    overall.set_postfix_str(_colour_postfix(account=label, total=_format_count(total_events)))
                except Exception:
                    logger.exception("Failed to sync account %s", addr)
                    results[addr] = {"fills": 0, "funding": 0, "ledger": 0, "error": True}
            return results

        # Threaded path with nested progress bars.
        # Pre-create all bars in the main thread using standard tqdm
        # (not tqdm_loggable) for reliable cursor positioning.
        n_bars = min(max_workers, len(accounts))
        overall = tqdm_std(
            total=len(accounts),
            desc="Syncing accounts",
            unit="account",
            position=0,
            colour="green",
        )
        worker_bars = [
            tqdm_std(
                total=3,
                desc=f"{_DIM}Worker {i + 1} idle{_RESET}",
                unit="step",
                leave=False,
                position=i + 1,
                colour="cyan",
            )
            for i in range(n_bars)
        ]

        # Thread-safe pool of pre-created bars and session clones
        bar_pool: list[tqdm_std] = list(worker_bars)
        bar_lock = threading.Lock()

        # Pre-create per-worker session clones. Each clone shares the same
        # rate-limiter adapters and ProxyStateManager but starts on a
        # different proxy for load distribution across IPs.
        session_pool: list[HyperliquidSession] = [session.clone_for_worker(proxy_start_index=i) for i in range(n_bars)]
        session_lock = threading.Lock()

        def _sync_worker(account: dict) -> tuple[str, dict[str, int]]:
            addr = account["address"]
            short_label = account.get("label") or addr[:10]
            with bar_lock:
                bar = bar_pool.pop()
            with session_lock:
                worker_session = session_pool.pop()
            try:
                result = self.sync_account(worker_session, addr, timeout=timeout, progress=bar, label=short_label)
                return addr, result
            except Exception:
                logger.exception("Failed to sync account %s", addr)
                return addr, {"fills": 0, "funding": 0, "ledger": 0, "error": True}
            finally:
                bar.set_description_str(f"{_DIM}Worker idle{_RESET}")
                bar.set_postfix_str("")
                with bar_lock:
                    bar_pool.append(bar)
                with session_lock:
                    session_pool.append(worker_session)

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_sync_worker, account): account for account in accounts}

                for future in as_completed(futures):
                    addr, result = future.result()
                    results[addr] = result
                    total_events += result.get("fills", 0) + result.get("funding", 0) + result.get("ledger", 0)
                    overall.update(1)
                    overall.set_postfix_str(_colour_postfix(total=_format_count(total_events)))
        finally:
            for bar in worker_bars:
                bar.close()
            overall.close()

        return results

    # ──────────────────────────────────────────────
    # Data retrieval
    # ──────────────────────────────────────────────

    def get_fills(
        self,
        address: HexAddress,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> list[Fill]:
        """Get stored fills for an account as Fill objects.

        :param address:
            Account address.
        :param start_time:
            Optional start time filter.
        :param end_time:
            Optional end time filter.
        :return:
            List of Fill objects sorted by timestamp ascending.
        """
        addr = address.lower()
        query = "SELECT trade_id, ts, coin, side, sz, px, closed_pnl, start_position, fee, oid FROM fills WHERE address = ?"
        params: list = [addr]

        if start_time is not None:
            query += " AND ts >= ?"
            params.append(int(start_time.timestamp() * 1000))
        if end_time is not None:
            query += " AND ts <= ?"
            params.append(int(end_time.timestamp() * 1000))

        query += " ORDER BY ts ASC"

        with self._db_lock:
            rows = self.con.execute(query, params).fetchall()

        fills = []
        for r in rows:
            fills.append(
                Fill(
                    coin=r[2],
                    side="B" if r[3] == 0 else "A",
                    size=Decimal(str(r[4])),
                    price=Decimal(str(r[5])),
                    timestamp_ms=r[1],
                    start_position=Decimal(str(r[7])) if r[7] is not None else Decimal("0"),
                    closed_pnl=Decimal(str(r[6])) if r[6] is not None else Decimal("0"),
                    direction_hint="",
                    hash=None,
                    order_id=r[9],
                    trade_id=r[0],
                    fee=Decimal(str(r[8])) if r[8] is not None else Decimal("0"),
                    fee_token="USDC",
                )
            )
        return fills

    def get_funding(
        self,
        address: HexAddress,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> list[FundingPayment]:
        """Get stored funding payments for an account.

        :param address:
            Account address.
        :param start_time:
            Optional start time filter.
        :param end_time:
            Optional end time filter.
        :return:
            List of FundingPayment objects sorted by timestamp ascending.
        """
        addr = address.lower()
        query = "SELECT ts, coin, usdc, sz, rate FROM funding WHERE address = ?"
        params: list = [addr]

        if start_time is not None:
            query += " AND ts >= ?"
            params.append(int(start_time.timestamp() * 1000))
        if end_time is not None:
            query += " AND ts <= ?"
            params.append(int(end_time.timestamp() * 1000))

        query += " ORDER BY ts ASC"

        with self._db_lock:
            rows = self.con.execute(query, params).fetchall()

        return [
            FundingPayment(
                coin=r[1],
                funding_rate=Decimal(str(r[4])) if r[4] is not None else Decimal("0"),
                usdc=Decimal(str(r[2])),
                position_size=Decimal(str(r[3])) if r[3] is not None else Decimal("0"),
                timestamp=datetime.datetime.fromtimestamp(r[0] / 1000),
                timestamp_ms=r[0],
            )
            for r in rows
        ]

    def get_fill_count(self, address: HexAddress) -> int:
        """Get the number of stored fills for an account."""
        with self._db_lock:
            result = self.con.execute(
                "SELECT COUNT(*) FROM fills WHERE address = ?",
                [address.lower()],
            ).fetchone()
        return result[0] if result else 0

    def get_funding_count(self, address: HexAddress) -> int:
        """Get the number of stored funding payments for an account."""
        with self._db_lock:
            result = self.con.execute(
                "SELECT COUNT(*) FROM funding WHERE address = ?",
                [address.lower()],
            ).fetchone()
        return result[0] if result else 0

    def get_ledger_count(self, address: HexAddress) -> int:
        """Get the number of stored ledger events for an account."""
        with self._db_lock:
            result = self.con.execute(
                "SELECT COUNT(*) FROM ledger WHERE address = ?",
                [address.lower()],
            ).fetchone()
        return result[0] if result else 0

    def get_total_row_counts(self) -> dict[str, int]:
        """Get total row counts across all accounts for each table.

        :return:
            Dict with keys ``fills``, ``funding``, ``ledger`` and integer counts.
        """
        with self._db_lock:
            fills = self.con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            funding = self.con.execute("SELECT COUNT(*) FROM funding").fetchone()[0]
            ledger = self.con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        return {"fills": fills, "funding": funding, "ledger": ledger}

    # ──────────────────────────────────────────────
    # Sync state
    # ──────────────────────────────────────────────

    def get_sync_state(self, address: HexAddress) -> dict[str, dict]:
        """Get sync state for all data types for an account.

        :param address:
            Account address.
        :return:
            Dict mapping data_type to state dict with oldest_ts, newest_ts, row_count, last_synced.
        """
        addr = address.lower()
        with self._db_lock:
            rows = self.con.execute(
                "SELECT data_type, oldest_ts, newest_ts, row_count, last_synced FROM sync_state WHERE address = ?",
                [addr],
            ).fetchall()
        return {
            r[0]: {
                "oldest_ts": r[1],
                "newest_ts": r[2],
                "row_count": r[3],
                "last_synced": r[4],
            }
            for r in rows
        }

    def _get_sync_state_row(self, address: str, data_type: str) -> dict | None:
        """Get sync state for a specific data type."""
        with self._db_lock:
            row = self.con.execute(
                "SELECT oldest_ts, newest_ts, row_count, last_synced FROM sync_state WHERE address = ? AND data_type = ?",
                [address, data_type],
            ).fetchone()
        if row is None:
            return None
        return {
            "oldest_ts": row[0],
            "newest_ts": row[1],
            "row_count": row[2],
            "last_synced": row[3],
        }

    def _update_sync_state_fills(self, address: str) -> None:
        """Recompute and store sync state for fills."""
        with self._db_lock:
            row = self.con.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM fills WHERE address = ?",
                [address],
            ).fetchone()
            now_ms = int(native_datetime_utc_now().timestamp() * 1000)
            self.con.execute(
                """
                INSERT INTO sync_state (address, data_type, oldest_ts, newest_ts, row_count, last_synced)
                VALUES (?, 'fills', ?, ?, ?, ?)
                ON CONFLICT (address, data_type) DO UPDATE SET
                    oldest_ts = EXCLUDED.oldest_ts,
                    newest_ts = EXCLUDED.newest_ts,
                    row_count = EXCLUDED.row_count,
                    last_synced = EXCLUDED.last_synced
                """,
                [address, row[0], row[1], row[2], now_ms],
            )

    def _update_sync_state_funding(self, address: str) -> None:
        """Recompute and store sync state for funding."""
        with self._db_lock:
            row = self.con.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM funding WHERE address = ?",
                [address],
            ).fetchone()
            now_ms = int(native_datetime_utc_now().timestamp() * 1000)
            self.con.execute(
                """
                INSERT INTO sync_state (address, data_type, oldest_ts, newest_ts, row_count, last_synced)
                VALUES (?, 'funding', ?, ?, ?, ?)
                ON CONFLICT (address, data_type) DO UPDATE SET
                    oldest_ts = EXCLUDED.oldest_ts,
                    newest_ts = EXCLUDED.newest_ts,
                    row_count = EXCLUDED.row_count,
                    last_synced = EXCLUDED.last_synced
                """,
                [address, row[0], row[1], row[2], now_ms],
            )

    def _update_sync_state_ledger(self, address: str) -> None:
        """Recompute and store sync state for ledger."""
        with self._db_lock:
            row = self.con.execute(
                "SELECT MIN(ts), MAX(ts), COUNT(*) FROM ledger WHERE address = ?",
                [address],
            ).fetchone()
            now_ms = int(native_datetime_utc_now().timestamp() * 1000)
            self.con.execute(
                """
                INSERT INTO sync_state (address, data_type, oldest_ts, newest_ts, row_count, last_synced)
                VALUES (?, 'ledger', ?, ?, ?, ?)
                ON CONFLICT (address, data_type) DO UPDATE SET
                    oldest_ts = EXCLUDED.oldest_ts,
                    newest_ts = EXCLUDED.newest_ts,
                    row_count = EXCLUDED.row_count,
                    last_synced = EXCLUDED.last_synced
                """,
                [address, row[0], row[1], row[2], now_ms],
            )
