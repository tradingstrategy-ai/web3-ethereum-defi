"""Integration test scaffold for Hyperliquid vault review Google Sheets sync.

This module intentionally avoids fake worksheet mocks. To run these tests
against a real Google Sheet, configure a dedicated service account and a
dedicated spreadsheet tab for testing.

Setup
=====

See :doc:`.claude/docs/gspread.md` in the repository root for the full
operator playbook, including the known limitation that sharing the test
spreadsheet with the service account cannot be automated and must be done
by a human in their own browser session.

Summary of required steps:

1. Enable the Google Sheets API in a dedicated Google Cloud project
2. Create a dedicated service account for these integration tests
3. Create a JSON key for that service account
4. Create a dedicated spreadsheet, or at minimum a dedicated worksheet tab,
   for test traffic only
5. Share that spreadsheet with the service account email as ``Editor`` —
   **this step must be done manually by a human operator**
6. Export the following environment variables before running pytest:

.. code-block:: shell

    export TEST_GS_SERVICE_ACCOUNT_JSON='{"type": "service_account", ...}'
    export TEST_GS_SHEET_URL=https://docs.google.com/spreadsheets/d/<ID>/edit
    export TEST_GS_WORKSHEET_NAME="Hyperliquid vault review integration"

``TEST_GS_SERVICE_ACCOUNT_JSON`` holds the raw contents of the
service-account JSON key as a single-line string, so no credential file
ever needs to live on disk alongside the repo.

7. Install the optional Google Sheets dependency:

.. code-block:: shell

    poetry install -E test -E gsheets

8. Run just this module:

.. code-block:: shell

    source .local-test.env && poetry run pytest tests/hyperliquid/test_vault_review_sync.py

Notes
=====

- These tests mutate the configured worksheet.
- Use a dedicated test worksheet name, not the production review tab.
- The integration checks below cover the real service-account login path and a
  basic write/read round-trip. Manual review preservation can then be checked
  interactively in the same sheet.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any

import pytest

gspread = pytest.importorskip("gspread", reason="Install the optional gsheets extra to run Google Sheets integration tests")

from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE  # noqa: E402
from eth_defi.hyperliquid.vault_review_sync import VaultReviewRow, fetch_vault_review_statuses, sync_vault_review_sheet  # noqa: E402

logger = logging.getLogger(__name__)

#: Worksheet tab used by the bulk test so it does not collide with the
#: happy-path 2-row test. ``sync_vault_review_sheet`` auto-creates it.
BULK_WORKSHEET_NAME = "Hyperliquid vault review integration (bulk)"

#: Upper bound on the full sync+readback round-trip against the real sheet.
#: A single ``worksheet.update`` call batches the entire sheet payload, so
#: most of the time is spent in gspread's clear+update+readback HTTP calls.
#: 180 s is generous for ~500 rows on a slow network; a local run is much
#: faster. Turning this into a hard assertion catches obvious regressions
#: (e.g. accidental per-row API calls) without being flaky.
BULK_SYNC_TIME_BUDGET_SECONDS = 180.0


def _get_google_sheet_test_config() -> tuple[dict[str, Any], str, str]:
    """Read Google Sheets integration-test configuration from the environment.

    :return:
        Tuple of ``(service_account_info, sheet_url, worksheet_name)``.
    """
    service_account_json = os.environ.get("TEST_GS_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_url = os.environ.get("TEST_GS_SHEET_URL", "").strip()
    worksheet_name = os.environ.get("TEST_GS_WORKSHEET_NAME", "").strip()

    if not service_account_json or not sheet_url or not worksheet_name:
        pytest.skip("Google Sheets integration test is not configured. Set TEST_GS_SERVICE_ACCOUNT_JSON, TEST_GS_SHEET_URL, and TEST_GS_WORKSHEET_NAME to run it.")

    try:
        service_account_info = json.loads(service_account_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"TEST_GS_SERVICE_ACCOUNT_JSON is not valid JSON: {exc}") from exc

    return service_account_info, sheet_url, worksheet_name


def test_vault_review_sheet_google_integration() -> None:
    """Exercise the real Google Sheets service-account flow with a live worksheet.

    The test writes two deterministic rows to the configured worksheet and then
    reads the review statuses back. Because the rows are inserted with blank
    manual review status, the readback should currently return ``None`` for
    both addresses.
    """
    service_account_info, sheet_url, worksheet_name = _get_google_sheet_test_config()

    rows = [
        VaultReviewRow(
            name="Integration test alpha",
            address="0x1111111111111111111111111111111111111111",
            apy_1m=0.11,
            tvl=1_111.0,
            followers=11,
            review_status=None,
        ),
        VaultReviewRow(
            name="Integration test bravo",
            address="0x2222222222222222222222222222222222222222",
            apy_1m=0.22,
            tvl=2_222.0,
            followers=22,
            review_status=None,
        ),
    ]

    sync_vault_review_sheet(
        rows=rows,
        sheet_url=sheet_url,
        worksheet_name=worksheet_name,
        service_account_info=service_account_info,
    )
    statuses = fetch_vault_review_statuses(
        sheet_url=sheet_url,
        worksheet_name=worksheet_name,
        service_account_info=service_account_info,
    )

    assert statuses["0x1111111111111111111111111111111111111111"] is None
    assert statuses["0x2222222222222222222222222222222222222222"] is None


def _optional_float(value: Any) -> float | None:
    """Convert a pandas cell value to ``float`` or ``None``.

    Pandas uses ``NaN`` to represent missing numeric data, which
    :py:class:`VaultReviewRow` models as ``None``. This mirrors the
    conversion logic in ``scripts/hyperliquid/daily-vault-metrics.py`` so
    the bulk test exercises the same code path the production pipeline
    uses when it pushes the review sheet.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    """Convert a pandas cell value to ``int`` or ``None``.

    See :py:func:`_optional_float` for the rationale.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return int(value)


def test_vault_review_sheet_google_bulk_integration() -> None:
    """Sync the full Hyperliquid vault metadata database to a real worksheet.

    This is the stress-test companion to
    :py:func:`test_vault_review_sheet_google_integration`. It intentionally
    pushes *every* vault currently tracked by the local DuckDB into the
    test spreadsheet so we catch performance regressions, payload-size
    issues, and edge-case data (``NaN`` APR/TVL, missing follower counts,
    unusual names) that the small 2-row happy path cannot surface.

    The test is skipped when either:

    - the Google Sheets integration-test environment variables are not
      configured, or
    - the Hyperliquid daily-metrics DuckDB at
      :py:data:`HYPERLIQUID_DAILY_METRICS_DATABASE` does not exist.

    Steps:

    1. Load the complete vault metadata DataFrame from the DuckDB.
    2. Convert every row to a :py:class:`VaultReviewRow`, applying the
       same ``NaN`` → ``None`` coercion the production script uses.
    3. Call :py:func:`sync_vault_review_sheet` against a dedicated bulk
       worksheet tab and time the round-trip.
    4. Read the review statuses back and assert every pushed address is
       present in the readback and that a sample address is stored
       lowercased (the upper-case form of that same address is **not**
       in the returned mapping).
    5. Assert that any manually-entered ``OK`` / ``Avoid`` statuses the
       operator already put into the sheet survive the sync
       unchanged — this is the "preserve manual review history" contract
       of :py:func:`sync_vault_review_sheet`. Preserved statuses from
       the pre-sync snapshot must equal the post-sync statuses for the
       same addresses.
    6. Assert the sync finished within
       :py:data:`BULK_SYNC_TIME_BUDGET_SECONDS` to catch accidental
       per-row API calls or other O(N) regressions.
    """
    service_account_info, sheet_url, _ = _get_google_sheet_test_config()

    # 1. Load the complete vault metadata DataFrame from the DuckDB.
    if not HYPERLIQUID_DAILY_METRICS_DATABASE.exists():
        pytest.skip(
            f"Hyperliquid daily-metrics DuckDB is missing at {HYPERLIQUID_DAILY_METRICS_DATABASE}; run scripts/hyperliquid/daily-vault-metrics.py to populate it before running the bulk integration test.",
        )

    # Lazy import so the unrelated happy-path test above does not need the
    # heavy eth_defi.hyperliquid.daily_metrics import chain.
    from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase  # noqa: PLC0415

    db = HyperliquidDailyMetricsDatabase(HYPERLIQUID_DAILY_METRICS_DATABASE)
    try:
        metadata_df = db.get_all_vault_metadata()
    finally:
        db.close()

    assert len(metadata_df) >= 100, f"Expected the DuckDB to contain at least ~100 vaults for a meaningful bulk test, got {len(metadata_df)}. The database may be freshly created; re-run daily-vault-metrics.py to populate it."

    # 2. Convert every row to a VaultReviewRow, mirroring daily-vault-metrics.py.
    rows = [
        VaultReviewRow(
            name=str(row["name"]),
            address=str(row["vault_address"]).lower(),
            apy_1m=_optional_float(row["apr"]),
            tvl=_optional_float(row["tvl"]),
            followers=_optional_int(row["follower_count"]),
            review_status=None,
        )
        for _, row in metadata_df.iterrows()
    ]
    pushed_addresses = {row.address for row in rows}

    # Snapshot any manually-entered review statuses *before* the sync so
    # we can check they survive the upsert untouched. When the bulk tab
    # is run for the first time this snapshot is empty — that is fine,
    # the assertion below becomes a no-op.
    pre_sync_statuses = fetch_vault_review_statuses(
        sheet_url=sheet_url,
        worksheet_name=BULK_WORKSHEET_NAME,
        service_account_info=service_account_info,
    )
    manually_reviewed = {address: status for address, status in pre_sync_statuses.items() if status is not None}
    logger.info("Bulk worksheet already has %d manually reviewed rows to preserve", len(manually_reviewed))

    # 3. Sync against the dedicated bulk worksheet tab and time it.
    start = time.monotonic()
    sync_vault_review_sheet(
        rows=rows,
        sheet_url=sheet_url,
        worksheet_name=BULK_WORKSHEET_NAME,
        service_account_info=service_account_info,
    )
    sync_elapsed = time.monotonic() - start

    # 4. Read the review statuses back and verify every pushed address.
    start = time.monotonic()
    post_sync_statuses = fetch_vault_review_statuses(
        sheet_url=sheet_url,
        worksheet_name=BULK_WORKSHEET_NAME,
        service_account_info=service_account_info,
    )
    readback_elapsed = time.monotonic() - start
    total_elapsed = sync_elapsed + readback_elapsed

    logger.info(
        "Bulk Google Sheets sync: %d vaults, sync=%.2fs, readback=%.2fs, total=%.2fs",
        len(rows),
        sync_elapsed,
        readback_elapsed,
        total_elapsed,
    )

    missing = pushed_addresses - post_sync_statuses.keys()
    assert not missing, f"{len(missing)} pushed addresses missing from readback, e.g. {sorted(missing)[:3]}"

    # Addresses must be stored lowercased: look up a sample by its lower-case
    # form and make sure the upper-case variant is not present.
    sample_address = next(iter(pushed_addresses))
    assert sample_address == sample_address.lower()
    assert sample_address in post_sync_statuses
    assert sample_address.upper() not in post_sync_statuses

    # 5. Manual review statuses the operator already put into the sheet
    #    must round-trip unchanged. This is the core "preserve review
    #    history" contract of sync_vault_review_sheet — if it regresses,
    #    a nightly sync would silently wipe days of manual review work.
    for address, expected in manually_reviewed.items():
        actual = post_sync_statuses.get(address)
        assert actual == expected, f"Manual review for {address} was overwritten: expected {expected!r}, got {actual!r}. sync_vault_review_sheet must preserve existing 'Review status' values for rows that are re-synced."

    # 6. Performance budget check. Fail loudly if a regression makes the
    #    sync suddenly take minutes instead of seconds.
    assert total_elapsed < BULK_SYNC_TIME_BUDGET_SECONDS, f"Bulk Google Sheets sync took {total_elapsed:.2f}s for {len(rows)} vaults, which exceeds the {BULK_SYNC_TIME_BUDGET_SECONDS:.0f}s budget. This usually means gspread is making per-row API calls instead of a batched update."
