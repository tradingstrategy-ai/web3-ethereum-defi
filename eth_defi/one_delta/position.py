"""1delta integration position handlers.

- Open and close short positions using 1delta protocol
"""

from web3.contract.contract import Contract, ContractFunction

from eth_defi.aave_v3.constants import MAX_AMOUNT, AaveV3InterestRateMode
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.one_delta.constants import Exchange, TradeOperation
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.utils import encode_path


def approve(
    one_delta_deployment: OneDeltaDeployment,
    *,
    collateral_token: Contract,
    borrow_token: Contract,
    atoken: Contract,
    vtoken: Contract,
    aave_v3_deployment: AaveV3Deployment,
    collateral_amount: int = MAX_AMOUNT,
    borrow_amount: int = MAX_AMOUNT,
    atoken_amount: int = MAX_AMOUNT,
    vtoken_amount: int = MAX_AMOUNT,
) -> list[ContractFunction]:
    """Approve all the tokens needed for the position.

    :param one_delta_deployment: 1delta deployment
    :param collateral_token: collateral token contract proxy
    :param borrow_token: borrow token contract proxy
    :param atoken: aToken contract proxy
    :param vtoken: vToken contract proxy
    :param collateral_amount: amount of collateral to be approved
    :param borrow_amount: amount of borrow token to be approved
    :param atoken_amount: amount of aToken to be approved
    :param vtoken_amount: amount of vToken to be approved
    :param aave_v3_deployment: Aave V3 deployment
    :return: list of approval functions
    """
    trader = one_delta_deployment.flash_aggregator
    proxy = one_delta_deployment.broker_proxy
    aave_v3_pool = aave_v3_deployment.pool

    approval_functions = []

    for token, amount in {
        collateral_token: collateral_amount,
        borrow_token: borrow_amount,
        atoken: atoken_amount,
    }.items():
        approval_functions.append(token.functions.approve(trader.address, amount))
        approval_functions.append(token.functions.approve(aave_v3_pool.address, amount))

    # approve delegate the vToken
    approval_functions.append(vtoken.functions.approveDelegation(proxy.address, vtoken_amount))

    return approval_functions


def open_short_position(
    one_delta_deployment: OneDeltaDeployment,
    *,
    collateral_token: Contract,
    borrow_token: Contract,
    pool_fee: int,
    collateral_amount: int,
    borrow_amount: int,
    wallet_address: str,
    min_collateral_amount_out: int = 0,
    exchange: Exchange = Exchange.UNISWAP_V3,
    interest_mode: AaveV3InterestRateMode = AaveV3InterestRateMode.VARIABLE,
    do_supply: bool = True,
) -> ContractFunction:
    """Supply collateral to Aave and open a short position using flash swap.

    NOTE: only single-hop swap is supported at the moment

    :param one_delta_deployment: 1delta deployment
    :param collateral_token: collateral token contract proxy
    :param borrow_token: borrow token contract proxy
    :param pool_fee: raw fee of the pool which is used for the swap
    :param collateral_amount: amount of collateral to be supplied
    :param borrow_amount: amount of borrow token to be borrowed
    :param wallet_address: wallet address of the user
    :param min_collateral_amount_out: minimum amount of collateral to be received
    :param exchange: exchange to be used for the swap
    :param interest_mode: interest mode, variable or stable
    :param do_supply: default to True, if False, only flash swap will be executed
    :return: multicall contract function to supply collateral and open the short position
    """

    call_transfer = one_delta_deployment.flash_aggregator.encodeABI(
        fn_name="transferERC20In",
        args=[
            collateral_token.address,
            collateral_amount,
        ],
    )

    call_deposit = one_delta_deployment.flash_aggregator.encodeABI(
        fn_name="deposit",
        args=[
            collateral_token.address,
            wallet_address,
        ],
    )

    path = encode_path(
        path=[
            borrow_token.address,
            collateral_token.address,
        ],
        fees=[pool_fee],
        exchanges=[exchange],
        operation=TradeOperation.OPEN,
        interest_mode=interest_mode,
    )

    call_swap = one_delta_deployment.flash_aggregator.encodeABI(
        fn_name="flashSwapExactIn",
        args=[
            borrow_amount,
            min_collateral_amount_out,
            path,
        ],
    )

    calls = [call_transfer, call_deposit, call_swap]
    if do_supply is False:
        calls = [call_swap]

    return one_delta_deployment.broker_proxy.functions.multicall(calls)


def close_short_position(
    one_delta_deployment: OneDeltaDeployment,
    *,
    collateral_token: Contract,
    borrow_token: Contract,
    atoken: Contract,
    pool_fee: int,
    wallet_address: str,
    exchange: Exchange = Exchange.UNISWAP_V3,
    interest_mode: AaveV3InterestRateMode = AaveV3InterestRateMode.VARIABLE,
    do_withdraw: bool = True,
) -> ContractFunction:
    """Close a short position using flash swap then withdraw collateral from Aave.

    NOTE:
    - only single-hop swap is supported at the moment
    - withdrawal doesn't work correctly if there are more than 1 opened positions
        from this wallet, so `do_withdraw` should be set to False in that case

    :param one_delta_deployment: 1delta deployment
    :param collateral_token: collateral token contract proxy
    :param borrow_token: borrow token contract proxy
    :param atoken: aToken contract proxy
    :param pool_fee: raw fee of the pool which is used for the swap
    :param collateral_amount: amount of collateral to be supplied
    :param borrow_amount: amount of borrow token to be borrowed
    :param wallet_address: wallet address of the user
    :param exchange: exchange to be used for the swap
    :param interest_mode: interest mode, variable or stable
    :param do_withdraw: default to True, if False, only flash swap will be executed
    :return: multicall contract function to close the short position then withdraw collateral
    """
    path = encode_path(
        path=[
            borrow_token.address,
            collateral_token.address,
        ],
        fees=[pool_fee],
        exchanges=[exchange],
        operation=TradeOperation.CLOSE,
        interest_mode=interest_mode,
    )

    call_swap = one_delta_deployment.flash_aggregator.encodeABI(
        fn_name="flashSwapAllOut",
        args=[
            MAX_AMOUNT,
            path,
        ],
    )

    call_transfer = one_delta_deployment.flash_aggregator.encodeABI(
        fn_name="transferERC20AllIn",
        args=[
            atoken.address,
        ],
    )

    call_withdraw = one_delta_deployment.flash_aggregator.encodeABI(
        fn_name="withdraw",
        args=[
            collateral_token.address,
            wallet_address,
        ],
    )

    calls = [call_swap, call_transfer, call_withdraw]
    if do_withdraw is False:
        calls = [call_swap]

    return one_delta_deployment.broker_proxy.functions.multicall(calls)
