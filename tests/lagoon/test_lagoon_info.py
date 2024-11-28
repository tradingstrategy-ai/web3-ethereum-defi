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
    vault = lagoon_vault
    info = vault.fetch_info()
    assert info["safe_address"] == "0x20415f3Ec0FEA974548184bdD6e67575D128953F"


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