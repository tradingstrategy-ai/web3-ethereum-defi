"""Uniswap v2 swap helper functions."""
import warnings
from typing import Callable, Optional

from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.uniswap_v2.deployment import FOREVER_DEADLINE, UniswapV2Deployment
from eth_defi.uniswap_v2.fees import estimate_buy_price, estimate_sell_price


def swap_with_slippage_protection(
    uniswap_v2_deployment: UniswapV2Deployment,
    *,
    recipient_address: HexAddress,
    base_token: Contract,
    quote_token: Contract,
    intermediate_token: Optional[Contract] = None,
    max_slippage: float = 0.1,
    amount_in: Optional[int] = None,
    amount_out: Optional[int] = None,
    fee: int = 30,
    deadline: int = FOREVER_DEADLINE,
) -> Callable:
    """Helper function to prepare a swap from quote token to base token (buy base token with quote token)
    with price estimation and slippage protection baked in.

    Example:

    .. code-block:: python

        # build transaction to swap from USDC to WETH
        swap_func = swap_with_slippage_protection(
            uniswap_v2_deployment=uniswap_v2,
            recipient_address=hot_wallet_address,
            base_token=weth,
            quote_token=usdc,
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

        # sign and broadcast
        signed_tx = hot_wallet.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
        assert tx_receipt.status == 1

        # similarly we can also swap USDC->WETH->DAI
        swap_func = swap_with_slippage_protection(
            uniswap_v2_deployment=uniswap_v2,
            recipient_address=hot_wallet_address,
            base_token=dai,
            quote_token=usdc,
            intermediate_token=weth,
            amount_out=dai_amount_expected,
            max_slippage=100,  # 100 bps = 1%
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

    :param uniswap_v2_deployment:
        Uniswap v2 deployment

    :param base_token:
        Base token of the trading pair

    :param quote_token:
        Quote token of the trading pair

    :param intermediate_token:
        Intermediate token which the swap can go through

    :param recipient_address:
        Recipient's address

    :param amount_in:
        How much of the quote token we want to pay, this has to be `None` if `amount_out` is specified.
        Must be in raw quote token units.

    :param amount_out:
        How much of the base token we want to receive, this has to be `None` if `amount_in` is specified
        Must be in raw base token units.

    :param max_slippage:
        Max slippage express in bps, default = 0.1 bps (0.001%)

    :param fee:
        Trading fee express in bps, default = 30 bps (0.3%)

    :param deadline:
        Time limit of the swap transaction, by default = forever (no deadline)

    :return:
        Prepared swap function which can be used directly to build transaction
    """

    if amount_in:
        assert type(amount_in) == int

    if amount_out:
        assert type(amount_out) == int

    assert max_slippage >= 0
    if max_slippage == 0:
        warnings.warn("The `max_slippage` is set to 0, this can potentially lead to reverted transaction. It's recommended to set use default max_slippage instead (0.1 bps) to ensure successful transaction")

    router = uniswap_v2_deployment.router
    path = [quote_token.address, base_token.address]
    if intermediate_token:
        path = [quote_token.address, intermediate_token.address, base_token.address]

    if amount_in:
        assert amount_out is None, "amount_in is specified, amount_out has to be None"

        estimated_min_amount_out: int = estimate_sell_price(
            uniswap=uniswap_v2_deployment,
            base_token=quote_token,
            quote_token=base_token,
            quantity=amount_in,
            slippage=max_slippage,
            fee=fee,
            intermediate_token=intermediate_token,
        )

        return router.functions.swapExactTokensForTokens(
            amount_in,
            estimated_min_amount_out,
            path,
            recipient_address,
            deadline,
        )
    elif amount_out:
        assert amount_in is None, "amount_out is specified, amount_in has to be None"

        estimated_max_amount_in: int = estimate_buy_price(
            uniswap=uniswap_v2_deployment,
            base_token=base_token,
            quote_token=quote_token,
            quantity=amount_out,
            slippage=max_slippage,
            fee=fee,
            intermediate_token=intermediate_token,
        )

        return router.functions.swapTokensForExactTokens(
            amount_out,
            estimated_max_amount_in,
            path,
            recipient_address,
            deadline,
        )
