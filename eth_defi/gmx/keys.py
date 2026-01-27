"""
GMX Protocol Key Generation Functions

This module provides key generation functions for GMX protocol data store keys.
These functions generate the proper keccak hashes used for accessing data
from the GMX datastore contract.

Complete implementation matching gmx-synthetics/utils/keys.ts
"""

from eth_abi import encode
from eth_utils import keccak


def apply_factor(value, factor):
    """Apply a 30-decimal factor to a value.

    GMX uses 30-decimal fixed-point arithmetic for many values.
    This function applies a factor stored as an integer with 30 decimals.

    :param value: Value to apply factor to
    :type value: numeric
    :param factor: Factor in 30-decimal format (e.g., 10^30 = 1.0)
    :type factor: int
    :return: Result of (value * factor) / 10^30
    :rtype: numeric
    """
    return value * factor / 10**30


def create_hash(data_type_list: list, data_value_list: list) -> bytes:
    """Create a keccak hash using ABI encoding.

    :param data_type_list: List of data types as strings (e.g., ["string", "address"])
    :type data_type_list: list
    :param data_value_list: List of values matching the data types
    :type data_value_list: list
    :return: Keccak-256 hashed key
    :rtype: bytes
    """
    byte_data = encode(data_type_list, data_value_list)
    return keccak(byte_data)


def create_hash_string(string: str) -> bytes:
    """Hash a string value using keccak-256.

    :param string: String to hash
    :type string: str
    :return: Keccak-256 hashed string
    :rtype: bytes
    """
    return create_hash(["string"], [string])


# ==============================================================================
# Basic Configuration
# ==============================================================================

WNT = create_hash_string("WNT")
NONCE = create_hash_string("NONCE")
FEE_RECEIVER = create_hash_string("FEE_RECEIVER")
HOLDING_ADDRESS = create_hash_string("HOLDING_ADDRESS")
SEQUENCER_GRACE_DURATION = create_hash_string("SEQUENCER_GRACE_DURATION")
IN_STRICT_PRICE_FEED_MODE = create_hash_string("IN_STRICT_PRICE_FEED_MODE")

# ==============================================================================
# Gas Configuration
# ==============================================================================

MIN_HANDLE_EXECUTION_ERROR_GAS = create_hash_string("MIN_HANDLE_EXECUTION_ERROR_GAS")
MIN_ADDITIONAL_GAS_FOR_EXECUTION = create_hash_string("MIN_ADDITIONAL_GAS_FOR_EXECUTION")
MIN_HANDLE_EXECUTION_ERROR_GAS_TO_FORWARD = create_hash_string("MIN_HANDLE_EXECUTION_ERROR_GAS_TO_FORWARD")
REFUND_EXECUTION_FEE_GAS_LIMIT = create_hash_string("REFUND_EXECUTION_FEE_GAS_LIMIT")
TOKEN_TRANSFER_GAS_LIMIT = create_hash_string("TOKEN_TRANSFER_GAS_LIMIT")
NATIVE_TOKEN_TRANSFER_GAS_LIMIT = create_hash_string("NATIVE_TOKEN_TRANSFER_GAS_LIMIT")
MAX_CALLBACK_GAS_LIMIT = create_hash_string("MAX_CALLBACK_GAS_LIMIT")

# ==============================================================================
# Leverage
# ==============================================================================

MAX_LEVERAGE = create_hash_string("MAX_LEVERAGE")

# ==============================================================================
# Lists
# ==============================================================================

MARKET_LIST = create_hash_string("MARKET_LIST")
DEPOSIT_LIST = create_hash_string("DEPOSIT_LIST")
ACCOUNT_DEPOSIT_LIST = create_hash_string("ACCOUNT_DEPOSIT_LIST")
GLV_LIST = create_hash_string("GLV_LIST")
GLV_DEPOSIT_LIST = create_hash_string("GLV_DEPOSIT_LIST")
ACCOUNT_GLV_DEPOSIT_LIST = create_hash_string("ACCOUNT_GLV_DEPOSIT_LIST")
WITHDRAWAL_LIST = create_hash_string("WITHDRAWAL_LIST")
ACCOUNT_WITHDRAWAL_LIST = create_hash_string("ACCOUNT_WITHDRAWAL_LIST")
GLV_WITHDRAWAL_LIST = create_hash_string("GLV_WITHDRAWAL_LIST")
ACCOUNT_GLV_WITHDRAWAL_LIST = create_hash_string("ACCOUNT_GLV_WITHDRAWAL_LIST")
SHIFT_LIST = create_hash_string("SHIFT_LIST")
ACCOUNT_SHIFT_LIST = create_hash_string("ACCOUNT_SHIFT_LIST")
GLV_SHIFT_LIST = create_hash_string("GLV_SHIFT_LIST")
POSITION_LIST = create_hash_string("POSITION_LIST")
ACCOUNT_POSITION_LIST = create_hash_string("ACCOUNT_POSITION_LIST")
ORDER_LIST = create_hash_string("ORDER_LIST")
ACCOUNT_ORDER_LIST = create_hash_string("ACCOUNT_ORDER_LIST")
SUBACCOUNT_LIST = create_hash_string("SUBACCOUNT_LIST")
AUTO_CANCEL_ORDER_LIST = create_hash_string("AUTO_CANCEL_ORDER_LIST")

# ==============================================================================
# Feature Flags
# ==============================================================================

CREATE_DEPOSIT_FEATURE_DISABLED = create_hash_string("CREATE_DEPOSIT_FEATURE_DISABLED")
CANCEL_DEPOSIT_FEATURE_DISABLED = create_hash_string("CANCEL_DEPOSIT_FEATURE_DISABLED")
EXECUTE_DEPOSIT_FEATURE_DISABLED = create_hash_string("EXECUTE_DEPOSIT_FEATURE_DISABLED")
GASLESS_FEATURE_DISABLED = create_hash_string("GASLESS_FEATURE_DISABLED")
JIT_FEATURE_DISABLED = create_hash_string("JIT_FEATURE_DISABLED")
CREATE_ORDER_FEATURE_DISABLED = create_hash_string("CREATE_ORDER_FEATURE_DISABLED")
EXECUTE_ORDER_FEATURE_DISABLED = create_hash_string("EXECUTE_ORDER_FEATURE_DISABLED")
EXECUTE_ADL_FEATURE_DISABLED = create_hash_string("EXECUTE_ADL_FEATURE_DISABLED")
UPDATE_ORDER_FEATURE_DISABLED = create_hash_string("UPDATE_ORDER_FEATURE_DISABLED")
CANCEL_ORDER_FEATURE_DISABLED = create_hash_string("CANCEL_ORDER_FEATURE_DISABLED")
CREATE_WITHDRAWAL_FEATURE_DISABLED = create_hash_string("CREATE_WITHDRAWAL_FEATURE_DISABLED")
CANCEL_WITHDRAWAL_FEATURE_DISABLED = create_hash_string("CANCEL_WITHDRAWAL_FEATURE_DISABLED")
EXECUTE_WITHDRAWAL_FEATURE_DISABLED = create_hash_string("EXECUTE_WITHDRAWAL_FEATURE_DISABLED")
EXECUTE_ATOMIC_WITHDRAWAL_FEATURE_DISABLED = create_hash_string("EXECUTE_ATOMIC_WITHDRAWAL_FEATURE_DISABLED")
CREATE_SHIFT_FEATURE_DISABLED = create_hash_string("CREATE_SHIFT_FEATURE_DISABLED")
EXECUTE_SHIFT_FEATURE_DISABLED = create_hash_string("EXECUTE_SHIFT_FEATURE_DISABLED")
CANCEL_SHIFT_FEATURE_DISABLED = create_hash_string("CANCEL_SHIFT_FEATURE_DISABLED")
CREATE_GLV_DEPOSIT_FEATURE_DISABLED = create_hash_string("CREATE_GLV_DEPOSIT_FEATURE_DISABLED")
GENERAL_CLAIM_FEATURE_DISABLED = create_hash_string("GENERAL_CLAIM_FEATURE_DISABLED")

# ==============================================================================
# Claimable Amounts & Factors
# ==============================================================================

CLAIMABLE_FEE_AMOUNT = create_hash_string("CLAIMABLE_FEE_AMOUNT")
CLAIMABLE_FUNDING_AMOUNT = create_hash_string("CLAIMABLE_FUNDING_AMOUNT")
CLAIMABLE_COLLATERAL_AMOUNT = create_hash_string("CLAIMABLE_COLLATERAL_AMOUNT")
CLAIMABLE_COLLATERAL_FACTOR = create_hash_string("CLAIMABLE_COLLATERAL_FACTOR")
CLAIMABLE_COLLATERAL_REDUCTION_FACTOR = create_hash_string("CLAIMABLE_COLLATERAL_REDUCTION_FACTOR")
CLAIMABLE_COLLATERAL_TIME_DIVISOR = create_hash_string("CLAIMABLE_COLLATERAL_TIME_DIVISOR")
CLAIMABLE_COLLATERAL_DELAY = create_hash_string("CLAIMABLE_COLLATERAL_DELAY")
CLAIMABLE_UI_FEE_AMOUNT = create_hash_string("CLAIMABLE_UI_FEE_AMOUNT")

# ==============================================================================
# Affiliate & UI Fees
# ==============================================================================

