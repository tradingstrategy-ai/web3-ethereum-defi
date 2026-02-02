"""Derive.xyz API constants and configuration.

This module defines API endpoints, rate limits, and enums for Derive.xyz integration.
"""

from enum import Enum
from pathlib import Path


#: Derive mainnet API URL (formerly Lyra)
DERIVE_MAINNET_API_URL = "https://api.lyra.finance"

#: Derive testnet API URL
DERIVE_TESTNET_API_URL = "https://api-demo.lyra.finance"

#: Derive mainnet WebSocket URL
DERIVE_MAINNET_WS_URL = "wss://api.lyra.finance/ws"

#: Derive testnet WebSocket URL
DERIVE_TESTNET_WS_URL = "wss://api-demo.lyra.finance/ws"

#: Default rate limit for Derive API requests per second
#:
#: Conservative estimate - adjust based on actual API limits
DEFAULT_REQUESTS_PER_SECOND = 2.0

#: Default number of retries for API requests
DEFAULT_RETRIES = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR = 0.5

#: Default SQLite database path for rate limiting state
#:
#: Using SQLite ensures thread-safe rate limiting across multiple threads
DERIVE_RATE_LIMIT_SQLITE_DATABASE = Path("~/.tradingstrategy/derive/rate-limit.sqlite").expanduser()

#: Derive Chain ID
DERIVE_CHAIN_ID = 957


class CollateralType(Enum):
    """Supported collateral types on Derive.

    #: USD Coin
    usdc = "usdc"

    #: Wrapped Ethereum
    weth = "weth"

    #: Wrapped Liquid Staked Ethereum
    wsteth = "wsteth"

    #: Wrapped Bitcoin
    wbtc = "wbtc"
    """

    usdc = "usdc"
    weth = "weth"
    wsteth = "wsteth"
    wbtc = "wbtc"


class SessionKeyScope(Enum):
    """Session key permission levels.

    #: View-only access for orders, account info, and history
    read_only = "read_only"

    #: Account management including settings and order cancellation
    account = "account"

    #: Full access including trading, deposits, and withdrawals
    admin = "admin"
    """

    read_only = "read_only"
    account = "account"
    admin = "admin"


class MarginType(Enum):
    """Margin calculation types for Derive accounts.

    #: Standard Margin
    standard_margin = "SM"

    #: Portfolio Margin
    portfolio_margin = "PM"
    """

    standard_margin = "SM"
    portfolio_margin = "PM"
