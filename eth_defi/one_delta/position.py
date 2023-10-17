"""Aave v3 loan"""
from eth_typing import HexAddress
from web3.contract.contract import Contract, ContractFunction

from eth_defi.aave_v3.constants import AaveV3InterestRateMode
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.utils import encode_path


def supply(
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
    supply_function = pool.functions.supply(token.address, amount, wallet_address, 0)

    # supply_function = one_delta_deployment.flash_aggregator.functions.deposit(token.address, wallet_address)

    return approve_function, supply_function


def approve_tokens():
    pass


def open_short_position(
    one_delta_deployment: OneDeltaDeployment,
    *,
    collateral_token: Contract,
    borrow_token: Contract,
    pool_fee: int,
    borrow_amount: int,
    min_collateral_amount_out: int = 0,
) -> ContractFunction:
    path = encode_path(
        [
            borrow_token.address,
            collateral_token.address,
        ],
        [pool_fee],
        [6],  # action: open position
        [0],  # pid: uniswap v3
        2,  # flag: variable borrow
    )

    # amount_in = int(0.5 * 10**18)
    # min_amount_out = 0  # TODO: improve later

    return one_delta_deployment.flash_aggregator.functions.flashSwapExactIn(
        borrow_amount,
        min_collateral_amount_out,
        path,
    )
