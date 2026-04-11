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
import os
from typing import Any

import pytest

gspread = pytest.importorskip("gspread", reason="Install the optional gsheets extra to run Google Sheets integration tests")

from eth_defi.hyperliquid.vault_review_sync import VaultReviewRow, fetch_vault_review_statuses, sync_vault_review_sheet  # noqa: E402


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
