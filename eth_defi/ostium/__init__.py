"""Ostium trading integration.

This module provides Python helpers for trading on Ostium (Gains Network-compatible)
perpetual exchange through GuardV0-protected vaults.

Ostium is a decentralized perpetual exchange on Arbitrum that supports:
- Cryptocurrencies (BTC, ETH, etc.)
- Forex pairs (EUR/USD, GBP/USD, etc.)
- Commodities (Gold, Silver, Oil, etc.)
- Indices (S&P 500, NASDAQ, etc.)

Key Components:
    - trading: High-level trading operations
    - types: Type definitions for trades and parameters
    - vault: Gains/Ostium vault integration (inherited)

Example:
    >>> from eth_defi.ostium.trading import OstiumTrader
    >>> from eth_defi.ostium.types import TradeDirection, OrderType
    >>> 
    >>> trader = OstiumTrader(web3, vault_address, asset_manager)
    >>> tx_hash = trader.open_position(
    ...     pair_index=0,  # BTC/USD
    ...     collateral_usdc=Decimal("100"),
    ...     leverage=1000,  # 10x
    ...     direction=TradeDirection.LONG,
    ... )

See Also:
    - https://ostium-labs.gitbook.io/ostium-docs/
    - https://github.com/0xOstium/smart-contracts-public
"""

from eth_defi.ostium.trading import (
    OstiumTrader,
    verify_guard_settings,
    format_ostium_price,
    parse_ostium_price,
    OSTIUM_ARBITRUM_TRADING,
    OSTIUM_ARBITRUM_STORAGE,
    OSTIUM_ARBITRUM_VAULT,
    USDC_ARBITRUM,
)
from eth_defi.ostium.types import (
    OrderType,
    TradeDirection,
    TradeParams,
    OstiumContracts,
    ContractCall,
    GuardValidation,
    TradeMetrics,
    OpenTrade,
)

__all__ = [
    # Trading
    "OstiumTrader",
    "verify_guard_settings",
    "format_ostium_price",
    "parse_ostium_price",
    # Constants
    "OSTIUM_ARBITRUM_TRADING",
    "OSTIUM_ARBITRUM_STORAGE",
    "OSTIUM_ARBITRUM_VAULT",
    "USDC_ARBITRUM",
    # Types
    "OrderType",
    "TradeDirection",
    "TradeParams",
    "OstiumContracts",
    "ContractCall",
    "GuardValidation",
    "TradeMetrics",
    "OpenTrade",
]
