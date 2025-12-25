"""
GMX Market Data Module

This module provides comprehensive access to GMX protocol market data through
a unified, high-level interface. It implements the Data Access Object (DAO)
pattern to abstract the complexity of the underlying GMX Python SDK while
providing consistent, well-structured access to all market information.

The module serves as the primary gateway for retrieving real-time and historical
market data from the GMX protocol, including liquidity metrics, pricing information,
funding rates, open interest, and user position data.

**Key Features:**

- **Unified Interface**: Single class providing access to all market data types
- **Read-Only Operations**: Safe data access without transaction capabilities
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
    config = GMXConfig(web3)
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

    # Check user positions (requires wallet address)
    user_positions = market_data.get_user_positions("0x742d...")

Note:
    This module uses read-only configuration to ensure safe data access without
    requiring wallet credentials or transaction signing capabilities.
"""

from typing import Optional

from eth_defi.gmx.types import MarketData, PositionSideData, PriceData, TVLData

from eth_defi.gmx.core.available_liquidity import GetAvailableLiquidity
from eth_defi.gmx.core.borrow_apr import GetBorrowAPR
from eth_defi.gmx.core.claimable_fees import GetClaimableFees
from eth_defi.gmx.core.pool_tvl import GetPoolTVL as ContractTVL
from eth_defi.gmx.core.funding_fee import GetFundingFee
from eth_defi.gmx.core.gm_prices import GetGMPrices as GMPrices
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.open_interest import GetOpenInterest
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.core.pool_tvl import GetPoolTVL
from eth_defi.gmx.core.glv_stats import GlvStats
from eth_defi.gmx.core.open_positions import GetOpenPositions

from eth_defi.gmx.config import GMXConfig


