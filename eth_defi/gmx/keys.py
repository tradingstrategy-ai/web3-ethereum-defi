"""
GMX Protocol Key Generation Functions

This module provides key generation functions for GMX protocol data store keys.
These functions generate the proper keccak hashes used for accessing data
from the GMX datastore contract.
"""

from eth_abi import encode
from eth_utils import keccak


def apply_factor(value, factor):
    """Apply a 30-decimal factor to a value.

    GMX uses 30-decimal fixed-point arithmetic for many values.
    This function applies a factor stored as an integer with 30 decimals.

    Parameters
    ----------
    value : numeric
        Value to apply factor to
    factor : int
        Factor in 30-decimal format (e.g., 10^30 = 1.0)

    Returns
    -------
    numeric
        Result of (value * factor) / 10^30
    """
    return value * factor / 10**30


def create_hash(data_type_list: list, data_value_list: list) -> bytes:
    """Create a keccak hash using ABI encoding.

    Parameters
    ----------
    data_type_list : list
        List of data types as strings (e.g., ["string", "address"])
    data_value_list : list
        List of values matching the data types

    Returns
    -------
    bytes
        Keccak-256 hashed key
    """
    byte_data = encode(data_type_list, data_value_list)
    return keccak(byte_data)


def create_hash_string(string: str) -> bytes:
    """Hash a string value using keccak-256.

    Parameters
    ----------
    string : str
        String to hash

    Returns
    -------
    bytes
        Keccak-256 hashed string
    """
    return create_hash(["string"], [string])


# Protocol key constants
ACCOUNT_POSITION_LIST = create_hash_string("ACCOUNT_POSITION_LIST")
CLAIMABLE_FEE_AMOUNT = create_hash_string("CLAIMABLE_FEE_AMOUNT")
DECREASE_ORDER_GAS_LIMIT = create_hash_string("DECREASE_ORDER_GAS_LIMIT")
DEPOSIT_GAS_LIMIT = create_hash_string("DEPOSIT_GAS_LIMIT")
WITHDRAWAL_GAS_LIMIT = create_hash_string("WITHDRAWAL_GAS_LIMIT")
EXECUTION_GAS_FEE_BASE_AMOUNT = create_hash_string("EXECUTION_GAS_FEE_BASE_AMOUNT")
EXECUTION_GAS_FEE_MULTIPLIER_FACTOR = create_hash_string("EXECUTION_GAS_FEE_MULTIPLIER_FACTOR")
INCREASE_ORDER_GAS_LIMIT = create_hash_string("INCREASE_ORDER_GAS_LIMIT")
MAX_OPEN_INTEREST = create_hash_string("MAX_OPEN_INTEREST")
MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS_KEY = create_hash_string("MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS")
MAX_PNL_FACTOR_FOR_TRADERS = create_hash_string("MAX_PNL_FACTOR_FOR_TRADERS")
MAX_PNL_FACTOR_FOR_DEPOSITS = create_hash_string("MAX_PNL_FACTOR_FOR_DEPOSITS")
MAX_PNL_FACTOR_FOR_WITHDRAWALS = create_hash_string("MAX_PNL_FACTOR_FOR_WITHDRAWALS")
MIN_ADDITIONAL_GAS_FOR_EXECUTION = create_hash_string("MIN_ADDITIONAL_GAS_FOR_EXECUTION")
MIN_COLLATERAL_USD = create_hash_string("MIN_COLLATERAL_USD")
MIN_COLLATERAL_FACTOR_KEY = create_hash_string("MIN_COLLATERAL_FACTOR")
MIN_POSITION_SIZE_USD = create_hash_string("MIN_POSITION_SIZE_USD")
OPEN_INTEREST_IN_TOKENS = create_hash_string("OPEN_INTEREST_IN_TOKENS")
OPEN_INTEREST = create_hash_string("OPEN_INTEREST")
OPEN_INTEREST_RESERVE_FACTOR = create_hash_string("OPEN_INTEREST_RESERVE_FACTOR")
POOL_AMOUNT = create_hash_string("POOL_AMOUNT")
RESERVE_FACTOR = create_hash_string("RESERVE_FACTOR")
SINGLE_SWAP_GAS_LIMIT = create_hash_string("SINGLE_SWAP_GAS_LIMIT")
SWAP_ORDER_GAS_LIMIT = create_hash_string("SWAP_ORDER_GAS_LIMIT")
VIRTUAL_TOKEN_ID = create_hash_string("VIRTUAL_TOKEN_ID")


def accountPositionListKey(account):
    return create_hash(["bytes32", "address"], [ACCOUNT_POSITION_LIST, account])


def claimable_fee_amount_key(market: str, token: str):
    return create_hash(["bytes32", "address", "address"], [CLAIMABLE_FEE_AMOUNT, market, token])


def decrease_order_gas_limit_key():
    return DECREASE_ORDER_GAS_LIMIT


def deposit_gas_limit_key():
    return DEPOSIT_GAS_LIMIT


def execution_gas_fee_base_amount_key():
    return EXECUTION_GAS_FEE_BASE_AMOUNT


def execution_gas_fee_multiplier_key():
    return EXECUTION_GAS_FEE_MULTIPLIER_FACTOR


def increase_order_gas_limit_key():
    return INCREASE_ORDER_GAS_LIMIT


def min_additional_gas_for_execution_key():
    return MIN_ADDITIONAL_GAS_FOR_EXECUTION


def min_collateral():
    return MIN_COLLATERAL_USD


def min_collateral_factor_key(market):
    return create_hash(["bytes32", "address"], [MIN_COLLATERAL_FACTOR_KEY, market])


def max_open_interest_key(market: str, is_long: bool):
    return create_hash(["bytes32", "address", "bool"], [MAX_OPEN_INTEREST, market, is_long])


def max_position_impact_factor_for_liquidations_key(market):
    return create_hash(
        ["bytes32", "address"],
        [MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS_KEY, market],
    )


def open_interest_in_tokens_key(market: str, collateral_token: str, is_long: bool):
    return create_hash(
        ["bytes32", "address", "address", "bool"],
        [OPEN_INTEREST_IN_TOKENS, market, collateral_token, is_long],
    )


def open_interest_key(market: str, collateral_token: str, is_long: bool):
    return create_hash(
        ["bytes32", "address", "address", "bool"],
        [OPEN_INTEREST, market, collateral_token, is_long],
    )


def open_interest_reserve_factor_key(market: str, is_long: bool):
    return create_hash(["bytes32", "address", "bool"], [OPEN_INTEREST_RESERVE_FACTOR, market, is_long])


def pool_amount_key(market: str, token: str):
    return create_hash(["bytes32", "address", "address"], [POOL_AMOUNT, market, token])


def reserve_factor_key(market: str, is_long: bool):
    return create_hash(["bytes32", "address", "bool"], [RESERVE_FACTOR, market, is_long])


def single_swap_gas_limit_key():
    return SINGLE_SWAP_GAS_LIMIT


def swap_order_gas_limit_key():
    return SWAP_ORDER_GAS_LIMIT


def virtualTokenIdKey(token: str):
    return create_hash(["bytes32", "address"], [VIRTUAL_TOKEN_ID, token])


def withdraw_gas_limit_key():
    return WITHDRAWAL_GAS_LIMIT


if __name__ == "__main__":
    # Example usage
    token = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    hash_data = virtualTokenIdKey(token)
    print(f"Virtual token ID key: {hash_data.hex()}")
