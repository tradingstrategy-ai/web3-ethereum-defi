"""Check Nest metadata updates preserve unrelated rows and selected chain scope."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def migration_module() -> ModuleType:
    """Load the Nest curator migration from its hyphenated script path.

    :return:
        Imported migration module.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "nest" / "migrate-vaults.py"
    spec = importlib.util.spec_from_file_location("migrate_nest_vaults", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_nest_curator_updates(migration_module: ModuleType) -> None:
    """Apply reviewed roles and tags only to catalogue-matched Nest rows."""
    nopal = VaultSpec(5042, "0xd258029cf5a177e3306e09fbea63424543a505c0")
    falx = VaultSpec(5042, "0x4738386d69cf5a7ac088da2887fc0df02795c5e7")
    apollo = VaultSpec(98866, "0x0000000000000000000000000000000000000001")
    unrelated = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    missing = VaultSpec(1, "0x0000000000000000000000000000000000000003")
    rows = {
        nopal: {"Protocol": "Nest", "_manager_name": None, "_strategy_tags": None},
        falx: {"Protocol": "Nest", "_manager_name": "FalconX", "_strategy_tags": None},
        apollo: {"Protocol": "Nest", "_manager_name": None},
        unrelated: {"Protocol": "Morpho", "_manager_name": "Unchanged"},
        missing: {"Protocol": "Nest", "_manager_name": "Keep me"},
    }
    vault_db = VaultDatabase(rows=rows)
    routes = {
        f"5042:{nopal.vault_address}": {"slug": "nest-opal-vault", "yield_source_partners": ["Superstate", "BlackOpal"]},
        f"5042:{falx.vault_address}": {"slug": "nest-falconx-clo", "yield_source_partners": ["M11 Credit", "FalconX"]},
        f"98866:{apollo.vault_address}": {"slug": "nest-acrdx-vault", "yield_source_partners": ["Apollo", "Centrifuge"]},
    }

    updates = migration_module.plan_existing_metadata_updates(vault_db, routes, {})

    assert {key: updates[nopal][key] for key in ("_manager_name", "_curator_slug", "_strategy_tags")} == {"_manager_name": "BlackOpal", "_curator_slug": "nest-dao", "_strategy_tags": {StrategyTag.rwa, StrategyTag.rwa_credit}}
    assert {key: updates[falx][key] for key in ("_manager_name", "_curator_slug", "_strategy_tags")} == {"_manager_name": "M11 Credit", "_curator_slug": "nest-dao", "_strategy_tags": {StrategyTag.lending}}
    assert updates[apollo]["_manager_name"] == "Apollo" and updates[apollo]["_curator_slug"] == "nest-dao"
    assert unrelated not in updates and missing not in updates
    assert rows[nopal]["_manager_name"] is None
    assert rows[missing]["_manager_name"] == "Keep me"

    arc_updates = migration_module.plan_existing_metadata_updates(vault_db, routes, {}, frozenset({5042}))
    assert nopal in arc_updates and falx in arc_updates
    assert apollo not in arc_updates