class GMXMarketData:
    """Comprehensive market data provider for the GMX protocol.

    Provides unified access to all GMX protocol market data through a single,
    consistent interface. Uses read-only access to ensure safe data retrieval
    without requiring wallet credentials or transaction signing capabilities.

    Attributes:
        gmx_config: Complete GMX configuration object for network settings
        config: Read-only configuration manager for safe data access operations
    """

    def __init__(self, gmx_config: GMXConfig):
        """Initialize the market data service.

        Sets up the market data provider by extracting a read-only configuration
        from the provided GMXConfig. The read-only configuration ensures that all
        data access operations are safe and cannot accidentally trigger transactions.

        :param gmx_config: Complete GMX configuration object containing network settings
            and optional wallet information. Only the read-only configuration
            component will be used for data access operations.
        """
        self.gmx_config = gmx_config
        self.config = gmx_config.get_config()

    def get_available_markets(self) -> MarketData:
        """Retrieve comprehensive information about all trading markets available on GMX.

        Returns detailed metadata about every trading pair and market supported by
        the GMX protocol on the configured network. The information includes market
        identifiers, supported assets, trading parameters, and current market status.

        Returns:
            Dictionary containing complete market information including market names,
            supported tokens, trading parameters, fees, and current status for all
            available GMX trading markets.
        """
        markets = Markets(self.gmx_config)
        return markets.get_available_markets()

    def get_available_liquidity(self) -> PositionSideData:
        """Get current available liquidity across all GMX markets and trading pairs.

        Retrieves real-time liquidity information showing how much capital is
        available for trading in each market. Available liquidity directly impacts
        the maximum position sizes that can be opened and the potential slippage
        for large trades, making this crucial information for trading strategy
        and risk management.

        Returns:
            Nested dictionary structure where outer keys are position sides (long/short)
            and inner dictionaries contain liquidity amounts for different markets.
        """
        # Use multicall approach for better performance (original max(0, ...) bug has been fixed)
        return GetAvailableLiquidity(self.gmx_config).get_data()

    def get_borrow_apr(self) -> PositionSideData:
        """
        Retrieve current annual percentage rates (APR) for borrowing across all markets.

        Borrowing rates represent the cost of leverage in perpetual futures trading.
        When traders open leveraged positions, they effectively borrow capital
        from liquidity providers and pay these rates as borrowing fees. The rates
        are dynamic and adjust based on utilization, market conditions, and
        available liquidity in each market.

        Returns:
            Nested dictionary where outer keys are market identifiers and
            inner dictionaries contain APR values for different assets and
            position types, expressed as decimal percentages.
        """
        return GetBorrowAPR(self.gmx_config).get_data()

    def get_claimable_fees(self) -> MarketData:
        """
        Get information about fees that can be claimed by liquidity providers.

        Liquidity providers on GMX earn fees from trading activity and can
        periodically claim these accumulated rewards. This method returns
        information about unclaimed fees available to the configured wallet
        address, including the amounts and types of rewards that have been
        earned but not yet withdrawn.

        Returns:
            Dictionary containing claimable fee information including amounts,
            asset types, and claiming mechanisms available to liquidity
            providers based on their LP token holdings.
        """
        return GetClaimableFees(self.gmx_config).get_data()

    def get_contract_tvl(self) -> TVLData:
        """
        Get Total Value Locked (TVL) at the individual contract level.

        This method provides granular TVL information showing the value of
        assets locked in specific GMX protocol contracts. Contract-level TVL
        gives insight into how capital is distributed across different protocol
        components and can be useful for understanding protocol health and
        capital efficiency.

        Returns:
            Dictionary containing TVL information organized by contract
            addresses, showing the value and composition of assets locked
            in each component of the GMX protocol
        """
        return ContractTVL(self.gmx_config).get_pool_balances()

    def get_funding_apr(self) -> PositionSideData:
        """
        Retrieve current funding rates (APR) for perpetual futures positions.

        Funding rates are periodic payments between long and short position
        holders that help keep perpetual futures prices aligned with spot
        prices. When funding rates are positive, long position holders pay
        short position holders, and vice versa. These rates adjust dynamically
        based on the imbalance between long and short positions.

        Returns:
            Nested dictionary where outer keys are market identifiers and
            inner dictionaries contain funding APR values for different
            position types, with positive values indicating longs pay shorts
        """
        return GetFundingFee(self.gmx_config).get_data()

    def get_gm_price(self) -> PriceData:
        """
        Get current prices and valuation data for GM (liquidity provider) tokens.

        GM tokens represent shares in GMX liquidity pools and their prices
        reflect the underlying value of the pooled assets plus accumulated
        fees. This pricing information is essential for liquidity providers
        to understand the value of their holdings and calculate returns on
        their liquidity provision activities.

        Returns:
            Dictionary containing GM token prices, underlying asset values,
            and other valuation metrics that determine the worth of liquidity
            provider positions in various GMX pools
        """
        return GMPrices(self.gmx_config).get_prices()

    def get_open_interest(self) -> PositionSideData:
        """
        Retrieve current open interest statistics across all trading markets.

        Open interest represents the total value of all outstanding positions
        in each market, providing insight into market activity levels and
        potential liquidity demands. High open interest indicates active
        trading and significant capital deployment, while changes in open
        interest can signal shifting market sentiment and trader positioning.

        Returns:
            Nested dictionary where outer keys are market identifiers and
            inner dictionaries contain open interest values for different
            position types (long/short) and underlying assets
        """
        return GetOpenInterest(self.gmx_config).get_data()

    def get_oracle_prices(self) -> PriceData:
        """
        Get current oracle price data for all assets supported by GMX protocol.

        Oracle prices are authoritative price feeds used by the GMX protocol
        for position valuation, liquidation calculations, and trade execution.
        These prices come from external price oracles and represent the
        protocol's view of current market values for all supported assets,
        ensuring accurate and manipulation-resistant pricing.

        Returns:
            Dictionary containing current oracle prices and metadata for all
            supported assets, including timestamps, confidence intervals,
            and other price feed quality indicators
        """
        return OraclePrices(self.config.chain).get_recent_prices()

    def get_pool_tvl(self) -> TVLData:
        """
        Get Total Value Locked (TVL) in all GMX liquidity pools.

        Pool TVL shows the total value of assets deposited by liquidity
        providers across all GMX trading pools. This metric is crucial for
        understanding protocol scale, liquidity depth, and the overall
        health of the GMX ecosystem. Higher TVL generally indicates greater
        trading capacity and reduced slippage for large trades.

        Returns:
            Dictionary containing detailed TVL information for each liquidity
            pool, including asset breakdown, pool composition, and total
            values across different asset types and markets
        """
        return GetPoolTVL(self.gmx_config).get_pool_balances()

    def get_glv_stats(self) -> MarketData:
        """
        Get comprehensive statistics for GLV (GMX Liquidity Vector) tokens.

        GLV tokens represent a more sophisticated liquidity provision mechanism
        that may include auto-compounding features, dynamic rebalancing, or
        other advanced strategies. These statistics provide insight into the
        performance and composition of GLV token holdings, helping liquidity
        providers understand the efficiency of these enhanced LP products.

        Returns:
            Dictionary containing GLV token statistics including performance
            metrics, composition data, yield information, and other relevant
            analytics for advanced liquidity provision strategies
        """
        return GlvStats(self.gmx_config).get_glv_stats()

    def get_user_positions(self, address: Optional[str] = None) -> MarketData:
        """Retrieve all open trading positions for a specific user address.

        This method provides detailed information about a user's current
        trading positions, including position sizes, entry prices, current
        profit/loss, margin requirements, and liquidation thresholds. This
        information is essential for position management, risk assessment,
        and portfolio analysis.

        :param address: Ethereum wallet address to query positions for. If not provided,
            uses the wallet address from the GMX configuration. Must be a valid
            Ethereum address format (0x...).
        :return: Dictionary containing detailed information about all open positions
            for the specified address, including position metrics, PnL data,
            margin information, and risk parameters
        """
        if address is None:
            address = self.gmx_config.get_wallet_address()

        return GetOpenPositions(self.gmx_config).get_data(address)
