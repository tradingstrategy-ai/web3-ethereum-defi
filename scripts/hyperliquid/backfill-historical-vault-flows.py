"""Backfill historical Hypercore ledger flows for deferred price repairs.

The normal Hyperliquid scanners fetch only a short rolling flow window. Old
daily price observations can therefore retain missing or incorrectly parsed
withdrawals even though ``userNonFundingLedgerUpdates`` still exposes the
ledger events. This script finds ``deferred_hf_nav`` observations in the
cleaned Parquet, fetches the surrounding ledger history, and updates the
existing daily DuckDB rows. It does not change share price, NAV, or PnL.

After the backfill, run the normal Hyperliquid export and vault-price wrangle.
The flow-reconciled wrangle step will then repair qualifying share-price paths.

Usage:

.. code-block:: shell

    # Preview every currently deferred vault.
    AUTODETECT=true \
      poetry run python scripts/hyperliquid/backfill-historical-vault-flows.py

    # Backfill Magixbox in the daily source database.
    DRY_RUN=false \
      VAULT_ADDRESSES=0x1764dd740aba4195643bbb6a44648e0306b00cfa \
      poetry run python scripts/hyperliquid/backfill-historical-vault-flows.py

Environment variables:

- ``DB_PATH``: Daily Hyperliquid DuckDB path.
- ``CLEANED_PARQUET_PATH``: Cleaned vault-price Parquet used to find candidates.
- ``VAULT_ADDRESSES``: Comma-separated candidate vaults to repair.
- ``AUTODETECT``: Select every current ``deferred_hf_nav`` vault. Default: false.
  Exactly one of ``VAULT_ADDRESSES`` or ``AUTODETECT=true`` is required.
- ``MAX_WORKERS``: Parallel API readers. Default: 8.
- ``ANCHOR_PADDING_DAYS``: Days fetched on both sides of candidates. Default: 8.
- ``REQUESTS_PER_SECOND``: Rate limit for each API session. Default: 1.
- ``DRY_RUN``: Preview without updating DuckDB. Default: true.
"""

import datetime
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress
from joblib import Parallel, delayed
from tabulate import tabulate

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.deposit import aggregate_daily_flows, fetch_vault_deposits
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

DEFAULT_CLEANED_PARQUET_PATH = Path.home() / ".tradingstrategy" / "vaults" / "cleaned-vault-prices-1h.parquet"


def create_iterated_duckdb_backup(db_path: Path) -> Path:
    """Create the next numbered backup of a Hyperliquid DuckDB file.

    Backups are written next to the source as ``.bak-0001``, ``.bak-0002``,
    and so on, and existing backups are never overwritten. If DuckDB has a
    write-ahead log sidecar, it is copied with the same backup prefix so the
    pair can be restored together. The caller must ensure no scanner is writing
    the database while the backup is taken.

    :param db_path:
        Hyperliquid DuckDB file that will be modified.
    :return:
        Path to the newly created main database backup.
    :raises FileNotFoundError:
        If the source database does not exist.
    """
    if not db_path.is_file():
        raise FileNotFoundError(db_path)

    backup_number = 1
    while True:
        backup_path = db_path.with_name(f"{db_path.name}.bak-{backup_number:04d}")
        backup_wal_path = Path(f"{backup_path}.wal")
        if not backup_path.exists() and not backup_wal_path.exists():
            break
        backup_number += 1

    shutil.copy2(db_path, backup_path)
    wal_path = Path(f"{db_path}.wal")
    if wal_path.is_file():
        shutil.copy2(wal_path, backup_wal_path)
    return backup_path


def select_historical_flow_candidates(
    cleaned: pd.DataFrame,
    requested_addresses: set[HexAddress],
    *,
    autodetect: bool,
) -> pd.DataFrame:
    """Select deferred Hypercore observations for the manual backfill.

    Explicit address selection and autodetection are mutually exclusive so a
    missing environment variable cannot accidentally expand a single-vault
    maintenance operation to every candidate. Autodetection means all rows
    currently marked ``deferred_hf_nav`` by the production wrangle.

    :param cleaned:
        Cleaned Hypercore prices with ``address``, ``timestamp``, and
        ``hypercore_repair_status`` columns.
    :param requested_addresses:
        Explicit lowercased Hyperliquid vault addresses.
    :param autodetect:
        Whether to select all current ``deferred_hf_nav`` vaults.
    :return:
        Matching deferred observations, with lowercased addresses and naive
        UTC timestamps.
    :raises RuntimeError:
        If selection mode is ambiguous or nothing matches.
    """
    if bool(requested_addresses) == autodetect:
        message = "Set exactly one of VAULT_ADDRESSES or AUTODETECT=true"
        raise RuntimeError(message)

    repair_status = cleaned["hypercore_repair_status"].astype("string").fillna("")
    candidates = cleaned[repair_status == "deferred_hf_nav"].copy()
    candidates["address"] = candidates["address"].str.lower()
    candidates["timestamp"] = pd.to_datetime(candidates["timestamp"])
    if requested_addresses:
        candidates = candidates[candidates["address"].isin(requested_addresses)]
    if candidates.empty:
        message = "No deferred_hf_nav Hypercore observations matched the requested vaults"
        raise RuntimeError(message)
    return candidates


