"""
GMX Gas Utilities
"""

from web3.contract import Contract

from eth_defi.gmx.keys import DEPOSIT_GAS_LIMIT, WITHDRAWAL_GAS_LIMIT, SINGLE_SWAP_GAS_LIMIT, SWAP_ORDER_GAS_LIMIT, INCREASE_ORDER_GAS_LIMIT, DECREASE_ORDER_GAS_LIMIT, EXECUTION_GAS_FEE_BASE_AMOUNT, EXECUTION_GAS_FEE_MULTIPLIER_FACTOR


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


def get_gas_limits(datastore_object: Contract) -> dict[str, int]:
    """
    Given a Web3 contract object of the datastore, return a dictionary with the gas limits
    (as integers) that correspond to various operations used for execution fee calculation.

    Parameters
    ----------
    datastore_object : web3.contract.Contract
        The connected contract instance.

    Returns
    -------
    dict[str, int]
        Gas limit values for various operations.
    """
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

    return gas_limits
