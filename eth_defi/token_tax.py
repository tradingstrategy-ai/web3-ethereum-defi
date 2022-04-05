from email.quoprimime import quote
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
from web3 import Web3

from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.abi import get_deployed_contract
from eth_defi.token import fetch_erc20_details

from dataclasses import dataclass


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
    transfer_tax:float

    #: How much % we lose the token when we sold it
    sell_tax: float


def estimate_token_taxes(
        uniswap: UniswapV2Deployment,
        base_token: HexAddress,
        quote_token: HexAddress,
        buy_account: HexAddress,
        sell_account: HexAddress
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

    :return:
        ToxTaxInfo tells us what we figure out about taxes.
        This can be later recorded to a database.
    """

    web3: Web3 = uniswap.web3
    router = uniswap.router

    quote_token_details = fetch_erc20_details(web3, quote_token)
    quote_token = quote_token_details.contract

    base_token_details = fetch_erc20_details(web3, base_token)
    base_token = base_token_details.contract

    # approve router to spend tokens
    quote_token.functions.approve(router.address, quote_token_details.convert_to_raw(99999)).transact({"from": buy_account})

    path = [quote_token.address, base_token.address]
    amountIn = quote_token_details.convert_to_raw(0.1)
    # Figure out base_token/quote_token trading pair
    initial_base_bal = base_token.functions.balanceOf(buy_account).call()
    # Buy base_token with buy_account
    router.functions.swapExactTokensForTokens(
        amountIn,
        0,
        path,
        buy_account,
        FOREVER_DEADLINE
    ).transact({"from": buy_account})

    received_amt = base_token.functions.balanceOf(buy_account).call() - initial_base_bal
    uniswap_price = router.functions.getAmountsOut(amountIn, path).call()[1]

    # Measure the loss as "buy tax"
    buy_tax = uniswap_price - received_amt
    buy_tax_percent = (buy_tax / uniswap_price) * 100

    print("Received Amount", received_amt)
    print("Uniswap Price", uniswap_price)
    print("Buy Tax", buy_tax)
    print("Buy Tax Percent", buy_tax_percent)


    # Transfer tokens to sell_account
    # Measure the loss as "transfer tax"
    base_token.functions.transfer(sell_account, received_amt).transact({"from": buy_account})

    received_amt_by_seller = base_token.functions.balanceOf(sell_account).call()

    transfer_tax = received_amt - received_amt_by_seller
    transfer_tax_percent = (transfer_tax / received_amt) * 100

    print("Seller Received Amount", received_amt_by_seller)
    print("Transfer Tax", transfer_tax)
    print("Transfer Tax Percent", transfer_tax_percent)

    # Sell tokens
    base_token.functions.approve(router.address, received_amt_by_seller).transact({"from": sell_account})
    path = [ base_token.address, quote_token.address]

    sell_tax = 0
    sell_tax_percent = 0
    try:
        # this method will revert in case of low liquidity of the token
        router.functions.swapExactTokensForTokens(
            received_amt_by_seller,
            0,
            path,
            sell_account,
            FOREVER_DEADLINE
        ).transact({"from": sell_account})
    except:
        print("Low liquidity. Sell method failed")

     # Measure the loss as "sell tax"
    received_amt_after_sell = quote_token.functions.balanceOf(sell_account).call()
    uniswap_price = router.functions.getAmountsOut(received_amt_by_seller, path).call()[1]

    if received_amt_after_sell > 0:
        sell_tax =   uniswap_price - received_amt_after_sell
        sell_tax_percent =  (sell_tax / uniswap_price) * 100 if uniswap_price > 0  else 0

    print("sell tax", sell_tax)
    print("sell tax percent", sell_tax_percent)