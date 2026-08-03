"""Perp DEX native vault deposit-permission export metadata."""

import datetime
from collections.abc import Callable

import pytest

from eth_defi.apex.vault_data_export import create_apex_vault_row
from eth_defi.grvt.vault_data_export import create_grvt_vault_row
from eth_defi.hibachi.vault_data_export import create_hibachi_vault_row
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row, normalise_hyperliquid_deposit_permissions
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.perp_dex.vault import PERP_VAULT_PUBLIC_DEPOSITS_CLOSED_NOTE, classify_perp_vault_deposit_access
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

PerpVaultRowFactory = Callable[[], tuple[VaultSpec, VaultRow]]
FIRST_SEEN = datetime.datetime.fromisoformat("2026-01-01T00:00:00")
LEGACY_HYPERLIQUID_VAULT_COUNT = 2


@pytest.mark.parametrize(
    "create_row",
    (
        lambda: create_hyperliquid_vault_row(
            vault_address="0x1111111111111111111111111111111111111111",
            name="Hyperliquid test vault",
            description="Public Hyperliquid native vault.",
            tvl=1_000_000.0,
            create_time=FIRST_SEEN,
        ),
        lambda: create_hyperliquid_vault_row(
            vault_address="0x2222222222222222222222222222222222222222",
            name="Hyperliquid protocol vault",
            description="Public HLP parent vault.",
            tvl=1_000_000.0,
            create_time=FIRST_SEEN,
            allow_deposits=False,
            relationship_type="parent",
        ),
        lambda: create_lighter_pool_row(
            account_index=1,
            name="Lighter test pool",
            description="Public Lighter pool.",
            tvl=1_000_000.0,
            created_at=FIRST_SEEN,
        ),
        lambda: create_grvt_vault_row(
            vault_id="VLT:test",
            chain_vault_id=1,
            name="GRVT test vault",
            description="Public GRVT strategy vault.",
            tvl=1_000_000.0,
            discoverable=True,
            status="active",
        ),
        lambda: create_apex_vault_row(
            vault_id="1",
            name="ApeX test vault",
            description="Public ApeX vault.",
            tvl=1_000_000.0,
            share_count=1_000.0,
            created_at=FIRST_SEEN,
            first_seen=FIRST_SEEN,
            status="VAULT_IN_PROCESS",
        ),
    ),
)
def test_native_perp_dex_rows_export_permissionless_deposit_policy(create_row: PerpVaultRowFactory) -> None:
    """Publicly open native perp DEX rows export as permissionless."""
    _spec, row = create_row()

    assert row["_deposit_permission"] == VaultDepositPermission.permissionless.value
    assert row.get("_whitelist_notes") is None


@pytest.mark.parametrize(
    "create_row",
    (
        lambda: create_hyperliquid_vault_row(
            vault_address="0x1111111111111111111111111111111111111111",
            name="Closed Hyperliquid vault",
            description=None,
            tvl=1_000_000.0,
            create_time=FIRST_SEEN,
            is_closed=True,
        ),
        lambda: create_hyperliquid_vault_row(
            vault_address="0x2222222222222222222222222222222222222222",
            name="Deposit-disabled Hyperliquid vault",
            description=None,
            tvl=1_000_000.0,
            create_time=FIRST_SEEN,
            allow_deposits=False,
        ),
        lambda: create_lighter_pool_row(
            account_index=2,
            name="Inactive Lighter pool",
            description=None,
            tvl=1_000_000.0,
            created_at=FIRST_SEEN,
            status=1,
        ),
        lambda: create_grvt_vault_row(
            vault_id="VLT:closed",
            chain_vault_id=2,
            name="Closed GRVT vault",
            description=None,
            tvl=1_000_000.0,
            discoverable=False,
            status="active",
        ),
        lambda: create_apex_vault_row(
            vault_id="2",
            name="Finished ApeX vault",
            description=None,
            tvl=1_000_000.0,
            share_count=1_000.0,
            created_at=FIRST_SEEN,
            first_seen=FIRST_SEEN,
            status="VAULT_FINISHED",
        ),
    ),
)
def test_closed_native_perp_dex_rows_export_whitelisted_deposit_policy(create_row: PerpVaultRowFactory) -> None:
    """Vaults not open to public deposits export as whitelisted."""
    _spec, row = create_row()

    assert row["_deposit_permission"] == VaultDepositPermission.whitelisted.value
    assert row["_whitelist_notes"]


