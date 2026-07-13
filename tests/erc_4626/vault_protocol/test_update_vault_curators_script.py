"""Test vault curator metadata refresh script helpers."""

import datetime
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "erc-4626" / "update-vault-curators.py"


@dataclass(slots=True)
class _VaultStub:
    """Minimal vault object exposing a manager name."""

    manager_name: str | None


def _load_update_vault_curators_module():
    """Load the vault curator update script as a module."""
    spec = importlib.util.spec_from_file_location("update_vault_curators", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_detection(spec: VaultSpec) -> ERC4262VaultDetection:
    """Create a minimal persisted T3tris detection row."""
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=HexAddress(spec.vault_address),
        first_seen_at_block=1,
        first_seen_at=datetime.datetime(2026, 6, 14, 20, 21, 20, tzinfo=datetime.UTC).replace(tzinfo=None),
        features={ERC4626Feature.t3tris_like},
        updated_at=datetime.datetime(2026, 7, 13, tzinfo=datetime.UTC).replace(tzinfo=None),
        deposit_count=0,
        redeem_count=0,
    )


def _make_row(spec: VaultSpec, manager_name: str | None) -> dict:
    """Create a minimal vault metadata row for script tests."""
    return {
        "Name": "First - USDC",
        "Protocol": "T3tris",
        "protocol_slug": "t3tris",
        "_detection_data": _make_detection(spec),
        "_manager_name": manager_name,
    }


def test_update_vault_curators_refreshes_manager_name() -> None:
    """Existing metadata rows are updated from the vault manager property."""
    module = _load_update_vault_curators_module()
    spec = VaultSpec(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f")
    vault_db = VaultDatabase(rows={spec: _make_row(spec, None)})

    updates = module.refresh_vault_curators_for_protocol(
        vault_db=vault_db,
        protocol_id="t3tris",
        web3_by_chain={42161: object()},
        token_cache=object(),
        vault_factory=lambda _web3, _detection, _token_cache: _VaultStub("First Capital"),
    )

    assert len(updates) == 1
    assert updates[0].changed
    assert vault_db.rows[spec]["_manager_name"] == "First Capital"


def test_update_vault_curators_respects_vault_id_allowlist() -> None:
    """Only selected vault ids are refreshed when an allowlist is supplied."""
    module = _load_update_vault_curators_module()
    first_spec = VaultSpec(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f")
    gami_spec = VaultSpec(42161, "0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f")
    vault_db = VaultDatabase(
        rows={
            first_spec: _make_row(first_spec, None),
            gami_spec: _make_row(gami_spec, None),
        }
    )

    updates = module.refresh_vault_curators_for_protocol(
        vault_db=vault_db,
        protocol_id="t3tris",
        web3_by_chain={42161: object()},
        token_cache=object(),
        vault_ids={first_spec},
        vault_factory=lambda _web3, _detection, _token_cache: _VaultStub("First Capital"),
    )

    assert [update.spec for update in updates] == [first_spec]
    assert vault_db.rows[first_spec]["_manager_name"] == "First Capital"
    assert vault_db.rows[gami_spec]["_manager_name"] is None


def test_update_vault_curators_rejects_missing_vault_id() -> None:
    """Requested vault ids must exist for the selected protocol."""
    module = _load_update_vault_curators_module()
    first_spec = VaultSpec(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f")
    missing_spec = VaultSpec(42161, "0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f")
    vault_db = VaultDatabase(rows={first_spec: _make_row(first_spec, None)})

    with pytest.raises(ValueError, match="Requested VAULT_ID entries were not found"):
        module.refresh_vault_curators_for_protocol(
            vault_db=vault_db,
            protocol_id="t3tris",
            web3_by_chain={42161: object()},
            token_cache=object(),
            vault_ids={missing_spec},
            vault_factory=lambda _web3, _detection, _token_cache: _VaultStub("First Capital"),
        )