AFFILIATE_REWARD = create_hash_string("AFFILIATE_REWARD")
MAX_UI_FEE_FACTOR = create_hash_string("MAX_UI_FEE_FACTOR")
MIN_AFFILIATE_REWARD_FACTOR = create_hash_string("MIN_AFFILIATE_REWARD_FACTOR")

# ==============================================================================
# Auto Cancel Orders
# ==============================================================================

MAX_AUTO_CANCEL_ORDERS = create_hash_string("MAX_AUTO_CANCEL_ORDERS")
MAX_TOTAL_CALLBACK_GAS_LIMIT_FOR_AUTO_CANCEL_ORDERS = create_hash_string(
    "MAX_TOTAL_CALLBACK_GAS_LIMIT_FOR_AUTO_CANCEL_ORDERS"
)

# ==============================================================================
# Market Configuration
# ==============================================================================

IS_MARKET_DISABLED = create_hash_string("IS_MARKET_DISABLED")
MAX_SWAP_PATH_LENGTH = create_hash_string("MAX_SWAP_PATH_LENGTH")
MIN_MARKET_TOKENS_FOR_FIRST_DEPOSIT = create_hash_string("MIN_MARKET_TOKENS_FOR_FIRST_DEPOSIT")

# ==============================================================================
# Oracle Configuration
# ==============================================================================

MIN_ORACLE_BLOCK_CONFIRMATIONS = create_hash_string("MIN_ORACLE_BLOCK_CONFIRMATIONS")
MAX_ORACLE_PRICE_AGE = create_hash_string("MAX_ORACLE_PRICE_AGE")
MAX_ATOMIC_ORACLE_PRICE_AGE = create_hash_string("MAX_ATOMIC_ORACLE_PRICE_AGE")
MAX_ORACLE_REF_PRICE_DEVIATION_FACTOR = create_hash_string("MAX_ORACLE_REF_PRICE_DEVIATION_FACTOR")
MIN_ORACLE_SIGNERS = create_hash_string("MIN_ORACLE_SIGNERS")
MAX_ORACLE_TIMESTAMP_RANGE = create_hash_string("MAX_ORACLE_TIMESTAMP_RANGE")
IS_ORACLE_PROVIDER_ENABLED = create_hash_string("IS_ORACLE_PROVIDER_ENABLED")
IS_ATOMIC_ORACLE_PROVIDER = create_hash_string("IS_ATOMIC_ORACLE_PROVIDER")
CHAINLINK_PAYMENT_TOKEN = create_hash_string("CHAINLINK_PAYMENT_TOKEN")
ORACLE_TYPE = create_hash_string("ORACLE_TYPE")
ORACLE_PROVIDER_FOR_TOKEN = create_hash_string("ORACLE_PROVIDER_FOR_TOKEN")
ORACLE_PROVIDER_UPDATED_AT = create_hash_string("ORACLE_PROVIDER_UPDATED_AT")
ORACLE_PROVIDER_MIN_CHANGE_DELAY = create_hash_string("ORACLE_PROVIDER_MIN_CHANGE_DELAY")
ORACLE_TIMESTAMP_ADJUSTMENT = create_hash_string("ORACLE_TIMESTAMP_ADJUSTMENT")

# ==============================================================================
# Collateral Configuration
# ==============================================================================

MIN_COLLATERAL_FACTOR = create_hash_string("MIN_COLLATERAL_FACTOR")
MIN_COLLATERAL_FACTOR_FOR_LIQUIDATION = create_hash_string("MIN_COLLATERAL_FACTOR_FOR_LIQUIDATION")
MIN_COLLATERAL_FACTOR_FOR_OPEN_INTEREST_MULTIPLIER = create_hash_string(
    "MIN_COLLATERAL_FACTOR_FOR_OPEN_INTEREST_MULTIPLIER"
)
MIN_COLLATERAL_USD = create_hash_string("MIN_COLLATERAL_USD")
MIN_POSITION_SIZE_USD = create_hash_string("MIN_POSITION_SIZE_USD")

# ==============================================================================
# Fee Receiver Factors
# ==============================================================================

SWAP_FEE_RECEIVER_FACTOR = create_hash_string("SWAP_FEE_RECEIVER_FACTOR")
ATOMIC_SWAP_FEE_TYPE = create_hash_string("ATOMIC_SWAP_FEE_TYPE")
POSITION_FEE_RECEIVER_FACTOR = create_hash_string("POSITION_FEE_RECEIVER_FACTOR")
LIQUIDATION_FEE_RECEIVER_FACTOR = create_hash_string("LIQUIDATION_FEE_RECEIVER_FACTOR")
BORROWING_FEE_RECEIVER_FACTOR = create_hash_string("BORROWING_FEE_RECEIVER_FACTOR")

# ==============================================================================
# Relay Fees (Gelato/Subaccounts)
# ==============================================================================

MAX_RELAY_FEE_SWAP_USD_FOR_SUBACCOUNT = create_hash_string("MAX_RELAY_FEE_SWAP_USD_FOR_SUBACCOUNT")
GELATO_RELAY_FEE_BASE_AMOUNT = create_hash_string("GELATO_RELAY_FEE_BASE_AMOUNT")
GELATO_RELAY_FEE_MULTIPLIER_FACTOR = create_hash_string("GELATO_RELAY_FEE_MULTIPLIER_FACTOR")
RELAY_FEE_ADDRESS = create_hash_string("RELAY_FEE_ADDRESS")

# ==============================================================================
# Request Expiration
# ==============================================================================

REQUEST_EXPIRATION_TIME = create_hash_string("REQUEST_EXPIRATION_TIME")
VALID_FROM_TIME = create_hash_string("VALID_FROM_TIME")

# ==============================================================================
# Price Feeds & Data Streams
# ==============================================================================

PRICE_FEED = create_hash_string("PRICE_FEED")
PRICE_FEED_MULTIPLIER = create_hash_string("PRICE_FEED_MULTIPLIER")
PRICE_FEED_HEARTBEAT_DURATION = create_hash_string("PRICE_FEED_HEARTBEAT_DURATION")
DATA_STREAM_ID = create_hash_string("DATA_STREAM_ID")
EDGE_DATA_STREAM_ID = create_hash_string("EDGE_DATA_STREAM_ID")
EDGE_DATA_STREAM_TOKEN_DECIMALS = create_hash_string("EDGE_DATA_STREAM_TOKEN_DECIMALS")
DATA_STREAM_MULTIPLIER = create_hash_string("DATA_STREAM_MULTIPLIER")
DATA_STREAM_SPREAD_REDUCTION_FACTOR = create_hash_string("DATA_STREAM_SPREAD_REDUCTION_FACTOR")
STABLE_PRICE = create_hash_string("STABLE_PRICE")

# ==============================================================================
# Open Interest
# ==============================================================================

OPEN_INTEREST = create_hash_string("OPEN_INTEREST")
OPEN_INTEREST_IN_TOKENS = create_hash_string("OPEN_INTEREST_IN_TOKENS")
MAX_OPEN_INTEREST = create_hash_string("MAX_OPEN_INTEREST")
OPEN_INTEREST_RESERVE_FACTOR = create_hash_string("OPEN_INTEREST_RESERVE_FACTOR")

# ==============================================================================
# Collateral & Pool
# ==============================================================================

COLLATERAL_SUM = create_hash_string("COLLATERAL_SUM")
POOL_AMOUNT = create_hash_string("POOL_AMOUNT")
MAX_POOL_AMOUNT = create_hash_string("MAX_POOL_AMOUNT")
MAX_POOL_USD_FOR_DEPOSIT = create_hash_string("MAX_POOL_USD_FOR_DEPOSIT")
MAX_COLLATERAL_SUM = create_hash_string("MAX_COLLATERAL_SUM")

# ==============================================================================
# Position Impact Pools
# ==============================================================================

POSITION_IMPACT_POOL_AMOUNT = create_hash_string("POSITION_IMPACT_POOL_AMOUNT")
LENT_POSITION_IMPACT_POOL_AMOUNT = create_hash_string("LENT_POSITION_IMPACT_POOL_AMOUNT")
PENDING_IMPACT_AMOUNT = create_hash_string("PENDING_IMPACT_AMOUNT")
TOTAL_PENDING_IMPACT_AMOUNT = create_hash_string("TOTAL_PENDING_IMPACT_AMOUNT")
MIN_POSITION_IMPACT_POOL_AMOUNT = create_hash_string("MIN_POSITION_IMPACT_POOL_AMOUNT")
POSITION_IMPACT_POOL_DISTRIBUTION_RATE = create_hash_string("POSITION_IMPACT_POOL_DISTRIBUTION_RATE")
POSITION_IMPACT_POOL_DISTRIBUTED_AT = create_hash_string("POSITION_IMPACT_POOL_DISTRIBUTED_AT")
MAX_LENDABLE_IMPACT_FACTOR = create_hash_string("MAX_LENDABLE_IMPACT_FACTOR")
MAX_LENDABLE_IMPACT_FACTOR_FOR_WITHDRAWALS = create_hash_string("MAX_LENDABLE_IMPACT_FACTOR_FOR_WITHDRAWALS")
MAX_LENDABLE_IMPACT_USD = create_hash_string("MAX_LENDABLE_IMPACT_USD")

# ==============================================================================
# Swap Impact
# ==============================================================================

SWAP_IMPACT_POOL_AMOUNT = create_hash_string("SWAP_IMPACT_POOL_AMOUNT")

# ==============================================================================
# Fee Factors
# ==============================================================================

