"""1delta proxy functions to interact with lending pool.

- Supply collateral to lending pool
- Withdraw collateral from lending pool
"""

from web3.contract.contract import Contract, ContractFunction

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.one_delta.deployment import OneDeltaDeployment

from eth_defi.compat import encode_abi_compat


def supply(
    one_delta_deployment: OneDeltaDeployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: str,
) -> ContractFunction:
    """Supply collateral to Aave

    :param one_delta_deployment: 1delta deployment
    :param token: collateral token contract proxy
    :param collateral_amount: amount of collateral to be supplied
    :param wallet_address: wallet address of the user
    :return: multicall contract function to supply collateral
    """

    calls = _build_supply_multicall(
        one_delta_deployment=one_delta_deployment,
        token=token,
        amount=amount,
        wallet_address=wallet_address,
    )
    return one_delta_deployment.broker_proxy.functions.multicall(calls)


def _build_supply_multicall(
    one_delta_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: str,
) -> list[str]:
    """Build multicall to supply collateral to Aave

    :param one_delta_deployment: 1delta deployment
    :param token: collateral token contract proxy
    :param collateral_amount: amount of collateral to be supplied
    :param wallet_address: wallet address of the user
    :return: list of encoded ABI calls
    """
    call_transfer = encode_abi_compat(
        contract=one_delta_deployment.flash_aggregator,
        fn_name="transferERC20In",
        args=[
            token.address,
            amount,
        ],
    )

    call_deposit = encode_abi_compat(
        contract=one_delta_deployment.flash_aggregator,
        fn_name="deposit",
        args=[
            token.address,
            wallet_address,
        ],
    )

    return [call_transfer, call_deposit]


def withdraw(
    one_delta_deployment: OneDeltaDeployment,
    *,
    token: Contract,
    atoken: Contract,
    amount: int,
    wallet_address: str,
) -> ContractFunction:
    """Withdraw collateral from Aave

    :param one_delta_deployment: 1delta deployment
    :param token: collateral token contract proxy
    :param atoken: aToken contract proxy
    :param collateral_amount: amount of collateral to be withdrawn
    :param wallet_address: wallet address of the user
    :return: multicall contract function to withdraw collateral
    """

    calls = _build_withdraw_multicall(
        one_delta_deployment=one_delta_deployment,
        token=token,
        atoken=atoken,
        amount=amount,
        wallet_address=wallet_address,
    )
    return one_delta_deployment.broker_proxy.functions.multicall(calls)


def _build_withdraw_multicall(
    one_delta_deployment,
    *,
    token: Contract,
    atoken: Contract,
    amount: int,
    wallet_address: str,
) -> list[str]:
    """Build multicall to withdraw collateral from Aave

    :param one_delta_deployment: 1delta deployment
    :param token: collateral token contract proxy
    :param atoken: aToken contract proxy
    :param collateral_amount: amount of collateral to be withdrawn, use MAX_AMOUNT to withdraw all collateral
    :param wallet_address: wallet address of the user
    :return: list of encoded ABI calls
    """
    if amount == MAX_AMOUNT:
        # use MAX_AMOUNT to make sure the whole balance is swept
        call_transfer = encode_abi_compat(
            contract=one_delta_deployment.flash_aggregator,
            fn_name="transferERC20AllIn",
            args=[atoken.address],
        )
    else:
        call_transfer = encode_abi_compat(
            contract=one_delta_deployment.flash_aggregator,
            fn_name="transferERC20In",
            args=[atoken.address, amount],
        )

    call_withdraw = encode_abi_compat(
        contract=one_delta_deployment.flash_aggregator,
        fn_name="withdraw",
        args=[
            token.address,
            wallet_address,
        ],
    )
    return [call_transfer, call_withdraw]
