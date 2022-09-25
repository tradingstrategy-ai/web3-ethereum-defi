"""
Functions for reading Aave v3 account status.
"""
import logging
from decimal import Decimal

from web3 import Web3

from eth_defi.abi import get_deployed_contract

from .rates import WAD

logger = logging.getLogger(__name__)


def aave_v3_get_deposit_balance(web3: Web3, deposit_address: str, account_address: str) -> Decimal:
    # Use the AToken contract to read the account's current deposit balance in the specified currency reserve
    AToken = get_deployed_contract(web3, "aave_v3/AToken.json", deposit_address)
    result = AToken.functions.balanceOf(account_address).call()
    return Decimal(result) / Decimal(WAD)


def aave_v3_get_variable_borrow_balance(web3: Web3, variable_borrow_address: str, account_address: str) -> Decimal:
    # Use the VariableDebtToken contract to read the account's current deposit balance in the specified currency reserve
    VariableDebtToken = get_deployed_contract(web3, "aave_v3/VariableDebtToken.json", variable_borrow_address)
    result = VariableDebtToken.functions.balanceOf(account_address).call()
    return Decimal(result) / Decimal(WAD)


def aave_v3_get_stable_borrow_balance(web3: Web3, stable_borrow_address: str, account_address: str) -> Decimal:
    # Use the StableDebtToken contract to read the account's current deposit balance in the specified currency reserve
    StableDebtToken = get_deployed_contract(web3, "aave_v3/StableDebtToken.json", stable_borrow_address)
    result = StableDebtToken.functions.balanceOf(account_address).call()
    return Decimal(result) / Decimal(WAD)
