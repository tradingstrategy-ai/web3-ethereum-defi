"""Lagoon arb bot.

- Create Uniswap v3 LP position that reflects
- Move LP range around depending on the fund share price
"""
from typing import Protocol

from eth_defi.basewallet import BaseWallet
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.uniswap_v3.pool import PoolDetails


class SharePriceCalculator(Protocol):

    def __call__(self, vault: LagoonVault) -> float:
        pass


class ETFManager:

    def __init__(
        self,
        web3: Web3,
        vault: LagoonVault,
        pool: PoolDetails,
        wallet: BaseWallet,
        share_price_calculator: SharePriceCalculator,
    ):
        """Create ETF position manager.

        :param web3:
            Web3 connection

        :param vault:
            Lagoon vault of which NAV / share price we track.

        :param pool:
            Uniswap v3 LP pool

        :param wallet:
            Hot wallet that owns tokens and the position.

        :param share_price_calculator:
            Takes vault and outputs the estimated share price.
        """
        self.web3 = web3
        self.vault = vault
        self.pool=  pool
        self.wallet = wallet
        self.share_price_calculator = share_price_calculator

    def update(self):
        """"""



