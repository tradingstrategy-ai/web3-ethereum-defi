"""Prepare Uniswap v2 swaps through Enzyme vault."""

from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import encode_function_call
from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_defi.enzyme.vault import Vault
from eth_defi.uniswap_v2.deployment import FOREVER_DEADLINE, UniswapV2Deployment
from eth_defi.uniswap_v2.fees import UniswapV2FeeCalculator


# fmt: off
def prepare_swap(
    enzyme: EnzymeDeployment,
    vault: Vault,
    uniswap_v2: UniswapV2Deployment,
    generic_adapter: Contract,
    token_in: Contract,
    token_out: Contract,
    swap_amount: int,
    token_intermediate: Contract | None = None,
) -> ContractFunction:
    """Prepare a Uniswap v2 swap transaction for Enzyme vault.

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
            uniswap_v2,
            generic_adapter,
            usdc,
            weth,
            200 * 10**6,  # 200 USD
        )

        tx_hash = prepared_tx.transact({"from": user_1})
        assert_transaction_success_with_explanation(web3, tx_hash)

        assert usdc.functions.balanceOf(vault.vault.address).call() == 1300 * 10**6  # USDC left
        assert weth.functions.balanceOf(vault.vault.address).call() == 124500872629987902  # ETH we bought 0.12450087262998791

    :param enzyme:
        Enzyme deploymeent

    :param vault:
        Vault that needs to perform the swap

    :param uniswap_v2:
        Uniswap v2 deployment

    :param generic_adapter:
        GenericAdapter contract we use for swaps

    :param token_in:
        ERC-20 token we sell

    :param token_out:
        ERC-20 token we buy

    :param swap_amount:
        Token in amount, raw

    :return:
        Transaction object that can be signed and executed
    """

    assert isinstance(generic_adapter, Contract), f"generic_adapter is needed for swap integration"

    price_helper = UniswapV2FeeCalculator(uniswap_v2)

    # Prepare the swap parameters
    token_in_swap_amount = swap_amount
    spend_asset_amounts = [token_in_swap_amount]
    spend_assets = [token_in]

    path = [token_in.address, token_out.address]
    if token_intermediate:
        path = [token_in.address, token_intermediate.address, token_out.address]

    expected_incoming_amount = price_helper.get_amount_out(token_in_swap_amount, path)
    incoming_assets = [token_out]
    min_incoming_assets_amounts = [expected_incoming_amount]

    # The vault performs a swap on Uniswap v2
    encoded_approve = encode_function_call(token_in.functions.approve, [uniswap_v2.router.address, token_in_swap_amount])

    # fmt: off
    encoded_swapExactTokensForTokens = encode_function_call(
        uniswap_v2.router.functions.swapExactTokensForTokens,
        [token_in_swap_amount, 1, path, generic_adapter.address, FOREVER_DEADLINE]
    )

    bound_call = execute_calls_for_generic_adapter(
        comptroller=vault.comptroller,
        external_calls=(
            (token_in, encoded_approve),
            (uniswap_v2.router, encoded_swapExactTokensForTokens),
        ),
        generic_adapter=generic_adapter,
        incoming_assets=incoming_assets,
        integration_manager=enzyme.contracts.integration_manager,
        min_incoming_asset_amounts=min_incoming_assets_amounts,
        spend_asset_amounts=spend_asset_amounts,
        spend_assets=spend_assets,
    )

    return bound_call
