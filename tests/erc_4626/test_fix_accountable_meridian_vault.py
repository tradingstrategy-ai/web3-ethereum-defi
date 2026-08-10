"""Test the targeted Accountable Meridian vault repair script."""

import importlib.util
from pathlib import Path

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

PREVIOUS_ROBINHOOD_WATERMARK = 21_000_000


def _load_fix_accountable_meridian_module():
    """Load the hyphenated Meridian repair script as a Python module.

    :return: Imported script module.
    """
    repo_root = Path(__file__).parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "fix-accountable-meridian-vault.py"
    spec = importlib.util.spec_from_file_location("fix_accountable_meridian_vault", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_meridian_detection_uses_accountable_features() -> None:
    """Generate a hardcoded classification record for the Accountable adapter."""
    module = _load_fix_accountable_meridian_module()
    detection = module.create_meridian_detection()

    assert detection.chain == module.ROBINHOOD_CHAIN_ID
    assert detection.address == module.MERIDIAN_VAULT_ADDRESS
    assert detection.first_seen_at_block == module.MERIDIAN_FIRST_SEEN_AT_BLOCK
    assert detection.features == {
        ERC4626Feature.erc_7540_like,
        ERC4626Feature.erc_7575_like,
        ERC4626Feature.accountable_like,
    }


def test_meridian_reader_state_removal_preserves_unrelated_entries() -> None:
    """Remove only the Meridian reader state before an optional rescan."""
    module = _load_fix_accountable_meridian_module()
    selected = module.selected_vault_spec()
    unrelated = VaultSpec(4663, "0x0000000000000000000000000000000000000001")

    states = module.remove_selected_reader_states(
        {
            selected: {"last_block": 123},
            unrelated: {"last_block": 456},
        }
    )

    assert states == {unrelated: {"last_block": 456}}


def test_meridian_metadata_upsert_preserves_robinhood_watermark() -> None:
    """A one-vault repair cannot claim the full Robinhood chain has been scanned."""
    module = _load_fix_accountable_meridian_module()
    database = VaultDatabase()
    database.last_scanned_block[module.ROBINHOOD_CHAIN_ID] = PREVIOUS_ROBINHOOD_WATERMARK
    row = {
        "Name": "Meridian Liquidity Provider",
        "Protocol": "Accountable",
        "features": {ERC4626Feature.accountable_like},
    }

    module.upsert_selected_metadata(database, end_block=28_000_000, row=row)

    assert database.last_scanned_block[module.ROBINHOOD_CHAIN_ID] == PREVIOUS_ROBINHOOD_WATERMARK
    assert database.rows[module.selected_vault_spec()] == row
    assert database.leads[module.selected_vault_spec()].address == module.MERIDIAN_VAULT_ADDRESS


def test_meridian_metadata_upsert_does_not_create_new_watermark() -> None:
    """Keep a metadata-only repair from advancing an absent chain cursor."""
    module = _load_fix_accountable_meridian_module()
    database = VaultDatabase()

    module.upsert_selected_metadata(database, end_block=28_000_000, row={"Protocol": "Accountable"})

    assert module.ROBINHOOD_CHAIN_ID not in database.last_scanned_block


def test_meridian_selected_history_scope_is_single_vault() -> None:
    """Constrain raw and cleaned history replacement to Meridian only."""
    module = _load_fix_accountable_meridian_module()

    assert module.selected_vault_addresses() == {"0x24b84023c8e4da635be228c380c09bfe5271bf9d"}
    assert module.selected_vault_spec_ids() == {module.selected_vault_spec().as_string_id()}
