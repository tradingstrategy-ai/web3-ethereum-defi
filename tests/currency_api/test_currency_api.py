"""Black-box integration tests for the currency_api exchange rate scanner.

These are real-network, no-mock tests: each one drives the real
:py:func:`eth_defi.currency_api.scanner.run_incremental_scan` against the live
fawazahmed0 Exchange API over a small bounded date window (via
``start_date``/``end_date``) into a temporary DuckDB, then asserts on the real
database state. Exact rates are never asserted (the source may revise history) —
only presence, sane bounds, idempotency, and gap-filling behaviour.

A fixed historical window (June 2026) is used so the dates are inside the
available range and older than the scanner grace window.
"""

import datetime
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.currency_api.cleaning import filter_known_bad_rates
from eth_defi.currency_api.client import DateRates, _parse_document
from eth_defi.currency_api.database import CurrencyRateDatabase
from eth_defi.currency_api.scanner import run_incremental_scan


def _rates_with_python_dates(db: CurrencyRateDatabase) -> pd.DataFrame:
    """Return the rates DataFrame with the DuckDB DATE column as ``datetime.date``.

    DuckDB materialises a ``DATE`` column as pandas ``Timestamp`` via ``.df()``;
    normalise it so set comparisons against ``datetime.date`` literals work.
    """
    df = db.get_rates_dataframe()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


#: Fixed historical scan window, comfortably inside the available range and
#: older than the default ``unavailable_grace_days``.
WINDOW_START = datetime.date(2026, 6, 1)
WINDOW_END = datetime.date(2026, 6, 3)

