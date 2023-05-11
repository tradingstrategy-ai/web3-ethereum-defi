"""Aave v3 loan"""
from web3.contract.contract import Contract, ContractFunction

from eth_defi.aave_v3.constants import AaveV3InterestRateMode


def supply(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: str,
) -> tuple[ContractFunction, ContractFunction]:
    """
    Opens a loan position in Aave v3 by depositing any Aave v3 reserve token and receiving aToken back.

    Example:

    .. code-block:: python

        # build transactions to supply USDC to Aave v3
        approve_fn, supply_fn = supply(
            aave_v3_deployment=aave_v3_deployment,
            token=usdc.contract,
            amount=amount,
            wallet_address=hot_wallet.address,
        )

        # approve
        tx = approve_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet.sign_transaction_with_new_nonce(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        assert_transaction_success_with_explanation(web3, tx_hash)

        # supply
        tx = supply_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet_account.sign_transaction_with_new_nonce(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        assert_transaction_success_with_explanation(web3, tx_hash)

    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to supply.
    :param wallet_address:
        Instance of :py:class:`eth_defi.hotwallet.Hotwallet`.
    :param amount:
        The amount of token to supply.
    :return:
        A tuple of 2 contract functions for approve and supply transaction.
    """

    pool = aave_v3_deployment.pool

    # TODO: maybe validate if Aave v3 actually supports this asset reserve

    # approve to supply
    approve_function = token.functions.approve(pool.address, amount)

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L145
    # address asset
    # uint256 amount
    # address onBehalfOf
    # uint16 referralCode
    supply_function = pool.functions.supply(token.address, amount, wallet_address, 0)

    return approve_function, supply_function


def withdraw(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: str,
) -> ContractFunction:
    """
    Withdraw from Aave v3
    """
    pool = aave_v3_deployment.pool

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L198
    # address asset
    # uint256 amount
    # address to
    return pool.functions.withdraw(token.address, amount, wallet_address)


def borrow(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: str,
    interest_rate_mode: AaveV3InterestRateMode = AaveV3InterestRateMode.VARIABLE,
) -> ContractFunction:
    """
    Borrow from Aave v3
    """
    pool = aave_v3_deployment.pool

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L221
    # address asset,
    # uint256 amount,
    # uint256 interestRateMode,
    # uint16 referralCode,
    # address onBehalfOf
    return pool.functions.borrow(token.address, amount, interest_rate_mode, 0, wallet_address)


def repay(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: str,
    interest_rate_mode: AaveV3InterestRateMode = AaveV3InterestRateMode.VARIABLE,
) -> tuple[ContractFunction, ContractFunction]:
    """
    Repay to Aave v3

    TODO: check repayWithATokens()
    """
    pool = aave_v3_deployment.pool

    # approve repay amount
    approve_function = token.functions.approve(pool.address, amount)

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L251
    # address asset,
    # uint256 amount,
    # uint256 interestRateMode,
    # address onBehalfOf
    repay_function = pool.functions.repay(token.address, amount, interest_rate_mode, wallet_address)

    return approve_function, repay_function
