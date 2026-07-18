"""Regression tests for the scoped Libeara CMTAT backfill helper."""

import pytest

from eth_defi.tokenised_fund.libeara import backfill
from eth_defi.tokenised_fund.libeara.constants import BELIF_ETHEREUM, CUMIU_ETHEREUM, ETHEREUM_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def backfill_history_module():
    """Return the Libeara backfill module.

    :return: Loaded script module.
    """
    return backfill


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
