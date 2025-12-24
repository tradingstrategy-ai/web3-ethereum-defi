"""Uniswap v2 individual trade analysis."""

import logging
from decimal import Decimal
from typing import Union

from hexbytes import HexBytes

from eth_defi.revert_reason import fetch_transaction_revert_reason
from web3 import Web3
from web3.logs import DISCARD

from eth_defi.abi import get_deployed_contract
from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.trade import TradeFail, TradeSuccess


logger = logging.getLogger(__name__)


def analyse_trade_by_hash(web3: Web3, uniswap: UniswapV2Deployment, tx_hash: str | HexBytes) -> Union[TradeSuccess, TradeFail]:
    """Analyse details of a Uniswap trade based on a transaction id.

    Analyses trade fees, etc. based on the event signatures in the transaction.
    Works only simp;e trades.

    Currently only supports simple analysis where there is one input token
    and one output token.

    .. note ::

        Only works if you have one trade per transaction.

    Example:

    .. code-block:: python

        analysis = analyse_trade(web3, uniswap_v2, tx_hash)
        assert isinstance(analysis, TradeSuccess)  # Trade was successful
        assert analysis.price == pytest.approx(Decimal("1744.899124998896692270848706"))  # ETC/USDC price
        assert analysis.get_effective_gas_price_gwei() == 1  # What gas was paid for this price

    .. note ::

        This code is still much under development and unlikely to support any
        advanced use cases yet.

    :param web3:
        Web3 instance
    :param uniswap:
        Uniswap deployment description
    :param tx_hash:
        Transaction hash as a string
    :return:
        :py:class:`TradeSuccess` or :py:class:`TradeFail` instance
    """

    # Example tx https://etherscan.io/tx/0xa8e6d47fb1429c7aec9d30332eafaeb515c8dfa73ab413c48560d8d6060c3193#eventlog
    # swapExactTokensForTokens

    tx = web3.eth.get_transaction(tx_hash)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
    return analyse_trade_by_receipt(web3, uniswap, tx, tx_hash, tx_receipt)


