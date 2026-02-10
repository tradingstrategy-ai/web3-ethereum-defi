"""Fixes and workaronds for various JSON-RPC service providers.

- Mainly to deal with unstable blockchain chain tip

See also

- :py:mod:`eth_defi.provider.fallback`

- :py:mod:`eth_defi.provider.ankr`

"""

import time

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.provider.ankr import is_ankr
from eth_defi.provider.anvil import is_anvil, is_mainnet_fork
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


def get_fork_safe_latest_block(web3: Web3) -> BlockIdentifier:
    """Get the latest block identifier that is safe for Anvil mainnet forks.

    - For Anvil mainnet forks, returns ``web3.eth.block_number`` (an integer)
      instead of ``"latest"`` to prevent the upstream RPC from resolving
      ``"latest"`` to the actual chain tip (which may be beyond the fork block
      or the upstream's available window).

    - For non-fork Anvil (test backend), returns ``"latest"`` as there is no upstream RPC.

    - For non-Anvil providers, returns ``"latest"``.

    This is needed because web3.py v7 forwards ``"latest"`` to the upstream RPC
    when Anvil needs to fetch state not cached at fork time. The upstream then
    resolves ``"latest"`` to its own chain tip, causing ``BlockOutOfRangeError``
    if the upstream has a limited block window.

    See :py:func:`get_safe_cached_latest_block_number` which uses this function.
    """
    if is_anvil(web3) and is_mainnet_fork(web3):
        return web3.eth.block_number
    return "latest"


def get_almost_latest_block_number(web3: Web3) -> int:
    """Get the latest block number with workarounds for low quality JSON-RPC service providers.

    Use this method instead of ``web3.eth.block_number``.

    Because low quality providers may lose the block of this block number
    on the subsequent API calls, we add some number of delay
    or confirmations to the chain tip, specified by :py:func:`get_block_tip_latency`.

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


#: Chain id
_latest_delayed_block_number_cache = {}


def get_safe_cached_latest_block_number(
    web3: Web3,
    chain_id: int,
    blocks=1000,
    cache_duration: int = 3600,
) -> BlockIdentifier:
    """Get almost "latest" block to work around broken JSON-RPC providers.

    - Not for high frequency usage, as it caches the block for `delay` seconds
    - No RPC call are made to
    - Disabled in Anvil configs

    Work around the error on Monad/Arbitrum/dRPC/shitty RPCs:

    .. code-block:: none

        {'message': 'upstream does not have the requested block yet', 'code': -32603}

    Their internal routing is likely broken and when calling `eth_call` with `latest` the request fails for no reason.

    :param chain_id:
        Chain id to use as part of the cache key

    :param blocks:
        Number of blocks to subtract from the latest block

    :param cache_duration:
        Number of seconds to cache the result

    :return:
        Latest block number minus `blocks`.

        May return "latest" for special configs like unit tests.

    """
    assert isinstance(chain_id, int), f"Expected int chain_id, got {type(chain_id)}"

    # Always check Anvil first - Anvil forks must never use cached block numbers
    # from previous non-Anvil tests running in the same pytest session,
    # as the cache is keyed by chain_id and a prior mainnet test could have
    # cached a block number that's beyond the Anvil fork point
    if is_anvil(web3):
        return get_fork_safe_latest_block(web3)

    now = time.time()

    cached = _latest_delayed_block_number_cache.get(chain_id)
    if cached is not None:
        cached_block, cached_time = cached
        if now - cached_time < cache_duration:
            return cached_block

    latest_block = web3.eth.block_number
    safe_block = max(1, latest_block - blocks)

    _latest_delayed_block_number_cache[chain_id] = (safe_block, now)

    return safe_block
