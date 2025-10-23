"""Constants and contract addresses for Gains Network integration.

This module contains network-specific contract addresses, pair indices,
and other constants needed for Gains Network trading.

References:
- Contract Addresses: https://gains-network.gitbook.io/docs-home/what-is-gains-network/contract-addresses
- Pair List: https://docs.gains.trade/gtrade-leveraged-trading/pair-list
"""
from enum import IntEnum

from eth_typing import ChecksumAddress
from cchecksum import to_checksum_address

from eth_defi.token import USDC_NATIVE_TOKEN, WRAPPED_NATIVE_TOKEN

# ============================================================================
# Contract Addresses by Network
# ============================================================================

#: Main trading contract (Diamond pattern)
GAINS_DIAMOND_ADDRESSES: dict[str, str] = {
    "arbitrum": to_checksum_address("0xFF162c694eAA571f685030649814282eA457f169"),
    "arbitrum-sepolia": to_checksum_address("0xd659a15812064C79E189fd950A189b15c75d3186"),
}

#: Collateral token addresses
COLLATERAL_TOKENS: dict[str, dict[str, ChecksumAddress]] = {
    "arbitrum": {
        "USDC": to_checksum_address(USDC_NATIVE_TOKEN[42161]),
        "DAI": to_checksum_address("0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1"),
        "WETH":to_checksum_address( WRAPPED_NATIVE_TOKEN[42161]),

    },
    "arbitrum-sepolia": {
        # Testnet tokens - get from gains.trade practice mode
        # DAI: Can be claimed on gains.trade (10,000 DAI free)
        # Query actual addresses from diamond contract using getCollaterals()
        "DAI": to_checksum_address("0x0000000000000000000000000000000000000000"),  # Placeholder
    },
}

#: Vault addresses (gToken vaults)
VAULT_ADDRESSES: dict[str, dict[str, str]] = {
    "arbitrum": {
        "gUSDC": to_checksum_address("0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"),
        "gDAI": to_checksum_address("0xd85E038593d7A098614721EaE955EC2022B9B91B"),
        "gETH": to_checksum_address("0x5977A9682D7AF81D347CFc338c61692163a2784C"),
    },
}


# ============================================================================
# Trading Pair Constants
# ============================================================================

class PairIndex(IntEnum):
    """Common trading pair indices on Gains Network.

    The pair index is used to identify trading pairs in the contract.
    Full list: https://docs.gains.trade/gtrade-leveraged-trading/pair-list
    """
    # Crypto pairs
    BTC_USD = 0
    ETH_USD = 1
    LINK_USD = 2
    # Add more

    # Forex pairs (indices vary, check current list)
    EUR_USD = 30
    GBP_USD = 31
    USD_JPY = 32

    # Commodities
    GOLD_USD = 50
    SILVER_USD = 51


# Pair symbol to index mapping
PAIR_SYMBOLS: dict[str, int] = {
    "BTC/USD": 0,
    "ETH/USD": 1,
    "LINK/USD": 2,
    "EUR/USD": 30,
    "GBP/USD": 31,
    "USD/JPY": 32,
    "GOLD/USD": 50,
    "SILVER/USD": 51,
}

# Reverse mapping
PAIR_INDICES: dict[int, str] = {v: k for k, v in PAIR_SYMBOLS.items()}

# ============================================================================
# Trading Limits and Constants
# ============================================================================

# Maximum leverage by asset class
MAX_LEVERAGE: dict[str, int] = {
    "crypto": 150,
    "forex": 1000,
    "commodities": 100,
}

# Pair groups (for determining max leverage)
PAIR_GROUPS: dict[int, str] = {
    0: "crypto",  # BTC
    1: "crypto",  # ETH
    2: "crypto",  # LINK
    30: "forex",  # EUR/USD
    31: "forex",  # GBP/USD
    32: "forex",  # USD/JPY
    50: "commodities",  # GOLD
    51: "commodities",  # SILVER
}

# Precision constants
PRICE_PRECISION = 10 ** 10  # Prices stored with 1e10 precision
COLLATERAL_PRECISION_USDC = 10 ** 6  # USDC has 6 decimals
COLLATERAL_PRECISION_DEFAULT = 10 ** 18  # DAI and WETH have 18 decimals

# Trading fee tiers (in basis points, e.g., 8 = 0.08%)
DEFAULT_TRADING_FEE = 8  # 0.08%
REFERRAL_FEE_DISCOUNT = 5  # 0.05% discount with referral

# Timeout for oracle price confirmation (seconds)
ORDER_TIMEOUT = 60  # Orders timeout after 60 seconds if not fulfilled

# ============================================================================
# Oracle and Backend
# ============================================================================

# Chainlink DON (Decentralized Oracle Network) endpoints
ORACLE_ENDPOINTS: dict[str, str] = {
    "arbitrum": "wss://",  # Add actual WebSocket endpoint
}

# Backend API endpoints for historical data
BACKEND_API: dict[str, str] = {
    "arbitrum": "https://backend-arbitrum.gains.trade",
}


# ============================================================================
# Helper Functions
# ============================================================================

def get_pair_index(symbol: str) -> int:
    """Get pair index from symbol.

    :param symbol: Pair symbol like 'BTC/USD'
    :return: Pair index
    :raises ValueError: If symbol not found
    """
    pair_index = PAIR_SYMBOLS.get(symbol)
    if pair_index is None:
        raise ValueError(f"Unknown pair symbol: {symbol}")
    return pair_index


def get_pair_symbol(index: int) -> str:
    """Get pair symbol from index.

    :param index: Pair index
    :return: Pair symbol like 'BTC/USD'
    :raises ValueError: If index not found
    """
    symbol = PAIR_INDICES.get(index)
    if symbol is None:
        raise ValueError(f"Unknown pair index: {index}")
    return symbol


def get_max_leverage(pair_index: int) -> int:
    """Get maximum leverage for a trading pair.

    :param pair_index: Pair index
    :return: Maximum leverage multiplier
    """
    group = PAIR_GROUPS.get(pair_index, "crypto")
    return MAX_LEVERAGE[group]


def get_collateral_decimals(collateral_symbol: str) -> int:
    """Get decimal precision for a collateral token.

    :param collateral_symbol: Token symbol ('USDC', 'DAI', 'WETH')
    :return: Number of decimals
    """
    if collateral_symbol == "USDC":
        return 6
    return 18  # DAI and WETH


def to_contract_price(price: float) -> int:
    """Convert a price to contract format.

    :param price: Price in decimal format
    :return: Price in contract format (1e10 precision)
    """
    return int(price * PRICE_PRECISION)


def from_contract_price(price: int) -> float:
    """Convert contract price to decimal format.

    :param price: Price in contract format
    :return: Price in decimal format
    """
    return price / PRICE_PRECISION


def to_collateral_amount(amount: float, collateral_symbol: str) -> int:
    """Convert collateral amount to raw token units.

    :param amount: Amount in decimal format
    :param collateral_symbol: Token symbol
    :return: Amount in raw token units
    """
    decimals = get_collateral_decimals(collateral_symbol)
    return int(amount * 10 ** decimals)


def from_collateral_amount(amount: int, collateral_symbol: str) -> float:
    """Convert raw token units to decimal amount.

    :param amount: Amount in raw token units
    :param collateral_symbol: Token symbol
    :return: Amount in decimal format
    """
    decimals = get_collateral_decimals(collateral_symbol)
    return amount / 10 ** decimals
