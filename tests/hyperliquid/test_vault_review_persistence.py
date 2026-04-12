"""Unit tests for the manual vault review status persistence path.

These tests exercise the plumbing that carries ``ReviewStatus`` values
from the Google Sheet into the ``VaultDatabase`` pickle and out through
:py:func:`eth_defi.research.vault_metrics.calculate_vault_record` into
the exported Series. They deliberately avoid network access and any
Google Sheets calls so they run in any CI environment that has the
``-E duckdb`` extra installed (needed to construct
:py:class:`~eth_defi.hyperliquid.daily_metrics.HyperliquidDailyMetricsDatabase`);
they do **not** need the optional ``-E gsheets`` extra.

What each test covers
=====================

1. :py:func:`test_create_hyperliquid_vault_row_accepts_manual_review_status`
   — ``create_hyperliquid_vault_row()`` stores the passed review enum on
   the row's ``_manual_review_status`` field.

2. :py:func:`test_merge_into_vault_database_applies_review_mapping`
   — when ``merge_into_vault_database`` receives a ``review_statuses``
   mapping, the new pickle rows reflect the mapping values.

3. :py:func:`test_merge_into_vault_database_carries_forward_when_sheet_is_down`
   — when the mapping is ``None`` (sheet unreachable), the merge
   preserves whatever ``_manual_review_status`` was already stored on
   the existing pickle row. This is the "Google Sheets is down" contract.

4. :py:func:`test_calculate_vault_record_emits_manual_review_status`
   — ``calculate_vault_record`` copies ``_manual_review_status`` from
   the vault metadata into the returned Series as a plain string so the
   existing ``export_lifetime_row`` JSON serialiser picks it up without
   needing to know about the enum type.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row, merge_into_vault_database
from eth_defi.hyperliquid.vault_review_sync import ReviewStatus
from eth_defi.research.vault_metrics import calculate_vault_record, slugify_vaults
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

ALPHA_ADDRESS = "0x1111111111111111111111111111111111111111"
BRAVO_ADDRESS = "0x2222222222222222222222222222222222222222"
CHARLIE_ADDRESS = "0x3333333333333333333333333333333333333333"


def _seed_metadata_db(db_path: Path, vaults: list[dict[str, Any]]) -> None:
    """Populate a fresh DuckDB with minimal vault_metadata rows.

    :param db_path:
        Path where the DuckDB file should be created. Must not exist yet.
    :param vaults:
        One dict per vault with at least ``address`` and ``name``. Other
        fields default to sensible values so the test stays compact.
    """
    assert not db_path.exists(), "Test DB path must be fresh"
    db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        for vault in vaults:
            db.upsert_vault_metadata(
                vault_address=vault["address"],
                name=vault["name"],
                leader="0x0000000000000000000000000000000000000000",
                description=None,
                is_closed=False,
                relationship_type="normal",
                create_time=datetime.datetime(2024, 1, 1),
                commission_rate=None,
                follower_count=vault.get("follower_count", 5),
                tvl=vault.get("tvl", 10_000.0),
                apr=vault.get("apr", 0.1),
                allow_deposits=True,
                flow_data_earliest_date=None,
            )
        db.save()
    finally:
        db.close()


def test_create_hyperliquid_vault_row_accepts_manual_review_status() -> None:
    """create_hyperliquid_vault_row stores the manual review enum on the row.

    1. Build a row with ``manual_review_status=ReviewStatus.avoid``.
    2. Assert the row's ``_manual_review_status`` field matches the enum.
    3. Build a second row without the argument and check the default is ``None``.
    """
    _, reviewed_row = create_hyperliquid_vault_row(
        vault_address=ALPHA_ADDRESS,
        name="Alpha",
        description=None,
        tvl=1_000.0,
        create_time=datetime.datetime(2024, 1, 1),
        manual_review_status=ReviewStatus.avoid,
    )
    assert reviewed_row["_manual_review_status"] is ReviewStatus.avoid

    _, default_row = create_hyperliquid_vault_row(
        vault_address=BRAVO_ADDRESS,
        name="Bravo",
        description=None,
        tvl=2_000.0,
        create_time=datetime.datetime(2024, 1, 1),
    )
    assert default_row["_manual_review_status"] is None


def test_merge_into_vault_database_applies_review_mapping(tmp_path: Path) -> None:
    """Explicit review_statuses mapping overrides per-vault values in the pickle.

    1. Seed a DuckDB with two Hyperliquid vaults.
    2. Call merge_into_vault_database with a mapping that marks Alpha ``ok``
       and Bravo ``avoid``.
    3. Reload the pickle and assert each VaultRow has the expected enum.
    """
    db_path = tmp_path / "daily-metrics.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"

    _seed_metadata_db(
        db_path,
        [
            {"address": ALPHA_ADDRESS, "name": "Alpha"},
            {"address": BRAVO_ADDRESS, "name": "Bravo"},
        ],
    )

    review_statuses = {
        ALPHA_ADDRESS: ReviewStatus.ok,
        BRAVO_ADDRESS: ReviewStatus.avoid,
    }

    db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        merge_into_vault_database(db, vault_db_path, review_statuses=review_statuses)
    finally:
        db.close()

    reloaded = VaultDatabase.read(vault_db_path)
    alpha_spec = VaultSpec(chain_id=HYPERCORE_CHAIN_ID, vault_address=ALPHA_ADDRESS)
    bravo_spec = VaultSpec(chain_id=HYPERCORE_CHAIN_ID, vault_address=BRAVO_ADDRESS)
    assert reloaded.rows[alpha_spec]["_manual_review_status"] is ReviewStatus.ok
    assert reloaded.rows[bravo_spec]["_manual_review_status"] is ReviewStatus.avoid


def test_merge_into_vault_database_carries_forward_when_sheet_is_down(tmp_path: Path) -> None:
    """When the sheet is unreachable the existing pickle values survive.

    1. First merge: explicitly stamp Alpha ``ok`` and Bravo ``avoid`` via
       an explicit review mapping.
    2. Second merge with ``review_statuses=None`` (simulating a Google
       Sheets outage). Metrics should refresh but the manual decisions
       must be preserved byte-for-byte from the first merge.
    3. Third merge with a partial mapping that only covers Alpha (e.g. the
       reviewer added a new vault Charlie in the meantime and the sheet
       doesn't know Bravo). Alpha gets overwritten, Bravo still carries
       forward, Charlie shows up as ``None`` (no decision yet).
    """
    db_path = tmp_path / "daily-metrics.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"

    _seed_metadata_db(
        db_path,
        [
            {"address": ALPHA_ADDRESS, "name": "Alpha"},
            {"address": BRAVO_ADDRESS, "name": "Bravo"},
        ],
    )

    # Step 1: seed manual reviews via an explicit mapping.
    db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        merge_into_vault_database(
            db,
            vault_db_path,
            review_statuses={
                ALPHA_ADDRESS: ReviewStatus.ok,
                BRAVO_ADDRESS: ReviewStatus.avoid,
            },
        )
    finally:
        db.close()

    # Step 2: second merge with review_statuses=None — sheet is "down".
    db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        merge_into_vault_database(db, vault_db_path, review_statuses=None)
    finally:
        db.close()

    reloaded = VaultDatabase.read(vault_db_path)
    alpha_spec = VaultSpec(chain_id=HYPERCORE_CHAIN_ID, vault_address=ALPHA_ADDRESS)
    bravo_spec = VaultSpec(chain_id=HYPERCORE_CHAIN_ID, vault_address=BRAVO_ADDRESS)
    assert reloaded.rows[alpha_spec]["_manual_review_status"] is ReviewStatus.ok, "Alpha's manual review must survive a sheet outage when review_statuses=None"
    assert reloaded.rows[bravo_spec]["_manual_review_status"] is ReviewStatus.avoid, "Bravo's manual review must survive a sheet outage when review_statuses=None"

    # Step 3: add a third vault to the DuckDB and do a partial mapping merge.
    db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        db.upsert_vault_metadata(
            vault_address=CHARLIE_ADDRESS,
            name="Charlie",
            leader="0x0000000000000000000000000000000000000000",
            description=None,
            is_closed=False,
            relationship_type="normal",
            create_time=datetime.datetime(2024, 1, 1),
            commission_rate=None,
            follower_count=3,
            tvl=3_000.0,
            apr=0.05,
            allow_deposits=True,
            flow_data_earliest_date=None,
        )
        db.save()

        # Alpha gets flipped to avoid, Bravo is absent from the mapping
        # (carry-forward), Charlie is a new vault with no review yet.
        merge_into_vault_database(
            db,
            vault_db_path,
            review_statuses={ALPHA_ADDRESS: ReviewStatus.avoid},
        )
    finally:
        db.close()

    reloaded = VaultDatabase.read(vault_db_path)
    charlie_spec = VaultSpec(chain_id=HYPERCORE_CHAIN_ID, vault_address=CHARLIE_ADDRESS)
    assert reloaded.rows[alpha_spec]["_manual_review_status"] is ReviewStatus.avoid
    assert reloaded.rows[bravo_spec]["_manual_review_status"] is ReviewStatus.avoid, "Bravo was not in the partial mapping so its pickle value must be carried forward"
    assert reloaded.rows[charlie_spec]["_manual_review_status"] is None


def test_calculate_vault_record_emits_manual_review_status() -> None:
    """calculate_vault_record surfaces manual_review_status as a plain string.

    1. Build a minimal VaultRow with ``_manual_review_status=ReviewStatus.ok``.
    2. Build a minimal 1h price DataFrame for the same vault.
    3. Call calculate_vault_record and assert the returned Series has
       ``manual_review_status == "ok"`` — i.e. the enum has been unwrapped
       so JSON serialisation downstream does not need enum awareness.
    4. Repeat with ``_manual_review_status`` absent and assert the field
       is ``None``.
    """
    address = ALPHA_ADDRESS
    chain_id = HYPERCORE_CHAIN_ID

    detection = ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=0,
        first_seen_at=datetime.datetime(2024, 1, 1),
        features={ERC4626Feature.hypercore_native},
        updated_at=datetime.datetime(2024, 1, 1),
        deposit_count=1,
        redeem_count=0,
    )
    fee_data = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0.0,
        performance=0.1,
        deposit=0.0,
        withdraw=0.0,
    )

    def _build_row(status: ReviewStatus | None) -> VaultRow:
        return {
            "Symbol": "ALPHA",
            "Name": "Alpha",
            "Address": address,
            "Denomination": "USDC",
            "Share token": "ALPHA",
            "NAV": Decimal("1000"),
            "Shares": Decimal("0"),
            "Protocol": "Hyperliquid",
            "Link": f"https://app.hyperliquid.xyz/vaults/{address}",
            "First seen": datetime.datetime(2024, 1, 1),
            "Mgmt fee": 0.0,
            "Perf fee": 0.1,
            "Deposit fee": 0.0,
            "Withdraw fee": 0.0,
            "Features": "",
            "_detection_data": detection,
            "_denomination_token": {"address": "0x2000000000000000000000000000000000000000", "symbol": "USDC", "decimals": 6},
            "_share_token": None,
            "_fees": fee_data,
            "_flags": set(),
            "_lockup": None,
            "_description": None,
            "_short_description": None,
            "_available_liquidity": None,
            "_utilisation": None,
            "_deposit_closed_reason": None,
            "_deposit_next_open": None,
            "_redemption_closed_reason": None,
            "_redemption_next_open": None,
            "_risk": None,
            "_manual_review_status": status,
        }

    # Minimal 1h price frame: one month of constant share price + TVL so
    # calculate_vault_record's period-metric math has something valid.
    index = pd.date_range("2024-01-01", periods=24 * 31, freq="1h")
    prices_df = pd.DataFrame(
        {
            "id": f"{chain_id}-{address}",
            "total_assets": 1_000.0,
            "share_price": 1.0,
            "event_count": 1,
            "chain": chain_id,
            "block_number": range(len(index)),
        },
        index=index,
    )

    spec = VaultSpec(chain_id=chain_id, vault_address=address)
    vault_id = f"{chain_id}-{address}"

    # Case 1: review present, emitted as the string "ok".
    metadata_rows = {spec: _build_row(ReviewStatus.ok)}
    slugify_vaults(metadata_rows)  # populates vault_slug / protocol_slug
    record = calculate_vault_record(
        prices_df=prices_df,
        vault_metadata_rows=metadata_rows,
        month_ago=index.max() - pd.Timedelta(days=30),
        three_months_ago=index.max() - pd.Timedelta(days=90),
        vault_id=vault_id,
    )
    assert record["manual_review_status"] == "ok"
    assert isinstance(record["manual_review_status"], str)

    # Case 2: review absent, emitted as None.
    metadata_rows = {spec: _build_row(None)}
    slugify_vaults(metadata_rows)
    record = calculate_vault_record(
        prices_df=prices_df,
        vault_metadata_rows=metadata_rows,
        month_ago=index.max() - pd.Timedelta(days=30),
        three_months_ago=index.max() - pd.Timedelta(days=90),
        vault_id=vault_id,
    )
    assert record["manual_review_status"] is None
