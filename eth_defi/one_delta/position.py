"""1delta integration position handlers.

- Open and close short positions using 1delta protocol
"""

from web3.contract.contract import Contract, ContractFunction

from eth_defi.aave_v3.constants import MAX_AMOUNT, AaveV3InterestRateMode
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.one_delta.constants import Exchange, TradeOperation, TradeType
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.lending import (
    _build_supply_multicall,
    _build_withdraw_multicall,
)
from eth_defi.one_delta.utils import encode_path
from eth_defi.compat import encode_abi_compat


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
    This function can also be used to increase existing short position of the same pair.

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

    call_swap = encode_abi_compat(
        contract=one_delta_deployment.flash_aggregator,
        fn_name="flashSwapExactIn",
        args=[
            borrow_amount,
            min_collateral_amount_out,
            path,
        ],
    )

    if do_supply is True:
        calls = _build_supply_multicall(
            one_delta_deployment=one_delta_deployment,
            token=collateral_token,
            amount=collateral_amount,
            wallet_address=wallet_address,
        ) + [call_swap]
    else:
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
    withdraw_collateral_amount: int = MAX_AMOUNT,
) -> ContractFunction:
    """Close a short position using flash swap then withdraw collateral from Aave.

    NOTE:
    - only single-hop swap is supported at the moment
    - `withdraw_collateral_amount` should be used wisely in case
    there are multiple positions opened with same collateral,
    it will affect the collateral amount to be left on Aave to back other positions hence affecting the liquidation threshold as well.

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
    :param withdraw_collateral_amount: the amount of collateral to be withdrawn, if 0 then only flash swap will be executed
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

    call_swap = encode_abi_compat(
        contract=one_delta_deployment.flash_aggregator,
        fn_name="flashSwapAllOut",
        args=[
            MAX_AMOUNT,
            path,
        ],
    )

    if withdraw_collateral_amount == 0:
        calls = [call_swap]
    else:
        calls = [call_swap] + _build_withdraw_multicall(
            one_delta_deployment=one_delta_deployment,
            token=collateral_token,
            atoken=atoken,
            amount=withdraw_collateral_amount,
            wallet_address=wallet_address,
        )

    return one_delta_deployment.broker_proxy.functions.multicall(calls)


def reduce_short_position(
    one_delta_deployment: OneDeltaDeployment,
    *,
    collateral_token: Contract,
    borrow_token: Contract,
    atoken: Contract,
    pool_fee: int,
    wallet_address: str,
    reduce_borrow_amount: int | None = None,
    reduce_collateral_amount: int | None = None,
    min_borrow_amount_out: int | None = None,
    max_collateral_amount_in: int | None = None,
    withdraw_collateral_amount: int | None = None,
    exchange: Exchange = Exchange.UNISWAP_V3,
    interest_mode: AaveV3InterestRateMode = AaveV3InterestRateMode.VARIABLE,
) -> ContractFunction:
    """Reduce a short position size.

    NOTE: only single-hop swap is supported at the moment

    :param one_delta_deployment: 1delta deployment
    :param collateral_token: collateral token contract proxy
    :param borrow_token: borrow token contract proxy
    :param pool_fee: raw fee of the pool which is used for the swap
    :param wallet_address: wallet address of the user
    :param reduce_borrow_amount: amount of borrow token to be reduced
    :param reduce_collateral_amount: amount of collateral to be reduced
    :param min_borrow_amount_out: minimum amount of borrow token to be received
    :param min_collateral_amount_out: minimum amount of collateral to be received
    :param withdraw_collateral_amount: the amount of collateral to be withdrawn, if 0 then only flash swap will be executed
    :param exchange: exchange to be used for the swap
    :param interest_mode: interest mode, variable or stable
    :return: multicall contract function to reduce short position then withdraw collateral
    """

    if reduce_borrow_amount:
        assert reduce_collateral_amount is None, "Only one of reduce_borrow_amount or reduce_collateral_amount should be set"
        assert max_collateral_amount_in is not None, "max_collateral_amount_in should be set when reduce_borrow_amount is set"
        assert withdraw_collateral_amount is not None, "withdraw_collateral_amount should be set when reduce_borrow_amount is set"

        path = encode_path(
            path=[
                collateral_token.address,
                borrow_token.address,
            ],
            fees=[pool_fee],
            exchanges=[exchange],
            operation=TradeOperation.TRIM,
            interest_mode=interest_mode,
            trade_type=TradeType.EXACT_OUTPUT,
        )

        call_swap = encode_abi_compat(
            contract=one_delta_deployment.flash_aggregator,
            fn_name="flashSwapExactOut",
            args=[
                reduce_borrow_amount,
                max_collateral_amount_in,
                path,
            ],
        )

    elif reduce_collateral_amount:
        assert reduce_borrow_amount is None, "Only one of reduce_borrow_amount or reduce_collateral_amount should be set"
        assert min_borrow_amount_out is not None, "min_borrow_amount_out should be set when reduce_collateral_amount is set"

        path = encode_path(
            path=[
                collateral_token.address,
                borrow_token.address,
            ],
            fees=[pool_fee],
            exchanges=[exchange],
            operation=TradeOperation.TRIM,
            interest_mode=interest_mode,
            trade_type=TradeType.EXACT_INPUT,
        )

        call_swap = encode_abi_compat(
            contract=one_delta_deployment.flash_aggregator,
            fn_name="flashSwapExactIn",
            args=[
                reduce_collateral_amount,
                min_borrow_amount_out,
                path,
            ],
        )

        if withdraw_collateral_amount is None:
            withdraw_collateral_amount = reduce_collateral_amount

    if withdraw_collateral_amount == 0:
        calls = [call_swap]
    else:
        call_transfer = encode_abi_compat(
            contract=one_delta_deployment.flash_aggregator,
            fn_name="transferERC20In",
            args=[atoken.address, withdraw_collateral_amount],
        )

        call_withdraw = encode_abi_compat(
            contract=one_delta_deployment.flash_aggregator,
            fn_name="withdraw",
            args=[
                collateral_token.address,
                wallet_address,
            ],
        )
        calls = [call_swap, call_transfer, call_withdraw]

    return one_delta_deployment.broker_proxy.functions.multicall(calls)
