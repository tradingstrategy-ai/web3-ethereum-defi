"""Fixes for Ankr and other broken JSON-RPC service providers.

- Mainly to deal with unstable blockchain chain tip
"""

from eth_defi.provider.named import get_provider_name
from web3 import Web3


def is_ankr(web3: Web3) -> bool:
    """Are we connected to Ankr as a provider."""
    name = get_provider_name(web3.provider)
    return name == "rpc.ankr.com"


def get_default_block_tip_latency(web3: Web3) -> int:
    """Workaround for Ankr and other node providers that do not handle the chain tip properly.

    Likely due to how requests are broken a block number available in one call
    disappears in the following. Thus, with broken JSON-RPC service providers
    we cannot trust ``web3.eth.block_number`` to work and we need to subtract this number from it.

    See :py:func:`get_block_tip_latency`

    :return:
        Number of blocks we need to subtract from the latest block
    """

    if is_ankr(web3):
        # Assume Ankr can safely deal with chain tip minus two blocks.
        # No idea what's a good value, Ankr does not tell,
        # so chosen by a fair dice.
        return 2

    # Assume correctly working node
    return 0


def get_block_tip_latency(web3: Web3) -> int:
    """What block tip latency we should use for this JSON-RPC provider

    - Defaults to zero

    - We have some built-in rules to work around broken providers like Ankr

    - You can override this by setting the latency sa ``web3.block_tip_latency`` attribute

    Example:

    .. code-block:: python

        # We cannot query the chain head on Ankr
        if not block_number:
            block_number = max(1, web3.eth.block_number - get_block_tip_latency(web3))

        timestamp = fetch_block_timestamp(web3, block_number)

        token = fetch_erc20_details(web3, asset.address)
        amount = token.fetch_balance_of(address, block_identifier=block_number)

    """
    latency_override = getattr(web3, "block_tip_latency", None)
    if latency_override is not None:
        assert type(latency_override) == int, f"Got {latency_override.__class__}"
        return latency_override

    return get_default_block_tip_latency(web3)
