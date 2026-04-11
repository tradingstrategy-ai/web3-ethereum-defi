"""Unit tests for Hyperliquid vault review spreadsheet sync."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

gspread = pytest.importorskip("gspread", reason="gspread not installed (optional gsheets dependency)")

from eth_defi.hyperliquid.vault_review_sync import ReviewStatus, VaultReviewRow, fetch_vault_review_statuses, sync_vault_review_sheet  # noqa: E402


@dataclass
class FakeWorksheet:
    """Small in-memory worksheet stub."""

    values: list[list[object]]

    def get_all_values(self) -> list[list[object]]:
        """Return stored worksheet values."""
        return [list(row) for row in self.values]

    def clear(self) -> None:
        """Clear worksheet contents."""
        self.values = []

    def update(self, _cell: str, values: list[list[object]]) -> None:
        """Replace worksheet contents."""
        self.values = [list(row) for row in values]


class FakeSpreadsheet:
    """Small in-memory spreadsheet stub."""

    def __init__(self, worksheets: dict[str, FakeWorksheet] | None = None) -> None:
        self.worksheets = worksheets or {}

    def worksheet(self, name: str) -> FakeWorksheet:
        """Fetch a worksheet or raise the gspread not-found error."""
        try:
            return self.worksheets[name]
        except KeyError as exc:
            raise gspread.exceptions.WorksheetNotFound(name) from exc

    def add_worksheet(self, title: str, _rows: str, _cols: str) -> FakeWorksheet:
        """Create a new worksheet."""
        worksheet = FakeWorksheet(values=[])
        self.worksheets[title] = worksheet
        return worksheet


class FakeClient:
    """Small in-memory gspread client stub."""

    def __init__(self, spreadsheet: FakeSpreadsheet) -> None:
        self.spreadsheet = spreadsheet

    def open_by_url(self, url: str) -> FakeSpreadsheet:
        """Return the configured spreadsheet."""
        assert url == "https://example.invalid/sheet"
        return self.spreadsheet


def test_sync_vault_review_sheet_preserves_manual_review_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing reviewed rows should survive reruns and new rows should append."""
    worksheet = FakeWorksheet(
        values=[
            ["Name", "address", "APY 1M", "TVL", "followers", "Review status"],
            ["Old Alpha", "0x1111111111111111111111111111111111111111", "0.1", "1000", "2", "OK"],
            ["Legacy", "0x9999999999999999999999999999999999999999", "0.2", "2000", "3", "Avoid"],
        ]
    )
    spreadsheet = FakeSpreadsheet({"Hyperliquid vault review": worksheet})
    monkeypatch.setattr(
        "eth_defi.hyperliquid.vault_review_sync._create_gspread_client",
        lambda **_: FakeClient(spreadsheet),
    )

    sync_vault_review_sheet(
        rows=[
            VaultReviewRow(
                name="Alpha Updated",
                address="0x1111111111111111111111111111111111111111",
                apy_1m=0.33,
                tvl=12_345.0,
                followers=7,
                review_status=None,
            ),
            VaultReviewRow(
                name="Bravo",
                address="0x2222222222222222222222222222222222222222",
                apy_1m=0.44,
                tvl=54_321.0,
                followers=9,
                review_status=None,
            ),
        ],
        sheet_url="https://example.invalid/sheet",
        worksheet_name="Hyperliquid vault review",
        service_account_info={"type": "service_account"},
    )

    assert worksheet.values[0] == ["Name", "address", "APY 1M", "TVL", "followers", "Review status"]
    assert worksheet.values[1] == ["Alpha Updated", "0x1111111111111111111111111111111111111111", 0.33, 12345.0, 7, "OK"]
    assert worksheet.values[2] == ["Legacy", "0x9999999999999999999999999999999999999999", 0.2, 2000.0, 3, "Avoid"]
    assert worksheet.values[3] == ["Bravo", "0x2222222222222222222222222222222222222222", 0.44, 54321.0, 9, ""]


def test_fetch_vault_review_statuses_parses_sheet_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reader should parse review values and lower-case addresses."""
    worksheet = FakeWorksheet(
        values=[
            ["Name", "address", "APY 1M", "TVL", "followers", "Review status"],
            ["Alpha", "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "0.1", "1000", "2", "OK"],
            ["Bravo", "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "0.2", "2000", "3", "Avoid"],
            ["Charlie", "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC", "0.3", "3000", "4", ""],
        ]
    )
    spreadsheet = FakeSpreadsheet({"Hyperliquid vault review": worksheet})
    monkeypatch.setattr(
        "eth_defi.hyperliquid.vault_review_sync._create_gspread_client",
        lambda **_: FakeClient(spreadsheet),
    )

    statuses = fetch_vault_review_statuses(
        sheet_url="https://example.invalid/sheet",
        worksheet_name="Hyperliquid vault review",
        service_account_info={"type": "service_account"},
    )

    assert statuses["0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] == ReviewStatus.ok
    assert statuses["0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"] == ReviewStatus.avoid
    assert statuses["0xcccccccccccccccccccccccccccccccccccccccc"] is None


def test_fetch_vault_review_statuses_rejects_invalid_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unexpected manual status values should fail loudly."""
    worksheet = FakeWorksheet(
        values=[
            ["Name", "address", "APY 1M", "TVL", "followers", "Review status"],
            ["Alpha", "0x1111111111111111111111111111111111111111", "0.1", "1000", "2", "Reject"],
        ]
    )
    spreadsheet = FakeSpreadsheet({"Hyperliquid vault review": worksheet})
    monkeypatch.setattr(
        "eth_defi.hyperliquid.vault_review_sync._create_gspread_client",
        lambda **_: FakeClient(spreadsheet),
    )

    with pytest.raises(ValueError, match="Unknown review status"):
        fetch_vault_review_statuses(
            sheet_url="https://example.invalid/sheet",
            worksheet_name="Hyperliquid vault review",
            service_account_info={"type": "service_account"},
        )


def test_sync_vault_review_sheet_rejects_duplicate_existing_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate addresses in the worksheet should fail instead of silently winning."""
    worksheet = FakeWorksheet(
        values=[
            ["Name", "address", "APY 1M", "TVL", "followers", "Review status"],
            ["Alpha", "0x1111111111111111111111111111111111111111", "0.1", "1000", "2", "OK"],
            ["Alpha clone", "0x1111111111111111111111111111111111111111", "0.2", "2000", "3", ""],
        ]
    )
    spreadsheet = FakeSpreadsheet({"Hyperliquid vault review": worksheet})
    monkeypatch.setattr(
        "eth_defi.hyperliquid.vault_review_sync._create_gspread_client",
        lambda **_: FakeClient(spreadsheet),
    )

    with pytest.raises(ValueError, match="Duplicate address in worksheet"):
        sync_vault_review_sheet(
            rows=[],
            sheet_url="https://example.invalid/sheet",
            worksheet_name="Hyperliquid vault review",
            service_account_info={"type": "service_account"},
        )
