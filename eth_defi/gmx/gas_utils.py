"""
GMX Gas Utilities
"""

import logging

from web3.contract import Contract

from eth_defi.gmx.constants import (
    EXECUTION_BUFFER_CRITICAL_THRESHOLD,
    EXECUTION_BUFFER_RECOMMENDED_MAX,
    EXECUTION_BUFFER_RECOMMENDED_MIN,
    EXECUTION_BUFFER_WARNING_THRESHOLD,
)
from eth_defi.gmx.keys import (
    DECREASE_ORDER_GAS_LIMIT,
    DEPOSIT_GAS_LIMIT,
    EXECUTION_GAS_FEE_BASE_AMOUNT,
    EXECUTION_GAS_FEE_BASE_AMOUNT_V2_1,
    EXECUTION_GAS_FEE_MULTIPLIER_FACTOR,
    EXECUTION_GAS_FEE_PER_ORACLE_PRICE,
    INCREASE_ORDER_GAS_LIMIT,
    SINGLE_SWAP_GAS_LIMIT,
    SWAP_ORDER_GAS_LIMIT,
    WITHDRAWAL_GAS_LIMIT,
    apply_factor,
)

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


def execution_gas_fee_base_amount_v2_1_key():
    return EXECUTION_GAS_FEE_BASE_AMOUNT_V2_1


def execution_gas_fee_multiplier_key():
    return EXECUTION_GAS_FEE_MULTIPLIER_FACTOR


def execution_gas_fee_per_oracle_price_key():
    return EXECUTION_GAS_FEE_PER_ORACLE_PRICE


def get_gas_limits(datastore_object: Contract, use_cache: bool = True) -> dict[str, int]:
    """Given a Web3 contract object of the datastore, return a dictionary with the gas limits
    (as integers) that correspond to various operations used for execution fee calculation.

    Uses module-level caching to avoid repeated RPC calls for the same datastore.
    Gas limits are cached per (chain_id, datastore_address) combination.

    :param datastore_object: The connected contract instance
    :type datastore_object: web3.contract.Contract
    :param use_cache: Whether to use cached values. Default is True
    :type use_cache: bool
    :return: Gas limit values for various operations
    :rtype: dict[str, int]
    """
    # Get cache key from contract
    chain_id = datastore_object.w3.eth.chain_id
    datastore_address = datastore_object.address.lower()
    cache_key = (chain_id, datastore_address)

    # Return cached value if available
    if use_cache and cache_key in _GAS_LIMITS_CACHE:
        return _GAS_LIMITS_CACHE[cache_key].copy()

    # Fetch gas limits from contract
    gas_limits = {
        "deposit": datastore_object.functions.getUint(deposit_gas_limit_key()).call(),
        "withdraw": datastore_object.functions.getUint(withdraw_gas_limit_key()).call(),
        "single_swap": datastore_object.functions.getUint(single_swap_gas_limit_key()).call(),
        "swap_order": datastore_object.functions.getUint(swap_order_gas_limit_key()).call(),
        "increase_order": datastore_object.functions.getUint(increase_order_gas_limit_key()).call(),
        "decrease_order": datastore_object.functions.getUint(decrease_order_gas_limit_key()).call(),
        "estimated_fee_base_gas_limit": datastore_object.functions.getUint(execution_gas_fee_base_amount_key()).call(),
        "estimated_fee_base_gas_limit_v2_1": datastore_object.functions.getUint(execution_gas_fee_base_amount_v2_1_key()).call(),
        "estimated_fee_multiplier_factor": datastore_object.functions.getUint(execution_gas_fee_multiplier_key()).call(),
        "estimated_fee_per_oracle_price": datastore_object.functions.getUint(execution_gas_fee_per_oracle_price_key()).call(),
        "multicall_base": 200000,  # Fixed constant not stored in datastore
    }

    # Cache the result
    if use_cache:
        _GAS_LIMITS_CACHE[cache_key] = gas_limits.copy()

    return gas_limits


logger = logging.getLogger(__name__)


def clear_gas_limits_cache():
    """Clear the module-level gas limits cache.

    Call this if you need to refresh gas limit values from the contract.
    """
    global _GAS_LIMITS_CACHE
    _GAS_LIMITS_CACHE.clear()


