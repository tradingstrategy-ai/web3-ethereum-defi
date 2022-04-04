from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
from web3 import Web3

from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.abi import get_deployed_contract

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

    quote_token = get_deployed_contract(web3, "ERC20MockDecimals.json", quote_token)

    # approve router to spend tokens
    quote_token.functions.approve(router.address, 99999 * 1e18).transact({"from": buy_account})

    quote_token_decimals = quote_token.functions.decimals().call()

    quote_token_amount = 10**quote_token_decimals  # exactly 1 unit of quote token

    path = [quote_token.address, base_token]
    # Figure out base_token/quote_token trading pair
    # Buy base_token with buy_account
    tx_hash = router.functions.swapExactTokensForTokens(
        quote_token_amount,
        0,
        path,
        buy_account,
        FOREVER_DEADLINE
    ).transact({"from": buy_account})

    print(tx_hash)

    # Measure the loss as "buy tax"
    # Transfer tokens to sell_account
    # Measure the loss as "transfer tax"
    # Sell tokens
    # Measure the loss as "sell tax"