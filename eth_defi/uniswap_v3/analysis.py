from typing import TypedDict

from web3 import Web3
from web3.logs import DISCARD

from eth_defi.abi import get_transaction_data_field
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.token import fetch_erc20_details
from eth_defi.trade import TradeFail, TradeSuccess
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.uniswap_v3.pool import fetch_pool_details
from eth_defi.uniswap_v3.utils import decode_path


def get_input_args(params: tuple | dict) -> tuple:
    """Names and decodes input arguments from router.decode_function_input()
    Note there is no support yet for SwapRouter02, it does not accept a deadline parameter
    See: https://docs.uniswap.org/contracts/v3/reference/periphery/interfaces/ISwapRouter#exactinputparams

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

    # The tranasction logs are likely to contain several events like Transfer,
    # Sync, etc. We are only interested in Swap events.
    # See https://docs.uniswap.org/contracts/v3/reference/core/interfaces/pool/IUniswapV3PoolEvents#swap
    swap_events = uniswap.PoolContract.events.Swap().process_receipt(tx_receipt, errors=DISCARD)

    # NOTE: we are interested in the last swap event
    # AttributeDict({'args': AttributeDict({'sender': '0x6D411e0A54382eD43F02410Ce1c7a7c122afA6E1', 'recipient': '0xC2c2C1C8871C189829d3CCD169010F430275BC70', 'amount0': -292184487391376249, 'amount1': 498353865, 'sqrtPriceX96': 3267615572280113943555521, 'liquidity': 41231056256176602, 'tick': -201931}), 'event': 'Swap', 'logIndex': 3, 'transactionIndex': 0, 'transactionHash': HexBytes('0xe7fff8231effe313010aed7d973fdbe75f58dc4a59c187b230e3fc101c58ec97'), 'address': '0x4529B3F2578Bf95c1604942fe1fCDeB93F1bb7b6', 'blockHash': HexBytes('0xe06feb724020c57c6a0392faf7db29fedf4246ce5126a5b743b2627b7dc69230'), 'blockNumber': 24})
    event = swap_events[-1]

    props = event["args"]
    amount0 = props["amount0"]
    amount1 = props["amount1"]
    tick = props["tick"]

    pool_address = event["address"]
    pool = fetch_pool_details(web3, pool_address)

    # Depending on the path, the out token can pop up as amount0Out or amount1Out
    # For complex swaps (unspported) we can have both
    assert (amount0 > 0 and amount1 < 0) or (amount0 < 0 and amount1 > 0), "Unsupported swap type"

    amount_out = amount0 if amount0 < 0 else amount1
    assert amount_out < 0, "amount out should be negative for uniswap v3"

    in_token_details = fetch_erc20_details(web3, path[0])
    out_token_details = fetch_erc20_details(web3, path[-1])
    price = pool.convert_price_to_human(tick)  # Return price of token0/token1

    amount_in = amount0 if amount0 > 0 else amount1
    lp_fee_paid = float(amount_in * pool.fee / 10**in_token_details.decimals)

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
        token0=pool.token0,
        token1=pool.token1,
        lp_fee_paid=lp_fee_paid,
    )
