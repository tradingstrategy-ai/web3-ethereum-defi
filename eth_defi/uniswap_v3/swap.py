"""Uniswap v3 swap helper functions."""
import warnings
from typing import Callable

from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.uniswap_v3.deployment import FOREVER_DEADLINE, UniswapV3Deployment
from eth_defi.uniswap_v3.price import UniswapV3PriceHelper
from eth_defi.uniswap_v3.utils import encode_path


def swap_with_slippage_protection(
    uniswap_v3_deployment: UniswapV3Deployment,
    *,
    recipient_address: HexAddress,
    base_token: Contract,
    quote_token: Contract,
    pool_fees: list[float],
    intermediate_token: Contract | None = None,
    max_slippage: float = 0.1,
    amount_in: int | None = None,
    amount_out: int | None = None,
    deadline: int = FOREVER_DEADLINE,
) -> Callable:
    """Helper function to prepare a swap from quote token to base token (buy base token with quote token)
    with price estimation and slippage protection baked in.

    Example:

    .. code-block:: python

        # build transaction to swap from USDC to WETH
        swap_func = swap_with_slippage_protection(
            uniswap_v3_deployment=uniswap_v3,
            recipient_address=hot_wallet_address,
            base_token=weth,
            quote_token=usdc,
            pool_fees=[weth_usdc_pool_trading_fee],
            amount_in=usdc_amount_to_pay,
            max_slippage=50,  # 50 bps = 0.5%
        )
        tx = swap_func.build_transaction(
            {
                "from": hot_wallet_address,
                "chainId": web3.eth.chain_id,
                "gas": 350_000,  # estimate max 350k gas per swap
            }
        )
        tx = fill_nonce(web3, tx)
        gas_fees = estimate_gas_fees(web3)
        apply_gas(tx, gas_fees)

        signed_tx = hot_wallet.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
        assert tx_receipt.status == 1

    :param uniswap_v3_deployment: an instance of `UniswapV3Deployment`
    :param recipient_address: Recipient's address
    :param base_token: Base token of the trading pair
    :param quote_token: Quote token of the trading pair
    :param intermediate_token: Intermediate token which the swap can go through
    :param pool_fees: List of all pools' trading fees in the path
    :param amount_in: How much of the quote token we want to pay, this has to be `None` if `amount_out` is specified
    :param amount_out: How much of the base token we want to receive, this has to be `None` if `amount_in` is specified
    :param max_slippage: Max slippage express in bps, default = 0.1 bps (0.001%)
    :param deadline: Time limit of the swap transaction, by default = forever (no deadline)
    :return: Prepared swap function which can be used directly to build transaction
    """
    if not amount_in and not amount_out:
        raise ValueError("amount_in is specified, amount_out has to be None")

    if max_slippage < 0:
        raise ValueError("max_slippage has to be equal or greater than 0")

    if max_slippage == 0:
        warnings.warn("max_slippage is set to 0, this can potentially lead to reverted transaction. It's recommended to set use default max_slippage instead (0.1 bps) to ensure successful transaction")

    router = uniswap_v3_deployment.swap_router
    price_helper = UniswapV3PriceHelper(uniswap_v3_deployment)

    path = [quote_token.address, base_token.address]
    if intermediate_token:
        path = [quote_token.address, intermediate_token.address, base_token.address]
    encoded_path = encode_path(path, pool_fees)

    if len(path) - 1 != len(pool_fees):
        raise ValueError(f"Expected {len(path) - 1} pool fees, got {len(pool_fees)}")

    if amount_in:
        if amount_out is not None:
            raise ValueError("amount_in is specified, amount_out has to be None")

        estimated_min_amount_out: int = price_helper.get_amount_out(
            amount_in=amount_in,
            path=path,
            fees=pool_fees,
            slippage=max_slippage,
        )

        return router.functions.exactInput(
            (
                encoded_path,
                recipient_address,
                deadline,
                amount_in,
                estimated_min_amount_out,
            )
        )
    elif amount_out:
        if amount_in is not None:
            raise ValueError("amount_out is specified, amount_in has to be None")

        estimated_max_amount_in: int = price_helper.get_amount_in(
            amount_out=amount_out,
            path=path,
            fees=pool_fees,
            slippage=max_slippage,
        )

        return router.functions.exactOutput(
            (
                encoded_path,
                recipient_address,
                deadline,
                amount_out,
                estimated_max_amount_in,
            )
        )
