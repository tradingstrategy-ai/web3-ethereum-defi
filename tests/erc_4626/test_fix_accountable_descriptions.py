"""Test the Accountable description metadata backfill script."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def _load_fix_accountable_descriptions_module():
    """Load the Accountable description backfill script as a Python module.

    :return:
        Loaded script module.
    """
    repo_root = Path(__file__).parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "fix-accountable-descriptions.py"
    spec = importlib.util.spec_from_file_location("fix_accountable_descriptions", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_refresh_accountable_descriptions_updates_only_accountable_rows() -> None:
    """Use strategy metadata for the full and short persisted vault descriptions.

    :return:
        None. Assertions validate the targeted database mutation.
    """
    module = _load_fix_accountable_descriptions_module()
    accountable_spec = VaultSpec(chain_id=143, vault_address="0x23b148d8f389c5821739381f1ff87bb7e1162566")
    other_spec = VaultSpec(chain_id=1, vault_address="0x0000000000000000000000000000000000000001")
    vault_db = VaultDatabase(
        rows={
            accountable_spec: {
                "Protocol": "Accountable",
                "Address": accountable_spec.vault_address,
                "_description": "Old manager profile.",
                "_short_description": "Old manager profile.",
            },
            other_spec: {
                "Protocol": "Morpho",
                "Address": other_spec.vault_address,
                "_description": "Unchanged description.",
                "_short_description": "Unchanged summary.",
            },
        }
    )
    strategy = "This is an auto-looping vault for aHYPER. It uses a lending market."
    updates = module.refresh_accountable_descriptions(
        vault_db,
        {
            accountable_spec.vault_address.upper(): {
                "name": "aHYPER Looping Vault",
                "description": strategy,
                "short_description": "This is an auto-looping vault for aHYPER.",
                "company_name": "Hyperithm",
                "company_url": "https://www.hyperithm.com/",
                "net_apy": None,
                "performance_fee": None,
                "yield_source": None,
                "loan_address": "0xE19b272b2fe4a54103A41F9B1c65dB3D2F6d886D",
            },
        },
    )

    assert len(updates) == 1
    assert updates[0].changed is True
    assert vault_db.rows[accountable_spec]["_description"] == strategy
    assert vault_db.rows[accountable_spec]["_short_description"] == "This is an auto-looping vault for aHYPER."
    assert vault_db.rows[other_spec]["_description"] == "Unchanged description."
    assert vault_db.rows[other_spec]["_short_description"] == "Unchanged summary."


def test_refresh_accountable_descriptions_does_not_partially_update() -> None:
    """Reject incomplete API metadata before mutating persisted rows.

    :return:
        None. Assertions validate atomic in-memory update preparation.
    """
    module = _load_fix_accountable_descriptions_module()
    first_spec = VaultSpec(chain_id=143, vault_address="0x23b148d8f389c5821739381f1ff87bb7e1162566")
    second_spec = VaultSpec(chain_id=143, vault_address="0x3a2c4aaae6776dc1c31316de559598f2f952e2cb")
    vault_db = VaultDatabase(
        rows={
            first_spec: {
                "Protocol": "Accountable",
                "Address": first_spec.vault_address,
                "_description": "First old description.",
                "_short_description": "First old summary.",
            },
            second_spec: {
                "Protocol": "Accountable",
                "Address": second_spec.vault_address,
                "_description": "Second old description.",
                "_short_description": "Second old summary.",
            },
        }
    )

    with pytest.raises(ValueError, match="missing vault"):
        module.refresh_accountable_descriptions(vault_db, {})

    assert vault_db.rows[first_spec]["_description"] == "First old description."
    assert vault_db.rows[second_spec]["_short_description"] == "Second old summary."


def test_refresh_accountable_descriptions_preserves_handwritten_metadata() -> None:
    """Prefer the address-scoped manager strategy over Accountable API metadata.

    :return:
        None. Assertions validate precedence of handwritten vault metadata.
    """
    module = _load_fix_accountable_descriptions_module()
    spec = VaultSpec(chain_id=1, vault_address="0x99351baed3d8ab544ccb08af96a105910fda71e7")
    vault_db = VaultDatabase(
        rows={
            spec: {
                "Protocol": "Accountable",
                "Address": spec.vault_address,
                "_description": "Old description.",
                "_short_description": "Old summary.",
            },
        }
    )

    module.refresh_accountable_descriptions(vault_db, {})

    assert vault_db.rows[spec]["_description"] == "Morini Capital's strategy arbitrages spreads between USD/TRY rates on Turkish crypto exchanges and fiat rails. Its TRY positions are continuously hedged."
    assert vault_db.rows[spec]["_short_description"] == "Delta-neutral USD/TRY foreign-exchange arbitrage strategy."
