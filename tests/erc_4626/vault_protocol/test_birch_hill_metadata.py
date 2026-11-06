"""Check curator attribution and strategy tags for both Birch Hill deployments."""

import datetime
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.erc_4626.vault_protocol.yearn.vault import BIRCH_HILL_RWA_USDC, YearnV3Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_TAGS = {StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending}


@pytest.mark.parametrize("protocol", ["yearn", "erc-4626"])
def test_birch_hill_flagship_curator(protocol: str) -> None:
    """Prefer the reviewed operator over protocol defaults and stale declarations.

    This export-time override repairs existing rows without refreshing a
    persisted curator field and does not claim same-address deployments elsewhere.

    :param protocol: Fresh or legacy protocol label in an exported row.
    """
    assert identify_curator(8453, "", "Renamed vault", BIRCH_HILL_RWA_USDC.vault_address, protocol, declared_curator_slug="yearn") == "birch-hill"
    assert identify_curator(1, "", "Unbranded vault", BIRCH_HILL_RWA_USDC.vault_address, "erc-4626") is None


def test_birch_hill_strategy_tags() -> None:
    """Resolve both real adapters while keeping unknown deployments unmapped.

    Returned tag sets must be independent of the maintained mapping.
    """
    flagship = YearnV3Vault(None, BIRCH_HILL_RWA_USDC)
    ecosystem = MorphoV2Vault(None, VaultSpec(8453, "0xb038cf3e233b78225f6d350ac8319bec8457db4a"))
    assert flagship.get_strategy_tags() == ecosystem.get_strategy_tags() == EXPECTED_TAGS
    flagship.get_strategy_tags().clear()
    assert flagship.get_strategy_tags() == EXPECTED_TAGS
    assert YearnV3Vault(None, VaultSpec(1, BIRCH_HILL_RWA_USDC.vault_address)).get_strategy_tags() is None
    assert YearnV3Vault(None, VaultSpec(8453, "0x0000000000000000000000000000000000000001")).get_strategy_tags() is None


@pytest.mark.parametrize("features", [{ERC4626Feature.yearn_v3_like}, {ERC4626Feature.yearn_v3_like, ERC4626Feature.yearn_registry_excluded, ERC4626Feature.yearn_compounder_like}])
@pytest.mark.parametrize("protocol", ["Yearn", "ERC-4626"])
def test_birch_hill_tag_migration(features: set[ERC4626Feature], protocol: str) -> None:
    """Persist tags with both protocol labels and preserve all unrelated data.

    Repeating the transformation must stage no additional changes.

    :param features: Fresh or legacy scanner features.
    :param protocol: Fresh or legacy stored protocol label.
    """
    path = Path(__file__).resolve().parents[3] / "migration/scripts/039-birch-hill-strategy-tags.py"
    spec = importlib.util.spec_from_file_location("birch_hill_tag_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    detection = ERC4262VaultDetection(chain=8453, address=BIRCH_HILL_RWA_USDC.vault_address, first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC).replace(tzinfo=None), features=features, updated_at=datetime.datetime(2026, 11, 6, tzinfo=datetime.UTC).replace(tzinfo=None), deposit_count=1, redeem_count=1)
    row = {"Protocol": protocol, "_detection_data": detection, "_strategy_tags": None, "_description": "Keep me"}
    unrelated = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    database = VaultDatabase(rows={BIRCH_HILL_RWA_USDC: row, unrelated: {"keep": 42}})
    context = MagicMock()
    context.metadata.return_value = database
    module.migrate(context)
    assert row["_strategy_tags"] == EXPECTED_TAGS
    assert row["_description"] == "Keep me"
    assert database.rows[unrelated] == {"keep": 42}
    module.migrate(context)
    assert context.report.call_args.kwargs["updated_fields"] == 0
