"""Extract a compact test fixture from the live trade history DuckDB.

Creates a small DuckDB with data for two accounts:

- A vault (0x15be61...) with vaultCreate/vaultDeposit/vaultWithdraw ledger events
- A trader (0x18cde6...) with deposit/withdraw ledger events

For each account, extracts the first 200 fills, matching funding within
that time window, and all ledger events.

Usage:

.. code-block:: shell

    poetry run python scripts/hyperliquid/extract-test-trade-history.py
"""

import datetime
import logging

import duckdb

logger = logging.getLogger(__name__)

SOURCE_DB = "~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb"
OUTPUT_DB = "tests/hyperliquid/fixtures/trade-history-sample.duckdb"

VAULT_ADDRESS = "0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66"
TRADER_ADDRESS = "0x18cde66120c9195fb6e50a4b1e13bce4c85d1300"

FILL_LIMIT = 200

#: Extend the funding window beyond the fill range to capture hourly funding payments
FUNDING_EXTRA_HOURS = 48


def main():
    logging.basicConfig(level=logging.INFO)

    from pathlib import Path

    source_path = Path(SOURCE_DB).expanduser()
    output_path = Path(OUTPUT_DB)

    # Copy without WAL to avoid corruption
    import shutil

    tmp_source = Path("/tmp/trade-history-extract-source.duckdb")
    shutil.copy2(source_path, tmp_source)

    src = duckdb.connect(str(tmp_source), read_only=True)

    # Remove existing output
    if output_path.exists():
        output_path.unlink()

    dst = duckdb.connect(str(output_path))

    # Create schema
    dst.execute("""
        CREATE TABLE accounts (
            address VARCHAR PRIMARY KEY,
            label VARCHAR,
            is_vault BOOLEAN NOT NULL DEFAULT TRUE,
            added_at BIGINT NOT NULL
        )
    """)
    dst.execute("""
        CREATE TABLE fills (
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
    dst.execute("""
        CREATE TABLE funding (
            address VARCHAR NOT NULL,
            ts BIGINT NOT NULL,
            coin VARCHAR NOT NULL,
            usdc FLOAT NOT NULL,
            sz FLOAT,
            rate FLOAT,
            PRIMARY KEY (address, ts, coin)
        )
    """)
    dst.execute("""
        CREATE TABLE ledger (
            address VARCHAR NOT NULL,
            ts BIGINT NOT NULL,
            event_type VARCHAR NOT NULL,
            usdc FLOAT NOT NULL,
            vault VARCHAR,
            PRIMARY KEY (address, ts, event_type)
        )
    """)
    dst.execute("""
        CREATE TABLE sync_state (
            address VARCHAR NOT NULL,
            data_type VARCHAR NOT NULL,
            oldest_ts BIGINT,
            newest_ts BIGINT,
            row_count INTEGER,
            last_synced BIGINT NOT NULL,
            PRIMARY KEY (address, data_type)
        )
    """)

    now_ms = int(datetime.datetime(2026, 3, 13).timestamp() * 1000)

    for address, is_vault, label in [
        (VAULT_ADDRESS, True, "Test Vault"),
        (TRADER_ADDRESS, False, "Test Trader"),
    ]:
        logger.info("Extracting %s (%s)...", label, address)

        # Insert account
        dst.execute(
            "INSERT INTO accounts (address, label, is_vault, added_at) VALUES (?, ?, ?, ?)",
            [address, label, is_vault, now_ms],
        )

        # Get first N fills
        fills = src.execute(
            "SELECT * FROM fills WHERE address = ? ORDER BY ts LIMIT ?",
            [address, FILL_LIMIT],
        ).fetchall()

        if fills:
            fill_cols = src.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'fills' ORDER BY ordinal_position").fetchall()
            logger.info("  Fills: %d rows", len(fills))

            # Determine time window from fills
            min_ts = min(f[2] for f in fills)  # ts is column index 2
            max_ts = max(f[2] for f in fills)
            logger.info(
                "  Fill time window: %s to %s",
                datetime.datetime.fromtimestamp(min_ts / 1000),
                datetime.datetime.fromtimestamp(max_ts / 1000),
            )

            # Insert fills
            placeholders = ", ".join(["?"] * len(fill_cols))
            dst.executemany(
                f"INSERT INTO fills VALUES ({placeholders})",
                fills,
            )

            # Get funding within the fill time window + extra hours for hourly payments
            funding_max_ts = max_ts + FUNDING_EXTRA_HOURS * 3600 * 1000
            funding = src.execute(
                "SELECT * FROM funding WHERE address = ? AND ts >= ? AND ts <= ? ORDER BY ts",
                [address, min_ts, funding_max_ts],
            ).fetchall()
            if funding:
                fund_cols = src.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'funding' ORDER BY ordinal_position").fetchall()
                placeholders = ", ".join(["?"] * len(fund_cols))
                dst.executemany(
                    f"INSERT INTO funding VALUES ({placeholders})",
                    funding,
                )
            logger.info("  Funding: %d rows", len(funding))
        else:
            logger.info("  No fills found")

        # Get ALL ledger events (they're small)
        ledger = src.execute(
            "SELECT * FROM ledger WHERE address = ? ORDER BY ts",
            [address],
        ).fetchall()
        if ledger:
            led_cols = src.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'ledger' ORDER BY ordinal_position").fetchall()
            placeholders = ", ".join(["?"] * len(led_cols))
            dst.executemany(
                f"INSERT INTO ledger VALUES ({placeholders})",
                ledger,
            )
        logger.info("  Ledger: %d rows", len(ledger))

    # Summary
    logger.info("=== Output summary ===")
    for table in ("accounts", "fills", "funding", "ledger"):
        count = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        logger.info("  %s: %d rows", table, count)

    dst.execute("CHECKPOINT")
    dst.close()
    src.close()
    tmp_source.unlink(missing_ok=True)

    import os

    size_kb = os.path.getsize(output_path) / 1024
    logger.info("Output file: %s (%.1f KB)", output_path, size_kb)
    logger.info("Done.")


if __name__ == "__main__":
    main()
