"""Tests for maintained vault strategy classifications."""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_VAULT_2_ADDRESS, ATOMA_VAULT_ADDRESS, AtomaVault
from eth_defi.erc_4626.vault_protocol.ethena.vault import EthenaVault
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.erc_4626.vault_protocol.symbiotic.vault import SymbioticVault
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YNRWAX_VAULT_ADDRESS, YieldNestVault
from eth_defi.vault.base import VaultBase
from eth_defi.vault.strategy_tag import StrategyTag


def test_atoma_strategy_tags() -> None:
    """Atoma's reviewed strategies return their maintained address tags."""
    vault = object.__new__(AtomaVault)
    vault.vault_address = ATOMA_VAULT_ADDRESS

    tags = vault.get_strategy_tags()

    assert tags == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    }

    assert tags is not None
    tags.add(StrategyTag.carry_trade)
    assert vault.get_strategy_tags() == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    }

    vault.vault_address = ATOMA_VAULT_2_ADDRESS
    assert vault.get_strategy_tags() == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
        StrategyTag.rwa,
    }


def test_staked_usde_strategy_tags() -> None:
    """Staked USDe returns its automated delta-neutral carry tags."""
    vault = object.__new__(EthenaVault)
    vault.vault_address = HexAddress("0x9d39a5de30e57443bff2a8307a4256c8797a3497")

    assert vault.get_strategy_tags() == {
        StrategyTag.algorithmic_trading,
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    }

    vault.vault_address = HexAddress("0x0000000000000000000000000000000000000000")
    assert vault.get_strategy_tags() is None


def test_yieldnest_strategy_tags() -> None:
    """YieldNest's RWA lending vault returns both relevant strategy tags."""
    vault = object.__new__(YieldNestVault)
    vault.vault_address = YNRWAX_VAULT_ADDRESS

    assert vault.get_strategy_tags() == {StrategyTag.rwa, StrategyTag.rwa_lending}


def test_prime_heloc_loop_strategy_tags() -> None:
    """Prime HELOC Loop returns its maintained looping and RWA tags."""
    vault = object.__new__(IPORVault)
    vault.vault_address = HexAddress("0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874")

    assert vault.get_strategy_tags() == {
        StrategyTag.lending_looping,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    }


def test_tau_base_usdc_lending_optimiser_strategy_tags() -> None:
    """TAU Base USDC LO returns its maintained optimisation tags."""
    vault = object.__new__(IPORVault)
    vault.vault_address = HexAddress("0x7f1f605e755c06d428a80db3d473fc46a14ee2cb")

    assert vault.get_strategy_tags() == {
        StrategyTag.algorithmic_trading,
        StrategyTag.lending_optimisation,
    }


def test_kpk_usdc_liquidlane_strategy_tags() -> None:
    """KPK USDC LiquidLane returns its automated market-making and RWA tags."""
    vault = object.__new__(SymbioticVault)
    vault.vault_address = HexAddress("0x8bcd746976885b5832bad07b4921e3f2dd1d3703")

    assert vault.get_strategy_tags() == {
        StrategyTag.algorithmic_trading,
        StrategyTag.market_making,
        StrategyTag.rwa,
    }


def test_axis_origin_strategy_tags() -> None:
    """Axis Origin returns its documented delta-neutral multi-strategy tags."""
    vault = object.__new__(UpshiftVault)
    vault.vault_address = HexAddress("0xad958c4c0c90bf0216e0f5472f074a9ab30f595f")

    assert vault.get_strategy_tags() == {
        StrategyTag.algorithmic_trading,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.multistrategy,
    }


def test_missing_strategy_tags_return_none() -> None:
    """Unmapped vaults retain the missing-information distinction."""
    vault = object.__new__(AtomaVault)
    vault.vault_address = HexAddress("0x0000000000000000000000000000000000000000")

    assert vault.get_strategy_tags() is None
    assert VaultBase.get_strategy_tags(vault) is None
