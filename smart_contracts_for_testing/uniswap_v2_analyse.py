"""Deploy a mock Uniswap v2 like decentralised exchange.

Compatible exchanges include, but not limited to

- Uniswap v2

- Sushiswap v2

- Pancakeswap v2 and v3

- QuickSwap

- TraderJoe

Under the hood we are using `SushiSwap v2 contracts <github.com/sushiswap/sushiswap>`_ for the deployment.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple, Optional, Union, List

from eth_typing import HexAddress
from web3 import Web3

from smart_contracts_for_testing.uniswap_v2 import UniswapV2Deployment



@dataclass
class TradeResult:
    #: What as the gas price used in wei
    effective_gas_price: int

    def get_effective_gas_price_gwei(self) -> Decimal:
        return Decimal(self.effective_gas_price) / Decimal(10**9)


@dataclass
class SuccessInfo(TradeResult):

    #: Routing path that was used for this trade
    path: List[HexAddress]

    amount_in: int
    amount_out_min: int


@dataclass
class FailInfo(TradeResult):
    revert_message: str


def analyse_trade(web3: Web3, uniswap: UniswapV2Deployment, tx_hash: hash) -> Union[SuccessInfo, FailInfo]:
    """Figure out fees paid in a Uniswap.

    Analyses trade fees, etc. based on the event signatures in the transaction.
    Works only simp;e trades.

    :param tx_receipt: Transaction receipt for the swap
    """

    # Example tx https://etherscan.io/tx/0xa8e6d47fb1429c7aec9d30332eafaeb515c8dfa73ab413c48560d8d6060c3193#eventlog
    # swapExactTokensForTokens

    tx = web3.eth.get_transaction(tx_hash)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    router = uniswap.router
    assert tx_receipt["to"] == router.address, f"For now, we can only analyze naive trades to the router. This tx was to {tx_receipt['to']}, router is {router.address}"

    effective_gas_price = tx_receipt["effectiveGasPrice"]

    # Tx reverted
    if tx_receipt["status"] != 1:
        return FailInfo(effective_gas_price)

    # https://stackoverflow.com/a/70737448/315168
    function, input_args = router.decode_function_input(tx["data"])

    path = input_args["path"]
    amount_in = input_args["amountIn"]
    amount_out_min = input_args["amountOutMin"]

    return SuccessInfo(
        effective_gas_price,
        path,
        amount_in,
        amount_out_min
    )


_GOOD_TRANSFER_SIGNATURES = (
    # https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/token/ERC20/IERC20.sol#L75
    "Transfer(address,address,uint)",
    # WETH9 wtf Transfer()
    # https://github.com/gnosis/canonical-weth/blob/master/contracts/WETH9.sol#L24
    "Transfer(address,address,uint,uint)",
)