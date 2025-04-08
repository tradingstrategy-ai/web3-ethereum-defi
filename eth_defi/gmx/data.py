"""
GMX Market Data Module

This module provides access to GMX protocol market data.
"""
from typing import Any, Optional

from gmx_python_sdk.scripts.v2.get.get_available_liquidity import GetAvailableLiquidity
from gmx_python_sdk.scripts.v2.get.get_borrow_apr import GetBorrowAPR
from gmx_python_sdk.scripts.v2.get.get_claimable_fees import GetClaimableFees
from gmx_python_sdk.scripts.v2.get.get_contract_balance import GetPoolTVL as ContractTVL
from gmx_python_sdk.scripts.v2.get.get_funding_apr import GetFundingFee
from gmx_python_sdk.scripts.v2.get.get_gm_prices import GMPrices
from gmx_python_sdk.scripts.v2.get.get_markets import Markets
from gmx_python_sdk.scripts.v2.get.get_open_interest import OpenInterest
from gmx_python_sdk.scripts.v2.get.get_oracle_prices import OraclePrices
from gmx_python_sdk.scripts.v2.get.get_pool_tvl import GetPoolTVL
from gmx_python_sdk.scripts.v2.get.get_glv_stats import GlvStats
from gmx_python_sdk.scripts.v2.get.get_open_positions import GetOpenPositions

from eth_defi.gmx.config import GMXConfig


class GMXMarketData:
    """
    Market data provider for GMX protocol.
    """

    def __init__(self, gmx_config: GMXConfig, to_json: bool = False, to_csv: bool = False):
        """
        Initialize market data service.

        Args:
            gmx_config: GMX configuration
            to_json: Whether to save results to JSON files
            to_csv: Whether to save results to CSV files
        """
        self.gmx_config = gmx_config
        self.config = gmx_config.get_read_config()
        # These 2 are needed to support the base class because
        # of whatever reason the devs decided to save the data at package level
        self.to_json = to_json
        self.to_csv = to_csv

    def get_available_markets(self) -> dict[str, Any]:
        """
        Get all available GMX markets with their details.
        """
        return Markets(self.config).get_available_markets()

    def get_available_liquidity(self) -> dict[str, dict[str, float]]:
        """
        Get available liquidity for all markets.
        """
        return GetAvailableLiquidity(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_borrow_apr(self) -> dict[str, dict[str, float]]:
        """
        Get current borrowing rates for all markets.
        """
        return GetBorrowAPR(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_claimable_fees(self) -> dict[str, Any]:
        """
        Get claimable fee information for liquidity providers.
        """
        return GetClaimableFees(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_contract_tvl(self) -> dict[str, Any]:
        """
        Get total value locked (TVL) at contract level.
        """
        return ContractTVL(self.config).get_pool_balances(to_json=self.to_json)

    def get_funding_apr(self) -> dict[str, dict[str, float]]:
        """
        Get current funding rates for all markets.
        """
        return GetFundingFee(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_gm_price(self) -> dict[str, Any]:
        """
        Get current GM (liquidity provider) token prices.
        """
        return GMPrices(self.config).get_price_traders(to_csv=self.to_csv, to_json=self.to_json)

    def get_open_interest(self) -> dict[str, dict[str, float]]:
        """
        Get current open interest for all markets.
        """
        return OpenInterest(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_oracle_prices(self) -> dict[str, Any]:
        """
        Get current oracle prices for all assets.
        """
        return OraclePrices(self.config.chain).get_recent_prices()

    def get_pool_tvl(self) -> dict[str, Any]:
        """
        Get total value locked (TVL) in liquidity pools.
        """
        return GetPoolTVL(self.config).get_pool_balances(to_csv=self.to_csv, to_json=self.to_json)

    def get_glv_stats(self) -> dict[str, Any]:
        """
        Get GLV (GMX Liquidity Vector) token statistics.
        """
        return GlvStats(self.config).get_glv_stats()

    def get_user_positions(self, address: Optional[str] = None) -> dict:
        """
        Get open positions for a user.

        Args:
            address: Wallet address to check positions for (optional)
        """
        if address is None:
            address = self.config.user_wallet_address

        return GetOpenPositions(self.config.chain, address=address).get_data()


if __name__ == "__main__":
    from web3 import Web3
    from dotenv import load_dotenv
    import os

    load_dotenv()
    # rpc_url = os.environ["ARBITRUM"]
    rpc_url = os.environ["AVALANCHE"]
    # Set up web3 connection
    web3 = Web3(Web3.HTTPProvider(rpc_url))

    # Create GMX configuration
    config = GMXConfig(web3, chain="avalanche")

    # Initialize market data module
    market_data = GMXMarketData(config)

    # Access market data
    # markets = market_data.get_available_markets()
    # liquidity = market_data.get_available_liquidity()
    # borrow_apr = market_data.get_borrow_apr()
    # claimable_fees = market_data.get_claimable_fees()
    # contract_tvl = market_data.get_contract_tvl()
    funding_apr = market_data.get_funding_apr()
    # gm_prices = market_data.get_gm_price()
    # open_interest = market_data.get_open_interest()
    # oracle_prices = market_data.get_oracle_prices()
    # pool_tvl = market_data.get_pool_tvl()
    # glv_price = market_data.get_glv_stats()
    print(funding_apr)
