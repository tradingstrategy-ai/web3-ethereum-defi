"""Markets data cache for fork testing.

This module provides pre-cached markets data to avoid RPC calls to getMarkets(),
which can timeout on slow fork RPCs. This is used during fork testing to speed
up order creation.

The markets cache is loaded once and reused for all subsequent order operations.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# Mainnet (Arbitrum) markets cache
# This is based on actual GMX market data from mainnet
ARBITRUM_MARKETS_CACHE = {
    # ETH/USD Market
    "0x70d95587d40A2caf56bd97485aB3Ee0E6B477Ac9": {
        "gmx_market_address": "0x70d95587d40A2caf56bd97485aB3Ee0E6B477Ac9",
        "market_symbol": "ETH",
        "index_token_address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
        "market_metadata": {
            "symbol": "ETH",
            "decimals": 18,
            "synthetic": False,
        },
        "long_token_metadata": {
            "symbol": "ETH",
            "decimals": 18,
            "synthetic": False,
        },
        "long_token_address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
        "short_token_metadata": {
            "symbol": "USDC",
            "decimals": 6,
            "synthetic": False,
        },
        "short_token_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    },
}

# Testnet (Arbitrum Sepolia) markets cache
ARBITRUM_SEPOLIA_MARKETS_CACHE = {
    # ETH Market (testnet)
    "0xE5580435E45d5eC6c0Ef3cB77D4c07bDdD75A9cB": {
        "gmx_market_address": "0xE5580435E45d5eC6c0Ef3cB77D4c07bDdD75A9cB",
        "market_symbol": "ETH",
        "index_token_address": "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73",  # WETH (testnet)
        "market_metadata": {
            "symbol": "ETH",
            "decimals": 18,
            "synthetic": False,
        },
        "long_token_metadata": {
            "symbol": "ETH",
            "decimals": 18,
            "synthetic": False,
        },
        "long_token_address": "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73",
        "short_token_metadata": {
            "symbol": "USDC",
            "decimals": 6,
            "synthetic": False,
        },
        "short_token_address": "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773",
    },
}


def get_markets_cache_for_chain(chain: str) -> Optional[Dict[str, Any]]:
    """
    Get cached markets data for a given chain.

    This returns pre-loaded market data to avoid RPC calls during fork testing.
    For fork networks (mainnet fork on Anvil), we use the mainnet cache.

    Args:
        chain: Chain identifier ('arbitrum', 'arbitrum_sepolia', or 'arbitrum_fork')

    Returns:
        Dictionary of cached markets or None if chain is not supported
    """
    chain_lower = chain.lower()

    if chain_lower in ["arbitrum", "arbitrum_fork", "arbitrum_mainnet"]:
        logger.debug(f"Using cached markets for {chain}")
        return ARBITRUM_MARKETS_CACHE

    elif chain_lower == "arbitrum_sepolia":
        logger.debug(f"Using cached markets for {chain}")
        return ARBITRUM_SEPOLIA_MARKETS_CACHE

    else:
        logger.warning(f"No cached markets available for chain: {chain}")
        return None


def inject_markets_cache_into_config(gmx_config) -> None:
    """
    Inject pre-cached markets data into a GMXConfig to avoid RPC calls.

    This patches the Markets class to return cached data instead of calling
    the Reader contract's getMarkets() method. This is essential for fork
    testing where RPC calls can timeout.

    Args:
        gmx_config: GMXConfig instance to patch
    """
    from eth_defi.gmx.core.markets import Markets

    chain = gmx_config.chain
    cache = get_markets_cache_for_chain(chain)

    if cache is None:
        logger.warning(f"No markets cache available for {chain}, RPC calls may timeout")
        return

    # Normalize cache keys to lowercase for case-insensitive lookup
    normalized_cache = {addr.lower(): data for addr, data in cache.items()}

    # Monkey-patch the Markets class to use our cache
    original_init = Markets.__init__

    def patched_init(self, config):
        # Call original init
        original_init(self, config)
        # Inject our cache with normalized keys
        self._markets_cache = normalized_cache

    Markets.__init__ = patched_init
    logger.info(f"Injected {len(normalized_cache)} cached markets for {chain}")
