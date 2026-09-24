"""GMX user-interface links for liquidity-provider products."""

from eth_typing import HexAddress


def get_gmx_pool_details_link(chain_id: int, product_address: HexAddress) -> str:
    """Build GMX's direct pool-details link for one GM or GLV product.

    The GMX interface identifies both GM and GLV pool detail pages through the
    product address in its ``market`` query parameter. The ``chainId`` query
    parameter switches the interface to the product's deployment chain before
    it resolves that address.

    :param chain_id:
        EVM deployment chain ID of the GM or GLV product.
    :param product_address:
        GM market-token or GLV share-token address.
    :return:
        Direct GMX pool-details URL with the deposit view selected.

    .. seealso::

        `GMX interface pool-details route <https://github.com/gmx-io/gmx-interface/blob/release/src/pages/PoolsDetails/PoolsDetails.tsx>`__.
    """

    return f"https://app.gmx.io/#/pools/details?market={product_address.lower()}&operation=Deposit&chainId={chain_id}"
