"""Querying the buy tax, transfer tax & sell tax of an ERC20 token

Read also unit test suite tests/test_token_tax.py to see the retrieval of token taxes for ELEPHANT token on BSC

"""
import logging
from typing import Optional

from eth_typing import HexAddress
from web3.exceptions import ContractLogicError

from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
from web3 import Web3

from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.token import fetch_erc20_details, TokenDetails

from dataclasses import dataclass


logger = logging.getLogger(__name__)


class SwapError(Exception):
    """The swap method reverted due to low liquidity of either the base or quote token"""


class TransferFromError(Exception):
    """The token is likely broken.

    See KICK on Ethereum mainnet.
    """


class OutOfGasDuringTransfer(Exception):
    """The token is likely some sort of ponzi with restricted transfer.

    See WETH-CGT on Ethereum mainnet.
    """


class OutOfGasDuringSell(Exception):
    """The token is likely some sort of ponzi with restricted transfer.

    See WETH-DEXE on Ethereum mainnet: 0xde4ee8057785a7e8e800db58f9784845a5c2cbd6
    """


class TransferFailure(Exception):
    """The token transfer failed for some random reason.

    VM Exception while processing transaction: revert Protection: 30 sec/tx allowed

    https://tradingstrategy.ai/trading-view/polygon/quickswap/kmc-usdc
    """


class SellFailed(Exception):
    """Could not sell the token."""


class ApprovalFailure(Exception):
    """Yet another random Ganache failure."""


@dataclass
class TokenTaxInfo:
    """Different token taxes we figured out."""

    #: Token in the question
    base_token: HexAddress

    #: Which token we traded against it
    quote_token: HexAddress

    #: How much % we lost of the token on buy
    buy_tax: float

    #: How much % we lose the token when we transfer between addresses
    transfer_tax: float

    #: How much % we lose the token when we sold it
    sell_tax: float


