import logging
from decimal import Decimal

from web3 import Web3

from eth_defi.abi import get_deployed_contract

from .rates import WAD

def deposit_in_aave(hot_wallet, aave_deposit_address, token_address, amount):
    """
    Deposits the specified amount of the specified token in Aave v3 and receives the corresponding aToken.

    Parameters:
        hot_wallet (HotWallet): Instance of a HotWallet with the token to deposit.
        aave_deposit_address (str): Ethereum address of the Aave deposit contract for the token.
        token_address (str): Ethereum address of the token to deposit.
        amount (int): Amount of the token to deposit, in its smallest unit of value (e.g. wei for ETH).

    Returns:
        str: Transaction hash of the deposit transaction.

    Example:
        hot_wallet = HotWallet.connect(PRIVATE_KEY, provider=Web3.HTTPProvider(GANACHE_URL))
        aave_deposit_address = "0x4ddc2d193948926d02f9b1fe9e1daa0718270ed5"
        token_address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"  # USDC
        amount = 1000000000000000000  # 1 USDC
        tx_hash = deposit_in_aave(hot_wallet, aave_deposit_address, token_address, amount)
    """
    # Connect to the Aave v3 API
    aave_api = AaveV3(Web3.HTTPProvider(GANACHE_URL))

    # Check the balance of the hot wallet before the deposit
    before_balance = hot_wallet.get_balance(token_address)

    # Deposit the specified amount of the token in Aave v3
    tx_hash = aave_api.reserves.deposit(aave_deposit_address, amount, {"from": hot_wallet.address}).transact()

    # Check the balance of the hot wallet after the deposit
    after_balance = hot_wallet.get_balance(token_address)

    # Calculate the amount of aToken received
    a_token_amount = before_balance - after_balance

    # Return the transaction hash of the deposit
    return tx_hash

def test_aave_deposit(hot_wallet, ganache_url, aave_deposit_address, token_address):
    """
    Test that the deposit in Aave v3 is correctly registered and the corresponding aToken is received.

    Parameters:
        hot_wallet (HotWallet): Instance of a HotWallet with the token to deposit.
        ganache_url (str): URL of the Ganache mainnet fork.
        aave_deposit_address (str): Ethereum address of the Aave deposit contract for the token.
        token_address (str): Ethereum address of the token to deposit.
    """
    # Connect to the Aave v3 API
    aave_api = AaveV3(Web3.HTTPProvider(ganache_url))

    # Check the balance of the hot wallet before the deposit
    before_balance = hot_wallet.get_balance(token_address)

    # Deposit 1 USDC in Aave v3
    amount = 1000000000000000000
    tx_hash = deposit_in_aave(hot_wallet, aave_deposit_address, token_address, amount)

    # Wait for the transaction to be mined
    web3 = Web3(Web3.HTTPProvider(ganache_url))
    receipt = web3.eth.waitForTransactionReceipt(tx_hash)

    # Check that the transaction was successful
    assert receipt.status == 1, f"Transaction failed with status {receipt.status}"

    # Check the balance of the hot wallet after the deposit
    after_balance = hot_wallet.get_balance(token_address)

    # Calculate the amount of aToken received
    a_token_amount = before_balance - after_balance

    # Check that the correct amount of aToken was received
    assert a_token_amount == amount, f"Incorrect amount of aToken received: expected {amount}, got {a_token_amount}"

    # Check that the deposit was correctly registered in Aave v3
    reserve_balance = aave_api.reserves.get_balance(aave_deposit_address, token_address)
    assert reserve_balance == amount, f"Incorrect balance in Aave reserve: expected {amount}, got {reserve_balance}"