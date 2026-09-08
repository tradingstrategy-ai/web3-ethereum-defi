"""Canonical links for Yearn vault and strategy pages."""

from eth_typing import HexAddress


#: Build every exported Yearn product link in one place, because Yearn has
#: changed the public frontend route without changing the deployed contracts.
#:
#: The former ``/v3/{chain_id}/{address}`` route is served by a legacy
#: frontend. It can return an HTTP success response for its application shell
#: even when the retired client cannot resolve a vault, leaving users with a
#: broken or permanently loading product page.
#:
#: Yearn's current frontend uses ``/vaults/{chain_id}/{address}`` for both V3
#: allocator vaults and TokenizedStrategy-based products. The chain id keeps
#: same-address deployments on different EVM chains distinct, and the vault
#: address lets the frontend look up the exact product without relying on a
#: mutable display name or a search-result ranking.
#:
#: Normalise the address to lowercase while building the path. EVM addresses
#: are case-insensitive there, and this means records loaded from lowercase
#: scanner metadata and checksum-form adapter properties publish one stable
#: URL instead of duplicate links for the same product.
#:
#: Keep all Yearn adapters delegated to this function. A later Yearn frontend
#: migration can then be updated once, rather than silently leaving one
#: Yearn-derived vault family with stale links.
def create_yearn_vault_link(chain_id: int, vault_address: HexAddress) -> str:
    """Create the canonical current-Yearn frontend URL for a vault.

    :param chain_id:
        EVM chain id where the vault is deployed.
    :param vault_address:
        Canonical Yearn vault or TokenizedStrategy contract address.
    :return:
        Current Yearn frontend URL for this chain/address pair.
    """

    return f"https://yearn.fi/vaults/{chain_id}/{vault_address.lower()}"