SWAP_FEE_FACTOR = create_hash_string("SWAP_FEE_FACTOR")
DEPOSIT_FEE_FACTOR = create_hash_string("DEPOSIT_FEE_FACTOR")
WITHDRAWAL_FEE_FACTOR = create_hash_string("WITHDRAWAL_FEE_FACTOR")
ATOMIC_SWAP_FEE_FACTOR = create_hash_string("ATOMIC_SWAP_FEE_FACTOR")
ATOMIC_WITHDRAWAL_FEE_FACTOR = create_hash_string("ATOMIC_WITHDRAWAL_FEE_FACTOR")

# ==============================================================================
# Impact Factors
# ==============================================================================

SWAP_IMPACT_FACTOR = create_hash_string("SWAP_IMPACT_FACTOR")
SWAP_IMPACT_EXPONENT_FACTOR = create_hash_string("SWAP_IMPACT_EXPONENT_FACTOR")
POSITION_IMPACT_FACTOR = create_hash_string("POSITION_IMPACT_FACTOR")
POSITION_IMPACT_EXPONENT_FACTOR = create_hash_string("POSITION_IMPACT_EXPONENT_FACTOR")
MAX_POSITION_IMPACT_FACTOR = create_hash_string("MAX_POSITION_IMPACT_FACTOR")
MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS = create_hash_string("MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS")

# ==============================================================================
# Position & Liquidation Fees
# ==============================================================================

POSITION_FEE_FACTOR = create_hash_string("POSITION_FEE_FACTOR")
LIQUIDATION_FEE_FACTOR = create_hash_string("LIQUIDATION_FEE_FACTOR")

# ==============================================================================
# Pro Trader Discounts
# ==============================================================================

PRO_TRADER_TIER = create_hash_string("PRO_TRADER_TIER")
PRO_DISCOUNT_FACTOR = create_hash_string("PRO_DISCOUNT_FACTOR")

# ==============================================================================
# Reserve Factors
# ==============================================================================

RESERVE_FACTOR = create_hash_string("RESERVE_FACTOR")

# ==============================================================================
# PNL Factors
# ==============================================================================

MAX_PNL_FACTOR = create_hash_string("MAX_PNL_FACTOR")
MAX_PNL_FACTOR_FOR_TRADERS = create_hash_string("MAX_PNL_FACTOR_FOR_TRADERS")
MAX_PNL_FACTOR_FOR_ADL = create_hash_string("MAX_PNL_FACTOR_FOR_ADL")
MIN_PNL_FACTOR_AFTER_ADL = create_hash_string("MIN_PNL_FACTOR_AFTER_ADL")
MAX_PNL_FACTOR_FOR_DEPOSITS = create_hash_string("MAX_PNL_FACTOR_FOR_DEPOSITS")
MAX_PNL_FACTOR_FOR_WITHDRAWALS = create_hash_string("MAX_PNL_FACTOR_FOR_WITHDRAWALS")

# ==============================================================================
# ADL (Auto-Deleveraging)
# ==============================================================================

LATEST_ADL_BLOCK = create_hash_string("LATEST_ADL_BLOCK")
IS_ADL_ENABLED = create_hash_string("IS_ADL_ENABLED")

# ==============================================================================
# Funding
# ==============================================================================

FUNDING_FACTOR = create_hash_string("FUNDING_FACTOR")
FUNDING_EXPONENT_FACTOR = create_hash_string("FUNDING_EXPONENT_FACTOR")
SAVED_FUNDING_FACTOR_PER_SECOND = create_hash_string("SAVED_FUNDING_FACTOR_PER_SECOND")
FUNDING_INCREASE_FACTOR_PER_SECOND = create_hash_string("FUNDING_INCREASE_FACTOR_PER_SECOND")
FUNDING_DECREASE_FACTOR_PER_SECOND = create_hash_string("FUNDING_DECREASE_FACTOR_PER_SECOND")
MIN_FUNDING_FACTOR_PER_SECOND = create_hash_string("MIN_FUNDING_FACTOR_PER_SECOND")
MAX_FUNDING_FACTOR_PER_SECOND = create_hash_string("MAX_FUNDING_FACTOR_PER_SECOND")
THRESHOLD_FOR_STABLE_FUNDING = create_hash_string("THRESHOLD_FOR_STABLE_FUNDING")
THRESHOLD_FOR_DECREASE_FUNDING = create_hash_string("THRESHOLD_FOR_DECREASE_FUNDING")
FUNDING_FEE_AMOUNT_PER_SIZE = create_hash_string("FUNDING_FEE_AMOUNT_PER_SIZE")
CLAIMABLE_FUNDING_AMOUNT_PER_SIZE = create_hash_string("CLAIMABLE_FUNDING_AMOUNT_PER_SIZE")
FUNDING_UPDATED_AT = create_hash_string("FUNDING_UPDATED_AT")

# ==============================================================================
# Borrowing
# ==============================================================================

OPTIMAL_USAGE_FACTOR = create_hash_string("OPTIMAL_USAGE_FACTOR")
BASE_BORROWING_FACTOR = create_hash_string("BASE_BORROWING_FACTOR")
ABOVE_OPTIMAL_USAGE_BORROWING_FACTOR = create_hash_string("ABOVE_OPTIMAL_USAGE_BORROWING_FACTOR")
BORROWING_FACTOR = create_hash_string("BORROWING_FACTOR")
BORROWING_EXPONENT_FACTOR = create_hash_string("BORROWING_EXPONENT_FACTOR")
SKIP_BORROWING_FEE_FOR_SMALLER_SIDE = create_hash_string("SKIP_BORROWING_FEE_FOR_SMALLER_SIDE")
USE_OPEN_INTEREST_IN_TOKENS_FOR_BALANCE = create_hash_string("USE_OPEN_INTEREST_IN_TOKENS_FOR_BALANCE")
CUMULATIVE_BORROWING_FACTOR = create_hash_string("CUMULATIVE_BORROWING_FACTOR")
CUMULATIVE_BORROWING_FACTOR_UPDATED_AT = create_hash_string("CUMULATIVE_BORROWING_FACTOR_UPDATED_AT")

# ==============================================================================
# Estimated & Execution Gas Fees
# ==============================================================================

ESTIMATED_GAS_FEE_BASE_AMOUNT_V2_1 = create_hash_string("ESTIMATED_GAS_FEE_BASE_AMOUNT_V2_1")
ESTIMATED_GAS_FEE_PER_ORACLE_PRICE = create_hash_string("ESTIMATED_GAS_FEE_PER_ORACLE_PRICE")
ESTIMATED_GAS_FEE_MULTIPLIER_FACTOR = create_hash_string("ESTIMATED_GAS_FEE_MULTIPLIER_FACTOR")
MAX_EXECUTION_FEE_MULTIPLIER_FACTOR = create_hash_string("MAX_EXECUTION_FEE_MULTIPLIER_FACTOR")
EXECUTION_GAS_FEE_BASE_AMOUNT = create_hash_string("EXECUTION_GAS_FEE_BASE_AMOUNT")
EXECUTION_GAS_FEE_BASE_AMOUNT_V2_1 = create_hash_string("EXECUTION_GAS_FEE_BASE_AMOUNT_V2_1")
EXECUTION_GAS_FEE_PER_ORACLE_PRICE = create_hash_string("EXECUTION_GAS_FEE_PER_ORACLE_PRICE")
EXECUTION_GAS_FEE_MULTIPLIER_FACTOR = create_hash_string("EXECUTION_GAS_FEE_MULTIPLIER_FACTOR")

# ==============================================================================
# Gas Limits
# ==============================================================================

DEPOSIT_GAS_LIMIT = create_hash_string("DEPOSIT_GAS_LIMIT")
CREATE_DEPOSIT_GAS_LIMIT = create_hash_string("CREATE_DEPOSIT_GAS_LIMIT")
CREATE_GLV_DEPOSIT_GAS_LIMIT = create_hash_string("CREATE_GLV_DEPOSIT_GAS_LIMIT")
CREATE_WITHDRAWAL_GAS_LIMIT = create_hash_string("CREATE_WITHDRAWAL_GAS_LIMIT")
CREATE_GLV_WITHDRAWAL_GAS_LIMIT = create_hash_string("CREATE_GLV_WITHDRAWAL_GAS_LIMIT")
WITHDRAWAL_GAS_LIMIT = create_hash_string("WITHDRAWAL_GAS_LIMIT")
SHIFT_GAS_LIMIT = create_hash_string("SHIFT_GAS_LIMIT")
SINGLE_SWAP_GAS_LIMIT = create_hash_string("SINGLE_SWAP_GAS_LIMIT")
INCREASE_ORDER_GAS_LIMIT = create_hash_string("INCREASE_ORDER_GAS_LIMIT")
DECREASE_ORDER_GAS_LIMIT = create_hash_string("DECREASE_ORDER_GAS_LIMIT")
SWAP_ORDER_GAS_LIMIT = create_hash_string("SWAP_ORDER_GAS_LIMIT")
SET_TRADER_REFERRAL_CODE_GAS_LIMIT = create_hash_string("SET_TRADER_REFERRAL_CODE_GAS_LIMIT")
REGISTER_CODE_GAS_LIMIT = create_hash_string("REGISTER_CODE_GAS_LIMIT")
GLV_DEPOSIT_GAS_LIMIT = create_hash_string("GLV_DEPOSIT_GAS_LIMIT")
GLV_WITHDRAWAL_GAS_LIMIT = create_hash_string("GLV_WITHDRAWAL_GAS_LIMIT")
GLV_SHIFT_GAS_LIMIT = create_hash_string("GLV_SHIFT_GAS_LIMIT")
GLV_PER_MARKET_GAS_LIMIT = create_hash_string("GLV_PER_MARKET_GAS_LIMIT")

