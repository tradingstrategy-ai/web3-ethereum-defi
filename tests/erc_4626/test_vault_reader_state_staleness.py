"""Test vault reader share price staleness bookkeeping."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, sync_vault_staleness_metadata

VAULT_ADDRESS = "0x0000000000000000000000000000000000000001"


def _make_state() -> VaultReaderState:
    vault = SimpleNamespace(
        first_seen_at_block=None,
        spec=VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS),
        vault_address=VAULT_ADDRESS,
    )
    return VaultReaderState(vault)


def test_reader_state_staleness_baselines_on_first_successful_read():
    """First successful read creates a staleness baseline."""
    state = _make_state()
    timestamp = datetime.datetime(2026, 1, 1, 12, 0)

    state.record_share_price_check(
        block_number=100,
        timestamp=timestamp,
        share_price=Decimal("1.0"),
        errors=None,
    )

    assert state.share_price_last_checked_at == timestamp
    assert state.share_price_last_checked_block == 100
    assert state.share_price_last_check_error is None
    assert state.last_significant_share_price == Decimal("1.0")
    assert state.share_price_last_changed_at == timestamp
    assert state.share_price_last_changed_block == 100


def test_reader_state_staleness_keeps_last_changed_for_small_moves():
    """Small share price moves update checked time but not changed time."""
    state = _make_state()
    first_timestamp = datetime.datetime(2026, 1, 1, 12, 0)
    second_timestamp = datetime.datetime(2026, 1, 2, 12, 0)

    state.record_share_price_check(
        block_number=100,
        timestamp=first_timestamp,
        share_price=Decimal("1.0"),
        errors=None,
    )
    state.record_share_price_check(
        block_number=200,
        timestamp=second_timestamp,
        share_price=Decimal("1.0005"),
        errors=None,
    )

    assert state.share_price_last_checked_at == second_timestamp
    assert state.share_price_last_checked_block == 200
    assert state.last_significant_share_price == Decimal("1.0")
    assert state.share_price_last_changed_at == first_timestamp
    assert state.share_price_last_changed_block == 100


def test_reader_state_staleness_updates_last_changed_for_significant_moves():
    """Large enough share price moves become the new staleness baseline."""
    state = _make_state()
    first_timestamp = datetime.datetime(2026, 1, 1, 12, 0)
    second_timestamp = datetime.datetime(2026, 1, 2, 12, 0)

    state.record_share_price_check(
        block_number=100,
        timestamp=first_timestamp,
        share_price=Decimal("1.0"),
        errors=None,
    )
    state.record_share_price_check(
        block_number=200,
        timestamp=second_timestamp,
        share_price=Decimal("1.002"),
        errors=None,
    )

    assert state.last_significant_share_price == Decimal("1.002")
    assert state.share_price_last_changed_at == second_timestamp
    assert state.share_price_last_changed_block == 200


def test_reader_state_staleness_failed_read_only_updates_checked_fields():
    """Failed share price reads do not alter significant price state."""
    state = _make_state()
    state.last_share_price = Decimal("1.5")
    first_timestamp = datetime.datetime(2026, 1, 1, 12, 0)
    second_timestamp = datetime.datetime(2026, 1, 2, 12, 0)

    state.record_share_price_check(
        block_number=100,
        timestamp=first_timestamp,
        share_price=Decimal("1.0"),
        errors=None,
    )
    state.record_share_price_check(
        block_number=200,
        timestamp=second_timestamp,
        share_price=None,
        errors=["convertToAssets call failed"],
    )

    assert state.share_price_last_checked_at == second_timestamp
    assert state.share_price_last_checked_block == 200
    assert state.share_price_last_check_error == "convertToAssets call failed"
    assert state.last_share_price == Decimal("1.5")
    assert state.last_significant_share_price == Decimal("1.0")
    assert state.share_price_last_changed_at == first_timestamp
    assert state.share_price_last_changed_block == 100


def test_sync_vault_staleness_metadata():
    """Reader state staleness fields are copied to vault metadata rows."""
    spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault_db = VaultDatabase(rows={spec: {}})
    checked_at = datetime.datetime(2026, 1, 2, 12, 0)
    changed_at = datetime.datetime(2026, 1, 1, 12, 0)

    updated = sync_vault_staleness_metadata(
        vault_db,
        {
            spec: {
                "share_price_last_changed_at": changed_at,
                "share_price_last_changed_block": 100,
                "share_price_last_checked_at": checked_at,
                "share_price_last_checked_block": 200,
                "share_price_last_check_error": None,
                "vault_poll_frequency": "large_tvl",
                "vault_poll_interval_seconds": 3600,
            }
        },
    )

    row = vault_db.rows[spec]
    assert updated == 1
    assert row["_share_price_last_changed_at"] == changed_at
    assert row["_share_price_last_changed_block"] == 100
    assert row["_share_price_last_checked_at"] == checked_at
    assert row["_share_price_last_checked_block"] == 200
    assert row["_share_price_last_check_error"] is None
    assert row["_vault_poll_frequency"] == "large_tvl"
    assert row["_vault_poll_interval_seconds"] == 3600
