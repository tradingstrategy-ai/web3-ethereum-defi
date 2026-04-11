"""Synchronise Hyperliquid vault review data with Google Sheets.

This module provides a small review workflow for Hyperliquid vaults:

1. Export vault metadata into a Google Spreadsheet for manual review
2. Preserve existing human-entered review decisions across re-syncs
3. Read the review decisions back into Python as a typed mapping

The spreadsheet is treated as a long-lived append/upsert ledger keyed by
vault address. Existing rows are never deleted automatically, even if a vault
disappears from the latest API scan. This preserves manual review history.

Google service account setup
============================

Use a dedicated Google Cloud project and a dedicated service account for this
sheet only.

1. Enable the Google Sheets API in the project
2. Create a service account dedicated to this pipeline
3. Create a JSON key for that service account
4. Share only the target spreadsheet with the service account email as Editor
5. Use the service account JSON with :py:func:`sync_vault_review_sheet` or
   :py:func:`fetch_vault_review_statuses`

This uses a service account credential, not a generic API key. The code
requests the Google Sheets scope only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from eth_typing import HexAddress

#: Column order used in the review spreadsheet.
#:
#: ``Trading Strategy`` and ``Hyperliquid`` are derived link columns that
#: the sync writes straight from the vault address. They let reviewers
#: click through to the Trading Strategy vault page and the native
#: Hyperliquid vault UI without leaving the spreadsheet.
SHEET_HEADERS = (
    "Name",
    "address",
    "APY 1M",
    "TVL",
    "followers",
    "Review status",
    "Trading Strategy",
    "Hyperliquid",
)

#: Columns we require to be present when reading the sheet back.
#:
#: The two link columns are derived on write and are never consumed on
#: read, so we don't require them: older sheets that predate the link
#: columns can still be synced and will be upgraded to the new schema on
#: the next write.
REQUIRED_READ_HEADERS = (
    "Name",
    "address",
    "APY 1M",
    "TVL",
    "followers",
    "Review status",
)

#: Trading Strategy address-based vault redirector URL template.
#:
#: See: https://tradingstrategy.ai/trading-view/vaults/address/0x2431edfcb662e6ff6deab113cc91878a0b53fb0f
TRADING_STRATEGY_VAULT_URL_TEMPLATE = "https://tradingstrategy.ai/trading-view/vaults/address/{address}"

#: Hyperliquid native vault UI URL template.
#:
#: See: https://app.hyperliquid.xyz/vaults/0x3df9769bbbb335340872f01d8157c779d73c6ed0
HYPERLIQUID_VAULT_URL_TEMPLATE = "https://app.hyperliquid.xyz/vaults/{address}"


class ReviewStatus(str, Enum):
    """Manual vault review decision."""

    ok = "ok"
    avoid = "avoid"


@dataclass(slots=True)
class VaultReviewRow:
    """Single spreadsheet row for Hyperliquid vault review."""

    #: Vault display name.
    name: str
    #: Hyperliquid vault address used as the primary key.
    address: HexAddress
    #: Hyperliquid API APR snapshot as a decimal percentage value.
    apy_1m: float | None
    #: Latest TVL in USD.
    tvl: float | None
    #: Latest follower count.
    followers: int | None
    #: Manual review decision.
    review_status: ReviewStatus | None = None


def _create_gspread_client(
    service_account_file: Path | None = None,
    service_account_info: Mapping[str, Any] | None = None,
):
    """Create an authorised gspread client.

    This helper lazy-imports the optional Google dependency so importing
    :pymod:`eth_defi.hyperliquid.vault_review_sync` does not require it.

    The repository's earlier Google Sheets tooling uses
    :py:func:`gspread.service_account` with a service-account JSON file.
    We keep that configuration style here and additionally support parsed
    credentials for callers that already hold the JSON payload in memory.

    :param service_account_file:
        Path to a Google service account JSON file.
    :param service_account_info:
        Parsed Google service account JSON credentials.
    :return:
        Authorised gspread client.
    """
    if service_account_file and service_account_info:
        message = "Pass either service_account_file or service_account_info, not both"
        raise ValueError(message)
    if not service_account_file and not service_account_info:
        message = "Either service_account_file or service_account_info is required"
        raise ValueError(message)

    try:
        import gspread  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extras
        message = "Google Sheets support requires the optional 'gsheets' dependencies"
        raise ImportError(message) from exc

    if service_account_info is not None:
        return gspread.service_account_from_dict(dict(service_account_info))
    return gspread.service_account(filename=str(service_account_file))


def _open_worksheet(
    sheet_url: str,
    worksheet_name: str,
    service_account_file: Path | None = None,
    service_account_info: Mapping[str, Any] | None = None,
):
    """Open or create the target worksheet.

    :param sheet_url:
        Google Sheets URL.
    :param worksheet_name:
        Worksheet tab name.
    :param service_account_file:
        Path to a Google service account JSON file.
    :param service_account_info:
        Parsed Google service account JSON credentials.
    :return:
        Tuple of ``(spreadsheet, worksheet)``.
    """
    client = _create_gspread_client(
        service_account_file=service_account_file,
        service_account_info=service_account_info,
    )
    spreadsheet = client.open_by_url(sheet_url)
    import gspread  # noqa: PLC0415

    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows="100", cols=str(len(SHEET_HEADERS)))

    return spreadsheet, worksheet


def _normalise_address(address: str) -> HexAddress:
    """Normalise a vault address for sheet storage and lookups."""
    return HexAddress(address.strip().lower())


def _parse_review_status(raw_value: str | None) -> ReviewStatus | None:
    """Parse a human-entered sheet status into an enum value.

    Comparison is case-insensitive so reviewers can type ``ok``, ``Ok``,
    ``OK``, ``avoid``, ``Avoid``, or ``AVOID`` and have them all round-trip
    through the sync. :py:func:`_format_review_status` always writes back
    the canonical ``OK`` / ``Avoid`` form on the next sync.
    """
    value = (raw_value or "").strip()
    if value == "":
        return None
    normalised = value.upper()
    if normalised == "OK":
        return ReviewStatus.ok
    if normalised == "AVOID":
        return ReviewStatus.avoid
    raise ValueError(f"Unknown review status {value!r}. Expected one of: OK, Avoid, empty (case-insensitive)")


def _format_review_status(review_status: ReviewStatus | None) -> str:
    """Format an enum review status for spreadsheet storage."""
    if review_status is None:
        return ""
    if review_status == ReviewStatus.ok:
        return "OK"
    if review_status == ReviewStatus.avoid:
        return "Avoid"
    raise ValueError(f"Unsupported review status {review_status!r}")


def _coerce_float(value: Any) -> float | None:
    """Convert a spreadsheet or DataFrame numeric cell to float."""
    if value in {"", None}:
        return None
    return float(value)


def _coerce_int(value: Any) -> int | None:
    """Convert a spreadsheet or DataFrame numeric cell to int."""
    if value in {"", None}:
        return None
    return int(value)


def _row_from_sheet_dict(row_dict: Mapping[str, str]) -> VaultReviewRow:
    """Build a typed row object from a worksheet row dictionary."""
    return VaultReviewRow(
        name=row_dict.get("Name", ""),
        address=_normalise_address(row_dict.get("address", "")),
        apy_1m=_coerce_float(row_dict.get("APY 1M")),
        tvl=_coerce_float(row_dict.get("TVL")),
        followers=_coerce_int(row_dict.get("followers")),
        review_status=_parse_review_status(row_dict.get("Review status")),
    )


def _row_to_sheet_values(row: VaultReviewRow) -> list[Any]:
    """Serialise a typed review row to worksheet values.

    The ``Trading Strategy`` and ``Hyperliquid`` columns are derived from
    the vault address on every sync — they are never read back — so they
    always reflect the latest URL templates even if the reviewer opens
    an old spreadsheet snapshot.
    """
    return [
        row.name,
        row.address,
        "" if row.apy_1m is None else row.apy_1m,
        "" if row.tvl is None else row.tvl,
        "" if row.followers is None else row.followers,
        _format_review_status(row.review_status),
        TRADING_STRATEGY_VAULT_URL_TEMPLATE.format(address=row.address),
        HYPERLIQUID_VAULT_URL_TEMPLATE.format(address=row.address),
    ]


def _extract_existing_rows(worksheet) -> tuple[list[HexAddress], dict[HexAddress, VaultReviewRow]]:
    """Read and validate the current worksheet contents.

    :param worksheet:
        gspread worksheet object.
    :return:
        Tuple of ``(existing_order, rows_by_address)``.
    """
    values = worksheet.get_all_values()
    if not values:
        return [], {}

    header = values[0]
    if not any(cell for cell in header):
        # Brand-new / empty worksheet: gspread returns ``[[]]`` (or a single row
        # of empty cells) instead of an empty list. Treat this as "no data".
        return [], {}

    rows = values[1:]
    column_index = {name: idx for idx, name in enumerate(header)}
    for required in REQUIRED_READ_HEADERS:
        if required not in column_index:
            raise ValueError(f"Worksheet is missing required column {required!r}")

    existing_order: list[HexAddress] = []
    rows_by_address: dict[HexAddress, VaultReviewRow] = {}

    for row in rows:
        row_dict = {name: row[column_index[name]] if column_index[name] < len(row) else "" for name in REQUIRED_READ_HEADERS}
        address = _normalise_address(row_dict["address"])
        if address in rows_by_address:
            raise ValueError(f"Duplicate address in worksheet: {address}")
        typed_row = _row_from_sheet_dict(row_dict)
        existing_order.append(address)
        rows_by_address[address] = typed_row

    return existing_order, rows_by_address


def sync_vault_review_sheet(
    rows: list[VaultReviewRow],
    sheet_url: str,
    worksheet_name: str = "Hyperliquid vault review",
    service_account_file: Path | None = None,
    service_account_info: Mapping[str, Any] | None = None,
) -> None:
    """Upsert Hyperliquid vault rows into a Google Sheet.

    Existing manual ``Review status`` values are preserved for matching
    addresses. Existing rows missing from the new input are left intact so the
    sheet acts as a durable review ledger.

    :param rows:
        Fresh rows from the Hyperliquid metadata database.
    :param sheet_url:
        Google Sheets URL.
    :param worksheet_name:
        Worksheet tab name.
    :param service_account_file:
        Path to a Google service account JSON file.
    :param service_account_info:
        Parsed Google service account JSON credentials.
    """
    _, worksheet = _open_worksheet(
        sheet_url=sheet_url,
        worksheet_name=worksheet_name,
        service_account_file=service_account_file,
        service_account_info=service_account_info,
    )

    existing_order, existing_rows = _extract_existing_rows(worksheet)

    incoming_by_address: dict[HexAddress, VaultReviewRow] = {}
    for row in rows:
        address = _normalise_address(row.address)
        if address in incoming_by_address:
            raise ValueError(f"Duplicate address in input rows: {address}")
        incoming_by_address[address] = VaultReviewRow(
            name=row.name,
            address=address,
            apy_1m=row.apy_1m,
            tvl=row.tvl,
            followers=row.followers,
            review_status=row.review_status,
        )

    final_rows: list[VaultReviewRow] = []
    seen: set[HexAddress] = set()

    for address in existing_order:
        existing_row = existing_rows[address]
        current_row = incoming_by_address.get(address)
        if current_row is None:
            final_rows.append(existing_row)
        else:
            current_row.review_status = existing_row.review_status
            final_rows.append(current_row)
            seen.add(address)

    for row in rows:
        address = _normalise_address(row.address)
        if address not in seen and address not in existing_rows:
            final_rows.append(
                VaultReviewRow(
                    name=row.name,
                    address=address,
                    apy_1m=row.apy_1m,
                    tvl=row.tvl,
                    followers=row.followers,
                    review_status=row.review_status,
                )
            )

    serialised_rows = [list(SHEET_HEADERS)]
    serialised_rows.extend(_row_to_sheet_values(row) for row in final_rows)

    worksheet.clear()
    worksheet.update("A1", serialised_rows)


def fetch_vault_review_statuses(
    sheet_url: str,
    worksheet_name: str = "Hyperliquid vault review",
    service_account_file: Path | None = None,
    service_account_info: Mapping[str, Any] | None = None,
) -> dict[HexAddress, ReviewStatus | None]:
    """Read manual review statuses back from the spreadsheet.

    :param sheet_url:
        Google Sheets URL.
    :param worksheet_name:
        Worksheet tab name.
    :param service_account_file:
        Path to a Google service account JSON file.
    :param service_account_info:
        Parsed Google service account JSON credentials.
    :return:
        Mapping of lowercased vault address to review status.
    """
    _, worksheet = _open_worksheet(
        sheet_url=sheet_url,
        worksheet_name=worksheet_name,
        service_account_file=service_account_file,
        service_account_info=service_account_info,
    )
    _, existing_rows = _extract_existing_rows(worksheet)
    return {address: row.review_status for address, row in existing_rows.items()}


def fetch_vault_review_statuse(*args, **kwargs) -> dict[HexAddress, ReviewStatus | None]:
    """Compatibility alias for the original misspelled function name."""
    return fetch_vault_review_statuses(*args, **kwargs)
