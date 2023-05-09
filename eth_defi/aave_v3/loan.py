"""Aave v3 deposit."""
from typing import Callable

from web3 import Web3

from eth_defi.abi import get_abi_by_filename
from eth_defi.hotwallet import HotWallet


def approve_token(web3: Web3, token_address: str, spender: str, amount: int) -> Callable:
    """
    Approve a specified amount of token to be spent.

    Example:

    .. code-block:: python

        # build transaction to approve ERC-20 to be spent.
        approve_func = approve_token(
            web3=web3,
            token_address=token_address,
            spender=spender,
            amount=amount
        )

        tx = approve_func.build_transaction(
            {
                "from": hot_wallet_address,
                "chainId": web3.eth.chain_id
            }
        )
        tx = fill_nonce(web3, tx)

        # sign and broadcast
        signed = hot_wallet_account.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = web3.eth.get_transaction_receipt(tx_hash)
        assert receipt.status == 1, "USDC transfer reverted"

    :param web3:
        Web3 Instance
    :param token_address:
        The address of the Aave v3 reserve token that you want to deposit.
    :param spender:
        The address of the spender to be approved.
    :param amount:
        The amount of token to be approved.
    :return:
        Callable approve transaction.
    """

    # Load token contract ABI
    token_abi = get_abi_by_filename("ERC20MockDecimals.json")["abi"]

    # Get token contract instance
    token_contract = web3.eth.contract(address=token_address, abi=token_abi)

    # Approve the token contract to spend the specified amount of token
    transaction = token_contract.functions.approve(spender, amount)

    # Return the transaction
    return transaction


def deposit_in_aave(
    web3: Web3,
    hot_wallet: HotWallet,
    aave_deposit_address: str,
    token_address: str,
    amount: int,
) -> Callable:
    """
    Opens a loan position in Aave v3 by depositing any Aave v3 reserve token and receiving aToken back.

    Example:

    .. code-block:: python

        # build transaction to deposit a reserve token
        deposit_func = deposit_in_aave(
            web3=web3,
            hot_wallet=hot_wallet_account,
            aave_deposit_address=AAVE_DEPOSIT_ADDRESS,
            token_address=USDC_ADDRESS,
            amount=amount
        )

        tx = deposit_func.build_transaction(
            {
                "from": hot_wallet_address,
                "chainId": web3.eth.chain_id
            }
        )
        tx = fill_nonce(web3, tx)

        # sign and broadcast
        signed = hot_wallet_account.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = web3.eth.get_transaction_receipt(tx_hash)
        assert receipt.status == 1, "Aave v3 deposit reverted"

    :param web3:
        Web3 Instance.
    :param hot_wallet:
        Instance of :py:class:`eth_defi.hotwallet.Hotwallet`.
    :param aave_deposit_address:
        The address of the Aave v3 contract where the deposit will be made.
    :param token_address:
        The address of the Aave v3 reserve token that you want to deposit.
    :param amount:
        The amount of token to deposit.
    :return:
        Callable deposit transaction.
    """
    # Load Aave v3 contract ABI
    aave_v3_abi = get_abi_by_filename("aave_v3/Pool.json")["abi"]

    # Get the Aave v3 contract instance
    aave_v3_contract = web3.eth.contract(address=aave_deposit_address, abi=aave_v3_abi)

    # Approve the Aave v3 contract to supply the specified amount of token
    transaction = aave_v3_contract.functions.supply(token_address, amount, hot_wallet.address, 0)

    # Return the transaction
    return transaction
