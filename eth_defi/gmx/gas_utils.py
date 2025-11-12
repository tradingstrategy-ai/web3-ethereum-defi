"""
GMX Gas Utilities
"""

from web3.contract import Contract

from eth_defi.gmx.keys import DEPOSIT_GAS_LIMIT, WITHDRAWAL_GAS_LIMIT, SINGLE_SWAP_GAS_LIMIT, SWAP_ORDER_GAS_LIMIT, INCREASE_ORDER_GAS_LIMIT, DECREASE_ORDER_GAS_LIMIT, EXECUTION_GAS_FEE_BASE_AMOUNT, EXECUTION_GAS_FEE_MULTIPLIER_FACTOR

# Module-level cache for gas limits to avoid repeated RPC calls
# Key: (chain_id, datastore_address)
_GAS_LIMITS_CACHE: dict[tuple[int, str], dict[str, int]] = {}


def deposit_gas_limit_key():
    return DEPOSIT_GAS_LIMIT


def withdraw_gas_limit_key():
    return WITHDRAWAL_GAS_LIMIT


def single_swap_gas_limit_key():
    return SINGLE_SWAP_GAS_LIMIT


def swap_order_gas_limit_key():
    return SWAP_ORDER_GAS_LIMIT


def increase_order_gas_limit_key():
    return INCREASE_ORDER_GAS_LIMIT


def decrease_order_gas_limit_key():
    return DECREASE_ORDER_GAS_LIMIT


def execution_gas_fee_base_amount_key():
    return EXECUTION_GAS_FEE_BASE_AMOUNT


def execution_gas_fee_multiplier_key():
    return EXECUTION_GAS_FEE_MULTIPLIER_FACTOR


def get_gas_limits(datastore_object: Contract, use_cache: bool = True) -> dict[str, int]:
    """
    Given a Web3 contract object of the datastore, return a dictionary with the gas limits
    (as integers) that correspond to various operations used for execution fee calculation.

    Uses module-level caching to avoid repeated RPC calls for the same datastore.
    Gas limits are cached per (chain_id, datastore_address) combination.

    Parameters
    ----------
    datastore_object : web3.contract.Contract
        The connected contract instance.
    use_cache : bool, optional
        Whether to use cached values. Default is True.

    Returns
    -------
    dict[str, int]
        Gas limit values for various operations.
    """
    # Get cache key from contract
    chain_id = datastore_object.w3.eth.chain_id
    datastore_address = datastore_object.address.lower()
    cache_key = (chain_id, datastore_address)

    # Return cached value if available
    if use_cache and cache_key in _GAS_LIMITS_CACHE:
        return _GAS_LIMITS_CACHE[cache_key].copy()

    # Fetch gas limits from contract (7 RPC calls)
    gas_limits = {
        "deposit": datastore_object.functions.getUint(deposit_gas_limit_key()).call(),
        "withdraw": datastore_object.functions.getUint(withdraw_gas_limit_key()).call(),
        "single_swap": datastore_object.functions.getUint(single_swap_gas_limit_key()).call(),
        "swap_order": datastore_object.functions.getUint(swap_order_gas_limit_key()).call(),
        "increase_order": datastore_object.functions.getUint(increase_order_gas_limit_key()).call(),
        "decrease_order": datastore_object.functions.getUint(decrease_order_gas_limit_key()).call(),
        "estimated_fee_base_gas_limit": datastore_object.functions.getUint(execution_gas_fee_base_amount_key()).call(),
        "estimated_fee_multiplier_factor": datastore_object.functions.getUint(execution_gas_fee_multiplier_key()).call(),
        "multicall_base": 200000,  # Fixed constant not stored in datastore
    }

    # Cache the result
    if use_cache:
        _GAS_LIMITS_CACHE[cache_key] = gas_limits.copy()

    return gas_limits


def clear_gas_limits_cache():
    """Clear the module-level gas limits cache.

    Call this if you need to refresh gas limit values from the contract.
    """
    global _GAS_LIMITS_CACHE
    _GAS_LIMITS_CACHE.clear()
