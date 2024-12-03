"""Lagoon Base mainnet fork based tests.

- Read various information out of the vault
"""

from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails

from eth_defi.vault.base import TradingUniverse


@pytest.fixture()
def read_only_vault(lagoon_vault) -> LagoonVault:
    """TODO: Optimise test speed - fetch vault data only once per this module"""
    return lagoon_vault


def test_lagoon_core_info(read_only_vault: LagoonVault):
    """Get core info of Lagoon vault"""
    vault = read_only_vault
    info = vault.fetch_info()
    assert info["address"].lower() == "0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c"
    assert info["safe"] == "0x20415f3Ec0FEA974548184bdD6e67575D128953F"
    assert info["valuationManager"] == "0x8358bBFb4Afc9B1eBe4e8C93Db8bF0586BD8331a"  # Hotkey, unlocked for tests
    assert len(info["owners"]) == 2


def test_lagoon_safe(read_only_vault: LagoonVault):
    """Get Safe instance from safe-eth-py library.

    For modules see

    https://app.safe.global/apps/open?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F&appUrl=https%3A%2F%2Fzodiac.gnosisguild.org%2F
    """
    vault = read_only_vault
    safe = vault.safe
    # No idea what these are, but let's test out
    assert safe.retrieve_owners() == ['0xc690827Ca7AFD92Ccff616F73Ec5AB7c273295f4', '0x8846189A4E46997Dd30Fd9e8bE48C1fA1B846920']
    assert safe.retrieve_modules() == ['0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947', '0x0Cdee1aCD67a424E476AD97bC60aa5F35D2556c9']


def test_lagoon_tokens(read_only_vault: LagoonVault):
    """We are denominated in the USDC"""
    vault = read_only_vault
    assert vault.denomination_token.symbol == "USDC"
    assert vault.share_token.symbol == "XMPL"
    assert vault.name == "Example"
    assert vault.symbol == "XMPL"


def test_lagoon_fetch_portfolio(
    web3: Web3,
    read_only_vault: LagoonVault,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
):
    """Read vault assets.

    https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
    """
    vault = read_only_vault

    universe = TradingUniverse(
        spot_token_addresses={
            base_weth.address,
            base_usdc.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)

    assert portfolio.spot_erc20 == {
        base_usdc.address: pytest.approx(Decimal(0.347953)),
        base_weth.address: pytest.approx(Decimal(1*10**-16)),
    }