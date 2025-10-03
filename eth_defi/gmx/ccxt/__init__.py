"""
CCXT Compatibility Module for GMX

This module provides CCXT-compatible interfaces for GMX protocol trading operations
to minimize migration overhead for users coming from CCXT-based trading systems.

Example usage:
    ```python
    from web3 import Web3
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.gmx.ccxt import GMXCCXTWrapper

    # Setup
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3, user_wallet_address="0x...")
    exchange = GMXCCXTWrapper(config)

    # Create orders using familiar CCXT methods
    result = exchange.create_market_buy_order("ETH/USD", 100.0)  # $100 ETH long
    ```
"""

from eth_defi.gmx.ccxt.wrapper import GMXCCXTWrapper

__all__ = ["GMXCCXTWrapper"]
