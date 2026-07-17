"""Regression tests for the scoped Centrifuge JTRSY migration."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.tokenised_fund.centrifuge.constants import ETHEREUM_CHAIN_ID, JTRSY_ETHEREUM
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

EXISTING_ETHEREUM_CURSOR = 21_000_000
OTHER_CHAIN_ID = 8453
OTHER_CHAIN_CURSOR = 25_000_000


@pytest.fixture
def backfill_jtrsy_module() -> ModuleType:
    """Load the hyphenated JTRSY migration as a Python module."""

    script_path = Path(__file__).parents[2] / "scripts" / "centrifuge" / "backfill-jtrsy.py"
    spec = importlib.util.spec_from_file_location("centrifuge_backfill_jtrsy", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing_cursor", [None, EXISTING_ETHEREUM_CURSOR])
def test_jtrsy_metadata_upsert_preserves_discovery_cursor(backfill_jtrsy_module: ModuleType, existing_cursor: int | None) -> None:
    """Preserve present and absent cursors while retaining unrelated rows."""

    cursors = {OTHER_CHAIN_ID: OTHER_CHAIN_CURSOR}
    if existing_cursor is not None:
        cursors[ETHEREUM_CHAIN_ID] = existing_cursor
    unrelated_spec = VaultSpec(ETHEREUM_CHAIN_ID, "0x0000000000000000000000000000000000000001")
    unrelated_row = {"Name": "Unrelated Ethereum vault", "Denomination": "USDC"}
    vault_db = VaultDatabase(rows={unrelated_spec: unrelated_row}, last_scanned_block=cursors.copy())
    lead = PotentialVaultMatch(
        chain=JTRSY_ETHEREUM.chain_id,
        address=JTRSY_ETHEREUM.token,
        first_seen_at_block=JTRSY_ETHEREUM.first_seen_at_block,
        first_seen_at=JTRSY_ETHEREUM.first_seen_at,
    )
    row = {"Name": "JTRSY", "Denomination": "USD"}

    backfill_jtrsy_module.upsert_jtrsy_metadata_preserving_discovery_cursor(vault_db, lead, row)

    assert vault_db.last_scanned_block == cursors
    assert vault_db.rows[unrelated_spec] == unrelated_row
    target_spec = VaultSpec(JTRSY_ETHEREUM.chain_id, JTRSY_ETHEREUM.token)
    assert vault_db.leads[target_spec] == lead
    assert vault_db.rows[target_spec] == row
