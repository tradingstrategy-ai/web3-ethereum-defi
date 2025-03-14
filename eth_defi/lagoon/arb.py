"""Lagoon arb bot.

- Create Uniswap v3 LP position that reflects
- Move LP range around depending on the fund share price
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from web3 import Web3

from eth_defi.basewallet import BaseWallet
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.uniswap_v3.pool import PoolDetails
from strategies.test_only.frozen_asset import logging

logger = logging.getLogger(__name__)


class SharePriceCalculator(Protocol):
    """Callback for calculating share price for a vault."""

    def __call__(self, vault: LagoonVault) -> Decimal:
        """
        :return:
            Share token price in the vault denomination token.
        """


@dataclass
class LPRange:
    """Represent uniswap ranged position."""
    poo: PoolDetails
    upper_tick: int
    lower_tick: int

    def get_human_description(self) -> str:
        """Describe this position."""


@dataclass
class Inventory:
    """How much base/quote we have for market making.
    """
    free_base: Decimal
    free_quote: Decimal
    position_base: Decimal
    position_quote: Decimal



class ETFARBStrategy:
    """Arbitrage strategy to keep Uniswap v3 LP pool price in sync with Lagoon vault share price.

    - Open an LP position
    - Move LP position range based on the Lagoon vault share price
    """

    def __init__(
        self,
        id: str,
        web3: Web3,
        vault: LagoonVault,
        pool: PoolDetails,
        wallet: BaseWallet,
        share_price_calculator: SharePriceCalculator,
    ):
        """Create ETF position manager.

        :param strategy_name:
            Human readable id for this strategy, used in logging.

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
        self.id = id
        self.web3 = web3
        self.vault = vault
        self.pool=  pool
        self.wallet = wallet
        self.share_price_calculator = share_price_calculator
        self.cycle = 0

    def update(self):
        """Run one cycle of LP position updates to reflect the Lagoon vault internal share price."""

        self.cycle += 1

        logger.info(
            "%s: strategy %s, preparing update cycle %d",
            self.__class__.__name__,
            self.id,
            self.cycle,
        )

        inventory = self.fetch_inventory()

        share_price = self.share_price_calculator(self.vault)
        logger.info(
            "Received share price: %s %s / share",
            share_price,
            self.vault.denomination_token.symbol,
        )
        range = self.calculate_lp_position_range(share_price, inventory)
        logger.info(
            "Calculated new range to be: %s",
            range.get_human_description(),
        )

    def calculate_lp_position_range(self, share_price: Decimal, inventory: Inventory) -> LPRange:
        """Calculates a range for the """

    def fetch_inventory(self) -> Inventory:
        """Get our token inventory from onchain"""