# ==============================================================================
# Virtual Markets & Tokens
# ==============================================================================

VIRTUAL_TOKEN_ID = create_hash_string("VIRTUAL_TOKEN_ID")
VIRTUAL_MARKET_ID = create_hash_string("VIRTUAL_MARKET_ID")
VIRTUAL_INVENTORY_FOR_SWAPS = create_hash_string("VIRTUAL_INVENTORY_FOR_SWAPS")
VIRTUAL_INVENTORY_FOR_POSITIONS = create_hash_string("VIRTUAL_INVENTORY_FOR_POSITIONS")

# ==============================================================================
# Subaccounts
# ==============================================================================

MAX_ALLOWED_SUBACCOUNT_ACTION_COUNT = create_hash_string("MAX_ALLOWED_SUBACCOUNT_ACTION_COUNT")
SUBACCOUNT_ACTION_COUNT = create_hash_string("SUBACCOUNT_ACTION_COUNT")
SUBACCOUNT_AUTO_TOP_UP_AMOUNT = create_hash_string("SUBACCOUNT_AUTO_TOP_UP_AMOUNT")
SUBACCOUNT_ORDER_ACTION = create_hash_string("SUBACCOUNT_ORDER_ACTION")
SUBACCOUNT_EXPIRES_AT = create_hash_string("SUBACCOUNT_EXPIRES_AT")
SUBACCOUNT_INTEGRATION_ID = create_hash_string("SUBACCOUNT_INTEGRATION_ID")
SUBACCOUNT_INTEGRATION_DISABLED = create_hash_string("SUBACCOUNT_INTEGRATION_DISABLED")

# ==============================================================================
# GLV (GM Liquidity Vault)
# ==============================================================================

GLV_SUPPORTED_MARKET_LIST = create_hash_string("GLV_SUPPORTED_MARKET_LIST")
MIN_GLV_TOKENS_FOR_FIRST_DEPOSIT = create_hash_string("MIN_GLV_TOKENS_FOR_FIRST_DEPOSIT")
GLV_SHIFT_MAX_LOSS_FACTOR = create_hash_string("GLV_SHIFT_MAX_LOSS_FACTOR")
GLV_MAX_MARKET_COUNT = create_hash_string("GLV_MAX_MARKET_COUNT")
GLV_MAX_MARKET_TOKEN_BALANCE_USD = create_hash_string("GLV_MAX_MARKET_TOKEN_BALANCE_USD")
GLV_MAX_MARKET_TOKEN_BALANCE_AMOUNT = create_hash_string("GLV_MAX_MARKET_TOKEN_BALANCE_AMOUNT")
GLV_SHIFT_MIN_INTERVAL = create_hash_string("GLV_SHIFT_MIN_INTERVAL")
IS_GLV_MARKET_DISABLED = create_hash_string("IS_GLV_MARKET_DISABLED")

# ==============================================================================
# Sync Config
# ==============================================================================

SYNC_CONFIG_FEATURE_DISABLED = create_hash_string("SYNC_CONFIG_FEATURE_DISABLED")
SYNC_CONFIG_MARKET_DISABLED = create_hash_string("SYNC_CONFIG_MARKET_DISABLED")
SYNC_CONFIG_PARAMETER_DISABLED = create_hash_string("SYNC_CONFIG_PARAMETER_DISABLED")
SYNC_CONFIG_MARKET_PARAMETER_DISABLED = create_hash_string("SYNC_CONFIG_MARKET_PARAMETER_DISABLED")
SYNC_CONFIG_UPDATE_COMPLETED = create_hash_string("SYNC_CONFIG_UPDATE_COMPLETED")
SYNC_CONFIG_LATEST_UPDATE_ID = create_hash_string("SYNC_CONFIG_LATEST_UPDATE_ID")

# ==============================================================================
# Buyback
# ==============================================================================

BUYBACK_BATCH_AMOUNT = create_hash_string("BUYBACK_BATCH_AMOUNT")
BUYBACK_AVAILABLE_FEE_AMOUNT = create_hash_string("BUYBACK_AVAILABLE_FEE_AMOUNT")
BUYBACK_GMX_FACTOR = create_hash_string("BUYBACK_GMX_FACTOR")
BUYBACK_MAX_PRICE_IMPACT_FACTOR = create_hash_string("BUYBACK_MAX_PRICE_IMPACT_FACTOR")
BUYBACK_MAX_PRICE_AGE = create_hash_string("BUYBACK_MAX_PRICE_AGE")
WITHDRAWABLE_BUYBACK_TOKEN_AMOUNT = create_hash_string("WITHDRAWABLE_BUYBACK_TOKEN_AMOUNT")

# ==============================================================================
# Multichain
# ==============================================================================

MULTICHAIN_BALANCE = create_hash_string("MULTICHAIN_BALANCE")
IS_MULTICHAIN_PROVIDER_ENABLED = create_hash_string("IS_MULTICHAIN_PROVIDER_ENABLED")
IS_MULTICHAIN_ENDPOINT_ENABLED = create_hash_string("IS_MULTICHAIN_ENDPOINT_ENABLED")
IS_RELAY_FEE_EXCLUDED = create_hash_string("IS_RELAY_FEE_EXCLUDED")
IS_SRC_CHAIN_ID_ENABLED = create_hash_string("IS_SRC_CHAIN_ID_ENABLED")
EID_TO_SRC_CHAIN_ID = create_hash_string("EID_TO_SRC_CHAIN_ID")
POSITION_LAST_SRC_CHAIN_ID = create_hash_string("POSITION_LAST_SRC_CHAIN_ID")
MULTICHAIN_READ_CHANNEL = create_hash_string("MULTICHAIN_READ_CHANNEL")
MULTICHAIN_PEERS = create_hash_string("MULTICHAIN_PEERS")
MULTICHAIN_CONFIRMATIONS = create_hash_string("MULTICHAIN_CONFIRMATIONS")
MULTICHAIN_AUTHORIZED_ORIGINATORS = create_hash_string("MULTICHAIN_AUTHORIZED_ORIGINATORS")

# ==============================================================================
# Claim System
# ==============================================================================

CLAIM_TERMS = create_hash_string("CLAIM_TERMS")

# ==============================================================================
# Data Actions
# ==============================================================================

MAX_DATA_LENGTH = create_hash_string("MAX_DATA_LENGTH")
GMX_DATA_ACTION = create_hash_string("GMX_DATA_ACTION")

# ==============================================================================
# Fee Distributor (Complex multi-chain system)
# ==============================================================================

FEE_DISTRIBUTOR_DISTRIBUTION_DAY = create_hash_string("FEE_DISTRIBUTOR_DISTRIBUTION_DAY")
FEE_DISTRIBUTOR_DISTRIBUTION_TIMESTAMP = create_hash_string("FEE_DISTRIBUTOR_DISTRIBUTION_TIMESTAMP")
FEE_DISTRIBUTOR_STATE = create_hash_string("FEE_DISTRIBUTOR_STATE")
FEE_DISTRIBUTOR_MAX_REFERRAL_REWARDS_WNT_USD_AMOUNT = create_hash_string(
    "FEE_DISTRIBUTOR_MAX_REFERRAL_REWARDS_WNT_USD_AMOUNT"
)
FEE_DISTRIBUTOR_MAX_REFERRAL_REWARDS_WNT_USD_FACTOR = create_hash_string(
    "FEE_DISTRIBUTOR_MAX_REFERRAL_REWARDS_WNT_USD_FACTOR"
)
FEE_DISTRIBUTOR_MAX_REFERRAL_REWARDS_ESGMX_AMOUNT = create_hash_string(
    "FEE_DISTRIBUTOR_MAX_REFERRAL_REWARDS_ESGMX_AMOUNT"
)
FEE_DISTRIBUTOR_GMX_PRICE = create_hash_string("FEE_DISTRIBUTOR_GMX_PRICE")
FEE_DISTRIBUTOR_WNT_PRICE = create_hash_string("FEE_DISTRIBUTOR_WNT_PRICE")
FEE_DISTRIBUTOR_MAX_READ_RESPONSE_DELAY = create_hash_string("FEE_DISTRIBUTOR_MAX_READ_RESPONSE_DELAY")
FEE_DISTRIBUTOR_GAS_LIMIT = create_hash_string("FEE_DISTRIBUTOR_GAS_LIMIT")
FEE_DISTRIBUTOR_CHAIN_ID = create_hash_string("FEE_DISTRIBUTOR_CHAIN_ID")
FEE_DISTRIBUTOR_FEE_AMOUNT_GMX = create_hash_string("FEE_DISTRIBUTOR_FEE_AMOUNT_GMX")
FEE_DISTRIBUTOR_TOTAL_FEE_AMOUNT_GMX = create_hash_string("FEE_DISTRIBUTOR_TOTAL_FEE_AMOUNT_GMX")
FEE_DISTRIBUTOR_STAKED_GMX = create_hash_string("FEE_DISTRIBUTOR_STAKED_GMX")
FEE_DISTRIBUTOR_TOTAL_STAKED_GMX = create_hash_string("FEE_DISTRIBUTOR_TOTAL_STAKED_GMX")
FEE_DISTRIBUTOR_BRIDGE_SLIPPAGE_FACTOR = create_hash_string("FEE_DISTRIBUTOR_BRIDGE_SLIPPAGE_FACTOR")
FEE_DISTRIBUTOR_READ_RESPONSE_TIMESTAMP = create_hash_string("FEE_DISTRIBUTOR_READ_RESPONSE_TIMESTAMP")
FEE_DISTRIBUTOR_LAYERZERO_CHAIN_ID = create_hash_string("FEE_DISTRIBUTOR_LAYERZERO_CHAIN_ID")
FEE_DISTRIBUTOR_ADDRESS_INFO = create_hash_string("FEE_DISTRIBUTOR_ADDRESS_INFO")
FEE_DISTRIBUTOR_ADDRESS_INFO_FOR_CHAIN = create_hash_string("FEE_DISTRIBUTOR_ADDRESS_INFO_FOR_CHAIN")
FEE_DISTRIBUTOR_KEEPER_COSTS = create_hash_string("FEE_DISTRIBUTOR_KEEPER_COSTS")
FEE_DISTRIBUTOR_CHAINLINK_FACTOR = create_hash_string("FEE_DISTRIBUTOR_CHAINLINK_FACTOR")
FEE_DISTRIBUTOR_MAX_WNT_AMOUNT_FROM_TREASURY = create_hash_string("FEE_DISTRIBUTOR_MAX_WNT_AMOUNT_FROM_TREASURY")
FEE_DISTRIBUTOR_V1_FEES_WNT_FACTOR = create_hash_string("FEE_DISTRIBUTOR_V1_FEES_WNT_FACTOR")
FEE_DISTRIBUTOR_V2_FEES_WNT_FACTOR = create_hash_string("FEE_DISTRIBUTOR_V2_FEES_WNT_FACTOR")

