"""Velora (ParaSwap) swap transaction building.

See `Velora API documentation <https://developers.velora.xyz>`__ for more details.
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from pprint import pformat

import requests
from eth_typing import HexAddress
from hexbytes import HexBytes

from eth_defi.token import TokenDetails
from eth_defi.velora.api import VeloraAPIError, get_velora_api_url
from eth_defi.velora.quote import VeloraQuote


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class VeloraSwapTransaction:
    """Velora swap transaction data.

    Contains all information needed to execute a swap on Augustus Swapper.
    """

    #: Token we are going to receive (token out)
    buy_token: TokenDetails

    #: Token we are losing (token in)
    sell_token: TokenDetails

    #: Amount of sell_token to spend (human-readable decimals)
    amount_in: Decimal

    #: Minimum amount of buy_token to receive (human-readable decimals)
    min_amount_out: Decimal

    #: Augustus Swapper contract address
    to: HexAddress

    #: Raw calldata to execute on Augustus Swapper
    calldata: HexBytes

    #: ETH value to send (usually 0 for ERC-20 swaps)
    value: int

    #: Original price route from quote (for reference)
    price_route: dict


@dataclass(slots=True, frozen=True)
class VeloraSwapResult:
    """Result of a Velora swap execution.

    Contains transaction hash and amounts from the executed swap.
    """

    #: Transaction hash
    tx_hash: HexBytes

    #: Token we received
    buy_token: TokenDetails

    #: Token we sold
    sell_token: TokenDetails

    #: Amount of sell_token spent (raw units)
    amount_sold: int

    #: Amount of buy_token received (raw units)
    amount_bought: int

    def get_amount_sold_decimal(self) -> Decimal:
        """Get amount sold in human-readable decimals."""
        return self.sell_token.convert_to_decimals(self.amount_sold)

    def get_amount_bought_decimal(self) -> Decimal:
        """Get amount bought in human-readable decimals."""
        return self.buy_token.convert_to_decimals(self.amount_bought)


def fetch_velora_swap_transaction(
    quote: VeloraQuote,
    user_address: HexAddress | str,
    slippage_bps: int = 250,
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=30),
    partner: str | None = None,
    deadline: int | None = None,
) -> VeloraSwapTransaction:
    """Build a Velora swap transaction from a quote.

    This calls the Velora /transactions endpoint to build the actual
    swap transaction calldata that can be executed on Augustus Swapper.

    Example:

    .. code-block:: python

        from eth_defi.velora.quote import fetch_velora_quote
        from eth_defi.velora.swap import fetch_velora_swap_transaction

        # First get a quote
        quote = fetch_velora_quote(
            from_=vault_address,
            buy_token=usdc,
            sell_token=weth,
            amount_in=Decimal("0.1"),
        )

        # Then build the swap transaction
        swap_tx = fetch_velora_swap_transaction(
            quote=quote,
            user_address=vault_address,
            slippage_bps=100,  # 1% slippage
        )

        # Execute on Augustus Swapper
        # tx = web3.eth.send_transaction({
        #     "to": swap_tx.to,
        #     "data": swap_tx.calldata,
        #     "value": swap_tx.value,
        # })

    :param quote:
        Quote from fetch_velora_quote()

    :param user_address:
        Address that will execute the swap (the Safe address for vault integration)

    :param slippage_bps:
        Allowed slippage in basis points (e.g., 250 = 2.5%)

    :param api_timeout:
        API request timeout

    :param partner:
        Partner name for analytics tracking

    :param deadline:
        UNIX timestamp after which the transaction is invalid

    :return:
        Swap transaction data ready for execution

    :raise VeloraAPIError:
        If the API returns an error
    """
    chain_id = quote.buy_token.chain_id
    base_url = get_velora_api_url()
    final_url = f"{base_url}/transactions/{chain_id}"

    # Build request body
    body = {
        "srcToken": quote.sell_token.address,
        "srcDecimals": quote.sell_token.decimals,
        "destToken": quote.buy_token.address,
        "destDecimals": quote.buy_token.decimals,
        "srcAmount": quote.data["srcAmount"],
        "priceRoute": quote.data,
        "slippage": slippage_bps,
        "userAddress": user_address,
    }

    if partner:
        body["partner"] = partner

    if deadline is not None:
        body["deadline"] = deadline

    # Query params for vault integration
    params = {
        "ignoreChecks": "true",
        "ignoreGasEstimate": "true",
    }

    logger.info(
        "Building Velora swap tx: %s -> %s, slippage: %d bps",
        quote.sell_token.symbol,
        quote.buy_token.symbol,
        slippage_bps,
    )
    logger.debug("Velora tx request: %s body=%s params=%s", final_url, pformat(body), params)

    response = requests.post(
        final_url,
        json=body,
        params=params,
        timeout=api_timeout.total_seconds(),
    )

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        error_message = response.text
        logger.error("Error building Velora swap tx: %s", error_message)
        raise VeloraAPIError(f"Error building Velora swap tx: {response.status_code} {error_message}\nBody: {pformat(body)}\nEndpoint: {final_url}") from e

    data = response.json()
    logger.debug("Velora tx response: %s", pformat(data))

    # Calculate min amount out based on slippage
    dest_amount = int(quote.data["destAmount"])
    min_amount_out_raw = dest_amount * (10000 - slippage_bps) // 10000
    min_amount_out = quote.buy_token.convert_to_decimals(min_amount_out_raw)

    return VeloraSwapTransaction(
        buy_token=quote.buy_token,
        sell_token=quote.sell_token,
        amount_in=quote.get_sell_amount(),
        min_amount_out=min_amount_out,
        to=HexAddress(data["to"]),
        calldata=HexBytes(data["data"]),
        value=int(data.get("value", "0")),
        price_route=quote.data,
    )
