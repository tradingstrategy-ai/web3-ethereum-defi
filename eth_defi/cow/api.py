"""Cow API utilities."""

from eth_defi.cow.constants import COWSWAP_API_ENDPOINTS


class CowAPIError(Exception):
    """Error returned by CowSwap API."""


def get_cowswap_api(chain_id: int) -> str:
    """Get CowSwap API endpoint for given chain ID.

    :param chain_id:
        Chain ID to get the endpoint for

    :return:
        CowSwap API endpoint URL

    :raise KeyError:
        If chain is not supported by CowSwap
    """
    return COWSWAP_API_ENDPOINTS[chain_id]
