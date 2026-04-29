"""Test vault metadata link refresh helper script."""

import datetime
import importlib.util
from pathlib import Path
from typing import cast

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def _load_update_vault_links_module():
    """Load the helper script as a Python module for unit testing."""
    repo_root = Path(__file__).parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "update-vault-links.py"
    spec = importlib.util.spec_from_file_location("update_vault_links", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _create_detection(chain_id: int, address: str) -> ERC4262VaultDetection:
    """Create a minimal persisted vault detection for link refresh tests."""
    timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=chain_id,
        address=address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features={ERC4626Feature.accountable_like},
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


class FakeVault:
    """Minimal vault class with native link generation."""

    def __init__(self, link: str):
        self.link = link

    def get_link(self) -> str:
        """Return a deterministic native vault link."""
        return self.link


def test_refresh_vault_links_for_protocol_updates_matching_rows():
    """Refresh links for matching protocol rows using the resolved vault class."""
    module = _load_update_vault_links_module()
    spec = VaultSpec(chain_id=143, vault_address="0x58ba69b289de313e66a13b7d1f822fc98b970554")
    other_spec = VaultSpec(chain_id=1, vault_address="0x0000000000000000000000000000000000000001")
    vault_db = VaultDatabase(
        rows={
            spec: {
                "Protocol": "Accountable",
                "Address": spec.vault_address,
                "Link": "https://yield.accountable.capital/vaults",
                "_detection_data": _create_detection(spec.chain_id, spec.vault_address),
            },
            other_spec: {
                "Protocol": "Morpho",
                "Address": other_spec.vault_address,
                "Link": "https://example.com/unchanged",
                "_detection_data": _create_detection(other_spec.chain_id, other_spec.vault_address),
            },
        }
    )

    def vault_factory(_web3, detection, _token_cache):
        return FakeVault(f"https://yield.accountable.capital/vaults/{detection.address}")

    updates = module.refresh_vault_links_for_protocol(
        vault_db=vault_db,
        protocol_id="accountable",
        web3_by_chain={143: object()},
        token_cache=cast(TokenDiskCache, object()),
        vault_factory=vault_factory,
    )

    assert len(updates) == 1
    assert updates[0].vault_class == "FakeVault"
    assert vault_db.rows[spec]["Link"] == "https://yield.accountable.capital/vaults/0x58ba69b289de313e66a13b7d1f822fc98b970554"
    assert vault_db.rows[other_spec]["Link"] == "https://example.com/unchanged"


def test_refresh_vault_links_for_protocol_does_not_partially_update_on_error():
    """Do not mutate any rows if one matching vault cannot be refreshed."""
    module = _load_update_vault_links_module()
    first_spec = VaultSpec(chain_id=143, vault_address="0x58ba69b289de313e66a13b7d1f822fc98b970554")
    second_spec = VaultSpec(chain_id=143, vault_address="0x3a2c4aaae6776dc1c31316de559598f2f952e2cb")
    vault_db = VaultDatabase(
        rows={
            first_spec: {
                "Protocol": "Accountable",
                "Address": first_spec.vault_address,
                "Link": "https://yield.accountable.capital/vaults",
                "_detection_data": _create_detection(first_spec.chain_id, first_spec.vault_address),
            },
            second_spec: {
                "Protocol": "Accountable",
                "Address": second_spec.vault_address,
                "Link": "https://yield.accountable.capital/vaults",
                "_detection_data": _create_detection(second_spec.chain_id, second_spec.vault_address),
            },
        }
    )

    def vault_factory(_web3, detection, _token_cache):
        if detection.address == second_spec.vault_address:
            msg = "Cannot resolve vault"
            raise ValueError(msg)
        return FakeVault(f"https://yield.accountable.capital/vaults/{detection.address}")

    with pytest.raises(ValueError, match="Cannot resolve vault"):
        module.refresh_vault_links_for_protocol(
            vault_db=vault_db,
            protocol_id="accountable",
            web3_by_chain={143: object()},
            token_cache=cast(TokenDiskCache, object()),
            vault_factory=vault_factory,
        )

    assert vault_db.rows[first_spec]["Link"] == "https://yield.accountable.capital/vaults"
    assert vault_db.rows[second_spec]["Link"] == "https://yield.accountable.capital/vaults"