# ==============================================================================
# Contributors
# ==============================================================================

CONTRIBUTOR_LAST_PAYMENT_AT = create_hash_string("CONTRIBUTOR_LAST_PAYMENT_AT")
CONTRIBUTOR_ACCOUNT_LIST = create_hash_string("CONTRIBUTOR_ACCOUNT_LIST")
CONTRIBUTOR_TOKEN_AMOUNT = create_hash_string("CONTRIBUTOR_TOKEN_AMOUNT")

# ==============================================================================
# Legacy key for backward compatibility
# ==============================================================================

MIN_COLLATERAL_FACTOR_KEY = MIN_COLLATERAL_FACTOR
MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS_KEY = MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS


# ==============================================================================
# Key Generation Functions
# ==============================================================================


def account_deposit_list_key(account: str) -> bytes:
    """Get account deposit list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ACCOUNT_DEPOSIT_LIST, account])


def account_withdrawal_list_key(account: str) -> bytes:
    """Get account withdrawal list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ACCOUNT_WITHDRAWAL_LIST, account])


def account_shift_list_key(account: str) -> bytes:
    """Get account shift list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ACCOUNT_SHIFT_LIST, account])


def account_position_list_key(account: str) -> bytes:
    """Get account position list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ACCOUNT_POSITION_LIST, account])


def account_order_list_key(account: str) -> bytes:
    """Get account order list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ACCOUNT_ORDER_LIST, account])


def account_glv_deposit_list_key(account: str) -> bytes:
    """Get account GLV deposit list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ACCOUNT_GLV_DEPOSIT_LIST, account])


def account_glv_withdrawal_list_key(account: str) -> bytes:
    """Get account GLV withdrawal list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ACCOUNT_GLV_WITHDRAWAL_LIST, account])


def subaccount_list_key(account: str) -> bytes:
    """Get subaccount list key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [SUBACCOUNT_LIST, account])


def auto_cancel_order_list_key(position_key: bytes) -> bytes:
    """Get auto cancel order list key.

    :param position_key: Position key (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "bytes32"], [AUTO_CANCEL_ORDER_LIST, position_key])


def is_market_disabled_key(market: str) -> bytes:
    """Get market disabled status key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [IS_MARKET_DISABLED, market])


def min_market_tokens_for_first_deposit_key(market: str) -> bytes:
    """Get minimum market tokens for first deposit key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MIN_MARKET_TOKENS_FOR_FIRST_DEPOSIT, market])


def create_deposit_feature_disabled_key(contract: str) -> bytes:
    """Get create deposit feature disabled key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [CREATE_DEPOSIT_FEATURE_DISABLED, contract])


def cancel_deposit_feature_disabled_key(contract: str) -> bytes:
    """Get cancel deposit feature disabled key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [CANCEL_DEPOSIT_FEATURE_DISABLED, contract])


def gasless_feature_disabled_key(module: str) -> bytes:
    """Get gasless feature disabled key.

    :param module: Module address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [GASLESS_FEATURE_DISABLED, module])


def jit_feature_disabled_key(contract: str) -> bytes:
    """Get JIT feature disabled key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [JIT_FEATURE_DISABLED, contract])


def execute_deposit_feature_disabled_key(contract: str) -> bytes:
    """Get execute deposit feature disabled key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [EXECUTE_DEPOSIT_FEATURE_DISABLED, contract])


def create_order_feature_disabled_key(contract: str, order_type: int) -> bytes:
    """Get create order feature disabled key.

    :param contract: Contract address
    :param order_type: Order type (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "uint256"], [CREATE_ORDER_FEATURE_DISABLED, contract, order_type])


def execute_order_feature_disabled_key(contract: str, order_type: int) -> bytes:
    """Get execute order feature disabled key.

    :param contract: Contract address
    :param order_type: Order type (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "uint256"], [EXECUTE_ORDER_FEATURE_DISABLED, contract, order_type])


def execute_adl_feature_disabled_key(contract: str, order_type: int) -> bytes:
    """Get execute ADL feature disabled key.

    :param contract: Contract address
    :param order_type: Order type (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "uint256"], [EXECUTE_ADL_FEATURE_DISABLED, contract, order_type])


def update_order_feature_disabled_key(contract: str, order_type: int) -> bytes:
    """Get update order feature disabled key.

    :param contract: Contract address
    :param order_type: Order type (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "uint256"], [UPDATE_ORDER_FEATURE_DISABLED, contract, order_type])


def cancel_order_feature_disabled_key(contract: str, order_type: int) -> bytes:
    """Get cancel order feature disabled key.

    :param contract: Contract address
    :param order_type: Order type (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "uint256"], [CANCEL_ORDER_FEATURE_DISABLED, contract, order_type])


def general_claim_feature_disabled_key(distribution_id: int) -> bytes:
    """Get general claim feature disabled key.

    :param distribution_id: Distribution ID (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [GENERAL_CLAIM_FEATURE_DISABLED, distribution_id])


def claimable_fee_amount_key(market: str, token: str) -> bytes:
    """Get claimable fee amount key.

    :param market: Market address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [CLAIMABLE_FEE_AMOUNT, market, token])


def claimable_funding_amount_key(market: str, token: str, account: str) -> bytes:
    """Get claimable funding amount key.

    :param market: Market address
    :param token: Token address
    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address", "address"], [CLAIMABLE_FUNDING_AMOUNT, market, token, account])


def claimable_collateral_amount_key(market: str, token: str, time_key: int, account: str) -> bytes:
    """Get claimable collateral amount key.

    :param market: Market address
    :param token: Token address
    :param time_key: Time key (uint256)
    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "uint256", "address"],
        [CLAIMABLE_COLLATERAL_AMOUNT, market, token, time_key, account],
    )


def claimable_collateral_factor_key(market: str, token: str, time_key: int) -> bytes:
    """Get claimable collateral factor key.

    :param market: Market address
    :param token: Token address
    :param time_key: Time key (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "uint256"], [CLAIMABLE_COLLATERAL_FACTOR, market, token, time_key]
    )


def claimable_collateral_factor_for_account_key(market: str, token: str, time_key: int, account: str) -> bytes:
    """Get claimable collateral factor for account key.

    :param market: Market address
    :param token: Token address
    :param time_key: Time key (uint256)
    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "uint256", "address"],
        [CLAIMABLE_COLLATERAL_FACTOR, market, token, time_key, account],
    )


def claimable_collateral_reduction_factor_for_account_key(market: str, token: str, time_key: int, account: str) -> bytes:
    """Get claimable collateral reduction factor for account key.

    :param market: Market address
    :param token: Token address
    :param time_key: Time key (uint256)
    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "uint256", "address"],
        [CLAIMABLE_COLLATERAL_REDUCTION_FACTOR, market, token, time_key, account],
    )


def claimable_ui_fee_amount_key(market: str, token: str, ui_fee_receiver: str) -> bytes:
    """Get claimable UI fee amount key.

    :param market: Market address
    :param token: Token address
    :param ui_fee_receiver: UI fee receiver address
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "address"], [CLAIMABLE_UI_FEE_AMOUNT, market, token, ui_fee_receiver]
    )


def affiliate_reward_key(market: str, token: str, account: str) -> bytes:
    """Get affiliate reward key.

    :param market: Market address
    :param token: Token address
    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address", "address"], [AFFILIATE_REWARD, market, token, account])


def min_affiliate_reward_factor_key(referral_tier_level: int) -> bytes:
    """Get minimum affiliate reward factor key.

    :param referral_tier_level: Referral tier level (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [MIN_AFFILIATE_REWARD_FACTOR, referral_tier_level])


def token_transfer_gas_limit_key(token: str) -> bytes:
    """Get token transfer gas limit key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [TOKEN_TRANSFER_GAS_LIMIT, token])


