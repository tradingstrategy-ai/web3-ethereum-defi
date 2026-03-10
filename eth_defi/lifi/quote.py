"""LI.FI cross-chain quote fetching.

See `LI.FI API documentation <https://docs.li.fi>`__ for more details.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from pprint import pformat

import requests

from eth_defi.lifi.api import LifiAPIError, get_lifi_api_url, get_lifi_headers


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class LifiQuote:
    """LI.FI cross-chain quote response.

    Contains all information needed to execute a cross-chain transfer,
    including the ready-to-sign transaction request.
    """

    #: Source chain ID
    source_chain_id: int

    #: Target chain ID
    target_chain_id: int

    #: Source token address
    from_token: str

    #: Destination token address
    to_token: str

    #: Amount to send (raw, with decimals)
    from_amount: int

    #: Estimated amount to receive (raw, with decimals)
    estimate_to_amount: int

    #: Minimum guaranteed amount to receive including slippage (raw, with decimals)
    estimate_to_amount_min: int

    #: Estimated gas cost in USD
    gas_cost_usd: Decimal | None

    #: Estimated execution duration in seconds
    execution_duration: int | None

    #: Full API response data for reference
    data: dict

    def get_transaction_request(self) -> dict:
        """Get the transaction request from the quote.

        This is the ready-to-sign transaction with ``from``, ``to``,
        ``data``, ``value``, ``gasLimit``, ``gasPrice``, and ``chainId``.

        :return:
            Transaction request dict from LI.FI API
        """
        return self.data.get("transactionRequest", {})

    def __str__(self) -> str:
        return f"LifiQuote(chain {self.source_chain_id} -> {self.target_chain_id}, from_amount={self.from_amount}, est_to_amount={self.estimate_to_amount}, gas_cost=${self.gas_cost_usd})"


def fetch_lifi_quote(
    from_chain_id: int,
    to_chain_id: int,
    from_token: str,
    to_token: str,
    from_amount: int,
    from_address: str,
    to_address: str | None = None,
    slippage: float = 0.03,
    order: str = "CHEAPEST",
    api_timeout: float = 30,
) -> LifiQuote:
    """Fetch a cross-chain quote from the LI.FI API.

    Calls ``GET /v1/quote`` to get a bridge/swap quote with
    ready-to-sign transaction data.

    Example:

    .. code-block:: python

        from eth_defi.lifi.quote import fetch_lifi_quote
        from eth_defi.lifi.constants import LIFI_NATIVE_TOKEN_ADDRESS

        quote = fetch_lifi_quote(
            from_chain_id=1,  # Ethereum
            to_chain_id=42161,  # Arbitrum
            from_token=LIFI_NATIVE_TOKEN_ADDRESS,
            to_token=LIFI_NATIVE_TOKEN_ADDRESS,
            from_amount=10000000000000000,  # 0.01 ETH in wei
            from_address="0xYourWalletAddress",
        )

        tx_request = quote.get_transaction_request()
        # Sign and send tx_request

    :param from_chain_id:
        Source chain ID

    :param to_chain_id:
        Destination chain ID

    :param from_token:
        Source token address (use ``LIFI_NATIVE_TOKEN_ADDRESS`` for native token)

    :param to_token:
        Destination token address (use ``LIFI_NATIVE_TOKEN_ADDRESS`` for native token)

    :param from_amount:
        Amount to send in raw units (wei for ETH)

    :param from_address:
        Sender wallet address

    :param to_address:
        Recipient wallet address. If None, defaults to ``from_address``.

    :param slippage:
        Maximum allowed slippage as a decimal (0.03 = 3%)

    :param order:
        Route preference: ``CHEAPEST`` or ``FASTEST``

    :param api_timeout:
        API request timeout in seconds

    :return:
        Quote with transaction data ready for signing

    :raise LifiAPIError:
        If the API returns an error or no route is found
    """
    base_url = get_lifi_api_url()
    url = f"{base_url}/quote"
    headers = get_lifi_headers()

    params = {
        "fromChain": str(from_chain_id),
        "toChain": str(to_chain_id),
        "fromToken": from_token,
        "toToken": to_token,
        "fromAmount": str(from_amount),
        "fromAddress": from_address,
        "slippage": str(slippage),
        "order": order,
    }

    if to_address:
        params["toAddress"] = to_address

    logger.info(
        "Fetching LI.FI quote: chain %s -> %s, amount: %s",
        from_chain_id,
        to_chain_id,
        from_amount,
    )
    logger.debug("LI.FI quote request: %s params=%s", url, pformat(params))

    response = requests.get(url, params=params, headers=headers, timeout=api_timeout)

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        raise LifiAPIError(f"Error fetching LI.FI quote: {response.status_code} {response.text}\nParams: {pformat(params)}\nEndpoint: {url}") from e

    data = response.json()
    logger.debug("LI.FI quote response: %s", pformat(data))

    # Extract estimate fields
    estimate = data.get("estimate", {})
    gas_costs = estimate.get("gasCosts", [])
    total_gas_usd = None
    if gas_costs:
        total_gas_usd = sum(Decimal(gc.get("amountUSD", "0")) for gc in gas_costs)

    return LifiQuote(
        source_chain_id=from_chain_id,
        target_chain_id=to_chain_id,
        from_token=from_token,
        to_token=to_token,
        from_amount=int(estimate.get("fromAmount", from_amount)),
        estimate_to_amount=int(estimate.get("toAmount", "0")),
        estimate_to_amount_min=int(estimate.get("toAmountMin", "0")),
        gas_cost_usd=total_gas_usd,
        execution_duration=estimate.get("executionDuration"),
        data=data,
    )