def analyse_trade_by_receipt(
    web3: Web3,
    uniswap: UniswapV2Deployment,
    tx: dict | None,
    tx_hash: str,
    tx_receipt: dict | None,
    pair_fee: float = None,
    sender_address: str | None = None,
) -> Union[TradeSuccess, TradeFail]:
    """Analyse details of a Uniswap trade based on already received receipt.

    See also :py:func:`analyse_trade_by_hash`.
    This function is more ideal for the cases where you know your transaction is already confirmed
    and you do not need to poll the chain for a receipt.

    .. note ::

        Only works if you have one trade per transaction.

    Example:

    .. code-block:: python

        tx_hash = router.functions.swapExactTokensForTokens(
            all_weth_amount,
            0,
            reverse_path,
            user_1,
            FOREVER_DEADLINE,
        ).transact({"from": user_1})

        tx = web3.eth.get_transaction(tx_hash)
        receipt = web3.eth.get_transaction_receipt(tx_hash)

        analysis = analyse_trade_by_receipt(web3, uniswap_v2, tx, tx_hash, receipt)
        assert isinstance(analysis, TradeSuccess)
        assert analysis.price == pytest.approx(Decimal("1744.899124998896692270848706"))

    :param web3:
        Web3 instance
    :param uniswap:
        Uniswap deployment description
    :param tx:
        Transaction data as a dictionary: needs to have `data` or `input` field to decode
    :param tx_hash:
        Transaction hash: needed for the call for the revert reason)
    :param tx_receipt:
        Transaction receipt to analyse
    :param pair_fee:
        The lp fee for this pair.
    :return:
        :py:class:`TradeSuccess` or :py:class:`TradeFail` instance
    """

    pair = uniswap.PairContract

    # Example tx https://etherscan.io/tx/0xa8e6d47fb1429c7aec9d30332eafaeb515c8dfa73ab413c48560d8d6060c3193#eventlog
    # swapExactTokensForTokens

    router = uniswap.router

    if tx is None:
        tx = web3.eth.get_transaction(tx_hash)

    if tx_receipt is None:
        tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    # assert tx_receipt["to"] == router.address, f"For now, we can only analyze naive trades to the router. This tx was to {tx_receipt['to']}, router is {router.address}"

    effective_gas_price = tx_receipt.get("effectiveGasPrice", 0)
    gas_used = tx_receipt["gasUsed"]

    # TODO: Unit test this code path
    # Tx reverted
    if tx_receipt["status"] != 1:
        reason = fetch_transaction_revert_reason(web3, tx_hash)
        return TradeFail(gas_used, effective_gas_price, revert_reason=reason)

    # Decode inputs going to the Uniswap swap
    # https://stackoverflow.com/a/70737448/315168
    # function, input_args = router.decode_function_input(get_transaction_data_field(tx))
    # path = input_args["path"]
    # assert function.fn_name == "swapExactTokensForTokens", f"Unsupported Uniswap v2 trade function {function}"
    # assert len(path), f"Seeing a bad path Uniswap routing {path}"

    # amount_in = input_args["amountIn"]
    # amount_out_min = input_args["amountOutMin"]

    # Decode the last output.
    # Assume Swap events go in the same chain as path
    swap = pair.events.Swap()

    # The tranasction logs are likely to contain several events like Transfer,
    # Sync, etc. We are only interested in Swap events.
    events = swap.process_receipt(tx_receipt, errors=DISCARD)

    assert len(events) > 0, f"No swap events detected:{tx_receipt}"

    # Reconstruct path
    path = []
    for evt in events:
        amount0_in = evt["args"]["amount0In"]
        amount1_in = evt["args"]["amount1In"]
        assert amount0_in == 0 or amount1_in == 0, "Unsupported analysis for multiple inputs"
        pair = get_deployed_contract(web3, "sushi/UniswapV2Pair.json", events[0]["address"])
        if amount0_in:
            token_address = pair.functions.token0().call()
            amount_in = amount0_in
        else:
            token_address = pair.functions.token1().call()
            amount_in = amount1_in
        path.append(token_address)

    amount0_in = events[0]["args"]["amount0In"]
    amount1_in = events[0]["args"]["amount1In"]
    assert amount0_in == 0 or amount1_in == 0, "Unsupported analysis for multiple inputs"

    first_pair = get_deployed_contract(web3, "sushi/UniswapV2Pair.json", events[0]["address"])
    if amount0_in:
        in_token_address = first_pair.functions.token0().call()
        amount_in = amount0_in
    else:
        in_token_address = first_pair.functions.token1().call()
        amount_in = amount1_in

    in_token_details = fetch_erc20_details(web3, in_token_address)

    # (AttributeDict({'args': AttributeDict({'sender': '0xDe09E74d4888Bc4e65F589e8c13Bce9F71DdF4c7', 'to': '0x2B5AD5c4795c026514f8317c7a215E218DcCD6cF', 'amount0In': 0, 'amount1In': 500000000000000000000, 'amount0Out': 284881561276680858, 'amount1Out': 0}), 'event': 'Swap', 'logIndex': 4, 'transactionIndex': 0, 'transactionHash': HexBytes('0x58312ff98147ca16c3a81019c8bca390cd78963175e4c0a30643d45d274df947'), 'address': '0x68931307eDCB44c3389C507dAb8D5D64D242e58f', 'blockHash': HexBytes('0x1222012923c7024b1d49e1a3e58552b89e230f8317ac1b031f070c4845d55db1'), 'blockNumber': 12}),)
    amount0_out = events[-1]["args"]["amount0Out"]
    amount1_out = events[-1]["args"]["amount1Out"]

    # Depending on the path, the out token can pop up as amount0Out or amount1Out
    # For complex swaps (unspported) we can have both
    assert amount0_out == 0 or amount1_out == 0, "Unsupported swap type: only one output token supported"

    last_pair = get_deployed_contract(web3, "sushi/UniswapV2Pair.json", events[-1]["address"])
    if amount0_out:
        out_token_address = last_pair.functions.token0().call()
        amount_out = amount0_out
    else:
        out_token_address = last_pair.functions.token1().call()
        amount_out = amount1_out

    out_token_details = fetch_erc20_details(web3, out_token_address)
    path.append(out_token_address)

    amount_out_cleaned = Decimal(amount_out) / Decimal(10**out_token_details.decimals)
    amount_in_cleaned = Decimal(amount_in) / Decimal(10**in_token_details.decimals)

    price = amount_out_cleaned / amount_in_cleaned

    lp_fee_paid = float(amount_in * pair_fee / 10**in_token_details.decimals) if pair_fee else None

    erc_20 = out_token_details.contract
    transfer = erc_20.events.Transfer()
    events = transfer.process_receipt(tx_receipt, errors=DISCARD)

    assert len(events) > 1, f"Uniswap v2 lacked transfer events: {tx_receipt}"
    filter_by_token_out_events = [e for e in events if e["address"].lower() == out_token_details.address_lower]

    if len(filter_by_token_out_events) >= 1:
        last_transfer = filter_by_token_out_events[-1]

        wallet_amount_in = last_transfer["args"]["value"]
        if wallet_amount_in != amount_out:
            untaxed_amount_out = amount_out
            amount_out = wallet_amount_in
        else:
            untaxed_amount_out = amount_out
    else:
        untaxed_amount_out = None

    return TradeSuccess(
        gas_used,
        effective_gas_price,
        path=path,
        amount_in=amount_in,
        amount_out_min=None,
        amount_out=amount_out,
        price=price,
        amount_in_decimals=in_token_details.decimals,
        amount_out_decimals=out_token_details.decimals,
        token0=None,
        token1=None,
        lp_fee_paid=lp_fee_paid,
        untaxed_amount_out=untaxed_amount_out,
    )


_GOOD_TRANSFER_SIGNATURES = (
    # https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/token/ERC20/IERC20.sol#L75
    "Transfer(address,address,uint)",
    # WETH9 wtf Transfer()
    # https://github.com/gnosis/canonical-weth/blob/master/contracts/WETH9.sol#L24
    "Transfer(address,address,uint,uint)",
)
