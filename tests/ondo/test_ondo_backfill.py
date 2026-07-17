"""Regression tests for the scoped Ondo migration."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.tokenised_fund.ondo.constants import ETHEREUM_CHAIN_ID, ONDO_PRODUCTS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

EXISTING_ETHEREUM_CURSOR = 23_000_000
OTHER_CHAIN_ID = 8453
OTHER_CHAIN_CURSOR = 25_000_000


@pytest.fixture
def backfill_history_module() -> ModuleType:
    """Load the hyphenated Ondo migration as a Python module."""

    script_path = Path(__file__).parents[2] / "scripts" / "ondo" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("ondo_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing_cursor", [None, EXISTING_ETHEREUM_CURSOR])
def test_ondo_metadata_upsert_preserves_discovery_cursor(backfill_history_module: ModuleType, existing_cursor: int | None) -> None:
    """Preserve present and absent cursors while retaining unrelated rows."""

    cursors = {OTHER_CHAIN_ID: OTHER_CHAIN_CURSOR}
    if existing_cursor is not None:
        cursors[ETHEREUM_CHAIN_ID] = existing_cursor
    unrelated_spec = VaultSpec(ETHEREUM_CHAIN_ID, "0x0000000000000000000000000000000000000001")
    unrelated_row = {"Name": "Unrelated Ethereum vault", "Denomination": "USDC"}
    vault_db = VaultDatabase(rows={unrelated_spec: unrelated_row}, last_scanned_block=cursors.copy())
    products = tuple(ONDO_PRODUCTS.values())
    leads = {product.token: backfill_history_module.create_lead(product) for product in products}
    rows = {VaultSpec(product.chain_id, product.token): {"Name": product.symbol, "Denomination": "USD"} for product in products}

    backfill_history_module.upsert_ondo_metadata_preserving_discovery_cursor(vault_db, leads, rows)

    assert vault_db.last_scanned_block == cursors
    assert vault_db.rows[unrelated_spec] == unrelated_row
    assert set(rows).issubset(vault_db.rows)
    assert {VaultSpec(ETHEREUM_CHAIN_ID, address) for address in leads}.issubset(vault_db.leads)


def test_ondo_reader_state_filter_is_chain_aware(backfill_history_module: ModuleType) -> None:
    """Remove reviewed Ethereum states without deleting address twins elsewhere."""

    product = next(iter(ONDO_PRODUCTS.values()))
    selected = VaultSpec(product.chain_id, product.token)
    cross_chain_twin = VaultSpec(8453, product.token)
    unrelated = VaultSpec(product.chain_id, "0x0000000000000000000000000000000000000002")
    states = {selected: {"replace": True}, cross_chain_twin: {"keep_twin": True}, unrelated: {"keep": True}}

    assert backfill_history_module.remove_ondo_reader_states(states) == {cross_chain_twin: {"keep_twin": True}, unrelated: {"keep": True}}
