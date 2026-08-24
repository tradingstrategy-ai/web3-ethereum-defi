"""Rysk Premium product records and runtime catalogue state."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class RyskPremiumPool:
    """One Rysk Premium pool returned by the official application catalogue.

    The immutable catalogue record supplies contract identities and strategy
    metadata used by both the scanner and the read-only adapter.
    """

    #: EVM chain hosting the pool-share token.
    chain_id: int
    #: ERC-20 LP share-token and pool address.
    address: HexAddress
    #: Curator-provided pool name.
    name: str
    #: Curator-provided strategy description, when available.
    description: str | None
    #: Rysk registry associated with the pool.
    registry: HexAddress
    #: Contract used to execute the curated option strategy.
    option_handler: HexAddress
    #: Subscription and withdrawal collateral token.
    asset: HexAddress
    #: Advertised option-writing strategy: ``"call"`` or ``"put"``.
    option_type: str
    #: Protocol fee charged against option premium, in basis points.
    option_sale_fee_bps: int
    #: Optional curator authority supplied by Rysk.
    authority: HexAddress | None = None
    #: First known Rysk snapshot block.
    first_seen_at_block: int | None = None
    #: Naive UTC timestamp corresponding to the first known snapshot block.
    first_seen_at: datetime.datetime | None = None


#: Current public Rysk Premium deployments, checked from ``/api/pools`` on
#: 2026-08-24.  The scanner refreshes this mutable mapping from the same API.
#: Keeping the reviewed seed allows adapter routing before the first refresh.
RYSK_PREMIUM_POOLS: dict[tuple[int, HexAddress], RyskPremiumPool] = {
    (999, HexAddress("0x0fe45639d2d4f8c3c999946a44c287fcff5fa541")): RyskPremiumPool(999, HexAddress("0x0fe45639d2d4f8c3c999946a44c287fcff5fa541"), "Hyperion HiHYPE/USDH PUT", None, HexAddress("0x65f1634932cdd7b5720c75247510e830822697cf"), HexAddress("0x743652267509c76fde0b4095969da16d608e123e"), HexAddress("0x111111a1a0667d36bd57c0a9f569b98057111111"), "put", 500),
    (999, HexAddress("0xd1ee594e67ef8e09903961d735ab7ad3009522f9")): RyskPremiumPool(999, HexAddress("0xd1ee594e67ef8e09903961d735ab7ad3009522f9"), "Hyperion HiHYPE/USDH CALL", None, HexAddress("0x9c327c99cb14fa07b23f2fd77a22c9451ee139bc"), HexAddress("0x284ed022dfdf0eea23be02de9b163e8ba5ecf4d1"), HexAddress("0xfd739d4e423301ce9385c1fb8850539d657c296d"), "call", 500),
    (999, HexAddress("0xa26801f689fbdf0ff96eff52077b958d1062ba85")): RyskPremiumPool(999, HexAddress("0xa26801f689fbdf0ff96eff52077b958d1062ba85"), "Hyperion HiHYPE/USDC PUT", None, HexAddress("0xb8dbfca0fd36cf5102cdf4d32087ca1e7b42f6c5"), HexAddress("0xc92c394982a32c98bb8781101a825b7abed9e732"), HexAddress("0xb88339cb7199b77e23db6e890353e22632ba630f"), "put", 500, HexAddress("0xf341cb6265c75640d5d072738e3c448b33e73fb2")),
    (999, HexAddress("0xca5b1d5d204c6a69f91d643332f4d3a0cfb2bc50")): RyskPremiumPool(999, HexAddress("0xca5b1d5d204c6a69f91d643332f4d3a0cfb2bc50"), "Hyperion HiHYPE/USDC CALL", "Hyperion KHYPE-USDC Call Pool", HexAddress("0x163eab0b867b2f12e3725225987bc9c056f2d72f"), HexAddress("0xe0945bedb832977201da20697bcde5f624ed02d8"), HexAddress("0xfd739d4e423301ce9385c1fb8850539d657c296d"), "call", 500, HexAddress("0xaa94c1ca8354457aad33dabf44d50e305f3ae931")),
    (1, HexAddress("0x7b258f15a5b981f97eca4794bdeedd3aa24ea423")): RyskPremiumPool(1, HexAddress("0x7b258f15a5b981f97eca4794bdeedd3aa24ea423"), "Arrakis wCOINx Call Vault", "wCOINx-USDC Call Vault", HexAddress("0xb362911250381400fbacea1f9cb0d3942d90ed22"), HexAddress("0xe6a0eb93355c7eb9b1342fbd4b881a6301a98226"), HexAddress("0x44c7ed7ffdf8465c9d27f60aec845eed3d49d56e"), "call", 500, HexAddress("0xade165d92387b3b7119ffe95682ae8cfe4c21e32")),
    (1, HexAddress("0x06e6bc81c15a5d73fc35b79ff67ff57d258d77c8")): RyskPremiumPool(1, HexAddress("0x06e6bc81c15a5d73fc35b79ff67ff57d258d77c8"), "KPK WETH Call Vault", "WETH-USDC Call Vault", HexAddress("0xf2932d2acc04d51623ccc2f4079621c860750ae2"), HexAddress("0x7a82399a1fc2e0b68891821de17b14a5302e284b"), HexAddress("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"), "call", 500, HexAddress("0xecf8a8141919eedff5f34d5cdfcc2120573950c4")),
    (1, HexAddress("0x1195826418541cb3e80a22ef5736a6794393c91a")): RyskPremiumPool(1, HexAddress("0x1195826418541cb3e80a22ef5736a6794393c91a"), "KPK WETH Put Vault", "WETH-USDC Put Vault", HexAddress("0x0941f9a243878d4d5922462d07c15027e3b9026b"), HexAddress("0x93bfe72a9729ae68c15c3d6da1206f408fca8c4e"), HexAddress("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"), "put", 500, HexAddress("0x59021a1ae23cc8b21c866d9c9ed1c4393c7cbbb4")),
    (1, HexAddress("0xce930ac025cc5675ec49cba71cc5ed0c7518c19a")): RyskPremiumPool(1, HexAddress("0xce930ac025cc5675ec49cba71cc5ed0c7518c19a"), "Sentinel wSPYx Call Vault", "wSPYx-USDC Call Vault", HexAddress("0xd94cac1d3320cfdc0e63987aa87678444200ac08"), HexAddress("0x76f9107778776efd055d1cba256db29e75438a09"), HexAddress("0xe7e553cd128f0011777323a0b44a7b96ea1cb540"), "call", 500, HexAddress("0xcdcccbc02fbfa2fa2062881136e3aa4e625d9d8c")),
}

#: Chains with a reviewed Rysk deployment and a matching scanner configuration.
#: Adding a new Rysk chain still requires adding its RPC configuration before
#: the scheduled multi-chain scanner can reach it.
RYSK_SUPPORTED_CHAIN_IDS = frozenset(pool.chain_id for pool in RYSK_PREMIUM_POOLS.values())
RYSK_PREMIUM_POOL_ADDRESSES = {pool.address for pool in RYSK_PREMIUM_POOLS.values()}


def install_rysk_premium_runtime_pools(pools: list[RyskPremiumPool]) -> None:
    """Install a freshly fetched Premium catalogue for this process.

    The mutable runtime overlay lets newly published pools route through the
    adapter without discarding the reviewed bootstrap catalogue.

    :param pools:
        Pools supplied by the official Rysk Premium catalogue endpoint.
    :return:
        None.
    """

    RYSK_PREMIUM_POOLS.update({(pool.chain_id, pool.address): pool for pool in pools})
    RYSK_PREMIUM_POOL_ADDRESSES.update(pool.address for pool in pools)


def is_rysk_premium_test_pool(pool: RyskPremiumPool) -> bool:
    """Identify issuer-labelled test products excluded from production metadata.

    Rysk publishes internal products through the same application endpoint as
    user-facing pools, so production synchronisation needs an explicit filter.

    :param pool:
        Public catalogue product.
    :return:
        ``True`` when Rysk labels the product as internal or test-only.
    """

    return pool.name.lower().startswith("rysk internal") or pool.description == "For test purposes only"
