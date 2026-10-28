"""Evidence-based, deployment-scoped Gains-family strategy classifications."""

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.metadata import REVIEWED_GAINS_VAULTS
from eth_defi.erc_4626.vault_protocol.gains.tags import STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag

EXPECTED_REVIEWED_ROWS = 24
EXPECTED_VERIFIED_ROWS = 10
EXPECTED_UNCONFIRMED_ROWS = 14

EXPECTED_TAGS = {StrategyTag.amm, StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.perpetual_futures}


@pytest.mark.parametrize(("chain_id", "address"), REVIEWED_GAINS_VAULTS)
def test_reviewed_deployment_strategy(chain_id: int, address: str) -> None:
    """Verified liquidity pools are classified; information gaps stay explicit."""
    metadata = REVIEWED_GAINS_VAULTS[chain_id, address]
    vault = GainsVault(web3=None, spec=VaultSpec(chain_id, address), features={ERC4626Feature.gains_like})
    if metadata.curator:
        assert vault.get_strategy_tags() == EXPECTED_TAGS
    else:
        assert address not in STRATEGY_TAGS
        assert vault.get_strategy_tags() is None


def test_reviewed_strategy_coverage() -> None:
    """The audit covers ten official gTrade pools and fourteen gaps."""
    assert len(REVIEWED_GAINS_VAULTS) == EXPECTED_REVIEWED_ROWS
    assert len(STRATEGY_TAGS) == EXPECTED_VERIFIED_ROWS
    assert sum(row.curator == "gains-network" for row in REVIEWED_GAINS_VAULTS.values()) == EXPECTED_VERIFIED_ROWS
    assert sum(row.curator is None for row in REVIEWED_GAINS_VAULTS.values()) == EXPECTED_UNCONFIRMED_ROWS


def test_tags_are_copied_and_chain_scoped() -> None:
    """A caller cannot mutate the maintained tags or reuse them on another chain."""
    address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    vault = GainsVault(web3=None, spec=VaultSpec(42161, address), features={ERC4626Feature.gains_like})
    tags = vault.get_strategy_tags()
    tags.clear()
    assert vault.get_strategy_tags() == EXPECTED_TAGS
    other_chain = GainsVault(web3=None, spec=VaultSpec(1, address), features={ERC4626Feature.gains_like})
    assert other_chain.get_strategy_tags() is None


def test_unmapped_gains_like_vault_returns_none() -> None:
    """An unclassified compatible vault preserves the missing distinction."""
    vault = GainsVault(web3=None, spec=VaultSpec(42161, "0x000000000000000000000000000000000000f00d"), features={ERC4626Feature.gains_like})
    assert vault.get_strategy_tags() is None