#: Loose sanity bounds per currency (USD base). The source may revise history,
#: so we only check the rate is in a plausible range, not an exact value.
RATE_BOUNDS = {
    "eur": (0.5, 1.5),
    "gbp": (0.5, 1.5),
    "jpy": (50.0, 500.0),
    "aud": (1.0, 2.5),
    "sgd": (1.0, 2.0),
    "try": (10.0, 100.0),
    "chf": (0.5, 1.5),
    "cad": (1.0, 2.5),
    "btc": (0.0, 1.0),
    "eth": (0.0, 1.0),
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return a temporary DuckDB path inside the pytest tmp directory."""
    return tmp_path / "exchange-rates.duckdb"


def test_limited_end_to_end_scan(db_path: Path):
    """Limited real-network end-to-end scan stores and is idempotent.

    1. Run a 3-day scan (2026-06-01..03) for the default currencies into a temp DB.
    2. Assert every (date, quote) cell is present (3 dates x 10 quotes = 30 rows),
       the source column is populated, rates are within loose sanity bounds, and
       there were no transient failures.
    3. Re-run the identical scan and assert the row count is unchanged (idempotent
       upsert, no duplicates).
    """

    # 1. Run a 3-day scan for the default currencies.
    result = run_incremental_scan(db_path=db_path, start_date=WINDOW_START, end_date=WINDOW_END)
    try:
        # 2. Assert completeness, source, bounds and no transient failures.
        assert result.transient_failures == 0
        df = _rates_with_python_dates(result.db)
        expected_dates = {WINDOW_START, WINDOW_START + datetime.timedelta(days=1), WINDOW_END}
        expected_row_count = len(expected_dates) * len(RATE_BOUNDS)
        assert len(df) == expected_row_count

        present_pairs = {(row.date, row.quote_currency) for row in df.itertuples()}
        for date in expected_dates:
            for quote in RATE_BOUNDS:
                assert (date, quote) in present_pairs, f"missing {date} {quote}"

        assert set(df["source"].unique()) == {"fawazahmed0"}

        for row in df.itertuples():
            low, high = RATE_BOUNDS[row.quote_currency]
            assert low < row.rate < high, f"{row.quote_currency} rate {row.rate} outside {low}..{high}"

        # 3. Re-run the identical scan: idempotent, no duplicate rows.
        rerun = run_incremental_scan(db_path=db_path, start_date=WINDOW_START, end_date=WINDOW_END)
        try:
            assert rerun.db.row_count() == expected_row_count
        finally:
            rerun.db.close()
    finally:
        result.db.close()


def test_incremental_gap_fill_and_reconfiguration(db_path: Path):
    """Completeness-driven resume backfills new dates and newly added currencies.

    1. Scan a partial window (2026-06-01..02) with only EUR and GBP; assert 4 rows.
    2. Re-run with an extended window (..06-04) and an added currency (JPY); assert
       the new dates are filled, the new JPY history is backfilled across all four
       dates, and the original EUR/GBP rows are not duplicated.
    """

    # 1. Scan a partial window with a reduced currency set.
    first = run_incremental_scan(
        db_path=db_path,
        quote_currencies=("eur", "gbp"),
        start_date=WINDOW_START,
        end_date=WINDOW_START + datetime.timedelta(days=1),
    )
    try:
        assert first.db.row_count() == 4  # 2 dates x 2 quotes
    finally:
        first.db.close()

    # 2. Re-run with an extended window and an added currency.
    extended_end = WINDOW_START + datetime.timedelta(days=3)  # 2026-06-04
    second = run_incremental_scan(
        db_path=db_path,
        quote_currencies=("eur", "gbp", "jpy"),
        start_date=WINDOW_START,
        end_date=extended_end,
    )
    try:
        df = _rates_with_python_dates(second.db)
        all_dates = {WINDOW_START + datetime.timedelta(days=i) for i in range(4)}

        # JPY (newly added) is backfilled across all four dates.
        jpy_dates = {row.date for row in df.itertuples() if row.quote_currency == "jpy"}
        assert jpy_dates == all_dates

        # EUR/GBP exist for all four dates with no duplication.
        for quote in ("eur", "gbp"):
            quote_dates = [row.date for row in df.itertuples() if row.quote_currency == quote]
            assert sorted(quote_dates) == sorted(all_dates)
            assert len(quote_dates) == len(set(quote_dates))  # no duplicates

        # Total = 4 dates x 3 quotes.
        assert second.db.row_count() == 12
    finally:
        second.db.close()


def test_missing_quote_recorded_not_transient(db_path: Path):
    """A nonexistent currency is recorded as a permanent gap, not retried forever.

    Uses a real request for a currency code that does not exist in the source
    (``zzz``) alongside a real one (``eur``), over an old window so the grace
    window has elapsed.

    1. Scan 2026-06-01..05 for ("eur", "zzz") into a temp DB.
    2. Assert the run had no transient failures and stored only the real EUR rows.
    3. Assert each (date, "zzz") cell was recorded in unavailable_rates as a
       permanent quote gap (so it will not be re-fetched forever).
    """

    # 1. Scan an old window mixing a real and a nonexistent currency.
    result = run_incremental_scan(
        db_path=db_path,
        quote_currencies=("eur", "zzz"),
        start_date=WINDOW_START,
        end_date=WINDOW_START + datetime.timedelta(days=4),  # 2026-06-05
    )
    try:
        # 2. A well-formed document with a missing quote is not a transient failure.
        assert result.transient_failures == 0
        assert result.db.row_count() == 5  # 5 dates x 1 present quote (eur)

        # 3. The nonexistent quote is recorded as a permanent gap for every date.
        unavailable = result.db.get_unavailable_pairs("usd", "fawazahmed0")
        zzz_dates = {date for date, quote in unavailable if quote == "zzz"}
        assert len(zzz_dates) == 5
    finally:
        result.db.close()


def test_transient_attempt_counter(db_path: Path):
    """The per-date transient-attempt counter persists, resets, and escalates.

    Exercises the bookkeeping the scanner's give-up budget relies on, against a
    real DuckDB (no mocks). A persistent non-404 failure cannot be forced against
    the live CDN, so this validates the mechanism directly.

    1. Set and read back a transient-failure count for a date.
    2. Overwrite it (simulating another failed run) and read the new value.
    3. Clear it (simulating the date resolving) and assert it is gone.
    4. Record a give-up as a persistent_error gap and assert it is tracked as
       unavailable so the scanner would no longer re-fetch it.
    """

    db = CurrencyRateDatabase(db_path)
    try:
        date = datetime.date(2026, 6, 1)

        # 1. Set and read back a count.
        db.set_transient_attempts(date, "usd", "fawazahmed0", 3)
        assert db.get_transient_attempts("usd", "fawazahmed0") == {date: 3}

        # 2. Overwrite with a higher count (next failed run).
        db.set_transient_attempts(date, "usd", "fawazahmed0", 4)
        assert db.get_transient_attempts("usd", "fawazahmed0") == {date: 4}

        # 3. Clear it once the date resolves.
        db.clear_transient_attempts(date, "usd", "fawazahmed0")
        assert db.get_transient_attempts("usd", "fawazahmed0") == {}

        # 4. Give-up recording marks the cell unavailable.
        db.record_unavailable(date, "usd", "eur", "fawazahmed0", reason="persistent_error")
        assert (date, "eur") in db.get_unavailable_pairs("usd", "fawazahmed0")
    finally:
        db.close()


def test_present_and_unavailable_disjoint(db_path: Path):
    """Storing data for a cell removes any prior gap record (tables stay disjoint).

    Runs against a real DuckDB (no mocks).

    1. Record a (date, quote) as an unavailable gap.
    2. Upsert real data for the same cell — the stale gap row is deleted.
    3. Recording the same cell as unavailable again is a no-op while it has data.
    """

    db = CurrencyRateDatabase(db_path)
    try:
        date = datetime.date(2026, 6, 1)

        # 1. Record the cell as a gap.
        db.record_unavailable(date, "usd", "eur", "fawazahmed0", reason="quote_missing")
        assert (date, "eur") in db.get_unavailable_pairs("usd", "fawazahmed0")

        # 2. Upsert real data for the same cell — gap removed, cell now present.
        db.upsert_rates(DateRates(date=date, base_currency="usd", source="fawazahmed0", rows=[("eur", 0.9)]))
        assert (date, "eur") in db.get_present_pairs("usd", "fawazahmed0")
        assert (date, "eur") not in db.get_unavailable_pairs("usd", "fawazahmed0")

        # 3. Reverse direction: recording a gap for an already-present cell is a
        #    no-op (e.g. a present date that later 404s on a tail refetch / give-up).
        db.record_unavailable(date, "usd", "eur", "fawazahmed0", reason="date_404", http_status=404)
        assert (date, "eur") in db.get_present_pairs("usd", "fawazahmed0")
        assert (date, "eur") not in db.get_unavailable_pairs("usd", "fawazahmed0")
    finally:
        db.close()


def test_parse_document_classifies_present_absent_and_malformed():
    """_parse_document distinguishes present, absent, and malformed quotes.

    Pure-function test on literal JSON bodies (no network, no mocks).

    1. A well-formed body returns `ok` with every requested quote parsed.
    2. A quote absent from the body is reported in `missing_quotes` (still `ok`).
    3. A present-but-unparseable value makes the whole date `transient_error`
       (corrupt data is retried, not stored or recorded as a permanent gap).
    """

    date = datetime.date(2026, 6, 1)
    quotes = ("eur", "gbp")

    # 1. Well-formed body → ok with both quotes.
    ok = _parse_document(date, "usd", quotes, "fawazahmed0", {"usd": {"eur": 0.9, "gbp": 0.8}})
    assert ok.status == "ok"
    assert dict(ok.rates.rows) == {"eur": 0.9, "gbp": 0.8}
    assert ok.missing_quotes == ()

    # 2. Absent quote → ok, reported as missing (a permanent per-quote gap).
    absent = _parse_document(date, "usd", quotes, "fawazahmed0", {"usd": {"eur": 0.9}})
    assert absent.status == "ok"
    assert absent.missing_quotes == ("gbp",)
    assert dict(absent.rates.rows) == {"eur": 0.9}

    # 3. Present but unparseable value → transient_error (retry, do not record).
    malformed = _parse_document(date, "usd", quotes, "fawazahmed0", {"usd": {"eur": 0.9, "gbp": "oops"}})
    assert malformed.status == "transient_error"


def test_filter_known_bad_rates_removes_source_outlier(db_path: Path):
    """The cleaning step removes the known 2025-12-06 BTC/ETH source outlier.

    Real-network, no mocks: scans the window around the known upstream glitch and
    applies the separate cleaning step.

    1. Scan 2025-12-05..07 for the default currencies (the source returns garbage
       btc/eth on 2025-12-06).
    2. Assert the raw data still contains the bad 2025-12-06 btc/eth cells
       (ingestion stores raw, unfiltered).
    3. Apply `filter_known_bad_rates` and assert exactly those two cells are gone,
       the row count drops by 2, and good rows (e.g. 2025-12-06 EUR) remain.
    """

    bad_date = datetime.date(2025, 12, 6)

    # 1. Scan the window around the known source glitch.
    result = run_incremental_scan(
        db_path=db_path,
        start_date=datetime.date(2025, 12, 5),
        end_date=datetime.date(2025, 12, 7),
    )
    try:
        raw = result.db.get_rates_dataframe()
        raw["date"] = pd.to_datetime(raw["date"]).dt.date

        # 2. Raw ingestion keeps the bad cells verbatim.
        raw_pairs = {(row.date, row.quote_currency) for row in raw.itertuples()}
        assert (bad_date, "btc") in raw_pairs
        assert (bad_date, "eth") in raw_pairs

        # 3. Cleaning removes exactly the two known-bad cells, keeps the rest.
        cleaned = filter_known_bad_rates(raw)
        cleaned_pairs = {(row.date, row.quote_currency) for row in cleaned.itertuples()}
        assert (bad_date, "btc") not in cleaned_pairs
        assert (bad_date, "eth") not in cleaned_pairs
        assert (bad_date, "eur") in cleaned_pairs  # good rows untouched
        assert len(cleaned) == len(raw) - 2
    finally:
        result.db.close()


def test_scan_script_entry_point(db_path: Path, tmp_path: Path):
    """The scan-currencies entry point runs end-to-end as an operator would.

    1. Invoke the ``scan-currencies`` entry point module
       (``python -m eth_defi.currency_api.cli``) as a subprocess with the date
       window, currency set and DB path passed via environment variables.
    2. Assert the process exits 0 and the DuckDB contains the expected rows.
    """

    # 1. Invoke the entry-point module as a subprocess with env-var configuration.
    env = {
        **os.environ,
        "DB_PATH": str(db_path),
        "START_DATE": WINDOW_START.isoformat(),
        "END_DATE": WINDOW_START.isoformat(),  # single day, keep it tiny
        "QUOTE_CURRENCIES": "eur,gbp",
        "LOG_LEVEL": "info",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "eth_defi.currency_api.cli"],
        env=env,
        cwd=tmp_path,  # keep logs/ out of the repo tree
        capture_output=True,
        text=True,
        timeout=120,
    )

    # 2. Assert clean exit and expected rows on disk.
    assert completed.returncode == 0, f"script failed: {completed.stderr}"
    db = CurrencyRateDatabase(db_path)
    try:
        assert db.row_count() == 2  # 1 date x 2 quotes
    finally:
        db.close()
