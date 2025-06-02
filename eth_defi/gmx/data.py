"""
GMX Market Data Module

This module provides comprehensive access to GMX protocol market data through
a unified, high-level interface. It implements the Data Access Object (DAO)
pattern to abstract the complexity of the underlying GMX Python SDK while
providing consistent, well-structured access to all market information.

The module serves as the primary gateway for retrieving real-time and historical
market data from the GMX protocol, including liquidity metrics, pricing information,
funding rates, open interest, and user position data. It supports both programmatic
data access and optional file export capabilities for data analysis workflows.

**Key Features:**

- **Unified Interface**: Single class providing access to all market data types
- **Read-Only Operations**: Safe data access without transaction capabilities
- **Export Support**: Optional JSON and CSV export for analysis workflows
- **Multi-Network**: Automatic network detection and appropriate data sourcing
- **Real-Time Data**: Direct integration with GMX protocol APIs and contracts

**Data Categories:**

- **Market Information**: Available markets, liquidity, and trading pairs
- **Pricing Data**: Oracle prices, GM token prices, and funding rates
- **Liquidity Metrics**: TVL, available liquidity, and pool balances
- **Trading Metrics**: Open interest, borrowing rates, and funding APR
- **User Data**: Individual position information and claimable fees

Example:

.. code-block:: python

    # Basic market data access
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3, chain="arbitrum")
    market_data = GMXMarketData(config)

    # Get current market overview
    markets = market_data.get_available_markets()
    liquidity = market_data.get_available_liquidity()
    open_interest = market_data.get_open_interest()

    # Access pricing information
    oracle_prices = market_data.get_oracle_prices()
    gm_prices = market_data.get_gm_price()

    # Monitor funding and borrowing costs
    funding_rates = market_data.get_funding_apr()
    borrow_rates = market_data.get_borrow_apr()

    # Export data for analysis
    analytics_data = GMXMarketData(config, to_json=True, to_csv=True)
    detailed_metrics = analytics_data.get_pool_tvl()  # Saves to files

    # Check user positions (requires wallet address)
    user_positions = market_data.get_user_positions("0x742d...")

Note:
    This module uses read-only configuration to ensure safe data access without
    requiring wallet credentials or transaction signing capabilities.

TODO:
    Handle the returned data better. Maybe with a class and add proper exceptions
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
    Comprehensive market data provider for the GMX protocol ecosystem.

    This class implements a facade pattern that provides unified access to all
    GMX protocol market data through a single, consistent interface. It abstracts
    the complexity of the underlying GMX Python SDK while maintaining full access
    to all data sources and metrics available through the protocol.

    The class is designed around the principle of read-only access, using only
    the read configuration from GMXConfig to ensure safe data retrieval without
    requiring wallet credentials or transaction signing capabilities. This makes
    it ideal for market analysis, monitoring systems, and data export workflows.

    **Architecture Pattern**: The class follows the Data Access Object (DAO)
    pattern, where each method corresponds to a specific type of market data.
    This creates a clean separation between data access logic and business logic,
    making the code easier to test, maintain, and extend.

    **Export Capabilities**: Optional file export functionality allows seamless
    integration with data analysis workflows. When enabled, data is automatically
    saved to JSON and/or CSV formats alongside being returned programmatically.

    :ivar gmx_config: Complete GMX configuration object for network and wallet settings
    :vartype gmx_config: GMXConfig
    :ivar config: Read-only configuration manager for safe data access operations
    :vartype config: ConfigManager
    :ivar to_json: Flag indicating whether to export data to JSON files
    :vartype to_json: bool
    :ivar to_csv: Flag indicating whether to export data to CSV files
    :vartype to_csv: bool
    """

    def __init__(self, gmx_config: GMXConfig, to_json: bool = False, to_csv: bool = False):
        """
        Initialize the market data service with configuration and export options.

        This constructor sets up the market data provider by extracting a read-only
        configuration from the provided GMXConfig and configuring optional file
        export capabilities. The read-only configuration ensures that all data
        access operations are safe and cannot accidentally trigger transactions.

        :param gmx_config:
            Complete GMX configuration object containing network settings and
            optional wallet information. Only the read-only configuration
            component will be used for data access operations
        :type gmx_config: GMXConfig
        :param to_json:
            Whether to automatically save retrieved data to JSON files.
            When enabled, each data retrieval method will create corresponding
            JSON files in the current working directory
        :type to_json: bool
        :param to_csv:
            Whether to automatically save retrieved data to CSV files.
            When enabled, each data retrieval method will create corresponding
            CSV files in the current working directory for tabular data
        :type to_csv: bool
        """
        self.gmx_config = gmx_config
        self.config = gmx_config.get_read_config()
        # These 2 are needed to support the base class because
        # of whatever reason the devs decided to save the data at package level
        self.to_json = to_json
        self.to_csv = to_csv

    def get_available_markets(self) -> dict[str, Any]:
        """
        Retrieve comprehensive information about all trading markets available on GMX.

        This method returns detailed metadata about every trading pair and market
        supported by the GMX protocol on the configured network. The information
        includes market identifiers, supported assets, trading parameters, and
        current market status, providing everything needed to understand the
        available trading opportunities.

        :return:
            Dictionary containing complete market information including market
            names, supported tokens, trading parameters, fees, and current
            status for all available GMX trading markets
        :rtype: dict[str, Any]
        """
        return Markets(self.config).get_available_markets()

    def get_available_liquidity(self) -> dict[str, dict[str, float]]:
        """
        Get current available liquidity across all GMX markets and trading pairs.

        This method retrieves real-time liquidity information showing how much
        capital is available for trading in each market. Available liquidity
        directly impacts the maximum position sizes that can be opened and the
        potential slippage for large trades, making this crucial information
        for trading strategy and risk management.

        :return:
            Nested dictionary structure where outer keys are market identifiers
            and inner dictionaries contain liquidity amounts for different
            assets and position types (long/short) within each market
        :rtype: dict[str, dict[str, float]]
        """
        return GetAvailableLiquidity(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_borrow_apr(self) -> dict[str, dict[str, float]]:
        """
        Retrieve current annual percentage rates (APR) for borrowing across all markets.

        Borrowing rates represent the cost of leverage in perpetual futures trading.
        When traders open leveraged positions, they effectively borrow capital
        from liquidity providers and pay these rates as borrowing fees. The rates
        are dynamic and adjust based on utilization, market conditions, and
        available liquidity in each market.

        :return:
            Nested dictionary where outer keys are market identifiers and
            inner dictionaries contain APR values for different assets and
            position types, expressed as decimal percentages
        :rtype: dict[str, dict[str, float]]
        """
        return GetBorrowAPR(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_claimable_fees(self) -> dict[str, Any]:
        """
        Get information about fees that can be claimed by liquidity providers.

        Liquidity providers on GMX earn fees from trading activity and can
        periodically claim these accumulated rewards. This method returns
        information about unclaimed fees available to the configured wallet
        address, including the amounts and types of rewards that have been
        earned but not yet withdrawn.

        :return:
            Dictionary containing claimable fee information including amounts,
            asset types, and claiming mechanisms available to liquidity
            providers based on their LP token holdings
        :rtype: dict[str, Any]
        """
        return GetClaimableFees(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_contract_tvl(self) -> dict[str, Any]:
        """
        Get Total Value Locked (TVL) at the individual contract level.

        This method provides granular TVL information showing the value of
        assets locked in specific GMX protocol contracts. Contract-level TVL
        gives insight into how capital is distributed across different protocol
        components and can be useful for understanding protocol health and
        capital efficiency.

        :return:
            Dictionary containing TVL information organized by contract
            addresses, showing the value and composition of assets locked
            in each component of the GMX protocol
        :rtype: dict[str, Any]
        """
        return ContractTVL(self.config).get_pool_balances(to_json=self.to_json)

    def get_funding_apr(self) -> dict[str, dict[str, float]]:
        """
        Retrieve current funding rates (APR) for perpetual futures positions.

        Funding rates are periodic payments between long and short position
        holders that help keep perpetual futures prices aligned with spot
        prices. When funding rates are positive, long position holders pay
        short position holders, and vice versa. These rates adjust dynamically
        based on the imbalance between long and short positions.

        :return:
            Nested dictionary where outer keys are market identifiers and
            inner dictionaries contain funding APR values for different
            position types, with positive values indicating longs pay shorts
        :rtype: dict[str, dict[str, float]]
        """
        return GetFundingFee(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_gm_price(self) -> dict[str, Any]:
        """
        Get current prices and valuation data for GM (liquidity provider) tokens.

        GM tokens represent shares in GMX liquidity pools and their prices
        reflect the underlying value of the pooled assets plus accumulated
        fees. This pricing information is essential for liquidity providers
        to understand the value of their holdings and calculate returns on
        their liquidity provision activities.

        :return:
            Dictionary containing GM token prices, underlying asset values,
            and other valuation metrics that determine the worth of liquidity
            provider positions in various GMX pools
        :rtype: dict[str, Any]
        """
        return GMPrices(self.config).get_price_traders(to_csv=self.to_csv, to_json=self.to_json)

    def get_open_interest(self) -> dict[str, dict[str, float]]:
        """
        Retrieve current open interest statistics across all trading markets.

        Open interest represents the total value of all outstanding positions
        in each market, providing insight into market activity levels and
        potential liquidity demands. High open interest indicates active
        trading and significant capital deployment, while changes in open
        interest can signal shifting market sentiment and trader positioning.

        :return:
            Nested dictionary where outer keys are market identifiers and
            inner dictionaries contain open interest values for different
            position types (long/short) and underlying assets
        :rtype: dict[str, dict[str, float]]
        """
        return OpenInterest(self.config).get_data(to_csv=self.to_csv, to_json=self.to_json)

    def get_oracle_prices(self) -> dict[str, Any]:
        """
        Get current oracle price data for all assets supported by GMX protocol.

        Oracle prices are authoritative price feeds used by the GMX protocol
        for position valuation, liquidation calculations, and trade execution.
        These prices come from external price oracles and represent the
        protocol's view of current market values for all supported assets,
        ensuring accurate and manipulation-resistant pricing.

        :return:
            Dictionary containing current oracle prices and metadata for all
            supported assets, including timestamps, confidence intervals,
            and other price feed quality indicators
        :rtype: dict[str, Any]
        """
        return OraclePrices(self.config.chain).get_recent_prices()

    def get_pool_tvl(self) -> dict[str, Any]:
        """
        Get Total Value Locked (TVL) in all GMX liquidity pools.

        Pool TVL shows the total value of assets deposited by liquidity
        providers across all GMX trading pools. This metric is crucial for
        understanding protocol scale, liquidity depth, and the overall
        health of the GMX ecosystem. Higher TVL generally indicates greater
        trading capacity and reduced slippage for large trades.

        :return:
            Dictionary containing detailed TVL information for each liquidity
            pool, including asset breakdown, pool composition, and total
            values across different asset types and markets
        :rtype: dict[str, Any]
        """
        return GetPoolTVL(self.config).get_pool_balances(to_csv=self.to_csv, to_json=self.to_json)

    def get_glv_stats(self) -> dict[str, Any]:
        """
        Get comprehensive statistics for GLV (GMX Liquidity Vector) tokens.

        GLV tokens represent a more sophisticated liquidity provision mechanism
        that may include auto-compounding features, dynamic rebalancing, or
        other advanced strategies. These statistics provide insight into the
        performance and composition of GLV token holdings, helping liquidity
        providers understand the efficiency of these enhanced LP products.

        :return:
            Dictionary containing GLV token statistics including performance
            metrics, composition data, yield information, and other relevant
            analytics for advanced liquidity provision strategies
        :rtype: dict[str, Any]
        """
        return GlvStats(self.config).get_glv_stats()

    def get_user_positions(self, address: Optional[str] = None) -> dict:
        """
        Retrieve all open trading positions for a specific user address.

        This method provides detailed information about a user's current
        trading positions, including position sizes, entry prices, current
        profit/loss, margin requirements, and liquidation thresholds. This
        information is essential for position management, risk assessment,
        and portfolio analysis.

        :param address:
            Ethereum wallet address to query positions for. If not provided,
            uses the wallet address from the GMX configuration. Must be a
            valid Ethereum address format (0x...)
        :type address: Optional[str]
        :return:
            Dictionary containing detailed information about all open positions
            for the specified address, including position metrics, PnL data,
            margin information, and risk parameters
        :rtype: dict
        """
        if address is None:
            address = self.config.user_wallet_address

        return GetOpenPositions(self.config, address=address).get_data()


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
    borrow_apr = market_data.get_borrow_apr()
    # claimable_fees = market_data.get_claimable_fees()
    # contract_tvl = market_data.get_contract_tvl()
    # funding_apr = market_data.get_funding_apr()
    # gm_prices = market_data.get_gm_price()
    # open_interest = market_data.get_open_interest()
    # oracle_prices = market_data.get_oracle_prices()
    # pool_tvl = market_data.get_pool_tvl()
    # glv_price = market_data.get_glv_stats()
    print(borrow_apr)
