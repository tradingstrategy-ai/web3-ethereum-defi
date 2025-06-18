"""Fixes and workaronds for various JSON-RPC service providers.

- Mainly to deal with unstable blockchain chain tip

See also

- :py:mod:`eth_defi.provider.fallback`

- :py:mod:`eth_defi.provider.ankr`

"""

from eth_defi.provider.ankr import is_ankr
from web3 import Web3

from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.mev_blocker import MEVBlockerProvider


def get_default_block_tip_latency(web3: Web3) -> int:
    """Workaround for Ankr and other node providers that do not handle the chain tip properly.

    Likely due to how requests are broken a block number available in one call
    disappears in the following. Thus, with broken JSON-RPC service providers
    we cannot trust ``web3.eth.block_number`` to work and we need to subtract this number from it.

    See :py:func:`get_block_tip_latency`

    :return:
        Number of blocks we need to subtract from the latest block
    """

    if isinstance(web3.provider, (FallbackProvider, MEVBlockerProvider)):
        # With fallback provider, assume 4 blocks delay,
        # so that if there is a fail over switch,
        # the next provider is likely to hae the block immediately
        return 4

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

    - If using :py:class:`eth_defi.provider.fallback.FallbackProvider`
      we use 4 blocks latency as multiple providers are unlikely
      to agree on a chain tip (blocks have not propagated yet).

    - We have some built-in rules to work out specific providers

    - You can override this by setting the latency sa ``web3.block_tip_latency`` attribute

    See the source code of :py:func:`get_default_block_tip_latency` for other rules.


    """
    latency_override = getattr(web3, "block_tip_latency", None)
    if latency_override is not None:
        assert type(latency_override) == int, f"Got {latency_override.__class__}"
        return latency_override

    return get_default_block_tip_latency(web3)


def set_block_tip_latency(web3: Web3, block_count: int):
    """Override the default block tip latency settings.

    Useful for unit testing, because unit testing assumes stuff
    has happened in the latest block you want to read.

    See :py:func:`get_block_tip_latency`.
    """
    web3.block_tip_latency = block_count


def get_almost_latest_block_number(web3: Web3) -> int:
    """Get the latest block number with workarounds for low quality JSON-RPC service providers.

    Use this method instead of ``web3.eth.block_number``.

    Because low quality providers may lose the block of this block number
    on the subsequent API calls, we add some number of delay
    or confirmations to the chain tip, specified by :py:funct:`get_block_tip_latency`.

    Providers with known issues

    - LlamaNodes

    - Ankr

    Example:

    .. code-block:: python

        from eth_defi.provider.broken_provider import get_almost_latest_block_number

        # We cannot query the chain head on Ankr or LlamaNodes,
        # so get the almost head
        if not block_number:
            block_number = get_almost_latest_block_number(web3)

        timestamp = fetch_block_timestamp(web3, block_number)

        token = fetch_erc20_details(web3, asset.address)
        amount = token.fetch_balance_of(address, block_identifier=block_number)

    """
    return max(1, web3.eth.block_number - get_block_tip_latency(web3))
