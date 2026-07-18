"""Regression tests for the scoped Superstate backfill migration."""

import pytest

from eth_defi.tokenised_fund.superstate import backfill
from eth_defi.tokenised_fund.superstate.constants import SUPERSTATE_ETHEREUM_CHAIN_ID, USTB_ETHEREUM_ADDRESS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

OTHER_CHAIN_ID = 8453
OTHER_CHAIN_CURSOR = 12_345_678
EXISTING_ETHEREUM_CURSOR = 19_000_000
METADATA_BLOCK = 25_553_227


@pytest.fixture
def backfill_history_module():
    """Return the Superstate backfill module."""

    return backfill


@pytest.mark.parametrize("ethereum_cursor", [EXISTING_ETHEREUM_CURSOR, None])
def test_ustb_metadata_upsert_preserves_unrelated_discovery_watermarks(backfill_history_module, ethereum_cursor: int | None) -> None:
    """Retain existing Ethereum and unrelated-chain discovery cursor state."""

    watermarks = {OTHER_CHAIN_ID: OTHER_CHAIN_CURSOR}
    if ethereum_cursor is not None:
        watermarks[SUPERSTATE_ETHEREUM_CHAIN_ID] = ethereum_cursor
    other_spec = VaultSpec(OTHER_CHAIN_ID, "0x0000000000000000000000000000000000000001")
    other_row = {"Name": "Unrelated Base vault", "Denomination": "USDC"}
    vault_db = VaultDatabase(rows={other_spec: other_row}, last_scanned_block=watermarks.copy())
    ustb_row = {"Name": "USTB", "Denomination": "USD"}

    backfill_history_module.upsert_ustb_metadata(vault_db, ustb_row, METADATA_BLOCK)

    expected_watermarks = watermarks
    assert vault_db.last_scanned_block == expected_watermarks
    assert vault_db.rows[other_spec] == other_row
    assert vault_db.rows[VaultSpec(SUPERSTATE_ETHEREUM_CHAIN_ID, USTB_ETHEREUM_ADDRESS)] == ustb_row
    assert VaultSpec(SUPERSTATE_ETHEREUM_CHAIN_ID, USTB_ETHEREUM_ADDRESS) in vault_db.leads
