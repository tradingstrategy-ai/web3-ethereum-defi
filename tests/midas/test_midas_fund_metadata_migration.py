"""Regression tests for the Midas tokenised-fund metadata migration."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.midas.constants import MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def fund_metadata_migration_module():
    """Load the hyphenated Midas fund-metadata migration module."""

    script_path = Path(__file__).parents[2] / "scripts" / "midas" / "migrate-fund-metadata.py"
    spec = importlib.util.spec_from_file_location("midas_fund_metadata_migration", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_midas_fund_metadata_migration_updates_only_reviewed_funds(fund_metadata_migration_module) -> None:
    """Repair mTBILL metadata without changing an unrelated Midas strategy product."""

    mtbill_spec = VaultSpec(MIDAS_MTBILL_ETHEREUM.chain_id, MIDAS_MTBILL_ETHEREUM.token)
    mbasis_spec = VaultSpec(MIDAS_MBASIS_ETHEREUM.chain_id, MIDAS_MBASIS_ETHEREUM.token)
    vault_db = VaultDatabase(
        rows={
            mtbill_spec: {
                "Name": "Midas US Treasury Bill Token",
                "Link": "https://midas.app/products",
                "_flags": set(),
                "_short_description": "Old summary",
                "_description": "Old description",
                "unrelated_field": "preserved",
            },
            mbasis_spec: {
                "Name": "Midas mBASIS",
                "_flags": set(),
                "_short_description": "Keep this strategy unchanged",
            },
        }
    )

    migration = fund_metadata_migration_module.create_fund_metadata_migration(
        MIDAS_MTBILL_ETHEREUM,
        vault_db.rows[mtbill_spec],
    )

    assert migration.changed_fields == ("_flags", "_short_description", "_description", "Link")
    fund_metadata_migration_module.apply_migrations(vault_db, [migration])

    mtbill_row = vault_db.rows[mtbill_spec]
    assert VaultFlag.tokenised_fund in mtbill_row["_flags"]
    assert mtbill_row["_short_description"] == MIDAS_MTBILL_ETHEREUM.short_description
    assert mtbill_row["_description"] == MIDAS_MTBILL_ETHEREUM.description
    assert mtbill_row["Link"] == MIDAS_MTBILL_ETHEREUM.product_link
    assert mtbill_row["unrelated_field"] == "preserved"
    assert vault_db.rows[mbasis_spec]["_short_description"] == "Keep this strategy unchanged"

    repeated = fund_metadata_migration_module.create_fund_metadata_migration(MIDAS_MTBILL_ETHEREUM, mtbill_row)
    assert not repeated.changed


def test_midas_fund_metadata_migration_selects_all_mtbill_deployments(monkeypatch: pytest.MonkeyPatch, fund_metadata_migration_module) -> None:
    """Select every reviewed mTBILL deployment by default, but no strategy products."""

    monkeypatch.delenv("NETWORKS", raising=False)
    monkeypatch.delenv("PRODUCTS", raising=False)

    products = list(fund_metadata_migration_module.iter_selected_fund_products())

    assert {product.symbol for product in products} == {"mTBILL"}
    assert {product.chain_id for product in products} == {1, 42161, 8453}
