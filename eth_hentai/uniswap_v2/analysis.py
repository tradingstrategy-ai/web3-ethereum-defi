"""Uniswap v2 individual trade analysis."""
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple, Union

from eth_typing import HexAddress
from web3 import Web3
from web3.logs import DISCARD

from eth_hentai.abi import get_contract, get_transaction_data_field
from eth_hentai.token import fetch_erc20_details
from eth_hentai.uniswap_v2.deployment import UniswapV2Deployment


@dataclass
class TradeResult:
    """A base class for Success/Fail trade result."""

    #: How many units of gas we burned
    gas_used: int

    #: What as the gas price used in wei
    effective_gas_price: int

    def get_effective_gas_price_gwei(self) -> Decimal:
        return Decimal(self.effective_gas_price) / Decimal(10**9)


@dataclass
class TradeSuccess(TradeResult):
    """Describe the result of a successful Uniswap swap."""

    #: Routing path that was used for this trade
    path: List[HexAddress]

    amount_in: int
    amount_out_min: int
    amount_out: int

    #: Overall price paid as in token (first in the path) to out token (last in the path).
    #: Price includes any fees paid during the order routing path.
    #: Note that you get inverse price, if you route ETH-USD or USD-ETH e.g. are you doing buy or sell.
    price: Decimal

    # Token information book keeping
    amount_in_decimals: int
    amount_out_decimals: int


@dataclass
class TradeFail(TradeResult):
    """Describe the result of a failed Uniswap swap.

    The transaction reverted for a reason or another.
    """

    #: Revert reason is unlikely to be available depending on the JSON-RPC node
    #: `Please read this article on the topic <https://medium.com/authereum/getting-ethereum-transaction-revert-reasons-the-easy-way-24203a4d1844>`_.
    revert_message: Optional[str] = None


def analyse_trade(web3: Web3, uniswap: UniswapV2Deployment, tx_hash: hash) -> Union[TradeSuccess, TradeFail]:
    """Analyse details of a Uniswap trade based on a transaction id.

    Analyses trade fees, etc. based on the event signatures in the transaction.
    Works only simp;e trades.

    Currently only supports simple analysis where there is one input token
    and one output token.

    Example:

    .. code-block:: python

        analysis = analyse_trade(web3, uniswap_v2, tx_hash)
        assert isinstance(analysis, TradeSuccess)  # Trade was successful
        assert analysis.price == pytest.approx(Decimal('1744.899124998896692270848706'))  # ETC/USDC price
        assert analysis.get_effective_gas_price_gwei() == 1  # What gas was paid for this price

    .. note ::

        This code is still much under development and unlikely to support any
        advanced use cases yet.

    :param tx_receipt: Transaction receipt for the swap
    :return: `TradeSuccess` or `TradeFail` analysis
    """

    pair = get_contract(web3, "UniswapV2Pair.json")

    # Example tx https://etherscan.io/tx/0xa8e6d47fb1429c7aec9d30332eafaeb515c8dfa73ab413c48560d8d6060c3193#eventlog
    # swapExactTokensForTokens

    tx = web3.eth.get_transaction(tx_hash)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    router = uniswap.router
    assert tx_receipt["to"] == router.address, f"For now, we can only analyze naive trades to the router. This tx was to {tx_receipt['to']}, router is {router.address}"

    effective_gas_price = tx_receipt["effectiveGasPrice"]
    gas_used = tx_receipt["gasUsed"]

    # Tx reverted
    if tx_receipt["status"] != 1:
        # See https://github.com/ethereum/web3.py/issues/1941
        # TODO: Add a logic to get revert reason, needs archival node as per
        # https://medium.com/authereum/getting-ethereum-transaction-revert-reasons-the-easy-way-24203a4d1844
        return TradeFail(gas_used, effective_gas_price, revert_message=None)

    # Decode inputs going to the Uniswap swap
    # https://stackoverflow.com/a/70737448/315168
    function, input_args = router.decode_function_input(get_transaction_data_field(tx))
    path = input_args["path"]

    assert function.fn_name == "swapExactTokensForTokens", f"Unsupported Uniswap v2 trade function {function}"
    assert len(path), f"Seeing a bad path Uniswap routing {path}"

    amount_in = input_args["amountIn"]
    amount_out_min = input_args["amountOutMin"]

    # Decode the last output.
    # Assume Swap events go in the same chain as path
    swap = pair.events.Swap()

    # The tranasction logs are likely to contain several events like Transfer,
    # Sync, etc. We are only interested in Swap events.
    events = swap.processReceipt(tx_receipt, errors=DISCARD)

    # (AttributeDict({'args': AttributeDict({'sender': '0xDe09E74d4888Bc4e65F589e8c13Bce9F71DdF4c7', 'to': '0x2B5AD5c4795c026514f8317c7a215E218DcCD6cF', 'amount0In': 0, 'amount1In': 500000000000000000000, 'amount0Out': 284881561276680858, 'amount1Out': 0}), 'event': 'Swap', 'logIndex': 4, 'transactionIndex': 0, 'transactionHash': HexBytes('0x58312ff98147ca16c3a81019c8bca390cd78963175e4c0a30643d45d274df947'), 'address': '0x68931307eDCB44c3389C507dAb8D5D64D242e58f', 'blockHash': HexBytes('0x1222012923c7024b1d49e1a3e58552b89e230f8317ac1b031f070c4845d55db1'), 'blockNumber': 12}),)
    amount0_out = events[-1]["args"]["amount0Out"]
    amount1_out = events[-1]["args"]["amount1Out"]

    # Depending on the path, the out token can pop up as amount0Out or amount1Out
    # For complex swaps (unspported) we can have both
    assert amount0_out == 0 or amount1_out == 0, "Unsupported swap type"

    amount_out = amount0_out if amount0_out > 0 else amount1_out

    in_token_details = fetch_erc20_details(web3, path[0])
    out_token_details = fetch_erc20_details(web3, path[-1])

    amount_out_cleaned = Decimal(amount_out) / Decimal(10**out_token_details.decimals)
    amount_in_cleaned = Decimal(amount_in) / Decimal(10**in_token_details.decimals)

    price = amount_out_cleaned / amount_in_cleaned

    return TradeSuccess(
        gas_used,
        effective_gas_price,
        path,
        amount_in,
        amount_out_min,
        amount_out,
        price,
        in_token_details.decimals,
        out_token_details.decimals,
    )


_GOOD_TRANSFER_SIGNATURES = (
    # https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/token/ERC20/IERC20.sol#L75
    "Transfer(address,address,uint)",
    # WETH9 wtf Transfer()
    # https://github.com/gnosis/canonical-weth/blob/master/contracts/WETH9.sol#L24
    "Transfer(address,address,uint,uint)",
)
