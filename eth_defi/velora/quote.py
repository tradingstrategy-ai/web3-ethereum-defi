"""Fetching Velora (ParaSwap) price quotes.

See `Velora API documentation <https://developers.velora.xyz>`__ for more details.
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from pprint import pformat

import requests
from eth_typing import HexAddress

from eth_defi.token import TokenDetails
from eth_defi.velora.api import VeloraAPIError, get_velora_api_url


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class VeloraQuote:
    """Velora price quote response.

    Contains the optimal route and pricing information for a swap.

    Example response data:

    .. code-block:: python

        {"blockNumber": 12345678, "network": 1, "srcToken": "0x...", "srcDecimals": 18, "srcAmount": "1000000000000000000", "destToken": "0x...", "destDecimals": 6, "destAmount": "3500000000", "bestRoute": [...], "gasCostUSD": "5.93", "contractAddress": "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57", "contractMethod": "multiSwap", "srcUSD": "3500.00", "destUSD": "3500.00"}
    """

    #: Token we are going to receive (token out)
    buy_token: TokenDetails

    #: Token we are losing (token in)
    sell_token: TokenDetails

    #: Raw data from Velora /prices endpoint (the priceRoute)
    #:
    #: This is passed to the /transactions endpoint to build the swap tx.
    data: dict

    def get_buy_amount(self) -> Decimal:
        """Get the buy amount from the quote.

        :return:
            Amount of buy token we will receive (human-readable decimals)
        """
        dest_amount = int(self.data["destAmount"])
        return self.buy_token.convert_to_decimals(dest_amount)

    def get_sell_amount(self) -> Decimal:
        """Get the sell amount from the quote.

        :return:
            Amount of sell token we will spend (human-readable decimals)
        """
        src_amount = int(self.data["srcAmount"])
        return self.sell_token.convert_to_decimals(src_amount)

    def get_price(self) -> Decimal:
        """Get the price implied by the quote (buy amount / sell amount).

        :return:
            Price as buy_token per sell_token
        """
        return self.get_buy_amount() / self.get_sell_amount()

    def get_gas_cost_usd(self) -> Decimal | None:
        """Get estimated gas cost in USD.

        :return:
            Gas cost in USD or None if not available
        """
        gas_cost = self.data.get("gasCostUSD")
        if gas_cost:
            return Decimal(gas_cost)
        return None

    def pformat(self) -> str:
        """Pretty format the quote data for logging."""
        summary = {
            "Buy": self.buy_token.symbol,
            "Sell": self.sell_token.symbol,
            "Price": str(self.get_price()),
            "Buy amount": str(self.get_buy_amount()),
            "Sell amount": str(self.get_sell_amount()),
            "Gas cost USD": str(self.get_gas_cost_usd()),
        }
        return pformat(summary)


def fetch_velora_quote(
    from_: HexAddress | str,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=30),
    partner: str | None = None,
    max_impact: int | None = None,
) -> VeloraQuote:
    """Fetch a Velora price quote for a given token pair.

    This calls the Velora /prices endpoint to get the optimal route
    and pricing for a swap.

    Example:

    .. code-block:: python

        from decimal import Decimal
        from eth_defi.token import fetch_erc20_details
        from eth_defi.velora.quote import fetch_velora_quote

        weth = fetch_erc20_details(web3, "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
        usdc = fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

        quote = fetch_velora_quote(
            from_=vault_address,
            buy_token=usdc,
            sell_token=weth,
            amount_in=Decimal("0.1"),
        )

        print(f"Price: {quote.get_price()}")
        print(f"Will receive: {quote.get_buy_amount()} USDC")

    :param from_:
        Address that will execute the swap (for routing optimisation)

    :param buy_token:
        Token to receive (destination token)

    :param sell_token:
        Token to sell (source token)

    :param amount_in:
        Amount of sell_token to swap (human-readable decimals)

    :param api_timeout:
        API request timeout

    :param partner:
        Partner name for analytics tracking

    :param max_impact:
        Maximum price impact threshold percentage (default 15%)

    :return:
        Quote containing route and pricing information

    :raise VeloraAPIError:
        If the API returns an error
    """
    chain_id = buy_token.chain_id
    assert chain_id == sell_token.chain_id, "Tokens must be on the same chain"

    base_url = get_velora_api_url()
    final_url = f"{base_url}/prices"

    # Build query parameters
    params = {
        "srcToken": sell_token.address,
        "srcDecimals": sell_token.decimals,
        "destToken": buy_token.address,
        "destDecimals": buy_token.decimals,
        "amount": str(sell_token.convert_to_raw(amount_in)),
        "side": "SELL",
        "network": chain_id,
        "userAddress": from_,
    }

    if partner:
        params["partner"] = partner

    if max_impact is not None:
        params["maxImpact"] = max_impact

    logger.info("Fetching Velora quote: %s -> %s, amount: %s", sell_token.symbol, buy_token.symbol, amount_in)
    logger.debug("Velora quote request: %s params=%s", final_url, params)

    response = requests.get(final_url, params=params, timeout=api_timeout.total_seconds())

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        error_message = response.text
        logger.error("Error fetching Velora quote: %s", error_message)
        raise VeloraAPIError(f"Error fetching Velora quote: {response.status_code} {error_message}\nParams: {pformat(params)}\nEndpoint: {final_url}") from e

    data = response.json()
    logger.debug("Velora quote response: %s", pformat(data))

    # API returns {"priceRoute": {...}}, extract the priceRoute
    price_route = data.get("priceRoute", data)

    return VeloraQuote(
        buy_token=buy_token,
        sell_token=sell_token,
        data=price_route,
    )
