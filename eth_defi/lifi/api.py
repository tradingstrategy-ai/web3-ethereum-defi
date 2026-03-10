"""LI.FI API utilities.

Helpers for interacting with the `LI.FI REST API <https://docs.li.fi>`__.
"""

import logging
import os
from decimal import Decimal

import requests

from eth_defi.lifi.constants import LIFI_API_KEY_ENV, LIFI_API_URL, LIFI_NATIVE_TOKEN_ADDRESS

logger = logging.getLogger(__name__)


class LifiAPIError(Exception):
    """Error returned by LI.FI API."""


def get_lifi_api_url() -> str:
    """Get LI.FI API base URL.

    :return:
        LI.FI API endpoint URL
    """
    return LIFI_API_URL


def get_lifi_headers() -> dict:
    """Get HTTP headers for LI.FI API requests.

    Reads the optional ``LIFI_API_KEY`` environment variable.
    If not set, logs a warning and returns headers without authentication.
    Without an API key, the rate limit is 10 requests per second.

    :return:
        Headers dict, possibly containing ``x-lifi-api-key``
    """
    headers = {}
    api_key = os.environ.get(LIFI_API_KEY_ENV)
    if api_key:
        headers["x-lifi-api-key"] = api_key
    else:
        logger.info(
            "Environment variable %s not set. LI.FI API rate limited to 10 req/s. Register at https://portal.li.fi to get an API key.",
            LIFI_API_KEY_ENV,
        )
    return headers


def fetch_lifi_token_price_usd(
    chain_id: int,
    token_address: str = LIFI_NATIVE_TOKEN_ADDRESS,
    api_timeout: float = 30,
) -> Decimal:
    """Fetch the USD price of a token using the LI.FI token endpoint.

    Uses ``GET /v1/token`` which returns ``priceUSD`` for any supported token.

    :param chain_id:
        Chain ID (e.g. 1 for Ethereum, 42161 for Arbitrum)

    :param token_address:
        Token contract address. Defaults to native token (zero address).

    :param api_timeout:
        API request timeout in seconds

    :return:
        Token price in USD

    :raise LifiAPIError:
        If the API returns an error or price is unavailable
    """
    base_url = get_lifi_api_url()
    url = f"{base_url}/token"
    headers = get_lifi_headers()
    params = {
        "chain": str(chain_id),
        "token": token_address,
    }

    logger.debug("Fetching LI.FI token price: chain=%s token=%s", chain_id, token_address)

    response = requests.get(url, params=params, headers=headers, timeout=api_timeout)

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        chain_name = f"chain {chain_id}"
        try:
            from eth_defi.chain import get_chain_name

            chain_name = f"{get_chain_name(chain_id)} (chain_id={chain_id})"
        except Exception:
            pass
        raise LifiAPIError(f"Error fetching token price from LI.FI for {chain_name}: {response.status_code} {response.text}") from e

    data = response.json()
    price_usd = data.get("priceUSD")
    if not price_usd:
        raise LifiAPIError(f"No priceUSD in LI.FI token response for chain {chain_id}, token {token_address}: {data}")

    return Decimal(price_usd)


def fetch_lifi_native_token_prices(
    chain_ids: list[int],
    api_timeout: float = 30,
) -> dict[int, Decimal]:
    """Fetch native token USD prices for multiple chains.

    Makes one API call per chain to the LI.FI token endpoint.

    :param chain_ids:
        List of chain IDs to fetch prices for

    :param api_timeout:
        API request timeout in seconds per request

    :return:
        Dict mapping chain_id to native token price in USD

    :raise LifiAPIError:
        If any API call fails
    """
    prices = {}
    for chain_id in chain_ids:
        prices[chain_id] = fetch_lifi_token_price_usd(
            chain_id=chain_id,
            token_address=LIFI_NATIVE_TOKEN_ADDRESS,
            api_timeout=api_timeout,
        )
        logger.info("Native token price for chain %s: $%s", chain_id, prices[chain_id])
    return prices
