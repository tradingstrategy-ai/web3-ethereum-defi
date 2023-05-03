"""Aave v3 loan"""
from typing import Callable

from web3.contract import Contract

from eth_defi.hotwallet import HotWallet
from eth_defi.trace import assert_transaction_success_with_explanation


def supply(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    hot_wallet: HotWallet,
) -> Callable:
    """
    Opens a loan position in Aave v3 by depositing any Aave v3 reserve token and receiving aToken back.

    Example:

    .. code-block:: python

        # build transaction to supply USDC to Aave v3
        supply_func = supply(
            aave_v3_deployment=aave_v3_deployment,
            hot_wallet=hot_wallet,
            token=usdc.contract,
            amount=amount,
        )

        tx = supply_func.build_transaction(
            {
                "from": hot_wallet.address,
                "chainId": web3.eth.chain_id
            }
        )

        # sign and broadcast
        signed = hot_wallet_account.sign_transaction_with_new_nonce(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        assert_transaction_success_with_explanation(web3, tx_hash)

    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to supply.
    :param hot_wallet:
        Instance of :py:class:`eth_defi.hotwallet.Hotwallet`.
    :param amount:
        The amount of token to supply.
    :return:
        Callable supply transaction.
    """
    web3 = aave_v3_deployment.web3
    pool = aave_v3_deployment.pool

    # TODO: maybe validate if Aave v3 actually supports this asset reserve

    # approve to supply
    tx = token.functions.approve(pool.address, amount).build_transaction({"from": hot_wallet.address})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # return the supply function
    return pool.functions.supply(token.address, amount, hot_wallet.address, 0)
