"""
GMX Core Module

This module provides the main GMXClient class that integrates all GMX functionality.
"""

from typing import Optional, Any

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.order import GMXOrderManager
from eth_defi.gmx.liquidity import GMXLiquidityManager

# from eth_defi.gmx.events import GMXEvents
from eth_defi.gmx.api import GMXAPI


class GMXClient:
    """
    Main client for interacting with the GMX protocol.

    This class integrates all GMX functionality, including trading, market data,
    liquidity provision, event monitoring, and API access.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize the GMX client.

        Args:
            config: GMX configuration object
        """
        self.config = config

        # Initialize sub-modules
        self.market_data = GMXMarketData(config)
        self.trading = GMXTrading(config)
        self.order_manager = GMXOrderManager(config)
        self.liquidity_manager = GMXLiquidityManager(config)
        self.events = GMXEvents(config)
        self.api = GMXAPI(config)

    def get_chain(self) -> str:
        """Get the current chain."""
        return self.config.get_chain()

    def get_wallet_address(self) -> Optional[str]:
        """Get the current wallet address."""
        return self.config.get_wallet_address()

    def has_write_capability(self) -> bool:
        """Check if the client can perform write operations."""
        return self.config.has_write_capability()

    def get_network_info(self) -> dict[str, Any]:
        """Get network information."""
        return self.config.get_network_info()
