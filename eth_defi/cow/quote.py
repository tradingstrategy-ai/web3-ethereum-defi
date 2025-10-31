"""Fetching CowSwap quotes."""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from pprint import pformat
from typing import Literal

import requests
from eth_typing import HexAddress

from eth_defi.lagoon.cowswap import logger
from eth_defi.cow.api import CowAPIError, get_cowswap_api
from eth_defi.token import TokenDetails


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Quote:
    """CowSwap quote response.

    How does it look like:

    ..code-block:: python

        {'Buy': 'USDC.e',
         'Price': '3839.194418725202484702282634',
         'Sell': 'WETH',
         'expiration': '2025-10-31T08:56:32.245289888Z',
         'from': '0xdcc6d3a3c006bb4a10b448b1ee750966395622c6',
         'id': 59969377,
         'quote': {'appData': '0x0000000000000000000000000000000000000000000000000000000000000000',
                   'buyAmount': '374313',
                   'buyToken': '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8',
                   'buyTokenBalance': 'erc20',
                   'feeAmount': '2502202500000',
                   'kind': 'sell',
                   'partiallyFillable': False,
                   'receiver': None,
                   'sellAmount': '97497797500000',
                   'sellToken': '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
                   'sellTokenBalance': 'erc20',
                   'signingScheme': 'presign',
                   'validTo': 1761902192},
         'verified': True}

    """

    #: Token we are going to receive (token out)
    buy_token: TokenDetails

    #: Token we are losing (token in)
    sell_token: TokenDetails

    #: Raw data from CowSwap quote endpoint
    #:
    #: See Order structure at https://docs.cow.fi/cow-protocol/reference/apis/orderbook
    data: dict

    def get_buy_amount(self) -> Decimal:
        """Get the buy amount from the quote."""
        quote = self.data["quote"]
        buy_amount = int(quote["buyAmount"])
        return self.buy_token.convert_to_decimals(buy_amount)

    def get_sell_amount(self) -> Decimal:
        """Get the sell amount from the quote."""
        quote = self.data["quote"]
        sell_amount = int(quote["sellAmount"])
        return self.sell_token.convert_to_decimals(sell_amount)

    def get_price(self) -> Decimal:
        """Get the price implied by the quote (buy amount / sell amount)."""
        price = self.get_buy_amount() / self.get_sell_amount()
        return price

    def pformat(self) -> str:
        """Pretty format the quote data."""
        data = {
            "Buy": self.buy_token.symbol,
            "Sell": self.sell_token.symbol,
            "Price": str(self.get_price()),
        }
        data.update(self.data)
        return pformat(data)


def fetch_quote(
    from_: HexAddress | str,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=30),
    price_quality: Literal["fast", "verified", "optional"] = "fast",
) -> Quote:
    """Fetch a CowSwap quote for a given token pair and amounts.

    .. note ::

        Work in progress.

    https://docs.cow.fi/cow-protocol/reference/apis/quote

    Example:

    .. code-block:: python

        chain_id = web3.eth.chain_id
        weth = fetch_erc20_details(
            web3,
            WRAPPED_NATIVE_TOKEN[chain_id],
        )

        usdce = fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id])

        amount = Decimal("0.0001")
        quoted_data = fetch_quote(
            from_="0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6",  # Dummy address
            buy_token=usdce,
            sell_token=weth,
            amount_in=amount,
            min_amount_out=amount / 2,
        )

        assert quoted_data["from"].startswith("0x")
        # assert quoted_data["expiration"] == "1970-01-01T00:00:00Z"
        assert quoted_data["id"] is None
        assert quoted_data["verified"] is False
        assert quoted_data["quote"]["sellToken"] == "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
        assert quoted_data["quote"]["buyToken"] == "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
        assert quoted_data["quote"]["receiver"] is None
        assert int(quoted_data["quote"]["sellAmount"]) > 1
        assert int(quoted_data["quote"]["buyAmount"]) > 1
        assert quoted_data["quote"]["validTo"] > 1761863893
        assert quoted_data["quote"]["appData"] == "0x0000000000000000000000000000000000000000000000000000000000000000"
        assert int(quoted_data["quote"]["feeAmount"]) > 1
        assert quoted_data["quote"]["kind"] == "sell"
        assert quoted_data["quote"]["partiallyFillable"] is False
        assert quoted_data["quote"]["sellTokenBalance"] == "erc20"
        assert quoted_data["quote"]["buyTokenBalance"] == "erc20"
        assert quoted_data["quote"]["signingScheme"] == "presign"
    """

    chain_id = buy_token.chain_id
    base_url = get_cowswap_api(chain_id)
    final_url = f"{base_url}/api/v1/quote"

    # See OrderQuoteRequest
    # https://docs.cow.fi/cow-protocol/reference/apis/orderbook
    params = {
        "from": from_,
        "buyToken": buy_token.address,
        "sellToken": sell_token.address,
        "sellAmountBeforeFee": str(sell_token.convert_to_raw(amount_in)),
        "kind": "sell",
        "buyTokenBalance": "erc20",
        "sellTokenBalance": "erc20",
        "priceQuality": price_quality,
        "onchainOrder": True,
        "signingScheme": "presign",
    }
    response = requests.post(final_url, json=params, timeout=api_timeout.total_seconds())

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        error_message = response.text
        logger.error(f"Error posting CowSwap order: {error_message}")
        raise CowAPIError(f"Error posting CowSwap order: {response.status_code} {error_message}\nData was:{pformat(params)}\nEndpoint: {final_url}") from e

    data = response.json()
    return Quote(
        buy_token=buy_token,
        sell_token=sell_token,
        data=data,
    )
