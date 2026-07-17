"""Regression tests for the scoped Libeara CMTAT backfill helper."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.tokenised_fund.libeara.constants import BELIF_ETHEREUM, CUMIU_ETHEREUM, ETHEREUM_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def backfill_history_module():
    """Load the hyphenated Libeara backfill script as a module.

    :return: Loaded script module.
    """
    script_path = Path(__file__).parents[2] / "scripts" / "libeara" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("libeara_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing_cursor", [None, 25_000_000])
def test_scoped_metadata_upsert_preserves_ethereum_discovery_cursor(backfill_history_module, existing_cursor: int | None) -> None:
    """Retain both present and absent cursors while adding only reviewed records.

    :param backfill_history_module: Loaded backfill helper module.
    :param existing_cursor: Existing Ethereum cursor, if the database has one.
    """
    vault_db = VaultDatabase()
    if existing_cursor is not None:
        vault_db.last_scanned_block[ETHEREUM_CHAIN_ID] = existing_cursor
    unrelated = VaultSpec(ETHEREUM_CHAIN_ID, "0x0000000000000000000000000000000000000001")
    vault_db.rows[unrelated] = {"Name": "Unrelated vault"}
    leads = {
        CUMIU_ETHEREUM.token: backfill_history_module._lead(CUMIU_ETHEREUM),
        BELIF_ETHEREUM.token: backfill_history_module._lead(BELIF_ETHEREUM),
    }
    rows = {
        VaultSpec(ETHEREUM_CHAIN_ID, CUMIU_ETHEREUM.token): {"Name": "CUMIU"},
        VaultSpec(ETHEREUM_CHAIN_ID, BELIF_ETHEREUM.token): {"Name": "BELIF"},
    }

    backfill_history_module.upsert_libeara_metadata_preserving_discovery_cursor(vault_db, leads, rows)

    if existing_cursor is None:
        assert ETHEREUM_CHAIN_ID not in vault_db.last_scanned_block
    else:
        assert vault_db.last_scanned_block[ETHEREUM_CHAIN_ID] == existing_cursor
    assert vault_db.rows[unrelated]["Name"] == "Unrelated vault"
    assert set(leads).issubset({spec.vault_address for spec in vault_db.leads})
