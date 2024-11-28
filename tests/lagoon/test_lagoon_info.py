"""Lagoon Base mainnet fork based tests.

- View Safe here https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
"""

from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails

from eth_defi.vault.base import TradingUniverse


def test_lagoon_info(lagoon_vault: LagoonVault):
    """Get core info of Lagoon vault"""
    vault = lagoon_vault
    info = vault.fetch_info()
    assert info["address"] == "0x20415f3Ec0FEA974548184bdD6e67575D128953F"
    assert len(info["owners"]) == 2


def test_lagoon_safe(lagoon_vault: LagoonVault):
    """Get Safe instance from safe-eth-py library.

    For modules see

    https://app.safe.global/apps/open?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F&appUrl=https%3A%2F%2Fzodiac.gnosisguild.org%2F
    """
    vault = lagoon_vault
    safe = vault.fetch_safe()
    # No idea what these are, but let's test out
    assert safe.retrieve_owners() == ['0xc690827Ca7AFD92Ccff616F73Ec5AB7c273295f4', '0x8846189A4E46997Dd30Fd9e8bE48C1fA1B846920']
    assert safe.retrieve_modules() == ['0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947', '0x0Cdee1aCD67a424E476AD97bC60aa5F35D2556c9']


def test_lagoon_fetch_portfolio(
    web3: Web3,
    lagoon_vault: LagoonVault,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
):
    """Read vault assets.

    https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
    """
    vault = lagoon_vault

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