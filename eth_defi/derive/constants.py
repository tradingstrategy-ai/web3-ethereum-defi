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

#: Derive Chain ID (mainnet)
DERIVE_CHAIN_ID = 957

#: Derive testnet Chain ID
DERIVE_TESTNET_CHAIN_ID = 901

#: Derive mainnet RPC URL
DERIVE_MAINNET_RPC_URL = "https://rpc.derive.xyz"

#: Derive testnet RPC URL
DERIVE_TESTNET_RPC_URL = "https://testnet-rpc.derive.xyz"

#: LightAccountFactory contract address (same on mainnet and testnet)
#:
#: Used to derive the counterfactual smart contract wallet (LightAccount)
#: address from an owner EOA address.
LIGHT_ACCOUNT_FACTORY_ADDRESS = "0x000000893A26168158fbeaDD9335Be5bC96592E2"

#: ERC-4337 EntryPoint contract address (same on mainnet and testnet)
ACCOUNT_ENTRY_POINT_ADDRESS = "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789"

#: Derive Matching contract address (testnet)
#:
#: Used by ``public/build_register_session_key_tx`` for session key registration.
MATCHING_CONTRACT_TESTNET = "0x3cc154e220c2197c5337b7Bd13363DD127Bc0C6E"

#: Derive Paymaster contract address (testnet)
PAYMASTER_CONTRACT_TESTNET = "0xa179c3b32d3eE58353d3F277b32D1e03DD33fFCA"

#: Derive Standard Risk Manager contract address (testnet)
STANDARD_RISK_MANAGER_TESTNET = "0x28bE681F7bEa6f465cbcA1D25A2125fe7533391C"

#: Derive Deposit Module contract address (testnet)
DEPOSIT_MODULE_TESTNET = "0x43223Db33AdA0575D2E100829543f8B04A37a1ec"

#: Derive Cash Asset contract address (testnet)
CASH_ASSET_TESTNET = "0x6caf294DaC985ff653d5aE75b4FF8E0A66025928"

#: Derive Trade Module contract address (testnet)
TRADE_MODULE_TESTNET = "0x87F2863866D85E3192a35A73b388BD625D83f2be"

#: EIP-712 domain separator for Derive testnet
DOMAIN_SEPARATOR_TESTNET = "0x9bcf4dc06df5d8bf23af818d5716491b995020f377d3b7b64c29ed14e3dd1105"

#: EIP-712 action typehash for Derive
ACTION_TYPEHASH = "0x4d7a9f27c403ff9c0f19bce61d76d82f9aa29f8d6d4b0c5474607d9770d1af17"

#: Derive ERC-4337 bundler URL (testnet)
BUNDLER_URL_TESTNET = "https://bundler-prod-testnet-0eakp60405.t.conduit.xyz"

#: Derive block explorer URL (testnet)
BLOCK_EXPLORER_TESTNET = "https://explorer-prod-testnet-0eakp60405.t.conduit.xyz"

#: LightAccountFactory ABI (minimal -- only getAddress and createAccount)
LIGHT_ACCOUNT_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "uint256", "name": "salt", "type": "uint256"},
        ],
        "name": "createAccount",
        "outputs": [
            {"internalType": "contract LightAccount", "name": "ret", "type": "address"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "uint256", "name": "salt", "type": "uint256"},
        ],
        "name": "getAddress",
        "outputs": [
            {"internalType": "address", "name": "", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


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