def price_feed_key(token: str) -> bytes:
    """Get price feed key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [PRICE_FEED, token])


def price_feed_multiplier_key(token: str) -> bytes:
    """Get price feed multiplier key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [PRICE_FEED_MULTIPLIER, token])


def price_feed_heartbeat_duration_key(token: str) -> bytes:
    """Get price feed heartbeat duration key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [PRICE_FEED_HEARTBEAT_DURATION, token])


def data_stream_id_key(token: str) -> bytes:
    """Get data stream ID key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [DATA_STREAM_ID, token])


def edge_data_stream_id_key(token: str) -> bytes:
    """Get edge data stream ID key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [EDGE_DATA_STREAM_ID, token])


def edge_data_stream_token_decimals_key(token: str) -> bytes:
    """Get edge data stream token decimals key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [EDGE_DATA_STREAM_TOKEN_DECIMALS, token])


def data_stream_multiplier_key(token: str) -> bytes:
    """Get data stream multiplier key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [DATA_STREAM_MULTIPLIER, token])


def data_stream_spread_reduction_factor_key(token: str) -> bytes:
    """Get data stream spread reduction factor key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [DATA_STREAM_SPREAD_REDUCTION_FACTOR, token])


def stable_price_key(token: str) -> bytes:
    """Get stable price key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [STABLE_PRICE, token])


def oracle_type_key(token: str) -> bytes:
    """Get oracle type key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ORACLE_TYPE, token])


def oracle_timestamp_adjustment_key(provider: str, token: str) -> bytes:
    """Get oracle timestamp adjustment key.

    :param provider: Provider address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [ORACLE_TIMESTAMP_ADJUSTMENT, provider, token])


def oracle_provider_for_token_key(oracle: str, token: str) -> bytes:
    """Get oracle provider for token key.

    :param oracle: Oracle address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [ORACLE_PROVIDER_FOR_TOKEN, oracle, token])


def oracle_provider_updated_at_key(token: str, provider: str) -> bytes:
    """Get oracle provider updated at key.

    :param token: Token address
    :param provider: Provider address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [ORACLE_PROVIDER_UPDATED_AT, token, provider])


def open_interest_key(market: str, collateral_token: str, is_long: bool) -> bytes:
    """Get open interest key.

    :param market: Market address
    :param collateral_token: Collateral token address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address", "bool"], [OPEN_INTEREST, market, collateral_token, is_long])


def open_interest_in_tokens_key(market: str, collateral_token: str, is_long: bool) -> bytes:
    """Get open interest in tokens key.

    :param market: Market address
    :param collateral_token: Collateral token address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "bool"], [OPEN_INTEREST_IN_TOKENS, market, collateral_token, is_long]
    )


def is_oracle_provider_enabled_key(provider: str) -> bytes:
    """Get oracle provider enabled status key.

    :param provider: Provider address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [IS_ORACLE_PROVIDER_ENABLED, provider])


def is_atomic_oracle_provider_key(provider: str) -> bytes:
    """Get atomic oracle provider status key.

    :param provider: Provider address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [IS_ATOMIC_ORACLE_PROVIDER, provider])


def min_collateral_factor_key(market: str) -> bytes:
    """Get minimum collateral factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MIN_COLLATERAL_FACTOR, market])


def min_collateral_factor_for_liquidation_key(market: str) -> bytes:
    """Get minimum collateral factor for liquidation key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MIN_COLLATERAL_FACTOR_FOR_LIQUIDATION, market])


def min_collateral_factor_for_open_interest_multiplier_key(market: str, is_long: bool) -> bytes:
    """Get minimum collateral factor for open interest multiplier key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "bool"], [MIN_COLLATERAL_FACTOR_FOR_OPEN_INTEREST_MULTIPLIER, market, is_long]
    )


def reserve_factor_key(market: str, is_long: bool) -> bytes:
    """Get reserve factor key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [RESERVE_FACTOR, market, is_long])


def open_interest_reserve_factor_key(market: str, is_long: bool) -> bytes:
    """Get open interest reserve factor key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [OPEN_INTEREST_RESERVE_FACTOR, market, is_long])


def max_pnl_factor_key(pnl_factor_type: bytes, market: str, is_long: bool) -> bytes:
    """Get maximum PNL factor key.

    :param pnl_factor_type: PNL factor type (bytes32)
    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "bytes32", "address", "bool"], [MAX_PNL_FACTOR, pnl_factor_type, market, is_long])


def min_pnl_factor_after_adl_key(market: str, is_long: bool) -> bytes:
    """Get minimum PNL factor after ADL key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [MIN_PNL_FACTOR_AFTER_ADL, market, is_long])


def collateral_sum_key(market: str, collateral_token: str, is_long: bool) -> bytes:
    """Get collateral sum key.

    :param market: Market address
    :param collateral_token: Collateral token address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address", "bool"], [COLLATERAL_SUM, market, collateral_token, is_long])


def pool_amount_key(market: str, token: str) -> bytes:
    """Get pool amount key.

    :param market: Market address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [POOL_AMOUNT, market, token])


def max_pool_amount_key(market: str, token: str) -> bytes:
    """Get maximum pool amount key.

    :param market: Market address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [MAX_POOL_AMOUNT, market, token])


def max_pool_usd_for_deposit_key(market: str, token: str) -> bytes:
    """Get maximum pool USD for deposit key.

    :param market: Market address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [MAX_POOL_USD_FOR_DEPOSIT, market, token])


def max_open_interest_key(market: str, is_long: bool) -> bytes:
    """Get maximum open interest key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [MAX_OPEN_INTEREST, market, is_long])


def position_impact_pool_amount_key(market: str) -> bytes:
    """Get position impact pool amount key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [POSITION_IMPACT_POOL_AMOUNT, market])


def lent_position_impact_pool_amount_key(market: str) -> bytes:
    """Get lent position impact pool amount key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [LENT_POSITION_IMPACT_POOL_AMOUNT, market])


def total_pending_impact_amount_key(market: str) -> bytes:
    """Get total pending impact amount key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [TOTAL_PENDING_IMPACT_AMOUNT, market])


def min_position_impact_pool_amount_key(market: str) -> bytes:
    """Get minimum position impact pool amount key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MIN_POSITION_IMPACT_POOL_AMOUNT, market])


def position_impact_pool_distribution_rate_key(market: str) -> bytes:
    """Get position impact pool distribution rate key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [POSITION_IMPACT_POOL_DISTRIBUTION_RATE, market])


def position_impact_pool_distributed_at_key(market: str) -> bytes:
    """Get position impact pool distributed at key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [POSITION_IMPACT_POOL_DISTRIBUTED_AT, market])


def max_lendable_impact_factor_key(market: str) -> bytes:
    """Get maximum lendable impact factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MAX_LENDABLE_IMPACT_FACTOR, market])


def max_lendable_impact_factor_for_withdrawals_key(market: str) -> bytes:
    """Get maximum lendable impact factor for withdrawals key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MAX_LENDABLE_IMPACT_FACTOR_FOR_WITHDRAWALS, market])


def max_lendable_impact_usd_key(market: str) -> bytes:
    """Get maximum lendable impact USD key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MAX_LENDABLE_IMPACT_USD, market])


def swap_impact_pool_amount_key(market: str, token: str) -> bytes:
    """Get swap impact pool amount key.

    :param market: Market address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [SWAP_IMPACT_POOL_AMOUNT, market, token])


def swap_fee_factor_key(market: str, balance_was_improved: bool) -> bytes:
    """Get swap fee factor key.

    :param market: Market address
    :param balance_was_improved: Whether balance was improved
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [SWAP_FEE_FACTOR, market, balance_was_improved])


def deposit_fee_factor_key(market: str, balance_was_improved: bool) -> bytes:
    """Get deposit fee factor key.

    :param market: Market address
    :param balance_was_improved: Whether balance was improved
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [DEPOSIT_FEE_FACTOR, market, balance_was_improved])


def withdrawal_fee_factor_key(market: str, balance_was_improved: bool) -> bytes:
    """Get withdrawal fee factor key.

    :param market: Market address
    :param balance_was_improved: Whether balance was improved
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [WITHDRAWAL_FEE_FACTOR, market, balance_was_improved])


def atomic_swap_fee_factor_key(market: str) -> bytes:
    """Get atomic swap fee factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ATOMIC_SWAP_FEE_FACTOR, market])


def atomic_withdrawal_fee_factor_key(market: str) -> bytes:
    """Get atomic withdrawal fee factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [ATOMIC_WITHDRAWAL_FEE_FACTOR, market])


def swap_impact_factor_key(market: str, is_positive: bool) -> bytes:
    """Get swap impact factor key.

    :param market: Market address
    :param is_positive: Is positive impact
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [SWAP_IMPACT_FACTOR, market, is_positive])


def swap_impact_exponent_factor_key(market: str) -> bytes:
    """Get swap impact exponent factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [SWAP_IMPACT_EXPONENT_FACTOR, market])


def position_impact_factor_key(market: str, is_positive: bool) -> bytes:
    """Get position impact factor key.

    :param market: Market address
    :param is_positive: Is positive impact
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [POSITION_IMPACT_FACTOR, market, is_positive])


def position_impact_exponent_factor_key(market: str, is_positive: bool) -> bytes:
    """Get position impact exponent factor key.

    :param market: Market address
    :param is_positive: Is positive impact
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [POSITION_IMPACT_EXPONENT_FACTOR, market, is_positive])


