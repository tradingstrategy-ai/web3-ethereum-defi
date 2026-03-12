"""DuckDB persistence for Hyperliquid account trading data.

Stores fills, funding payments, and ledger updates for a whitelisted set of
accounts (vaults or normal addresses). Incremental sync accumulates data
beyond the 10K fill API limit by fetching only new records on each run.

The sync is crash-resumeable: partial batches are safely re-inserted on
restart via ``INSERT OR IGNORE`` on natural primary keys.

Schema
------

Five tables:

- ``accounts`` — whitelisted addresses to track
- ``fills`` — individual trade fills from ``userFillsByTime``
- ``funding`` — funding payments from ``userFunding``
- ``ledger`` — deposit/withdrawal events from ``userNonFundingLedgerUpdates``
- ``sync_state`` — per-account watermarks for incremental sync

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
from pathlib import Path

import duckdb
from eth_typing import HexAddress
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


class HyperliquidTradeHistoryDatabase:
    """DuckDB database for storing Hyperliquid account trading data.

    Stores fills, funding payments, and ledger updates for whitelisted
    accounts. Supports incremental sync that accumulates data beyond
    the 10K fill API limit.

    The database is crash-resumeable: interrupted syncs can be safely
    re-run without data loss or duplicates.
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

        Idempotent — re-adding an existing account updates the label.

        :param address:
            Hyperliquid account address.
        :param label:
            Human-readable name (e.g. "Growi HF").
        :param is_vault:
            Whether this is a vault account.
        """
        now_ms = int(native_datetime_utc_now().timestamp() * 1000)
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
        self.con.execute("DELETE FROM accounts WHERE address = ?", [addr])
        if purge_data:
            for table in ("fills", "funding", "ledger", "sync_state"):
                self.con.execute(f"DELETE FROM {table} WHERE address = ?", [addr])
            logger.info("Purged all data for account %s", address)
        else:
            logger.info("Removed account %s from whitelist (data preserved)", address)

    def get_accounts(self) -> list[dict]:
        """Get all whitelisted accounts.

        :return:
            List of account dicts with address, label, is_vault, added_at.
        """
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
    ) -> int:
        """Fetch new fills since last sync and store them.

        Incremental: only fetches fills newer than the last stored timestamp.
        Uses ``INSERT OR IGNORE`` to handle overlapping batches safely.

        :param session:
            Hyperliquid API session.
        :param address:
            Account address.
        :param start_time:
            Override start time (default: use sync_state or 1 year ago).
        :param end_time:
            Override end time (default: now).
        :param timeout:
            HTTP request timeout.
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

        progress = tqdm(
            desc=f"Fills {addr[:10]}",
            unit="fill",
            leave=False,
        )

        try:
            while current_start_ms < end_ms:
                payload = {
                    "type": "userFillsByTime",
                    "user": addr,
                    "startTime": current_start_ms,
                    "endTime": end_ms,
                }

                response = session.post(
                    f"{session.api_url}/info",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
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
                    inserted = self._insert_fills_batch(rows)
                    total_inserted += inserted

                # Update sync state after each batch
                self._update_sync_state_fills(addr)

                progress.update(len(raw_fills))
                progress.set_postfix(
                    batch=batch_num,
                    fetched=total_fetched,
                    inserted=total_inserted,
                )

                # Paginate forward: API returns oldest first
                if newest_batch_ts is not None:
                    current_start_ms = newest_batch_ts + 1

                if len(raw_fills) < MAX_PER_REQUEST:
                    break
        finally:
            progress.close()

        logger.info("Synced %d new fills for %s (fetched %d)", total_inserted, addr, total_fetched)
        return total_inserted

    def _insert_fills_batch(self, rows: list[tuple]) -> int:
        """Insert a batch of fill rows, ignoring duplicates.

        :return: Number of rows actually inserted.
        """
        before = self.con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        self.con.executemany(
            """
            INSERT OR IGNORE INTO fills (address, trade_id, ts, coin, side, sz, px, closed_pnl, start_position, fee, oid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        after = self.con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
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
    ) -> int:
        """Fetch new funding payments since last sync and store them.

        :param session:
            Hyperliquid API session.
        :param address:
            Account address.
        :param start_time:
            Override start time.
        :param end_time:
            Override end time.
        :param timeout:
            HTTP request timeout.
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

        progress = tqdm(
            desc=f"Funding {addr[:10]}",
            unit="payment",
            leave=False,
        )

        try:
            while current_start_ms < end_ms:
                payload = {
                    "type": "userFunding",
                    "user": addr,
                    "startTime": current_start_ms,
                    "endTime": end_ms,
                }

                response = session.post(
                    f"{session.api_url}/info",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
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
                    inserted = self._insert_funding_batch(rows)
                    total_inserted += inserted

                self._update_sync_state_funding(addr)

                progress.update(len(raw_funding))
                progress.set_postfix(
                    batch=batch_num,
                    fetched=total_fetched,
                    inserted=total_inserted,
                )

                # Paginate forward: API returns oldest first
                if newest_batch_ts is not None:
                    current_start_ms = newest_batch_ts + 1

                if len(raw_funding) < MAX_FUNDING_PER_REQUEST:
                    break
        finally:
            progress.close()

        logger.info("Synced %d new funding payments for %s (fetched %d)", total_inserted, addr, total_fetched)
        return total_inserted

    def _insert_funding_batch(self, rows: list[tuple]) -> int:
        """Insert a batch of funding rows, ignoring duplicates."""
        before = self.con.execute("SELECT COUNT(*) FROM funding").fetchone()[0]
        self.con.executemany(
            """
            INSERT OR IGNORE INTO funding (address, ts, coin, usdc, sz, rate)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        after = self.con.execute("SELECT COUNT(*) FROM funding").fetchone()[0]
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
    ) -> int:
        """Fetch new ledger events since last sync and store them.

        :param session:
            Hyperliquid API session.
        :param address:
            Account address.
        :param start_time:
            Override start time.
        :param end_time:
            Override end time.
        :param timeout:
            HTTP request timeout.
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

        progress = tqdm(
            desc=f"Ledger {addr[:10]}",
            unit="event",
            leave=False,
        )

        try:
            while current_start_ms < end_ms:
                payload = {
                    "type": "userNonFundingLedgerUpdates",
                    "user": addr,
                    "startTime": current_start_ms,
                    "endTime": end_ms,
                }

                response = session.post(
                    f"{session.api_url}/info",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
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
                    inserted = self._insert_ledger_batch(rows)
                    total_inserted += inserted

                self._update_sync_state_ledger(addr)

                progress.update(len(raw_updates))
                progress.set_postfix(
                    batch=batch_num,
                    fetched=total_fetched,
                    inserted=total_inserted,
                )

                # Paginate forward: API returns oldest first
                if newest_batch_ts is not None:
                    current_start_ms = newest_batch_ts + 1

                if len(raw_updates) < MAX_PER_REQUEST:
                    break
        finally:
            progress.close()

        logger.info("Synced %d new ledger events for %s (fetched %d)", total_inserted, addr, total_fetched)
        return total_inserted

    def _insert_ledger_batch(self, rows: list[tuple]) -> int:
        """Insert a batch of ledger rows, ignoring duplicates."""
        before = self.con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        self.con.executemany(
            """
            INSERT OR IGNORE INTO ledger (address, ts, event_type, usdc, vault)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        after = self.con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
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
    ) -> dict[str, int]:
        """Sync all data types for a single account.

        Wraps fills, funding, and ledger syncs in a single transaction
        for crash safety.

        :param session:
            Hyperliquid API session.
        :param address:
            Account address.
        :param start_time:
            Override start time.
        :param end_time:
            Override end time.
        :param timeout:
            HTTP request timeout.
        :return:
            Dict with counts: ``{"fills": N, "funding": N, "ledger": N}``.
        """
        addr = address.lower()
        logger.info("Syncing all data for account %s", addr)

        self.con.execute("BEGIN TRANSACTION")
        try:
            fills_count = self.sync_account_fills(session, addr, start_time=start_time, end_time=end_time, timeout=timeout)
            funding_count = self.sync_account_funding(session, addr, start_time=start_time, end_time=end_time, timeout=timeout)
            ledger_count = self.sync_account_ledger(session, addr, start_time=start_time, end_time=end_time, timeout=timeout)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise

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

        :param session:
            Hyperliquid API session.
        :param max_workers:
            Number of parallel workers. Currently only sequential (1) is
            supported since DuckDB doesn't support concurrent writes.
        :param timeout:
            HTTP request timeout.
        :return:
            Dict mapping address to sync counts.
        """
        accounts = self.get_accounts()
        results = {}
        total_events = 0
        progress = tqdm(
            accounts,
            desc="Syncing accounts",
            unit="account",
        )
        for account in progress:
            addr = account["address"]
            label = account.get("label", addr[:10])
            progress.set_postfix(account=label, total_events=f"{total_events:,}")
            try:
                result = self.sync_account(session, addr, timeout=timeout)
                results[addr] = result
                total_events += result.get("fills", 0) + result.get("funding", 0) + result.get("ledger", 0)
                progress.set_postfix(account=label, total_events=f"{total_events:,}")
            except Exception:
                logger.exception("Failed to sync account %s", addr)
                results[addr] = {"fills": 0, "funding": 0, "ledger": 0, "error": True}
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
        rows = self.con.execute(query, params).fetchall()

        from decimal import Decimal

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
        rows = self.con.execute(query, params).fetchall()

        from decimal import Decimal

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
        result = self.con.execute(
            "SELECT COUNT(*) FROM fills WHERE address = ?",
            [address.lower()],
        ).fetchone()
        return result[0] if result else 0

    def get_funding_count(self, address: HexAddress) -> int:
        """Get the number of stored funding payments for an account."""
        result = self.con.execute(
            "SELECT COUNT(*) FROM funding WHERE address = ?",
            [address.lower()],
        ).fetchone()
        return result[0] if result else 0

    def get_ledger_count(self, address: HexAddress) -> int:
        """Get the number of stored ledger events for an account."""
        result = self.con.execute(
            "SELECT COUNT(*) FROM ledger WHERE address = ?",
            [address.lower()],
        ).fetchone()
        return result[0] if result else 0

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
