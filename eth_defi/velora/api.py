"""Velora API utilities."""

from eth_typing import HexAddress

from eth_defi.velora.constants import (
    VELORA_API_URL,
    VELORA_AUGUSTUS_SWAPPER,
    VELORA_TOKEN_TRANSFER_PROXY,
)


class VeloraAPIError(Exception):
    """Error returned by Velora API."""


def get_velora_api_url() -> str:
    """Get Velora API base URL.

    :return:
        Velora API endpoint URL
    """
    return VELORA_API_URL


def get_augustus_swapper(chain_id: int) -> HexAddress:
    """Get Augustus Swapper contract address for a chain.

    :param chain_id:
        Chain ID to get the address for

    :return:
        Augustus Swapper contract address

    :raise KeyError:
        If chain is not supported by Velora
    """
    try:
        return VELORA_AUGUSTUS_SWAPPER[chain_id]
    except KeyError:
        raise KeyError(f"Velora does not support chain ID {chain_id}. Supported chains: {list(VELORA_AUGUSTUS_SWAPPER.keys())}")


def get_token_transfer_proxy(chain_id: int) -> HexAddress:
    """Get TokenTransferProxy contract address for a chain.

    This is the contract that users must approve for token spending.

    .. warning::

        Approve TokenTransferProxy, NOT Augustus Swapper.
        Funds may be lost if approved to Augustus directly.

    :param chain_id:
        Chain ID to get the address for

    :return:
        TokenTransferProxy contract address

    :raise KeyError:
        If chain is not supported by Velora
    """
    try:
        return VELORA_TOKEN_TRANSFER_PROXY[chain_id]
    except KeyError:
        raise KeyError(f"Velora does not support chain ID {chain_id}. Supported chains: {list(VELORA_TOKEN_TRANSFER_PROXY.keys())}")
