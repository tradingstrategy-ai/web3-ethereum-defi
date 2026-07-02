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

Default: ``~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb``

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
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
from eth_typing import HexAddress
from tqdm import tqdm as tqdm_std
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.api import (
    fetch_active_asset_data_raw,
    fetch_frontend_open_orders_raw,
    fetch_historical_orders_raw,
    fetch_open_orders_raw,
    fetch_perp_clearinghouse_state_raw,
    fetch_user_twap_slice_fills_raw,
)
from eth_defi.hyperliquid.position import Fill
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.hyperliquid.trade_history import (
    FundingPayment,
    attach_funding_to_trades,
    group_fills_into_trades,
)
from eth_defi.hyperliquid.position import reconstruct_position_history

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LedgerEvent:
    """A deposit, withdrawal, or other non-funding ledger event from DuckDB storage.

    Represents a row from the ``ledger`` table. The ``event_type`` field
    contains the raw API type string (e.g. ``"vaultDeposit"``,
    ``"vaultWithdraw"``, ``"deposit"``, ``"withdraw"``).
    """

    #: Event timestamp
    timestamp: datetime.datetime
    #: Timestamp in milliseconds (for storage and sorting)
    timestamp_ms: int
    #: Raw API event type (e.g. "vaultDeposit", "withdraw", "deposit")
    event_type: str
    #: USDC amount
    usdc: float
    #: Associated vault address (if any)
    vault: str | None


#: Default DuckDB path for trade history
DEFAULT_TRADE_HISTORY_DB_PATH = Path("~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb").expanduser()

#: Maximum records per API request for fills and ledger
MAX_PER_REQUEST = 2000

#: Maximum records per API request for funding
MAX_FUNDING_PER_REQUEST = 500

#: Snapshot run schema version
SNAPSHOT_VERSION = 1

#: Account-level raw snapshot sources
SNAPSHOT_SOURCE_NAMES: tuple[str, ...] = (
    "clearinghouseState",
    "openOrders",
    "frontendOpenOrders",
    "historicalOrders",
    "userTwapSliceFills",
)


