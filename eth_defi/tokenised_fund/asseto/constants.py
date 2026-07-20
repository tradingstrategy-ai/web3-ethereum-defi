"""Asseto product metadata.

Asseto AoABT is a permissioned ERC-20 tokenised fund product.  Its share
token, subscription/redemption hub and NAV pricer are separate contracts, so
the shared vault scanner needs this product-level mapping instead of ERC-4626
introspection.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress

from eth_defi.types import Percent

#: Fiat and stablecoin denominations which already represent USD accounting.
ASSETO_USD_DENOMINATIONS = frozenset({"USD", "USDC", "USDT"})


@dataclass(slots=True, frozen=True)
class AssetoProduct:
    """Asseto on-chain product metadata.

    :param chain_id:
        EVM chain hosting the product.
    :param token:
        ERC-20 tokenised fund share address.
    :param manager:
        Asseto ``AoABTManager`` request/claim contract, when published.
    :param pricer:
        Asseto ``Pricer`` contract that publishes NAV/share in base-18 USD,
        when published.
    :param collateral:
        Stablecoin used by the manager for subscriptions and redemptions, when
        the product publishes it.
    :param first_seen_at_block:
        Token proxy deployment block.
    :param first_seen_at:
        Token proxy deployment timestamp as a naive UTC datetime.
    :param management_fee:
        Documented annual underlying-fund management fee, when available.
    :param performance_fee:
        Documented underlying-fund performance fee, when available.
    :param has_custom_fees:
        Whether the fund fee terms include conditions that cannot be expressed
        as one scalar percentage.
    """

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    manager: HexAddress | None
    pricer: HexAddress | None
    collateral: HexAddress | None
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    management_fee: Percent | None = None
    performance_fee: Percent | None = None
    has_custom_fees: bool = False
    #: Currency in which Asseto publishes NAV/share.
    denomination_symbol: str | None = None
    #: Historical units of denomination currency per USD, keyed by UTC timestamp.
    usd_exchange_rates: tuple[tuple[int, Decimal], ...] = ()
    #: Asseto public product-registry identifier for its display NAV history.
    offchain_product_id: int | None = None
    #: Product key required by Asseto's off-chain product endpoints.
    offchain_product_name: str | None = None
    #: Informational public product description.
    description: str | None = None


@dataclass(slots=True, frozen=True)
class AssetoCurator:
    """Reviewed investment-manager attribution for an Asseto share token."""

    #: Human-readable manager name exported in vault scan metadata.
    manager_name: str

    #: Curator feeder identifier used by the public vault export.
    curator_slug: str


#: HashKey Chain EVM chain id.
HASHKEY_CHAIN_ID = 177

#: AoABT on HashKey Chain.
#:
#: Token proxy: https://hsk.blockscout.com/address/0x80C080acd48ED66a35Ae8A24BC1198672215A9bD
#: Manager: https://hsk.blockscout.com/address/0x6dB7eA55c94fb0F4b22D6b384C18CdAa3B33d746
#: Pricer: https://hsk.blockscout.com/address/0xD72529F8b54fcB59010F2141FC328aDa5Aa72abb
ASSETO_AOABT_HASHKEY = AssetoProduct(
    chain_id=HASHKEY_CHAIN_ID,
    token=HexAddress("0x80c080acd48ed66a35ae8a24bc1198672215a9bd"),
    symbol="AoABT",
    product_name="Asseto Orient Arbitrage Token",
    manager=HexAddress("0x6db7ea55c94fb0f4b22d6b384c18cdaa3b33d746"),
    pricer=HexAddress("0xd72529f8b54fcb59010f2141fc328ada5aa72abb"),
    collateral=HexAddress("0xf1b50ed67a9e2cc94ad3c477779e2d4cbfff9029"),
    first_seen_at_block=3_068_926,
    first_seen_at=datetime.datetime(2025, 2, 25, 12, 3, 7, tzinfo=datetime.UTC).replace(tzinfo=None),
    management_fee=0.01,
    performance_fee=0.20,
    has_custom_fees=True,
    denomination_symbol="USDT",
    description="AoABT tokenises the Asseto Orient Arbitrage Strategy and offers daily U.S. dollar yields backed one-to-one by the underlying strategy.",
)

#: Product lookup used by the adapter and chain-aware classification.
ASSETO_PRODUCTS: dict[tuple[int, HexAddress], AssetoProduct] = {
    (ASSETO_AOABT_HASHKEY.chain_id, ASSETO_AOABT_HASHKEY.token): ASSETO_AOABT_HASHKEY,
}

#: Address-only lookup used for hardcoded protocol routing.
ASSETO_PRODUCTS_BY_TOKEN: dict[HexAddress, AssetoProduct] = {product.token: product for product in ASSETO_PRODUCTS.values()}

#: Reviewed manager attribution for Asseto products on supported EVM chains.
#:
#: Asseto is the tokenisation and administration platform, not the portfolio
#: manager. These exact mappings deliberately exclude HashKey, Pharos and
#: Prospero deployments, which are not supported by the shared vault export.
ASSETO_CURATORS: dict[tuple[int, HexAddress], AssetoCurator] = {
    # ChinaAMC USD Digital Money Market Fund Class B USD.
    (1, HexAddress("0x78e80da0616887b46a31f39310c2a8b0fbd6a42d")): AssetoCurator("China Asset Management (Hong Kong)", "chinaamc-hong-kong"),
    (56, HexAddress("0x1ec3aa07e3898f1e6d4f23b5dce1bdbecb5c1fe1")): AssetoCurator("China Asset Management (Hong Kong)", "chinaamc-hong-kong"),
    # CMS USD Money Market Fund share classes and CMS-managed CFSAI.
    (1, HexAddress("0x907c00d587daff16d028fe1e131d6dd3c6bf2f4b")): AssetoCurator("CMS Asset Management (HK)", "cms-asset-management-hk"),
    (1, HexAddress("0x498d9329555471bf6073a5f2d047f746d522a373")): AssetoCurator("CMS Asset Management (HK)", "cms-asset-management-hk"),
    (56, HexAddress("0x1775504c5873e179ea2f8abfce3861ec74d159bc")): AssetoCurator("CMS Asset Management (HK)", "cms-asset-management-hk"),
    (1, HexAddress("0x4867ad1a74b38b0aeff4fff251ed0dadae4f4630")): AssetoCurator("CMS Asset Management (HK)", "cms-asset-management-hk"),
    (1, HexAddress("0x6dc4674573380aff6c3359e19da5cbb6afceb5c3")): AssetoCurator("CMS Asset Management (HK)", "cms-asset-management-hk"),
    # Additional Asseto-backed fund managers reviewed from the underlying fund.
    (1, HexAddress("0x286d9f099587f567ece2b70ebb64b94acd672d76")): AssetoCurator("CNCB (Hong Kong) Capital Limited", "cncb-capital"),
    (1, HexAddress("0x63e19fb814eb737730ac0afbb52b351695b97176")): AssetoCurator("GaoTeng Global Asset Management Limited", "gaoteng-global-asset-management"),
    (1, HexAddress("0x50bdaff4bceb852f006f657f47c68fcc417f7beb")): AssetoCurator("Haitong International Asset Management (HK) Limited", "haitong-international-asset-management"),
    (1, HexAddress("0xf3a2a5de306b063d75c86b6352832639b7263a3b")): AssetoCurator("Muzinich & Co.", "muzinich"),
    (56, HexAddress("0xfb8cb7630bc3cb34a6a9846ec03de3a32393ee65")): AssetoCurator("Muzinich & Co.", "muzinich"),
    (1, HexAddress("0xf252c5bd43907a6cab079e990845a37a7c5730d9")): AssetoCurator("Partners Group", "partners-group"),
    (56, HexAddress("0x50bf2924cee59737ead76e881643ed8569bae6e8")): AssetoCurator("Partners Group", "partners-group"),
    (1, HexAddress("0x3be5dd4a34f1c6a112048b9df908ced4372d5049")): AssetoCurator("Epoch RWA", "epoch-rwa"),
    # Existing reviewed Asseto strategy advisers/managers.
    (43114, HexAddress("0xb2ea3e7b80317c4e20d1927034162176e25834e2")): AssetoCurator("DFZQ / Orient Securities International", "dfzq"),
    (1, HexAddress("0x383730608d98b82470d733369a839f6b7e8cfda5")): AssetoCurator("DL Holdings", "dl-holdings"),
}

#: Hardcoded leads for tokenised funds that do not emit ERC-4626 events.
ASSETO_HARDCODED_LEADS = tuple(
    (
        product.chain_id,
        product.token,
        product.first_seen_at_block,
        product.first_seen_at,
    )
    for product in ASSETO_PRODUCTS.values()
)
