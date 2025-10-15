"""
GMX Core Module

This module provides the main GMXClient class that integrates all GMX functionality.
"""

from typing import Optional, Any

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData
from eth_defi.gmx.trading import GMXTrading

# from eth_defi.gmx.events import GMXEvents
from eth_defi.gmx.api import GMXAPI


class GMXClient:
    """
    Main client for interacting with the GMX protocol.

    This class serves as the primary entry point for all GMX protocol interactions,
    providing a unified interface that coordinates trading, market data access,
    liquidity management, and order handling. It follows a composition pattern
    where specialized managers handle different aspects of GMX functionality.

    The client automatically initializes all sub-modules and provides convenient
    access to configuration and wallet information. It's designed to be the single
    object developers need to interact with GMX across all supported networks.

    Example:

    .. code-block:: python

        # Initialize GMX client for Arbitrum with read-only access
        config = GMXConfig(
            chain="arbitrum",
            rpc_url="https://arb1.arbitrum.io/rpc",
        )

        # Check configuration
        print(f"Connected to {gmx.get_chain()}")
        print(f"Write capability: {gmx.has_write_capability()}")

        # Access market data
        positions = gmx.market_data.get_positions()

        # Place trades (requires wallet configuration)
        if gmx.has_write_capability():
            trade_result = gmx.trading.open_position(
                market="ETH/USD",
                side="long",
                size_usd=1000,
            )

    :ivar config: Configuration object containing network and wallet settings
    :vartype config: GMXConfig
    :ivar market_data: Market data access and analysis functionality
    :vartype market_data: GMXMarketData
    :ivar trading: Trading execution and position management
    :vartype trading: GMXTrading
    :ivar order_manager: Order creation, modification, and monitoring
    :vartype order_manager: GMXOrderManager
    :ivar liquidity_manager: Liquidity provision and LP token management
    :vartype liquidity_manager: GMXLiquidityManager
    :ivar api: Direct access to GMX API endpoints
    :vartype api: GMXAPI
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize the GMX client with the provided configuration.

        This constructor sets up all sub-modules and validates that the
        configuration provides sufficient information for the requested
        operations. Each sub-module is initialized with the same configuration
        to ensure consistent network and wallet settings across all operations.

        :param config:
            GMX configuration object containing network settings, RPC endpoints,
            wallet information, and other protocol-specific parameters
        :type config: GMXConfig
        """
        self.config = config

        # Initialize sub-modules
        self.market_data = GMXMarketData(config)
        self.trading = GMXTrading(config)
        # TODO: Make the new OrderManager
        # self.order_manager = GMXOrderManager(config)
        # self.events = GMXEvents(config)
        self.api = GMXAPI(config)

    def get_chain(self) -> str:
        """
        Get the blockchain network currently configured for this client.

        This method returns the network identifier (e.g., "arbitrum", "avalanche")
        that was specified during client initialization. This is useful for
        conditional logic based on network-specific features or parameters.

        :return:
            Network identifier string indicating which blockchain network
            this client is configured to use
        :rtype: str
        """
        return self.config.get_chain()

    def get_wallet_address(self) -> Optional[str]:
        """
        Get the wallet address associated with this client, if configured.

        Returns the Ethereum address of the wallet that was configured for
        this client instance. This address will be used for all trading and
        liquidity operations. Returns None if the client was initialized
        without wallet credentials (read-only mode).

        :return:
            Ethereum wallet address as a hexadecimal string, or None if
            no wallet was configured during initialization
        :rtype: Optional[str]
        """
        return self.config.get_wallet_address()

    def has_write_capability(self) -> bool:
        """
        Check whether this client can perform transactions that modify blockchain state.

        Returns True if the client has been configured with wallet credentials
        and private keys necessary to sign and submit transactions. Returns False
        for read-only clients that can only query data without making changes.

        This is essential to check before attempting trading operations, as
        write operations will fail if the client lacks proper wallet configuration.

        :return:
            True if the client can sign and submit transactions, False if
            the client is configured for read-only operations
        :rtype: bool
        """
        return self.config.has_write_capability()

    def get_network_info(self) -> dict[str, Any]:
        """
        Get comprehensive information about the configured blockchain network.

        Returns detailed network information including RPC endpoints, contract
        addresses, block numbers, and other network-specific parameters. This
        information is useful for debugging connectivity issues or understanding
        the current network state.

        :return:
            Dictionary containing network configuration details such as
            RPC URLs, contract addresses, current block number, and
            network-specific parameters
        :rtype: dict[str, Any]
        """
        return self.config.get_network_info()