def test_grvt_missing_public_status_exports_unknown() -> None:
    """Legacy GRVT rows without access metadata do not claim public access."""
    _spec, row = create_grvt_vault_row(
        vault_id="VLT:legacy",
        chain_vault_id=3,
        name="Legacy GRVT vault",
        description=None,
        tvl=1_000_000.0,
    )

    assert row["_deposit_permission"] == VaultDepositPermission.unknown.value


def test_hibachi_without_public_deposit_status_exports_unknown() -> None:
    """Hibachi does not expose a source field proving public deposit access."""
    _spec, row = create_hibachi_vault_row(
        vault_id=1,
        symbol="HBT",
        name="Hibachi test vault",
        description=None,
        tvl=1_000_000.0,
    )

    assert row["_deposit_permission"] == VaultDepositPermission.unknown.value
    assert row["_whitelist_notes"] is None


def test_unknown_apex_status_exports_unknown() -> None:
    """Unrecognised ApeX lifecycle states do not invent deposit availability."""
    _spec, row = create_apex_vault_row(
        vault_id="3",
        name="Unknown ApeX vault",
        description=None,
        tvl=1_000_000.0,
        share_count=1_000.0,
        created_at=FIRST_SEEN,
        first_seen=FIRST_SEEN,
        status="VAULT_FUTURE_STATUS",
    )

    assert row["_deposit_permission"] == VaultDepositPermission.unknown.value
    assert row["_whitelist_notes"] is None


def test_closed_perp_vault_access_requires_qualification() -> None:
    """The compatibility whitelist value cannot omit its semantic caveat."""
    with pytest.raises(ValueError, match="closed_reason is required"):
        classify_perp_vault_deposit_access(public_deposits_open=False)

    access = classify_perp_vault_deposit_access(public_deposits_open=False, closed_reason="Deposits paused")

    assert access.permission is VaultDepositPermission.whitelisted
    assert PERP_VAULT_PUBLIC_DEPOSITS_CLOSED_NOTE in access.whitelist_notes


def test_legacy_hyperliquid_rows_migrate_from_last_observed_deposit_state() -> None:
    """Retained Hyperliquid rows use their last source-backed closure reason."""
    open_spec, open_row = create_hyperliquid_vault_row(
        vault_address="0x3333333333333333333333333333333333333333",
        name="Legacy open vault",
        description=None,
        tvl=1_000_000.0,
        create_time=FIRST_SEEN,
    )
    closed_spec, closed_row = create_hyperliquid_vault_row(
        vault_address="0x4444444444444444444444444444444444444444",
        name="Legacy closed vault",
        description=None,
        tvl=1_000_000.0,
        create_time=FIRST_SEEN,
        is_closed=True,
    )
    open_row.pop("_deposit_permission")
    closed_row.pop("_deposit_permission")
    vault_db = VaultDatabase()
    vault_db.rows[open_spec] = open_row
    vault_db.rows[closed_spec] = closed_row

    changed = normalise_hyperliquid_deposit_permissions(vault_db)

    assert changed == LEGACY_HYPERLIQUID_VAULT_COUNT
    assert open_row["_deposit_permission"] == VaultDepositPermission.permissionless.value
    assert closed_row["_deposit_permission"] == VaultDepositPermission.whitelisted.value
    assert normalise_hyperliquid_deposit_permissions(vault_db) == 0


def test_legacy_hyperliquid_unknown_reason_does_not_claim_public_access() -> None:
    """Future retained closure reasons remain unknown until classified."""
    spec, row = create_hyperliquid_vault_row(
        vault_address="0x5555555555555555555555555555555555555555",
        name="Future state vault",
        description=None,
        tvl=1_000_000.0,
        create_time=FIRST_SEEN,
    )
    row["_deposit_closed_reason"] = "Future source status"
    vault_db = VaultDatabase()
    vault_db.rows[spec] = row

    assert normalise_hyperliquid_deposit_permissions(vault_db) == 1
    assert row["_deposit_permission"] == VaultDepositPermission.unknown.value
    assert row["_whitelist_notes"] is None
