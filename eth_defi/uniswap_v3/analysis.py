from decimal import Decimal

from web3 import Web3
from web3.logs import DISCARD

from eth_defi.abi import get_transaction_data_field
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.token import fetch_erc20_details
from eth_defi.trade import TradeFail, TradeSuccess
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.pool import fetch_pool_details
from eth_defi.uniswap_v3.utils import decode_path


def get_input_args(params: tuple | dict) -> dict:
    """Names and decodes input arguments from router.decode_function_input()
    Note there is no support yet for SwapRouter02, it does not accept a deadline parameter
    See: https://docs.uniswap.org/contracts/v3/reference/periphery/interfaces/ISwapRouter#exactinputparams


    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    :params:
    params from router.decode_function_input

    :returns:
        Dict of exactInputParams as specified in the link above
    """

    if type(params) == dict:
        # Web3 6.0+
        full_path_decoded = decode_path(params["path"])
        # TODO: add support for SwapRouter02 which does not accept deadline parameter
        return {
            "path": full_path_decoded,
            "recipient": params["recipient"],
            "deadline": params["deadline"],
            "amountIn": params["amountIn"],
            "amountOutMinimum": params["amountOutMinimum"],
        }
    else:
        if len(params) == 4:
            # SwapRouterV2
            full_path_decoded = decode_path(params[0])
            return {
                "path": full_path_decoded,  # Undecoded
                "recipient": params[1],
                "amountIn": params[2],
                "amountOutMinimum": params[3],
            }
        else:
            full_path_decoded = decode_path(params[0])
            return {
                "path": full_path_decoded,
                "recipient": params[1],
                "deadline": params[2],
                "amountIn": params[3],
                "amountOutMinimum": params[4],
            }


def analyse_trade_by_receipt(
    web3: Web3,
    uniswap: UniswapV3Deployment,
    tx: dict,
    tx_hash: str | bytes,
    tx_receipt: dict,
    input_args: tuple | None = None,
) -> TradeSuccess | TradeFail:
    """Analyse a Uniswpa v3 trade.

    Figure out

    - The success of the trade

    - Slippage, etc.

    .. warning::

        Do not use `TradeSuccess.price` directly, as this price depends on in which order token0 and token1
        are in the pool smart contract. Use `TradeSuccess.get_human_price()` instead.

    :param tx_receipt:
        Transaction receipt

    :param input_args:
        The swap input arguments.

        If not given automatically decode from `tx`.
        You need to pass this for Enzyme transactions, because transaction payload is too complex to decode.
    """
    router = uniswap.swap_router
    assert tx_receipt["to"] == router.address, f"For now, we can only analyze naive trades to the router. This tx was to {tx_receipt['to']}, router is {router.address}"

    effective_gas_price = tx_receipt.get("effectiveGasPrice", 0)
    gas_used = tx_receipt["gasUsed"]

    # TODO: Unit test this code path
    # Tx reverted
    if tx_receipt["status"] != 1:
        reason = fetch_transaction_revert_reason(web3, tx_hash)
        return TradeFail(gas_used, effective_gas_price, revert_reason=reason)

    if input_args is None:
        # Decode inputs going to the Uniswap swap
        # https://stackoverflow.com/a/70737448/315168
        function, params_struct = router.decode_function_input(get_transaction_data_field(tx))
        input_args = get_input_args(params_struct["params"])
        assert function.fn_name == "exactInput", f"Unsupported Uniswap v3 trade function {function}"
    else:
        # Decode from Enzyme stored input
        # Note that this is how Web3.py presents this
        # <Function exactInput((bytes,address,uint256,uint256,uint256)) bound to ((b"'\x91\xbc\xa1\xf2\xdeFa\xed\x88\xa3\x0c\x99\xa7\xa9D\x9a\xa8At\x00\x01\xf4\rP\x0b\x1d\x8e\x8e\xf3\x1e!\xc9\x9d\x1d\xb9\xa6DM:\xdf\x12p", '0xfC3035f60A3d862E0753eA3D2Eec7679227E8B37', 9223372036854775808, 1000000, 1144586690647966336),)>
        input_args = get_input_args(input_args[0])

    path = input_args["path"]

    assert len(path), f"Seeing a bad path Uniswap routing {path}"

    amount_in = input_args["amountIn"]
    amount_out_min = input_args["amountOutMinimum"]

    in_token_details = fetch_erc20_details(web3, path[0])
    out_token_details = fetch_erc20_details(web3, path[-1])

    # The tranasction logs are likely to contain several events like Transfer,
    # Sync, etc. We are only interested in Swap events.
    # See https://docs.uniswap.org/contracts/v3/reference/core/interfaces/pool/IUniswapV3PoolEvents#swap
    swap_events = uniswap.PoolContract.events.Swap().process_receipt(tx_receipt, errors=DISCARD)

    if len(swap_events) == 1:
        event = swap_events[0]

        props = event["args"]
        amount0 = props["amount0"]
        amount1 = props["amount1"]
        tick = props["tick"]

        # Depending on the path, the out token can pop up as amount0Out or amount1Out
        # For complex swaps (unspported) we can have both
        assert (amount0 > 0 and amount1 < 0) or (amount0 < 0 and amount1 > 0), "Unsupported swap type"

        if amount0 > 0:
            amount_in = amount0
            amount_out = amount1
        else:
            amount_in = amount1
            amount_out = amount0

        # NOTE: LP fee paid in token_in amount, not USD
        pool = fetch_pool_details(web3, event["address"])
        lp_fee_paid = float(amount_in * pool.fee / 10**in_token_details.decimals)
    else:
        first_event = swap_events[0]
        if first_event["args"]["amount0"] > 0:
            amount_in = first_event["args"]["amount0"]
        else:
            amount_in = first_event["args"]["amount1"]

        last_event = swap_events[-1]
        if last_event["args"]["amount0"] > 0:
            amount_out = last_event["args"]["amount1"]
        else:
            amount_out = last_event["args"]["amount0"]

        # NOTE: with multiple hops, we make a temporary workaround that all pools in the path have similar fees
        first_pool = fetch_pool_details(web3, first_event["address"])
        lp_fee_paid = float(amount_in * first_pool.fee / 10**in_token_details.decimals) * len(swap_events)

    assert amount_out < 0, "amount out should be negative for Uniswap v3 swap"

    amount_out_cleaned = Decimal(abs(amount_out)) / Decimal(10**out_token_details.decimals)
    amount_in_cleaned = Decimal(abs(amount_in)) / Decimal(10**in_token_details.decimals)

    price = amount_out_cleaned / amount_in_cleaned

    return TradeSuccess(
        gas_used,
        effective_gas_price,
        path,
        amount_in,
        amount_out_min,
        abs(amount_out),
        price,
        in_token_details.decimals,
        out_token_details.decimals,
        token0=in_token_details,
        token1=out_token_details,
        lp_fee_paid=lp_fee_paid,
    )