@dataclass(slots=True)
class HistoricalFlowBackfill:
    """Fetched ledger data for one historical repair window.

    Carries one API result from the parallel read phase into the serial DuckDB
    update and reporting phase.
    """

    #: Hyperliquid vault address.
    vault_address: HexAddress
    #: Human-readable vault name from cleaned data.
    name: str
    #: First daily DuckDB observation to update.
    start_date: datetime.date
    #: Last daily DuckDB observation to update.
    end_date: datetime.date
    #: Number of ``deferred_hf_nav`` rows selecting this window.
    candidate_count: int
    #: Number of ledger events returned by Hyperliquid.
    event_count: int
    #: Deposit and withdrawal events aggregated by UTC calendar date.
    daily_flows: dict[datetime.date, tuple[int, int, float, float]]


def fetch_historical_flow_backfill(
    vault_address: HexAddress,
    name: str,
    candidate_timestamps: pd.Series,
    anchor_padding_days: int,
    requests_per_second: float,
) -> HistoricalFlowBackfill:
    """Fetch the ledger window needed by one vault's deferred repairs.

    The window extends past the deferred observation by the same eight-day
    limit used for HF price anchors. Hyperliquid's canonical
    ``userNonFundingLedgerUpdates`` endpoint supplies the underlying deposit
    and withdrawal events.

    :param vault_address:
        Hyperliquid vault address.
    :param name:
        Human-readable vault name for reporting.
    :param candidate_timestamps:
        Naive UTC timestamps of deferred daily observations.
    :param anchor_padding_days:
        Number of calendar days included before and after the candidates.
    :param requests_per_second:
        Per-session Hyperliquid API request limit.
    :return:
        Fetched and daily-aggregated ledger flow data.
    """
    padding = datetime.timedelta(days=anchor_padding_days)
    first_timestamp = candidate_timestamps.min().to_pydatetime()
    last_timestamp = candidate_timestamps.max().to_pydatetime()
    start_date = (first_timestamp - padding).date()
    end_date = (last_timestamp + padding).date()
    start_time = datetime.datetime.combine(start_date, datetime.time.min)
    end_time = datetime.datetime.combine(end_date, datetime.time.max)

    session = create_hyperliquid_session(requests_per_second=requests_per_second)
    events = list(
        fetch_vault_deposits(
            session,
            vault_address,
            start_time=start_time,
            end_time=end_time,
        )
    )
    return HistoricalFlowBackfill(
        vault_address=vault_address,
        name=name,
        start_date=start_date,
        end_date=end_date,
        candidate_count=len(candidate_timestamps),
        event_count=len(events),
        daily_flows=aggregate_daily_flows(events),
    )


def main() -> None:  # noqa: PLR0914
    """Fetch deferred-vault ledger history and optionally update daily DuckDB.

    Configuration is read from environment variables documented in the module
    header. The script defaults to a read-only preview. With ``DRY_RUN=false``,
    only flow columns on already existing daily observations are updated.

    :return:
        None.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    db_path = Path(os.environ.get("DB_PATH", str(HYPERLIQUID_DAILY_METRICS_DATABASE))).expanduser()
    cleaned_path = Path(os.environ.get("CLEANED_PARQUET_PATH", str(DEFAULT_CLEANED_PARQUET_PATH))).expanduser()
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    anchor_padding_days = int(os.environ.get("ANCHOR_PADDING_DAYS", "8"))
    requests_per_second = float(os.environ.get("REQUESTS_PER_SECOND", "1"))
    dry_run = os.environ.get("DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}
    autodetect = os.environ.get("AUTODETECT", "false").strip().lower() in {"1", "true", "yes"}
    requested_addresses = {HexAddress(value.strip().lower()) for value in os.environ.get("VAULT_ADDRESSES", "").split(",") if value.strip()}

    cleaned = pd.read_parquet(cleaned_path, filters=[("chain", "==", HYPERCORE_CHAIN_ID)]).reset_index()
    candidates = select_historical_flow_candidates(cleaned, requested_addresses, autodetect=autodetect)

    fetch_jobs = []
    for vault_address, group in candidates.groupby("address", sort=False):
        names = group["name"].dropna() if "name" in group.columns else pd.Series(dtype="string")
        name = str(names.iloc[-1]) if len(names) else vault_address
        fetch_jobs.append(
            delayed(fetch_historical_flow_backfill)(
                HexAddress(vault_address),
                name,
                group["timestamp"],
                anchor_padding_days,
                requests_per_second,
            )
        )

    backfills = Parallel(n_jobs=max_workers, prefer="threads")(fetch_jobs)
    updated_rows: dict[HexAddress, int] = {}
    backup_path: Path | None = None
    if not dry_run:
        backup_path = create_iterated_duckdb_backup(db_path)
        logger.info("Created DuckDB backup %s", backup_path)
        db = HyperliquidDailyMetricsDatabase(db_path)
        try:
            updated_rows = {
                backfill.vault_address: db.update_historical_daily_flows(
                    backfill.vault_address,
                    backfill.daily_flows,
                    backfill.start_date,
                    backfill.end_date,
                )
                for backfill in backfills
            }
            db.save()
        finally:
            db.close()

    report = pd.DataFrame(
        [
            {
                "name": backfill.name,
                "address": backfill.vault_address,
                "start": backfill.start_date,
                "end": backfill.end_date,
                "candidates": backfill.candidate_count,
                "ledger_events": backfill.event_count,
                "active_flow_days": len(backfill.daily_flows),
                "updated_rows": updated_rows.get(backfill.vault_address, 0),
            }
            for backfill in backfills
        ]
    )
    print(tabulate(report, headers="keys", tablefmt="simple", showindex=False))
    mode = "Previewed" if dry_run else "Backfilled"
    logger.info("%s %d deferred observations across %d Hypercore vaults", mode, int(report["candidates"].sum()), len(report))
    if backup_path is not None:
        logger.info("Database backup: %s", backup_path)


if __name__ == "__main__":
    main()