def max_position_impact_factor_key(market: str, is_positive: bool) -> bytes:
    """Get maximum position impact factor key.

    :param market: Market address
    :param is_positive: Is positive impact
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [MAX_POSITION_IMPACT_FACTOR, market, is_positive])


def max_position_impact_factor_for_liquidations_key(market: str) -> bytes:
    """Get maximum position impact factor for liquidations key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS, market])


def position_fee_factor_key(market: str, balance_was_improved: bool) -> bytes:
    """Get position fee factor key.

    :param market: Market address
    :param balance_was_improved: Whether balance was improved
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [POSITION_FEE_FACTOR, market, balance_was_improved])


def pro_trader_tier_key(account: str) -> bytes:
    """Get pro trader tier key.

    :param account: Account address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [PRO_TRADER_TIER, account])


def pro_discount_factor_key(pro_tier: int) -> bytes:
    """Get pro discount factor key.

    :param pro_tier: Pro tier (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [PRO_DISCOUNT_FACTOR, pro_tier])


def liquidation_fee_factor_key(market: str) -> bytes:
    """Get liquidation fee factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [LIQUIDATION_FEE_FACTOR, market])


def latest_adl_block_key(market: str, is_long: bool) -> bytes:
    """Get latest ADL block key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [LATEST_ADL_BLOCK, market, is_long])


def is_adl_enabled_key(market: str, is_long: bool) -> bytes:
    """Get ADL enabled status key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [IS_ADL_ENABLED, market, is_long])


def funding_factor_key(market: str) -> bytes:
    """Get funding factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [FUNDING_FACTOR, market])


def funding_exponent_factor_key(market: str) -> bytes:
    """Get funding exponent factor key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [FUNDING_EXPONENT_FACTOR, market])


def saved_funding_factor_per_second_key(market: str) -> bytes:
    """Get saved funding factor per second key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [SAVED_FUNDING_FACTOR_PER_SECOND, market])


def funding_increase_factor_per_second_key(market: str) -> bytes:
    """Get funding increase factor per second key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [FUNDING_INCREASE_FACTOR_PER_SECOND, market])


def funding_decrease_factor_per_second_key(market: str) -> bytes:
    """Get funding decrease factor per second key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [FUNDING_DECREASE_FACTOR_PER_SECOND, market])


def min_funding_factor_per_second_key(market: str) -> bytes:
    """Get minimum funding factor per second key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MIN_FUNDING_FACTOR_PER_SECOND, market])


def max_funding_factor_per_second_key(market: str) -> bytes:
    """Get maximum funding factor per second key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MAX_FUNDING_FACTOR_PER_SECOND, market])


def threshold_for_stable_funding_key(market: str) -> bytes:
    """Get threshold for stable funding key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [THRESHOLD_FOR_STABLE_FUNDING, market])


def threshold_for_decrease_funding_key(market: str) -> bytes:
    """Get threshold for decrease funding key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [THRESHOLD_FOR_DECREASE_FUNDING, market])


def funding_fee_amount_per_size_key(market: str, collateral_token: str, is_long: bool) -> bytes:
    """Get funding fee amount per size key.

    :param market: Market address
    :param collateral_token: Collateral token address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "bool"], [FUNDING_FEE_AMOUNT_PER_SIZE, market, collateral_token, is_long]
    )


def claimable_funding_amount_per_size_key(market: str, collateral_token: str, is_long: bool) -> bytes:
    """Get claimable funding amount per size key.

    :param market: Market address
    :param collateral_token: Collateral token address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "bool"], [CLAIMABLE_FUNDING_AMOUNT_PER_SIZE, market, collateral_token, is_long]
    )


def funding_updated_at_key(market: str) -> bytes:
    """Get funding updated at key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [FUNDING_UPDATED_AT, market])


def borrowing_factor_key(market: str, is_long: bool) -> bytes:
    """Get borrowing factor key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [BORROWING_FACTOR, market, is_long])


def borrowing_exponent_factor_key(market: str, is_long: bool) -> bytes:
    """Get borrowing exponent factor key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [BORROWING_EXPONENT_FACTOR, market, is_long])


def cumulative_borrowing_factor_key(market: str, is_long: bool) -> bytes:
    """Get cumulative borrowing factor key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [CUMULATIVE_BORROWING_FACTOR, market, is_long])


def cumulative_borrowing_factor_updated_at_key(market: str, is_long: bool) -> bytes:
    """Get cumulative borrowing factor updated at key.

    :param market: Market address
    :param is_long: Is long position
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "bool"], [CUMULATIVE_BORROWING_FACTOR_UPDATED_AT, market, is_long])


def virtual_token_id_key(token: str) -> bytes:
    """Get virtual token ID key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [VIRTUAL_TOKEN_ID, token])


def virtual_market_id_key(market: str) -> bytes:
    """Get virtual market ID key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [VIRTUAL_MARKET_ID, market])


def virtual_inventory_for_swaps_key(virtual_market_id: bytes, is_long_token: bool) -> bytes:
    """Get virtual inventory for swaps key.

    :param virtual_market_id: Virtual market ID (bytes32)
    :param is_long_token: Is long token
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "bytes32", "bool"], [VIRTUAL_INVENTORY_FOR_SWAPS, virtual_market_id, is_long_token])


def virtual_inventory_for_positions_key(virtual_token_id: bytes) -> bytes:
    """Get virtual inventory for positions key.

    :param virtual_token_id: Virtual token ID (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "bytes32"], [VIRTUAL_INVENTORY_FOR_POSITIONS, virtual_token_id])


def max_allowed_subaccount_action_count_key(account: str, subaccount: str, action_type: bytes) -> bytes:
    """Get maximum allowed subaccount action count key.

    :param account: Account address
    :param subaccount: Subaccount address
    :param action_type: Action type (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "bytes32"], [MAX_ALLOWED_SUBACCOUNT_ACTION_COUNT, account, subaccount, action_type]
    )


def subaccount_expires_at_key(account: str, subaccount: str, action_type: bytes) -> bytes:
    """Get subaccount expires at key.

    :param account: Account address
    :param subaccount: Subaccount address
    :param action_type: Action type (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "bytes32"], [SUBACCOUNT_EXPIRES_AT, account, subaccount, action_type]
    )


def subaccount_action_count_key(account: str, subaccount: str, action_type: bytes) -> bytes:
    """Get subaccount action count key.

    :param account: Account address
    :param subaccount: Subaccount address
    :param action_type: Action type (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "address", "address", "bytes32"], [SUBACCOUNT_ACTION_COUNT, account, subaccount, action_type]
    )


def subaccount_auto_top_up_amount_key(account: str, subaccount: str) -> bytes:
    """Get subaccount auto top up amount key.

    :param account: Account address
    :param subaccount: Subaccount address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [SUBACCOUNT_AUTO_TOP_UP_AMOUNT, account, subaccount])


def glv_supported_market_list_key(glv: str) -> bytes:
    """Get GLV supported market list key.

    :param glv: GLV address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [GLV_SUPPORTED_MARKET_LIST, glv])


def min_glv_tokens_for_first_glv_deposit_key(glv: str) -> bytes:
    """Get minimum GLV tokens for first GLV deposit key.

    :param glv: GLV address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MIN_GLV_TOKENS_FOR_FIRST_DEPOSIT, glv])


def glv_max_market_token_balance_usd_key(glv: str, market: str) -> bytes:
    """Get GLV maximum market token balance USD key.

    :param glv: GLV address
    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [GLV_MAX_MARKET_TOKEN_BALANCE_USD, glv, market])


def glv_max_market_token_balance_amount_key(glv: str, market: str) -> bytes:
    """Get GLV maximum market token balance amount key.

    :param glv: GLV address
    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [GLV_MAX_MARKET_TOKEN_BALANCE_AMOUNT, glv, market])


def glv_shift_min_interval_key(glv: str) -> bytes:
    """Get GLV shift minimum interval key.

    :param glv: GLV address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [GLV_SHIFT_MIN_INTERVAL, glv])


def glv_shift_max_loss_factor_key(glv: str) -> bytes:
    """Get GLV shift maximum loss factor key.

    :param glv: GLV address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [GLV_SHIFT_MAX_LOSS_FACTOR, glv])


def is_glv_market_disabled_key(glv: str, market: str) -> bytes:
    """Get GLV market disabled status key.

    :param glv: GLV address
    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [IS_GLV_MARKET_DISABLED, glv, market])


def sync_config_feature_disabled_key(contract: str) -> bytes:
    """Get sync config feature disabled key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [SYNC_CONFIG_FEATURE_DISABLED, contract])


def sync_config_market_disabled_key(market: str) -> bytes:
    """Get sync config market disabled key.

    :param market: Market address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [SYNC_CONFIG_MARKET_DISABLED, market])


def sync_config_parameter_disabled_key(parameter: str) -> bytes:
    """Get sync config parameter disabled key.

    :param parameter: Parameter name (string)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "string"], [SYNC_CONFIG_PARAMETER_DISABLED, parameter])


