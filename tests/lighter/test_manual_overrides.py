"""Manual Lighter metadata takes precedence over provider status."""

import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from eth_defi.lighter.constants import LIGHTER_ETHEREUM, LIGHTER_ROBINHOOD, LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.vault.flag import VAULT_FLAGS_AND_NOTES, VaultFlag
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.mark.parametrize("status", [0, 1, None, 99])
def test_manual_deposit_closure(status: int | None) -> None:
    """Honour a manual closure regardless of the API status.

    The identically numbered Ethereum pool must retain provider-derived access,
    and a closure must not change withdrawal availability or remove the note.

    :param status: Active, inactive, missing or unknown provider status.
    :return: ``None``; assertions check exported access and deployment isolation.
    """
    _, row = create_lighter_pool_row(LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX, "LLP", None, 100, None, status=status, deployment=LIGHTER_ROBINHOOD)
    assert row["_flags"] == {VaultFlag.perp_dex_trading_vault, VaultFlag.deposits_closed}
    assert row["_deposit_closed_reason"] == "Public deposits are closed."
    assert row["_deposit_permission"] == "whitelisted"
    assert "Public deposits are closed" in row["_whitelist_notes"]
    assert row["_redemption_closed_reason"] is None
    assert "USDG-denominated protocol insurance fund" in row["_notes"]
    _, ethereum = create_lighter_pool_row(LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX, "LLP", None, 100, None, status=status, deployment=LIGHTER_ETHEREUM)
    assert ethereum["_flags"] == {VaultFlag.perp_dex_trading_vault}
    assert ethereum["_deposit_permission"] == ({0: "permissionless", 1: "whitelisted"}.get(status, "unknown"))
    assert ethereum["_deposit_closed_reason"] == ("Pool not active (status 1)" if status == 1 else None)


def test_manual_flags_preserve_descriptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Combine maintained flags and descriptions without losing native flags.

    Centaur's existing description override remains authoritative when another
    manual flag is assigned, while notes follow the same registry.

    :param monkeypatch: Restore the temporary manual flag entry after the test.
    :return: ``None``; assertions validate exported metadata precedence.
    """
    monkeypatch.setitem(VAULT_FLAGS_AND_NOTES, "lighter-pool-281474976488848", (VaultFlag.review_needed, "Review requested."))
    _, row = create_lighter_pool_row(281474976488848, "Post Fiat", "Provider copy.", 100, None, status=0)
    assert row["_flags"] == {VaultFlag.perp_dex_trading_vault, VaultFlag.review_needed}
    assert "goodalexander" in row["_description"]
    metadata = get_handwritten_vault_metadata(LIGHTER_ETHEREUM.chain_id, row["Address"])
    assert row["_short_description"] == metadata.short_description
    assert row["_notes"] == "Review requested."
    assert row["_deposit_permission"] == "permissionless"


def test_closure_migration_preserves_metadata() -> None:
    """Refresh only closure fields and preserve unrelated saved information.

    Repeating the migration must report no changes. A missing pool must not
    create a synthetic empty row in the metadata database.

    :return: ``None``; assertions validate preservation and repeatability.
    """
    migration = runpy.run_path(Path(__file__).parents[2] / "migration/scripts/081-lighter-robinhood-deposits-closed.py")
    spec, row = create_lighter_pool_row(LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX, "LLP", "Saved copy.", 100, None, deployment=LIGHTER_ROBINHOOD)
    row.update(_flags={VaultFlag.perp_dex_trading_vault, VaultFlag.review_needed}, _deposit_closed_reason=None, _deposit_permission="permissionless", _whitelist_notes=None)
    original = row.copy()
    database = VaultDatabase(rows={spec: row})
    context = Mock()
    context.metadata.return_value = database
    migration["migrate"](context)
    assert row["_flags"] == original["_flags"] | {VaultFlag.deposits_closed}
    assert row["_deposit_closed_reason"] == "Public deposits are closed."
    assert row["_deposit_permission"] == "whitelisted"
    assert "Public deposits are closed" in row["_whitelist_notes"]
    for field in original.keys() - {"_flags", *migration["FIELDS"]}:
        assert row[field] == original[field]
    migration["migrate"](context)
    assert context.report.call_args.kwargs["updated_fields"] == 0
    database.rows.clear()
    migration["migrate"](context)
    assert database.rows == {}
    assert context.report.call_args.kwargs["missing_specs"] == [spec.as_string_id()]
