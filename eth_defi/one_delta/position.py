"""Aave v3 loan"""
from eth_typing import HexAddress
from web3.contract.contract import Contract, ContractFunction

from eth_defi.aave_v3.constants import AaveV3InterestRateMode
from eth_defi.one_delta.deployment import OneDeltaDeployment


def open_short_position(
    one_delta_deployment: OneDeltaDeployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: HexAddress,
) -> tuple[ContractFunction, ContractFunction]:
    """
    Opens a loan position in Aave v3 by depositing any Aave v3 reserve token and receiving aToken back.

    Example:

    .. code-block:: python



    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to supply.
    :param amount:
        The amount of token to supply.
    :param wallet_address:
        Your wallet address.
    :return:
        A tuple of 2 contract functions for approve and supply transaction.
    """

    pool = one_delta_deployment.aave_v3.pool

    # approve to supply
    approve_function = token.functions.approve(pool.address, amount)

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L145
    # address asset
    # uint256 amount
    # address onBehalfOf
    # uint16 referralCode
    # supply_function = pool.functions.supply(token.address, amount, wallet_address, 0)

    deposit_function = one_delta_deployment.flash_aggregator.functions.deposit(token.address, wallet_address)

    return approve_function, deposit_function


def withdraw(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: HexAddress,
) -> ContractFunction:
    """
    Withdraw the deposit from Aave v3.

    Example:

    .. code-block:: python

        # build transactions to withdraw all USDC you deposited from Aave v3
        withdraw_fn = withdraw(
            aave_v3_deployment=aave_v3_deployment,
            token=usdc.contract,
            amount=MAX_AMOUNT,
            wallet_address=hot_wallet.address,
        )

        tx = withdraw_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet_account.sign_transaction_with_new_nonce(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        assert_transaction_success_with_explanation(web3, tx_hash)

    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to withdraw.
    :param amount:
        The amount of token to withdraw. Set `MAX_AMOUNT` if you want to withdraw everything.
    :param wallet_address:
        Your wallet address.
    :return:
        Withdraw contract function.
    """
    pool = aave_v3_deployment.pool

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L198
    # address asset
    # uint256 amount
    # address to
    return pool.functions.withdraw(token.address, amount, wallet_address)