def calculate_execution_fee(
    gas_limits: dict[str, int],
    gas_price: int,
    order_type: str = "decrease_order",
    oracle_price_count: int = 2,
) -> int:
    """Calculate execution fee using GMX's formula.

    GMX calculates minimum execution fee as:
        adjustedGasLimit = baseGasLimit + (oracleCount * perOracleGas) + applyFactor(estimatedGasLimit, multiplierFactor)
        minExecutionFee = adjustedGasLimit * tx.gasprice

    Where applyFactor(value, factor) = value * factor / 10^30

    :param gas_limits: Gas limits dictionary from get_gas_limits()
    :type gas_limits: dict[str, int]
    :param gas_price: Gas price in wei (should be maxFeePerGas for EIP-1559)
    :type gas_price: int
    :param order_type: Order type key: "increase_order", "decrease_order", "swap_order", etc.
    :type order_type: str
    :param oracle_price_count: Number of oracle prices needed (typically 2 for most orders)
    :type oracle_price_count: int
    :return: Calculated execution fee in wei
    :rtype: int
    """
    # Get base gas limit (prefer V2.1 if available, fallback to V1)
    base_gas_limit = gas_limits.get("estimated_fee_base_gas_limit_v2_1", 0)
    if base_gas_limit == 0:
        base_gas_limit = gas_limits.get("estimated_fee_base_gas_limit", 0)

    # Add per-oracle-price gas
    per_oracle_gas = gas_limits.get("estimated_fee_per_oracle_price", 0)
    base_gas_limit += per_oracle_gas * oracle_price_count

    # Get the order-specific gas limit
    estimated_gas_limit = gas_limits.get(order_type, 2000000)

    # Get multiplier factor (in 30-decimal format)
    multiplier_factor = gas_limits.get("estimated_fee_multiplier_factor", 10**30)

    # Apply factor: value * factor / 10^30
    adjusted_order_gas = apply_factor(estimated_gas_limit, multiplier_factor)

    # Total adjusted gas limit
    total_gas_limit = int(base_gas_limit + adjusted_order_gas)

    # Calculate fee
    execution_fee = total_gas_limit * gas_price

    logger.info(
        "GMX execution fee: base_gas=%d, per_oracle=%d, order_gas=%d, multiplier=%d, adjusted_order_gas=%d, total_gas=%d, gas_price=%d gwei, fee=%d wei (%.6f ETH)",
        base_gas_limit - (per_oracle_gas * oracle_price_count),
        per_oracle_gas,
        estimated_gas_limit,
        multiplier_factor,
        int(adjusted_order_gas),
        total_gas_limit,
        gas_price // 10**9,
        execution_fee,
        execution_fee / 10**18,
    )

    return execution_fee


def validate_execution_buffer(execution_buffer: float) -> None:
    """Check the execution buffer value and log warnings if it is dangerously low.

    This does not raise an exception — it only emits log messages so that the
    caller can proceed with the order while being informed of the risk.

    :param execution_buffer:
        The multiplier to validate. Values below
        :data:`~eth_defi.gmx.constants.EXECUTION_BUFFER_CRITICAL_THRESHOLD` (1.2)
        emit a critical error; values below
        :data:`~eth_defi.gmx.constants.EXECUTION_BUFFER_WARNING_THRESHOLD` (1.5)
        emit a warning.
    """
    if execution_buffer < EXECUTION_BUFFER_CRITICAL_THRESHOLD:
        logger.error(
            "CRITICAL: executionBuffer=%.1fx is DANGEROUSLY LOW! GMX keepers will likely reject this order. Minimum safe value: %.1fx. Recommended: %.1f-%.1fx. Your order may fail with InsufficientExecutionFee error.",
            execution_buffer,
            EXECUTION_BUFFER_WARNING_THRESHOLD,
            EXECUTION_BUFFER_RECOMMENDED_MIN,
            EXECUTION_BUFFER_RECOMMENDED_MAX,
        )
    elif execution_buffer < EXECUTION_BUFFER_WARNING_THRESHOLD:
        logger.warning(
            "WARNING: executionBuffer=%.1fx is very low. Consider increasing to %.1f-%.1fx to avoid order failures during gas spikes.",
            execution_buffer,
            EXECUTION_BUFFER_RECOMMENDED_MIN,
            EXECUTION_BUFFER_RECOMMENDED_MAX,
        )


def apply_execution_buffer(
    base_fee: int,
    execution_buffer: float,
    validate: bool = True,
) -> int:
    """Apply the execution buffer multiplier to a base execution fee.

    Multiplies the base fee by the given buffer to produce a fee high enough
    for GMX keepers to execute profitably. Any excess is refunded by GMX.

    :param base_fee:
        Raw execution fee in wei, typically ``gas_limit * gas_price``.
    :param execution_buffer:
        Multiplier to apply. See
        :data:`~eth_defi.gmx.constants.DEFAULT_EXECUTION_BUFFER` for the
        recommended default.
    :param validate:
        If ``True`` (default), call :func:`validate_execution_buffer` before
        applying. Set to ``False`` to skip validation when the buffer has
        already been validated earlier in the call chain.
    :return:
        The buffered execution fee in wei.
    """
    if validate:
        validate_execution_buffer(execution_buffer)
    return int(base_fee * execution_buffer)

    return execution_fee