def _json_dumps(data: object) -> str:
    """Serialise raw payload data to compact JSON text."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True)


def _decimal_or_none(value: object) -> Decimal | None:
    """Convert a raw payload value to :py:class:`Decimal` when present."""
    if value is None:
        return None
    return Decimal(str(value))


def _float_or_none(value: object) -> float | None:
    """Convert a raw payload value to float when present."""
    if value is None:
        return None
    return float(value)


def _int_or_none(value: object) -> int | None:
    """Convert a raw payload value to int when present."""
    if value is None:
        return None
    return int(value)


def _normalise_order_entry(entry: dict) -> tuple[dict, str | None, int | None]:
    """Extract an order payload plus optional status metadata."""
    order = entry.get("order", entry)
    return order, entry.get("status"), _int_or_none(entry.get("statusTimestamp"))


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
        """Close the database connection.

        Uses ``_db_lock`` so any in-flight database operation completes
        before the connection is torn down.
        """
        with self._db_lock:
            if self.con is not None:
                self.con.close()
                self.con = None

    def save(self):
        """Force a checkpoint to ensure data is persisted to disk."""
        with self._db_lock:
            if self.con is None:
                return
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

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS account_snapshot_runs (
                address VARCHAR NOT NULL,
                ts BIGINT NOT NULL,
                label VARCHAR,
                is_vault BOOLEAN NOT NULL,
                dex VARCHAR NOT NULL DEFAULT '',
                fills_row_count INTEGER NOT NULL,
                funding_row_count INTEGER NOT NULL,
                ledger_row_count INTEGER NOT NULL,
                open_position_count INTEGER NOT NULL DEFAULT 0,
                open_trade_count INTEGER NOT NULL DEFAULT 0,
                open_order_count INTEGER NOT NULL DEFAULT 0,
                historical_order_count INTEGER,
                twap_slice_fill_count INTEGER,
                snapshot_version INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (address, ts)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS account_snapshot_sources (
                address VARCHAR NOT NULL,
                ts BIGINT NOT NULL,
                source VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                item_count INTEGER,
                payload_json VARCHAR,
                error_message VARCHAR,
                PRIMARY KEY (address, ts, source)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS open_position_snapshots (
                address VARCHAR NOT NULL,
                ts BIGINT NOT NULL,
                coin VARCHAR NOT NULL,
                position_type VARCHAR,
                size FLOAT NOT NULL,
                entry_px FLOAT,
                unrealised_pnl FLOAT NOT NULL,
                margin_used FLOAT NOT NULL,
                position_value FLOAT NOT NULL,
                liquidation_px FLOAT,
                leverage_type VARCHAR,
                leverage_value INTEGER,
                max_leverage INTEGER,
                return_on_equity FLOAT,
                cumulative_funding_all_time FLOAT,
                cumulative_funding_since_open FLOAT,
                cumulative_funding_since_change FLOAT,
                mark_px FLOAT,
                available_to_trade_long FLOAT,
                available_to_trade_short FLOAT,
                max_trade_sz_long FLOAT,
                max_trade_sz_short FLOAT,
                position_json VARCHAR NOT NULL,
                active_asset_data_json VARCHAR,
                PRIMARY KEY (address, ts, coin)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS open_trade_snapshots (
                address VARCHAR NOT NULL,
                ts BIGINT NOT NULL,
                trade_index INTEGER NOT NULL,
                coin VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                is_complete BOOLEAN NOT NULL,
                opened_at BIGINT NOT NULL,
                entry_price FLOAT,
                current_size FLOAT NOT NULL,
                max_size FLOAT NOT NULL,
                realised_pnl FLOAT NOT NULL,
                funding_pnl FLOAT NOT NULL,
                total_fees FLOAT NOT NULL,
                net_pnl FLOAT NOT NULL,
                unrealised_pnl FLOAT,
                fill_count INTEGER NOT NULL,
                trade_json VARCHAR NOT NULL,
                PRIMARY KEY (address, ts, trade_index)
            )
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS open_order_snapshots (
                address VARCHAR NOT NULL,
                ts BIGINT NOT NULL,
                order_index INTEGER NOT NULL,
                source VARCHAR NOT NULL,
                coin VARCHAR NOT NULL,
                side VARCHAR,
                limit_px FLOAT,
                sz FLOAT,
                orig_sz FLOAT,
                oid BIGINT,
                cloid VARCHAR,
                order_ts BIGINT,
                status VARCHAR,
                status_timestamp BIGINT,
                trigger_condition VARCHAR,
                is_trigger BOOLEAN,
                trigger_px FLOAT,
                is_position_tpsl BOOLEAN,
                reduce_only BOOLEAN,
                order_type VARCHAR,
                tif VARCHAR,
                order_json VARCHAR NOT NULL,
                PRIMARY KEY (address, ts, order_index)
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

        Idempotent — re-adding an existing account updates the label.
        The ``is_vault`` flag can only be upgraded from ``False`` to ``True``,
        never downgraded, to prevent ``SCAN=top_traders`` runs from
        incorrectly clearing the vault flag on known vaults.

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
                ON CONFLICT (address) DO UPDATE SET
                    label = COALESCE(EXCLUDED.label, accounts.label),
                    is_vault = accounts.is_vault OR EXCLUDED.is_vault
                """,
                [address.lower(), label, is_vault, now_ms],
            )
        logger.info("Added account %s (%s) to whitelist", address, label or "unlabelled")

    #: Ledger event types that only appear for vault accounts
    VAULT_LEDGER_EVENT_TYPES: set[str] = {"vaultCreate", "vaultDeposit", "vaultWithdraw", "vaultDistribution", "vaultLeaderCommission"}

    def is_vault_address(self, address: HexAddress) -> bool:
        """Detect whether an account is a vault from its stored ledger events.

        Scans for vault-specific event types (``vaultCreate``,
        ``vaultDeposit``, ``vaultWithdraw``, etc.) which only appear
        for vault accounts. This is more reliable than the ``is_vault``
        flag in the accounts table, which can be incorrectly set when
        accounts are added via trader scanning modes.

        Falls back to the ``is_vault`` flag if no ledger events exist yet.

        :param address:
            Account address.
        :return:
            ``True`` if vault-specific ledger events are found, or if
            the ``is_vault`` flag is ``True`` in the accounts table.
        """
        addr = address.lower()
        with self._db_lock:
            vault_event = self.con.execute(
                """
                SELECT 1 FROM ledger
                WHERE address = ? AND event_type IN ('vaultCreate', 'vaultDeposit', 'vaultWithdraw', 'vaultDistribution', 'vaultLeaderCommission')
                LIMIT 1
                """,
                [addr],
            ).fetchone()

        if vault_event is not None:
            return True

        # Fall back to DB flag
        with self._db_lock:
            row = self.con.execute(
                "SELECT is_vault FROM accounts WHERE address = ?",
                [addr],
            ).fetchone()

        return bool(row[0]) if row else False

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

    def get_accounts(self, is_vault: bool | None = None) -> list[dict]:
        """Get whitelisted accounts, optionally filtered by vault status.

        :param is_vault:
            If ``True``, return only vault accounts.
            If ``False``, return only trader accounts.
            If ``None`` (default), return all accounts.
        :return:
            List of account dicts with address, label, is_vault, added_at.
        """
        with self._db_lock:
            if is_vault is None:
                result = self.con.execute("SELECT address, label, is_vault, added_at FROM accounts ORDER BY added_at").fetchall()
            else:
                result = self.con.execute("SELECT address, label, is_vault, added_at FROM accounts WHERE is_vault = ? ORDER BY added_at", [is_vault]).fetchall()
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

        # Always update sync state so resume logic knows this window was scanned,
        # even when the API returned zero fills.
        self._update_sync_state_fills(addr)

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

        # Always update sync state so resume logic knows this window was scanned,
        # even when the API returned zero funding payments.
        self._update_sync_state_funding(addr)

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

        # Always update sync state so resume logic knows this window was scanned,
        # even when the API returned zero ledger events.
        self._update_sync_state_ledger(addr)

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
    # Snapshots
    # ──────────────────────────────────────────────

    def _delete_snapshot_rows(self, address: str, timestamp_ms: int) -> None:
        """Delete any existing snapshot rows for one account + timestamp."""
        with self._db_lock:
            for table in (
                "account_snapshot_runs",
                "account_snapshot_sources",
                "open_position_snapshots",
                "open_trade_snapshots",
                "open_order_snapshots",
            ):
                self.con.execute(
                    f"DELETE FROM {table} WHERE address = ? AND ts = ?",
                    [address, timestamp_ms],
                )

    def _insert_snapshot_run(self, row: tuple) -> None:
        """Insert one snapshot run row."""
        with self._db_lock:
            self.con.execute(
                """
                INSERT INTO account_snapshot_runs (
                    address, ts, label, is_vault, dex,
                    fills_row_count, funding_row_count, ledger_row_count,
                    open_position_count, open_trade_count, open_order_count,
                    historical_order_count, twap_slice_fill_count, snapshot_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )

    def _insert_snapshot_source(
        self,
        address: str,
        timestamp_ms: int,
        source: str,
        *,
        status: str,
        item_count: int | None,
        payload: object | None,
        error_message: str | None = None,
    ) -> None:
        """Insert one raw source payload or source error."""
        payload_json = _json_dumps(payload) if payload is not None else None
        with self._db_lock:
            self.con.execute(
                """
                INSERT INTO account_snapshot_sources (
                    address, ts, source, status, item_count, payload_json, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [address, timestamp_ms, source, status, item_count, payload_json, error_message],
            )

    def _insert_open_positions_batch(self, rows: list[tuple]) -> None:
        """Insert materialised open position rows for a snapshot."""
        if not rows:
            return
        with self._db_lock:
            self.con.executemany(
                """
                INSERT INTO open_position_snapshots (
                    address, ts, coin, position_type, size, entry_px,
                    unrealised_pnl, margin_used, position_value, liquidation_px,
                    leverage_type, leverage_value, max_leverage, return_on_equity,
                    cumulative_funding_all_time, cumulative_funding_since_open,
                    cumulative_funding_since_change, mark_px,
                    available_to_trade_long, available_to_trade_short,
                    max_trade_sz_long, max_trade_sz_short, position_json,
                    active_asset_data_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _insert_open_orders_batch(self, rows: list[tuple]) -> None:
        """Insert materialised open order rows for a snapshot."""
        if not rows:
            return
        with self._db_lock:
            self.con.executemany(
                """
                INSERT INTO open_order_snapshots (
                    address, ts, order_index, source, coin, side, limit_px,
                    sz, orig_sz, oid, cloid, order_ts, status,
                    status_timestamp, trigger_condition, is_trigger, trigger_px,
                    is_position_tpsl, reduce_only, order_type, tif, order_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _insert_open_trades_batch(self, rows: list[tuple]) -> None:
        """Insert materialised derived open trade rows for a snapshot."""
        if not rows:
            return
        with self._db_lock:
            self.con.executemany(
                """
                INSERT INTO open_trade_snapshots (
                    address, ts, trade_index, coin, direction, is_complete,
                    opened_at, entry_price, current_size, max_size,
                    realised_pnl, funding_pnl, total_fees, net_pnl,
                    unrealised_pnl, fill_count, trade_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def capture_account_snapshots(
        self,
        session: HyperliquidSession,
        address: HexAddress,
        *,
        is_vault: bool,
        label: str | None = None,
        timeout: float = 30.0,
        snapshot_time: datetime.datetime | None = None,
    ) -> dict[str, int]:
        """Capture open-state snapshots for a single account.

        Stores raw payloads for each source and materialises row-level
        open positions, open orders, and derived open trades.
        """
        addr = address.lower()
        snapshot_time = snapshot_time or native_datetime_utc_now()
        timestamp_ms = int(snapshot_time.timestamp() * 1000)

        self._delete_snapshot_rows(addr, timestamp_ms)

        fills = self.get_fills(addr)
        funding = self.get_funding(addr)
        ledger_count = self.get_ledger_count(addr)

        clearinghouse_positions: dict[str, object] = {}
        open_orders_payload: list[dict] | None = None
        frontend_open_orders_payload: list[dict] | None = None
        historical_orders_payload: list[dict] | None = None
        twap_slice_fills_payload: list[dict] | None = None
        open_positions_rows: list[tuple] = []
        open_order_rows: list[tuple] = []

        try:
            raw_clearinghouse = fetch_perp_clearinghouse_state_raw(session, addr, timeout=timeout)
            for asset_position in raw_clearinghouse.get("assetPositions", []):
                pos = asset_position.get("position", asset_position)
                leverage = pos.get("leverage", {})
                cumulative_funding = pos.get("cumFunding", {})
                coin = pos["coin"]
                clearinghouse_positions[coin] = _decimal_or_none(pos.get("unrealizedPnl"))

                active_asset_data = None
                source_name = f"activeAssetData:{coin}"
                try:
                    active_asset_data = fetch_active_asset_data_raw(session, addr, coin, timeout=timeout)
                    self._insert_snapshot_source(
                        addr,
                        timestamp_ms,
                        source_name,
                        status="ok",
                        item_count=1,
                        payload=active_asset_data,
                    )
                except Exception as exc:
                    logger.warning("Failed to fetch %s for %s", source_name, addr, exc_info=True)
                    self._insert_snapshot_source(
                        addr,
                        timestamp_ms,
                        source_name,
                        status="error",
                        item_count=None,
                        payload=None,
                        error_message=str(exc),
                    )

                available_to_trade = active_asset_data.get("availableToTrade", []) if active_asset_data else []
                max_trade_szs = active_asset_data.get("maxTradeSzs", []) if active_asset_data else []
                open_positions_rows.append(
                    (
                        addr,
                        timestamp_ms,
                        coin,
                        asset_position.get("type"),
                        float(pos.get("szi", 0)),
                        _float_or_none(pos.get("entryPx")),
                        float(pos.get("unrealizedPnl", 0)),
                        float(pos.get("marginUsed", 0)),
                        float(pos.get("positionValue", 0)),
                        _float_or_none(pos.get("liquidationPx")),
                        leverage.get("type"),
                        _int_or_none(leverage.get("value")),
                        _int_or_none(pos.get("maxLeverage")),
                        _float_or_none(pos.get("returnOnEquity")),
                        _float_or_none(cumulative_funding.get("allTime")),
                        _float_or_none(cumulative_funding.get("sinceOpen")),
                        _float_or_none(cumulative_funding.get("sinceChange")),
                        _float_or_none(active_asset_data.get("markPx")) if active_asset_data else None,
                        _float_or_none(available_to_trade[0]) if len(available_to_trade) > 0 else None,
                        _float_or_none(available_to_trade[1]) if len(available_to_trade) > 1 else None,
                        _float_or_none(max_trade_szs[0]) if len(max_trade_szs) > 0 else None,
                        _float_or_none(max_trade_szs[1]) if len(max_trade_szs) > 1 else None,
                        _json_dumps(asset_position),
                        _json_dumps(active_asset_data) if active_asset_data is not None else None,
                    )
                )

            self._insert_snapshot_source(
                addr,
                timestamp_ms,
                "clearinghouseState",
                status="ok",
                item_count=len(raw_clearinghouse["assetPositions"]),
                payload=raw_clearinghouse,
            )
        except Exception as exc:
            logger.warning("Failed to fetch clearinghouseState for %s", addr, exc_info=True)
            self._insert_snapshot_source(
                addr,
                timestamp_ms,
                "clearinghouseState",
                status="error",
                item_count=None,
                payload=None,
                error_message=str(exc),
            )

        for source_name, fetcher in (
            ("openOrders", fetch_open_orders_raw),
            ("frontendOpenOrders", fetch_frontend_open_orders_raw),
            ("historicalOrders", fetch_historical_orders_raw),
            ("userTwapSliceFills", fetch_user_twap_slice_fills_raw),
        ):
            try:
                payload = fetcher(session, addr, timeout=timeout)
                self._insert_snapshot_source(
                    addr,
                    timestamp_ms,
                    source_name,
                    status="ok",
                    item_count=len(payload),
                    payload=payload,
                )
                if source_name == "openOrders":
                    open_orders_payload = payload
                elif source_name == "frontendOpenOrders":
                    frontend_open_orders_payload = payload
                elif source_name == "historicalOrders":
                    historical_orders_payload = payload
                else:
                    twap_slice_fills_payload = payload
            except Exception as exc:
                logger.warning("Failed to fetch %s for %s", source_name, addr, exc_info=True)
                self._insert_snapshot_source(
                    addr,
                    timestamp_ms,
                    source_name,
                    status="error",
                    item_count=None,
                    payload=None,
                    error_message=str(exc),
                )

        chosen_order_source = "frontendOpenOrders" if frontend_open_orders_payload is not None else "openOrders"
        chosen_orders = frontend_open_orders_payload if frontend_open_orders_payload is not None else (open_orders_payload or [])
        for index, entry in enumerate(chosen_orders):
            order, status, status_timestamp = _normalise_order_entry(entry)
            open_order_rows.append(
                (
                    addr,
                    timestamp_ms,
                    index,
                    chosen_order_source,
                    order["coin"],
                    order.get("side"),
                    _float_or_none(order.get("limitPx")),
                    _float_or_none(order.get("sz")),
                    _float_or_none(order.get("origSz")),
                    _int_or_none(order.get("oid")),
                    order.get("cloid"),
                    _int_or_none(order.get("timestamp")),
                    status,
                    status_timestamp,
                    order.get("triggerCondition"),
                    order.get("isTrigger"),
                    _float_or_none(order.get("triggerPx")),
                    order.get("isPositionTpsl"),
                    order.get("reduceOnly"),
                    order.get("orderType"),
                    order.get("tif"),
                    _json_dumps(entry),
                )
            )

        events = list(reconstruct_position_history(iter(fills))) if fills else []
        closed_trades, open_trades = group_fills_into_trades(iter(events), fills=fills)
        attach_funding_to_trades(closed_trades, open_trades, funding)

        open_trade_rows = []
        for index, trade in enumerate(open_trades):
            live_position = clearinghouse_positions.get(trade.coin)
            trade.unrealised_pnl = live_position
            trade_payload = {
                "coin": trade.coin,
                "direction": trade.direction.value,
                "is_complete": trade.is_complete,
                "opened_at": int(trade.opened_at.timestamp() * 1000),
                "entry_price": float(trade.entry_price),
                "current_size": float(trade.current_size),
                "max_size": float(trade.max_size),
                "realised_pnl": float(trade.realised_pnl),
                "funding_pnl": float(trade.funding_pnl),
                "total_fees": float(trade.total_fees),
                "net_pnl": float(trade.net_pnl),
                "unrealised_pnl": _float_or_none(trade.unrealised_pnl),
                "fill_count": trade.fill_count,
            }
            open_trade_rows.append(
                (
                    addr,
                    timestamp_ms,
                    index,
                    trade.coin,
                    trade.direction.value,
                    trade.is_complete,
                    int(trade.opened_at.timestamp() * 1000),
                    float(trade.entry_price),
                    float(trade.current_size),
                    float(trade.max_size),
                    float(trade.realised_pnl),
                    float(trade.funding_pnl),
                    float(trade.total_fees),
                    float(trade.net_pnl),
                    _float_or_none(trade.unrealised_pnl),
                    trade.fill_count,
                    _json_dumps(trade_payload),
                )
            )

        self._insert_snapshot_run(
            (
                addr,
                timestamp_ms,
                label,
                is_vault,
                "",
                len(fills),
                len(funding),
                ledger_count,
                len(open_positions_rows),
                len(open_trade_rows),
                len(open_order_rows),
                len(historical_orders_payload) if historical_orders_payload is not None else None,
                len(twap_slice_fills_payload) if twap_slice_fills_payload is not None else None,
                SNAPSHOT_VERSION,
            )
        )
        self._insert_open_positions_batch(open_positions_rows)
        self._insert_open_orders_batch(open_order_rows)
        self._insert_open_trades_batch(open_trade_rows)

        return {
            "open_positions": len(open_positions_rows),
            "open_trades": len(open_trade_rows),
            "open_orders": len(open_order_rows),
        }

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
        capture_snapshots: bool = True,
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
        :param capture_snapshots:
            Capture live open-state snapshots after syncing event history.
        :return:
            Dict with counts for synced event history and snapshots.
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
            progress.total = 4 if capture_snapshots else 3
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

        snapshot_counts = {"open_positions": 0, "open_trades": 0, "open_orders": 0}
        if capture_snapshots:
            if progress is not None:
                progress.set_description_str(_colour_desc("Snapshots", short))
            snapshot_counts = self.capture_account_snapshots(
                session,
                addr,
                is_vault=self.is_vault_address(addr),
                label=label,
                timeout=timeout,
            )
            self.save()
            if progress is not None:
                progress.update(1)
                progress.set_postfix_str(
                    _colour_postfix(
                        fills=_format_count(fills_count),
                        funding=_format_count(funding_count),
                        ledger=_format_count(ledger_count),
                        open_positions=_format_count(snapshot_counts["open_positions"]),
                        open_trades=_format_count(snapshot_counts["open_trades"]),
                        open_orders=_format_count(snapshot_counts["open_orders"]),
                    )
                )

        result = {"fills": fills_count, "funding": funding_count, "ledger": ledger_count, **snapshot_counts}
        logger.info("Sync complete for %s: %s", addr, result)
        return result

    def sync_all(
        self,
        session: HyperliquidSession,
        max_workers: int = 1,
        timeout: float = 30.0,
        is_vault: bool | None = None,
        capture_snapshots: bool = True,
    ) -> dict[str, dict[str, int]]:
        """Sync whitelisted accounts, optionally filtered by vault status.

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
        :param is_vault:
            If ``True``, sync only vault accounts.
            If ``False``, sync only trader accounts.
            If ``None`` (default), sync all accounts.
        :return:
            Dict mapping address to sync counts.
        """
        accounts = self.get_accounts(is_vault=is_vault)
        if not accounts:
            return {}

        # Print existing database entry counts before syncing
        existing = self.con.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM fills),
                (SELECT COUNT(*) FROM funding),
                (SELECT COUNT(*) FROM ledger),
                (SELECT COUNT(*) FROM account_snapshot_runs)
            """
        ).fetchone()
        print(  # noqa: T201
            f"Database: {_format_count(existing[0])} fills, {_format_count(existing[1])} funding, {_format_count(existing[2])} ledger entries, {_format_count(existing[3])} snapshot runs for {len(accounts)} accounts"
        )

        results = {}
        total_fills = 0
        total_funding = 0
        total_ledger = 0
        total_open_positions = 0
        total_open_trades = 0
        total_open_orders = 0

        def _proxy_postfix(sessions: list[HyperliquidSession], **kwargs) -> str:
            """Build postfix string including proxy rotation stats if proxies are used."""
            total_rotations = sum(s.rotation_count for s in sessions)
            if total_rotations > 0:
                kwargs["rotations"] = str(total_rotations)
            return _colour_postfix(**kwargs)

        def _totals_postfix(sessions, **extra):
            return _proxy_postfix(
                sessions,
                fills=_format_count(total_fills),
                funding=_format_count(total_funding),
                ledger=_format_count(total_ledger),
                open_positions=_format_count(total_open_positions),
                open_trades=_format_count(total_open_trades),
                open_orders=_format_count(total_open_orders),
                **extra,
            )

        if max_workers <= 1:
            # Sequential path: use the session directly (proxy rotation is built in)
            all_sessions = [session]
            overall = tqdm(
                accounts,
                desc="Syncing accounts",
                unit="account",
                colour="green",
            )
            for account in overall:
                addr = account["address"]
                label = account.get("label", addr[:10])
                overall.set_postfix_str(_totals_postfix(all_sessions, account=label))
                try:
                    result = self.sync_account(session, addr, timeout=timeout, capture_snapshots=capture_snapshots)
                    results[addr] = result
                    total_fills += result.get("fills", 0)
                    total_funding += result.get("funding", 0)
                    total_ledger += result.get("ledger", 0)
                    total_open_positions += result.get("open_positions", 0)
                    total_open_trades += result.get("open_trades", 0)
                    total_open_orders += result.get("open_orders", 0)
                    overall.set_postfix_str(_totals_postfix(all_sessions, account=label))
                except Exception:
                    logger.exception("Failed to sync account %s", addr)
                    results[addr] = {"fills": 0, "funding": 0, "ledger": 0, "open_positions": 0, "open_trades": 0, "open_orders": 0, "error": True}
            return results

        # Threaded path with nested progress bars.
        # Pre-create all bars in the main thread using standard tqdm
        # (not tqdm_loggable) for reliable cursor positioning.
        n_bars = min(max_workers, len(accounts))

        # Pre-create per-worker session clones. Each clone shares the same
        # rate-limiter adapters and ProxyStateManager but starts on a
        # different proxy for load distribution across IPs.
        all_sessions: list[HyperliquidSession] = [session.clone_for_worker(proxy_start_index=i) for i in range(n_bars)]
        session_pool: list[HyperliquidSession] = list(all_sessions)
        session_lock = threading.Lock()

        overall = tqdm_std(
            total=len(accounts),
            desc="Syncing accounts",
            unit="account",
            position=0,
            colour="green",
        )
        overall.set_postfix_str(_totals_postfix(all_sessions, workers=str(n_bars)))
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

        # Thread-safe pool of pre-created bars
        bar_pool: list[tqdm_std] = list(worker_bars)
        bar_lock = threading.Lock()

        def _sync_worker(account: dict) -> tuple[str, dict[str, int]]:
            addr = account["address"]
            short_label = account.get("label") or addr[:10]
            with bar_lock:
                bar = bar_pool.pop()
            with session_lock:
                worker_session = session_pool.pop()
            try:
                result = self.sync_account(worker_session, addr, timeout=timeout, progress=bar, label=short_label, capture_snapshots=capture_snapshots)
                return addr, result
            except Exception:
                logger.exception("Failed to sync account %s", addr)
                return addr, {"fills": 0, "funding": 0, "ledger": 0, "open_positions": 0, "open_trades": 0, "open_orders": 0, "error": True}
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

                try:
                    for future in as_completed(futures):
                        addr, result = future.result()
                        results[addr] = result
                        total_fills += result.get("fills", 0)
                        total_funding += result.get("funding", 0)
                        total_ledger += result.get("ledger", 0)
                        total_open_positions += result.get("open_positions", 0)
                        total_open_trades += result.get("open_trades", 0)
                        total_open_orders += result.get("open_orders", 0)
                        overall.update(1)
                        overall.set_postfix_str(_totals_postfix(all_sessions, workers=str(n_bars)))
                except KeyboardInterrupt:
                    # Cancel pending futures and wait for running workers to
                    # finish their current database operations.  Without this,
                    # db.close() in the caller sets self.con = None while
                    # workers still hold references to the connection.
                    for f in futures:
                        f.cancel()
                    try:
                        executor.shutdown(wait=True, cancel_futures=True)
                    except KeyboardInterrupt:
                        pass  # Second Ctrl+C — stop waiting
                    raise
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
                timestamp=datetime.datetime.fromtimestamp(r[0] / 1000, tz=datetime.timezone.utc).replace(tzinfo=None),
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

    def get_ledger(
        self,
        address: HexAddress,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> list[LedgerEvent]:
        """Get stored ledger events for an account.

        :param address:
            Account address.
        :param start_time:
            Optional start time filter.
        :param end_time:
            Optional end time filter.
        :return:
            List of LedgerEvent objects sorted by timestamp ascending.
        """
        addr = address.lower()
        query = "SELECT ts, event_type, usdc, vault FROM ledger WHERE address = ?"
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
            LedgerEvent(
                timestamp=datetime.datetime.fromtimestamp(r[0] / 1000, tz=datetime.timezone.utc).replace(tzinfo=None),
                timestamp_ms=r[0],
                event_type=r[1],
                usdc=r[2],
                vault=r[3],
            )
            for r in rows
        ]

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
            Dict with row counts for core event and snapshot tables.
        """
        with self._db_lock:
            fills = self.con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            funding = self.con.execute("SELECT COUNT(*) FROM funding").fetchone()[0]
            ledger = self.con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
            snapshot_runs = self.con.execute("SELECT COUNT(*) FROM account_snapshot_runs").fetchone()[0]
            snapshot_sources = self.con.execute("SELECT COUNT(*) FROM account_snapshot_sources").fetchone()[0]
            open_positions = self.con.execute("SELECT COUNT(*) FROM open_position_snapshots").fetchone()[0]
            open_trades = self.con.execute("SELECT COUNT(*) FROM open_trade_snapshots").fetchone()[0]
            open_orders = self.con.execute("SELECT COUNT(*) FROM open_order_snapshots").fetchone()[0]
        return {
            "fills": fills,
            "funding": funding,
            "ledger": ledger,
            "snapshot_runs": snapshot_runs,
            "snapshot_sources": snapshot_sources,
            "open_positions": open_positions,
            "open_trades": open_trades,
            "open_orders": open_orders,
        }

    def _get_latest_snapshot_timestamp(self, address: HexAddress) -> int | None:
        """Get the latest snapshot timestamp for an account."""
        with self._db_lock:
            row = self.con.execute(
                "SELECT MAX(ts) FROM account_snapshot_runs WHERE address = ?",
                [address.lower()],
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    def get_snapshot_runs(self, address: HexAddress) -> list[dict]:
        """Get all snapshot runs for an account."""
        with self._db_lock:
            rows = self.con.execute(
                """
                SELECT ts, label, is_vault, dex, fills_row_count, funding_row_count,
                       ledger_row_count, open_position_count, open_trade_count,
                       open_order_count, historical_order_count,
                       twap_slice_fill_count, snapshot_version
                FROM account_snapshot_runs
                WHERE address = ?
                ORDER BY ts ASC
                """,
                [address.lower()],
            ).fetchall()
        return [
            {
                "ts": r[0],
                "label": r[1],
                "is_vault": r[2],
                "dex": r[3],
                "fills_row_count": r[4],
                "funding_row_count": r[5],
                "ledger_row_count": r[6],
                "open_position_count": r[7],
                "open_trade_count": r[8],
                "open_order_count": r[9],
                "historical_order_count": r[10],
                "twap_slice_fill_count": r[11],
                "snapshot_version": r[12],
            }
            for r in rows
        ]

    def get_snapshot_source(
        self,
        address: HexAddress,
        source: str,
        timestamp_ms: int | None = None,
    ) -> dict | None:
        """Get one raw snapshot source payload."""
        addr = address.lower()
        timestamp_ms = timestamp_ms if timestamp_ms is not None else self._get_latest_snapshot_timestamp(addr)
        if timestamp_ms is None:
            return None

        with self._db_lock:
            row = self.con.execute(
                """
                SELECT status, item_count, payload_json, error_message
                FROM account_snapshot_sources
                WHERE address = ? AND ts = ? AND source = ?
                """,
                [addr, timestamp_ms, source],
            ).fetchone()

        if row is None:
            return None
        return {
            "status": row[0],
            "item_count": row[1],
            "payload_json": row[2],
            "error_message": row[3],
        }

    def get_open_position_snapshots(
        self,
        address: HexAddress,
        timestamp_ms: int | None = None,
    ) -> list[dict]:
        """Get materialised open positions for a snapshot."""
        addr = address.lower()
        timestamp_ms = timestamp_ms if timestamp_ms is not None else self._get_latest_snapshot_timestamp(addr)
        if timestamp_ms is None:
            return []

        with self._db_lock:
            rows = self.con.execute(
                """
                SELECT coin, position_type, size, entry_px, unrealised_pnl,
                       margin_used, position_value, liquidation_px, leverage_type,
                       leverage_value, max_leverage, return_on_equity,
                       cumulative_funding_all_time, cumulative_funding_since_open,
                       cumulative_funding_since_change, mark_px,
                       available_to_trade_long, available_to_trade_short,
                       max_trade_sz_long, max_trade_sz_short,
                       position_json, active_asset_data_json
                FROM open_position_snapshots
                WHERE address = ? AND ts = ?
                ORDER BY coin ASC
                """,
                [addr, timestamp_ms],
            ).fetchall()
        return [
            {
                "coin": r[0],
                "position_type": r[1],
                "size": r[2],
                "entry_px": r[3],
                "unrealised_pnl": r[4],
                "margin_used": r[5],
                "position_value": r[6],
                "liquidation_px": r[7],
                "leverage_type": r[8],
                "leverage_value": r[9],
                "max_leverage": r[10],
                "return_on_equity": r[11],
                "cumulative_funding_all_time": r[12],
                "cumulative_funding_since_open": r[13],
                "cumulative_funding_since_change": r[14],
                "mark_px": r[15],
                "available_to_trade_long": r[16],
                "available_to_trade_short": r[17],
                "max_trade_sz_long": r[18],
                "max_trade_sz_short": r[19],
                "position_json": r[20],
                "active_asset_data_json": r[21],
            }
            for r in rows
        ]

    def get_open_order_snapshots(
        self,
        address: HexAddress,
        timestamp_ms: int | None = None,
    ) -> list[dict]:
        """Get materialised open orders for a snapshot."""
        addr = address.lower()
        timestamp_ms = timestamp_ms if timestamp_ms is not None else self._get_latest_snapshot_timestamp(addr)
        if timestamp_ms is None:
            return []

        with self._db_lock:
            rows = self.con.execute(
                """
                SELECT order_index, source, coin, side, limit_px, sz, orig_sz,
                       oid, cloid, order_ts, status, status_timestamp,
                       trigger_condition, is_trigger, trigger_px,
                       is_position_tpsl, reduce_only, order_type, tif, order_json
                FROM open_order_snapshots
                WHERE address = ? AND ts = ?
                ORDER BY order_index ASC
                """,
                [addr, timestamp_ms],
            ).fetchall()
        return [
            {
                "order_index": r[0],
                "source": r[1],
                "coin": r[2],
                "side": r[3],
                "limit_px": r[4],
                "sz": r[5],
                "orig_sz": r[6],
                "oid": r[7],
                "cloid": r[8],
                "order_ts": r[9],
                "status": r[10],
                "status_timestamp": r[11],
                "trigger_condition": r[12],
                "is_trigger": r[13],
                "trigger_px": r[14],
                "is_position_tpsl": r[15],
                "reduce_only": r[16],
                "order_type": r[17],
                "tif": r[18],
                "order_json": r[19],
            }
            for r in rows
        ]

    def get_open_trade_snapshots(
        self,
        address: HexAddress,
        timestamp_ms: int | None = None,
    ) -> list[dict]:
        """Get materialised derived open trades for a snapshot."""
        addr = address.lower()
        timestamp_ms = timestamp_ms if timestamp_ms is not None else self._get_latest_snapshot_timestamp(addr)
        if timestamp_ms is None:
            return []

        with self._db_lock:
            rows = self.con.execute(
                """
                SELECT trade_index, coin, direction, is_complete, opened_at,
                       entry_price, current_size, max_size, realised_pnl,
                       funding_pnl, total_fees, net_pnl, unrealised_pnl,
                       fill_count, trade_json
                FROM open_trade_snapshots
                WHERE address = ? AND ts = ?
                ORDER BY trade_index ASC
                """,
                [addr, timestamp_ms],
            ).fetchall()
        return [
            {
                "trade_index": r[0],
                "coin": r[1],
                "direction": r[2],
                "is_complete": r[3],
                "opened_at": r[4],
                "entry_price": r[5],
                "current_size": r[6],
                "max_size": r[7],
                "realised_pnl": r[8],
                "funding_pnl": r[9],
                "total_fees": r[10],
                "net_pnl": r[11],
                "unrealised_pnl": r[12],
                "fill_count": r[13],
                "trade_json": r[14],
            }
            for r in rows
        ]

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
