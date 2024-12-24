"""Uniswap Universal Router swap helper functions.
"""

import warnings
from typing import Callable
import logging
from datetime import datetime

from eth_typing import HexAddress
from eth_account.messages import SignableMessage
from web3.types import Wei
from web3.contract.contract import Contract, ContractFunction

from eth_defi.uniswap_universal_router.deployment import UniswapUniversalRouterDeployment
from uniswap_universal_router_decoder import RouterCodec, FunctionRecipient


logger = logging.getLogger(__name__)


def approve_token(
    universal_router_deployment: UniswapUniversalRouterDeployment,
    *,
    token: Contract,
    amount: int,
    deadline: int = FOREVER_DEADLINE,
    permit2_nonce: int = 0,
) -> tuple[ContractFunction, dict, SignableMessage]:
    codec = RouterCodec(universal_router_deployment.web3)

    # Make sure that Permit2 contract is approved
    approve_fn = token.functions.approve(universal_router_deployment.permit2.address, amount)

    # Build Permit2 signable message
    # by default expires in 5 mins
    expiration = int(datetime.now().timestamp() + 180)
    data, signable_message = codec.create_permit2_signable_message(
        token.address,
        amount=amount,
        expiration=expiration,
        nonce=permit2_nonce,
        spender=universal_router_deployment.router.address,
        deadline=deadline,
        chain_id=universal_router_deployment.web3.eth.chain_id,
    )

    return approve_fn, data, signable_message


def swap_uniswap_v3(
    universal_router_deployment: UniswapUniversalRouterDeployment,
    *,
    path: list[HexAddress | int],
    permit2_data: dict,
    permit2_signed_message,
    max_slippage: float = 15,
    amount_in: int | None = None,
    amount_out_min: int | None = None,
    amount_out: int | None = None,
    amount_in_max: int | None = None,
) -> Callable:
    """Helper function to prepare a swap from quote token to base token (buy base token with quote token)
    with price estimation and slippage protection baked in.

    :param universal_router_deployment: an instance of `UniswapUniversalRouterDeployment`
    :param path: List of tokens and pool fees in the swap path
    :param permit2_data: Permit2 data
    :param permit2_signed_message: Permit2 signed message
    :param max_slippage: Max slippage express in BPS.
        Expressed as BPS * 100, or 1/1,000,000 units.

        For example if your swap is directly between two pools, e.g, WETH-USDC 5 bps, and not routed through additional pools,
        `pool_fees` would be `[500]`.

    :param amount_in: How much of the quote token we want to pay, this has to be `None` if `amount_out` is specified
    :param amount_out: How much of the base token we want to receive, this has to be `None` if `amount_in` is specified

    :param max_slippage:
        Max slippage express in BPS.

        The default is 15 BPS (0.15%)

    :param deadline: Time limit of the swap transaction, by default = forever (no deadline)
    :return: Prepared swap function which can be used directly to build transaction
    """
    if not amount_in and not amount_out:
        raise ValueError("amount_in is specified, amount_out has to be None")

    if max_slippage < 0:
        raise ValueError("max_slippage has to be equal or greater than 0")

    if max_slippage == 0:
        warnings.warn("max_slippage is set to 0, this can potentially lead to reverted transaction. It's recommended to set use default max_slippage instead (0.1 bps) to ensure successful transaction")

    codec = RouterCodec(universal_router_deployment.web3)

    if amount_in:
        if amount_out_min is None:
            raise ValueError("amount_out_min is required for swap exact in")

        if amount_out is not None:
            raise ValueError("amount_in is specified, amount_out has to be None")

        swap_fn = (
            codec.encode.chain()
            .permit2_permit(permit2_data, permit2_signed_message)
            .v3_swap_exact_in(
                function_recipient=FunctionRecipient.SENDER,
                amount_in=Wei(amount_in),
                amount_out_min=Wei(amount_out_min),
                path=path,
                payer_is_sender=True,
            )
        )

        return swap_fn
    elif amount_out:
        raise NotImplementedError("exactOutput is not implemented yet")