def sync_config_market_parameter_disabled_key(market: str, parameter: str) -> bytes:
    """Get sync config market parameter disabled key.

    :param market: Market address
    :param parameter: Parameter name (string)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "string"], [SYNC_CONFIG_MARKET_PARAMETER_DISABLED, market, parameter])


def sync_config_update_completed_key(update_id: int) -> bytes:
    """Get sync config update completed key.

    :param update_id: Update ID (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [SYNC_CONFIG_UPDATE_COMPLETED, update_id])


def sync_config_latest_update_id_key() -> bytes:
    """Get sync config latest update ID key.

    :return: Sync config latest update ID constant
    """
    return SYNC_CONFIG_LATEST_UPDATE_ID


def buyback_batch_amount_key(token: str) -> bytes:
    """Get buyback batch amount key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [BUYBACK_BATCH_AMOUNT, token])


def buyback_available_fee_amount_key(fee_token: str, swap_token: str) -> bytes:
    """Get buyback available fee amount key.

    :param fee_token: Fee token address
    :param swap_token: Swap token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [BUYBACK_AVAILABLE_FEE_AMOUNT, fee_token, swap_token])


def buyback_gmx_factor_key(version: int) -> bytes:
    """Get buyback GMX factor key.

    :param version: Version (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [BUYBACK_GMX_FACTOR, version])


def buyback_max_price_impact_factor_key(token: str) -> bytes:
    """Get buyback maximum price impact factor key.

    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [BUYBACK_MAX_PRICE_IMPACT_FACTOR, token])


def withdrawable_buyback_token_amount_key(buyback_token: str) -> bytes:
    """Get withdrawable buyback token amount key.

    :param buyback_token: Buyback token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [WITHDRAWABLE_BUYBACK_TOKEN_AMOUNT, buyback_token])


def is_multichain_provider_enabled_key(contract: str) -> bytes:
    """Get multichain provider enabled status key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [IS_MULTICHAIN_PROVIDER_ENABLED, contract])


def is_multichain_endpoint_enabled_key(contract: str) -> bytes:
    """Get multichain endpoint enabled status key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [IS_MULTICHAIN_ENDPOINT_ENABLED, contract])


def is_relay_fee_excluded_key(contract: str) -> bytes:
    """Get relay fee excluded status key.

    :param contract: Contract address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [IS_RELAY_FEE_EXCLUDED, contract])


def is_src_chain_id_enabled_key(src_chain_id: int) -> bytes:
    """Get source chain ID enabled status key.

    :param src_chain_id: Source chain ID (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [IS_SRC_CHAIN_ID_ENABLED, src_chain_id])


def eid_to_src_chain_id_key(eid: int) -> bytes:
    """Get EID to source chain ID key.

    :param eid: EID (uint32)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint32"], [EID_TO_SRC_CHAIN_ID, eid])


def position_last_src_chain_id_key(position_key: bytes) -> bytes:
    """Get position last source chain ID key.

    :param position_key: Position key (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "bytes32"], [POSITION_LAST_SRC_CHAIN_ID, position_key])


def claim_terms_key(distribution_id: int) -> bytes:
    """Get claim terms key.

    :param distribution_id: Distribution ID (uint256 or string)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [CLAIM_TERMS, distribution_id])


def multichain_balance_key(account: str, token: str) -> bytes:
    """Get multichain balance key.

    :param account: Account address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [MULTICHAIN_BALANCE, account, token])


def multichain_peers_key(read_channel: str) -> bytes:
    """Get multichain peers key.

    :param read_channel: Read channel (uint32)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint32"], [MULTICHAIN_PEERS, read_channel])


def multichain_confirmations_key(eid: str) -> bytes:
    """Get multichain confirmations key.

    :param eid: EID (uint32)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint32"], [MULTICHAIN_CONFIRMATIONS, eid])


def multichain_authorized_originators_key(originator: str) -> bytes:
    """Get multichain authorized originators key.

    :param originator: Originator address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address"], [MULTICHAIN_AUTHORIZED_ORIGINATORS, originator])


def fee_distributor_fee_amount_gmx_key(chain_id: int) -> bytes:
    """Get fee distributor fee amount GMX key.

    :param chain_id: Chain ID (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [FEE_DISTRIBUTOR_FEE_AMOUNT_GMX, chain_id])


def fee_distributor_staked_gmx_key(chain_id: int) -> bytes:
    """Get fee distributor staked GMX key.

    :param chain_id: Chain ID (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [FEE_DISTRIBUTOR_STAKED_GMX, chain_id])


def fee_distributor_bridge_slippage_factor_key(chain_id: int) -> bytes:
    """Get fee distributor bridge slippage factor key.

    :param chain_id: Chain ID (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [FEE_DISTRIBUTOR_BRIDGE_SLIPPAGE_FACTOR, chain_id])


def fee_distributor_layerzero_chain_id_key(chain_id: int) -> bytes:
    """Get fee distributor LayerZero chain ID key.

    :param chain_id: Chain ID (uint256)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256"], [FEE_DISTRIBUTOR_LAYERZERO_CHAIN_ID, chain_id])


def fee_distributor_address_info_key(chain_id: int, address_name: str) -> bytes:
    """Get fee distributor address info key.

    :param chain_id: Chain ID (uint256)
    :param address_name: Address name (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "uint256", "bytes32"], [FEE_DISTRIBUTOR_ADDRESS_INFO, address_name])


def fee_distributor_address_info_for_chain_key(chain_id: int, address_name: str) -> bytes:
    """Get fee distributor address info for chain key.

    :param chain_id: Chain ID (uint256)
    :param address_name: Address name (bytes32)
    :return: Keccak-256 hashed key
    """
    return create_hash(
        ["bytes32", "uint256", "bytes32"], [FEE_DISTRIBUTOR_ADDRESS_INFO_FOR_CHAIN, chain_id, address_name]
    )


def contributor_token_amount_key(account: str, token: str) -> bytes:
    """Get contributor token amount key.

    :param account: Account address
    :param token: Token address
    :return: Keccak-256 hashed key
    """
    return create_hash(["bytes32", "address", "address"], [CONTRIBUTOR_TOKEN_AMOUNT, account, token])


# ==============================================================================
# Gas Limit Getters (Simple Accessors)
# ==============================================================================


def deposit_gas_limit_key() -> bytes:
    """Get deposit gas limit key.

    :return: Deposit gas limit constant
    """
    return DEPOSIT_GAS_LIMIT


def withdrawal_gas_limit_key() -> bytes:
    """Get withdrawal gas limit key.

    :return: Withdrawal gas limit constant
    """
    return WITHDRAWAL_GAS_LIMIT


def shift_gas_limit_key() -> bytes:
    """Get shift gas limit key.

    :return: Shift gas limit constant
    """
    return SHIFT_GAS_LIMIT


def single_swap_gas_limit_key() -> bytes:
    """Get single swap gas limit key.

    :return: Single swap gas limit constant
    """
    return SINGLE_SWAP_GAS_LIMIT


def increase_order_gas_limit_key() -> bytes:
    """Get increase order gas limit key.

    :return: Increase order gas limit constant
    """
    return INCREASE_ORDER_GAS_LIMIT


def decrease_order_gas_limit_key() -> bytes:
    """Get decrease order gas limit key.

    :return: Decrease order gas limit constant
    """
    return DECREASE_ORDER_GAS_LIMIT


def swap_order_gas_limit_key() -> bytes:
    """Get swap order gas limit key.

    :return: Swap order gas limit constant
    """
    return SWAP_ORDER_GAS_LIMIT


def glv_deposit_gas_limit_key() -> bytes:
    """Get GLV deposit gas limit key.

    :return: GLV deposit gas limit constant
    """
    return GLV_DEPOSIT_GAS_LIMIT


def glv_withdrawal_gas_limit_key() -> bytes:
    """Get GLV withdrawal gas limit key.

    :return: GLV withdrawal gas limit constant
    """
    return GLV_WITHDRAWAL_GAS_LIMIT


def glv_shift_gas_limit_key() -> bytes:
    """Get GLV shift gas limit key.

    :return: GLV shift gas limit constant
    """
    return GLV_SHIFT_GAS_LIMIT


def glv_per_market_gas_limit_key() -> bytes:
    """Get GLV per market gas limit key.

    :return: GLV per market gas limit constant
    """
    return GLV_PER_MARKET_GAS_LIMIT


def execution_gas_fee_base_amount_key() -> bytes:
    """Get execution gas fee base amount key.

    :return: Execution gas fee base amount constant
    """
    return EXECUTION_GAS_FEE_BASE_AMOUNT


def execution_gas_fee_multiplier_key() -> bytes:
    """Get execution gas fee multiplier key.

    :return: Execution gas fee multiplier factor constant
    """
    return EXECUTION_GAS_FEE_MULTIPLIER_FACTOR


def min_additional_gas_for_execution_key() -> bytes:
    """Get minimum additional gas for execution key.

    :return: Minimum additional gas for execution constant
    """
    return MIN_ADDITIONAL_GAS_FOR_EXECUTION


def min_collateral() -> bytes:
    """Get minimum collateral key.

    :return: Minimum collateral USD constant
    """
    return MIN_COLLATERAL_USD


# ==============================================================================
# Legacy Function Names (for backward compatibility)
# ==============================================================================

# Maintain camelCase function names for backward compatibility
accountPositionListKey = account_position_list_key
virtualTokenIdKey = virtual_token_id_key
withdraw_gas_limit_key = withdrawal_gas_limit_key


if __name__ == "__main__":
    # Example usage
    token = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    hash_data = virtual_token_id_key(token)
    print(f"Virtual token ID key: {hash_data.hex()}")