def estimate_token_taxes(
    uniswap: UniswapV2Deployment,
    base_token: HexAddress,
    quote_token: HexAddress,
    buy_account: HexAddress,
    sell_account: HexAddress,
    buy_amount: float,
    approve=True,
    quote_token_details: Optional[TokenDetails] = None,
    base_token_details: Optional[TokenDetails] = None,
    gas_limit: Optional[int] = None,
    gas_price: Optional[int] = None,
) -> TokenTaxInfo:
    """Estimates different token taxes for a token by running Ganache simulations for it.

     :param uniswap:
         Uniswap deployment on a Ganache mainnet fork.
         Set up prior calling this function.
         See `ganache.py` and `test_ganache.py` for more details.

     :param base_token:
         The token of which tax properties we are figuring out.

     :param quote_token:
         Address of the quote token used for the trading pair. E.g. `BUDS`, `WBNB`
         Based on this information we can derive Uniswap trading pair address.

     :param buy_account:
         The account that does initial buy to measure the buy tax.
         This account must be loaded with gas money (ETH/BNB) and `quote_token`
         for a purchase.

     :param sell_account:
         The account that receives the token transfer and does the sell to measure the sell tax.
         This account must be loaded with gas money for the sell.

     :param approve:
         Perform quote token approval before wap test

    :param base_token_details:
         Pass base token details. If not given automatically fetch.

     :param quote_token_details:
         Pass quote token details. If not given automatically fetch.

     :param gas_limit:
         Use this gas limit for all transactions, so that
         we do not need to call eth_estimateGas on the node.

     :param gas_price:
         Use this gas price for all transactions, so that
         we do not need to call eth_estimateGas on the node.

     :return:
         ToxTaxInfo tells us what we figure out about taxes.
         This can be later recorded to a database.
    """
    web3: Web3 = uniswap.web3
    router = uniswap.router

    if not quote_token_details:
        # No need to consider brokeness of token metadata
        # when calculating tax
        quote_token_details = fetch_erc20_details(web3, quote_token, raise_on_error=False)
    quote_token = quote_token_details.contract

    if not base_token_details:
        # No need to consider brokeness of token metadata
        # when calculating tax
        base_token_details = fetch_erc20_details(web3, base_token, raise_on_error=False)
    base_token = base_token_details.contract

    if gas_limit:
        # Try to eliminate some RPC calls by not doing gas oracle requests
        # https://web3js.readthedocs.io/en/v1.2.11/web3-eth.html#sendtransaction
        generic_tx_params = {
            "gas": gas_limit,
            "gasPrice": gas_price,
        }
    else:
        generic_tx_params = {}

    # approve router to spend tokens
    if approve:
        quote_token.functions.approve(router.address, quote_token_details.convert_to_raw(buy_amount)).transact({"from": buy_account} | generic_tx_params)

    path = [quote_token.address, base_token.address]
    amountIn = quote_token_details.convert_to_raw(buy_amount)
    # Figure out base_token/quote_token trading pair
    initial_base_bal = base_token.functions.balanceOf(buy_account).call()

    # Buy base_token with buy_account
    try:
        logger.info("Attempting to buy for path %s", path)
        router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(amountIn, 0, path, buy_account, FOREVER_DEADLINE).transact({"from": buy_account} | generic_tx_params)
    except ContractLogicError as e:
        msg = str(e)
        if "TRANSFER_FAILED" in msg or "TRANSFER_FROM_FAILED" in msg:
            raise TransferFromError(f"Token does not co-operate:{base_token_details.symbol} - {quote_token_details.symbol}, {e} to router {router.address}") from e
        raise
    except Exception as e:
        raise SwapError(f"swapExactTokensForTokensSupportingFeeOnTransferTokens() buy failed:{base_token_details.symbol} - {quote_token_details.symbol}, {e} to router {router.address}") from e

    received_amt = base_token.functions.balanceOf(buy_account).call() - initial_base_bal

    if received_amt == 0:
        # Nothing was received when we bought the token, so assume 100% tax
        # Would cause division by zero later
        return TokenTaxInfo(base_token.address, quote_token.address, 1.0, 1.0, 1.0)

    uniswap_price = router.functions.getAmountsOut(amountIn, path).call()[1]

    # Measure the loss as "buy tax"
    buy_tax_percent = (uniswap_price - received_amt) / uniswap_price

    # Transfer tokens to sell_account
    # Measure the loss as "transfer tax"
    try:
        base_token.functions.transfer(sell_account, received_amt).transact({"from": buy_account} | generic_tx_params)
    except ValueError as e:
        if "out of gas" in str(e):
            raise OutOfGasDuringTransfer(f"Out of gas during transfer: {e}") from e
        else:
            raise TransferFailure(f"Transfer failure: {e}") from e

    received_amt_by_seller = base_token.functions.balanceOf(sell_account).call()

    transfer_tax_percent = (received_amt - received_amt_by_seller) / received_amt

    # Sell tokens
    try:
        base_token.functions.approve(router.address, received_amt_by_seller).transact({"from": sell_account} | generic_tx_params)
    except ValueError as e:
        if "out of gas" in str(e):
            raise ApprovalFailure() from e

    path = [base_token.address, quote_token.address]

    sell_tax = 0
    sell_tax_percent = 0
    try:
        # this method will revert in case of low liquidity of the token
        logger.info("Attempting to see for path %s", path)
        router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(received_amt_by_seller, 0, path, sell_account, FOREVER_DEADLINE).transact({"from": sell_account} | generic_tx_params)
    except ValueError as e:
        if "VM Exception while processing transaction: revert" in str(e):
            raise SellFailed(f"Could not sell {base_token_details.symbol} - {quote_token_details.symbol}: {e}") from e
        elif "out of gas" in str(e):
            raise OutOfGasDuringTransfer() from e
        raise
    except Exception as e:
        raise SwapError(f"Sell failed. swapExactTokensForTokensSupportingFeeOnTransferTokens() method failed: {base_token_details.symbol} - {quote_token_details.symbol}: {e}") from e

    # Measure the loss as "sell tax"
    received_amt_after_sell = quote_token.functions.balanceOf(sell_account).call()
    uniswap_price = router.functions.getAmountsOut(received_amt_by_seller, path).call()[1]

    if received_amt_after_sell > 0:
        sell_tax = uniswap_price - received_amt_after_sell
        sell_tax_percent = (sell_tax / uniswap_price) if uniswap_price > 0 else 0

    return TokenTaxInfo(base_token.address, quote_token.address, buy_tax_percent, transfer_tax_percent, sell_tax_percent)
