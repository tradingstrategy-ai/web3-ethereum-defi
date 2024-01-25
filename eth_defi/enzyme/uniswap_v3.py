"""Prepare Uniswap v3 swaps through Enzyme vault."""

from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import encode_function_call
from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_defi.enzyme.vault import Vault
from eth_defi.uniswap_v3.deployment import FOREVER_DEADLINE, UniswapV3Deployment
from eth_defi.uniswap_v3.price import UniswapV3PriceHelper
from eth_defi.uniswap_v3.utils import encode_path


def prepare_swap(
    enzyme: EnzymeDeployment,
    vault: Vault,
    uniswap_v3: UniswapV3Deployment,
    generic_adapter: Contract,
    *,
    token_in: Contract,
    token_out: Contract,
    pool_fees: list[int],
    token_in_amount: int,
    token_intermediate: Contract | None = None,
) -> ContractFunction:
    """Prepare a Uniswap v3 swap transaction for Enzyme vault.

    - Tells the Enzyme vault to swap some tokens

    - Swap from token_in to token_out

    - Must be transacted from the vault owner account

    .. note ::

        This function is designed for unit testing, may have unrealistic slippage parameters, etc.

    Example:

    .. code-block:: python

        # Vault swaps USDC->ETH for both users
        # Buy ETH worth of 200 USD
        prepared_tx = prepare_swap(
            deployment,
            vault,
            uniswap_v3,
            generic_adapter,
            token_in=usdc_token.contract,
            token_out=weth_token.contract,
            pool_fees=[3000],
            token_in_amount=200 * 10**6,  # 200 USD
        )

        tx_hash = prepared_tx.transact({"from": user_1})
        assert_transaction_success_with_explanation(web3, tx_hash)

        assert usdc.functions.balanceOf(vault.vault.address).call() == 1300 * 10**6  # USDC left
        assert weth.functions.balanceOf(vault.vault.address).call() == 124500872629987902  # ETH we bought 0.12450087262998791

    :param enzyme:
        Enzyme deploymeent

    :param vault:
        Vault that needs to perform the swap

    :param uniswap_v3:
        Uniswap v3 deployment

    :param generic_adapter:
        GenericAdapter contract we use for swaps

    :param token_in:
        ERC-20 token we sell

    :param token_out:
        ERC-20 token we buy

    :param pool_fees:
        Pool fees of the pools in the route

    :param token_in_amount:
        Token in amount, raw

    :return:
        Transaction object that can be signed and executed
    """

    assert isinstance(generic_adapter, Contract), f"generic_adapter is needed for swap integration"

    router = uniswap_v3.swap_router
    price_helper = UniswapV3PriceHelper(uniswap_v3)

    # Prepare the swap parameters
    spend_asset_amounts = [token_in_amount]
    spend_assets = [token_in]

    path = [token_in.address, token_out.address]
    if token_intermediate:
        path = [token_in.address, token_intermediate.address, token_out.address]
    assert len(path) == len(pool_fees) + 1
    encoded_path = encode_path(path, pool_fees)
    incoming_assets = [token_out]

    # estimate output amount
    estimated_min_amount_out: int = price_helper.get_amount_out(
        amount_in=token_in_amount,
        path=path,
        fees=pool_fees,
    )
    min_incoming_assets_amounts = [estimated_min_amount_out]

    # The vault performs a swap on Uniswap v3
    encoded_approve = encode_function_call(
        token_in.functions.approve,
        [router.address, token_in_amount],
    )

    encoded_swap = encode_function_call(
        router.functions.exactInput,
        [
            (
                encoded_path,
                generic_adapter.address,
                FOREVER_DEADLINE,
                token_in_amount,
                1,
            )
        ],
    )

    bound_call = execute_calls_for_generic_adapter(
        comptroller=vault.comptroller,
        external_calls=(
            (token_in, encoded_approve),
            (router, encoded_swap),
        ),
        generic_adapter=generic_adapter,
        incoming_assets=incoming_assets,
        integration_manager=enzyme.contracts.integration_manager,
        min_incoming_asset_amounts=min_incoming_assets_amounts,
        spend_asset_amounts=spend_asset_amounts,
        spend_assets=spend_assets,
    )

    return bound_call
