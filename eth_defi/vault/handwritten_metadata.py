"""Handwritten vault descriptions maintained from authoritative manager sources.

The mappings here deliberately cover only products for which the vault manager
publishes an authoritative strategy page or supplies reviewed listing copy.
Adapters use this module as an address-scoped override,
preserving normal API-derived metadata for every other vault.
"""

from dataclasses import dataclass
from typing import Final

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class HandwrittenVaultMetadata:
    """Curated display metadata for one known vault.

    :param name:
        Human-readable vault name.
    :param short_description:
        Listing-friendly strategy summary.
    :param description:
        Longer description of the strategy, optionally with Markdown links.
    :param link:
        Authoritative manager-maintained vault detail page.
    """

    name: str
    short_description: str
    description: str
    link: str


#: Morini Capital's canonical public overview of its curated vault strategies.
MORINI_VAULTS_OVERVIEW_URL: Final[str] = "https://morini.capital/"

#: Handwritten metadata for Morini Capital's curated vaults.
#:
#: Piku uses Accountable and Midas as issuance infrastructure. Their APIs do
#: not provide the strategy descriptions maintained by Morini, so key this map
#: by the Ethereum share-token address rather than by protocol-specific IDs.
MORINI_CAPITAL_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (1, HexAddress("0x99351baed3d8ab544ccb08af96a105910fda71e7")): HandwrittenVaultMetadata(
        name="Morini FXArbUSDTRY",
        short_description="Delta-neutral USD/TRY foreign-exchange arbitrage strategy.",
        description=("Morini Capital's strategy arbitrages spreads between USD/TRY rates on Turkish crypto exchanges and fiat rails. Its TRY positions are continuously hedged."),
        link="https://piku.co/vaults/detail/aFXArbUSDTRY",
    ),
    (1, HexAddress("0x827ce7e8e35861d9ac7fe002755767b695a5594a")): HandwrittenVaultMetadata(
        name="Morini StockMarketTRBasisTrade Vault",
        short_description="Turkish equity and single-stock-futures basis-trade strategy.",
        description=("Morini Capital's strategy captures pricing discrepancies between Turkish cash equities and their single-stock-futures counterparts on Borsa Istanbul."),
        link="https://piku.co/vaults/detail/StockMarketTRBasisTrade",
    ),
    (1, HexAddress("0x2bf11d2e04bc40daa95c24b8b90ec4f5c57dd326")): HandwrittenVaultMetadata(
        name="Morini CarryTradeUSDTRYLeverage Vault",
        short_description="Leveraged USD/TRY carry and futures-basis strategy.",
        description=("Morini Capital's strategy uses a leveraged carry trade to capture the TRY overnight interest rate and pricing discrepancies in USD/TRY futures."),
        link="https://piku.co/vaults/detail/CarryTradeUSDTRYLeverage",
    ),
}


#: Centaur's Ethereum-deployment Lighter pools, verified on 2026-10-26.
#:
#: These are native pool identifiers on synthetic chain 9998, not EVM addresses.
#: The authoritative overview links each pool to its individual strategy page:
#: https://docs.centaur.io/strategies/lighter-pools/overview
CENTAUR_VAULT_METADATA: Final[dict[tuple[int, str], HandwrittenVaultMetadata]] = {
    (9998, "lighter-pool-281474976488848"): HandwrittenVaultMetadata(
        name="Post Fiat",
        short_description="Discretionary long/short equity and macro trading based on goodalexander's public posts.",
        description=("Discretionary long/short book across global equities and macro, built from @goodalexander's public posts. Thematic single-stock longs and shorts are held until he signals an exit or a level is hit. Operator fees fund discretionary depositor loss rebates. @goodalexander is not affiliated with Centaur. See the [Post Fiat strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/post-fiat)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/post-fiat",
    ),
    (9998, "lighter-pool-281474976488851"): HandwrittenVaultMetadata(
        name="Inverse 1000x",
        short_description="Takes the opposite side of Jonah's public crypto and US technology calls.",
        description=("A fade vault taking the opposite side of Jonah's (@jvb_xyz) calls on crypto and US technology, sourced from his X posts and the 1000x livestream and podcast. Market-wide index calls are excluded. Operator fees fund discretionary depositor loss rebates. Jonah is not affiliated with Centaur. See the [Inverse 1000x strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/inverse-1000x)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/inverse-1000x",
    ),
    (9998, "lighter-pool-281474976488853"): HandwrittenVaultMetadata(
        name="Serenity",
        short_description="Long exposure to semiconductors, memory and Korean technology based on aleabitoreddit's public thesis.",
        description=("Long book built from @aleabitoreddit's AI-bottleneck thesis: semiconductors, memory and Korean technology. Operator fees fund discretionary depositor loss rebates. @aleabitoreddit is not affiliated with Centaur. See the [Serenity strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/serenity)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/serenity",
    ),
    (9998, "lighter-pool-281474976488856"): HandwrittenVaultMetadata(
        name="Lazy Villager",
        short_description="Concentrated, high-conviction crypto trading based on The Giver's public posts.",
        description=("Concentrated, high-conviction crypto book built from The Giver's (@lazyvillager1) posts: contrarian swings, sized large and managed tightly. Operator fees fund discretionary depositor loss rebates. The Giver is not affiliated with Centaur. See the [Lazy Villager strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/lazy-villager)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/lazy-villager",
    ),
    (9998, "lighter-pool-281474976489166"): HandwrittenVaultMetadata(
        name="Kole World",
        short_description="Crypto swing trading across majors and memecoins based on Zoran Kole's public signals.",
        description=("Crypto swing book built from Zoran Kole's (@captain_kole) signals, spanning majors and memecoins. Operator fees fund discretionary depositor loss rebates. Zoran Kole is not affiliated with Centaur. See the [Kole World strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/kole-world)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/kole-world",
    ),
    (9998, "lighter-pool-281474976489165"): HandwrittenVaultMetadata(
        name="Black Bull",
        short_description="Crypto and crypto-adjacent equity longs based on Ansem's public posts.",
        description=("Long book built from Ansem's (@blknoiz06) posts: high-conviction core holdings plus rotational positions across crypto and crypto-adjacent equities. Operator fees buy and permanently burn $ANSEM on Solana. Ansem is not affiliated with Centaur or the buy-and-burn programme. See the [Black Bull strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/black-bull)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/black-bull",
    ),
}


def get_handwritten_vault_metadata(chain_id: int, address: HexAddress | str) -> HandwrittenVaultMetadata | None:
    """Look up curated metadata for a vault.

    The address is normalised before lookup because scanner and Web3 code may
    supply either a checksum or lower-case address. Native pool identifiers
    retain their deployment-specific prefix to avoid cross-deployment matches.

    :param chain_id:
        EVM chain identifier or native protocol's synthetic chain identifier.
    :param address:
        Vault share-token address or native pool identifier.
    :return:
        Curated metadata when the vault is known, otherwise ``None``.
    """

    key = (chain_id, str(address).lower())
    return CENTAUR_VAULT_METADATA.get(key) or MORINI_CAPITAL_VAULT_METADATA.get(key)


def format_handwritten_vault_note(metadata: HandwrittenVaultMetadata) -> str:
    """Format a source-linked vault note for scanner exports.

    The note supplements the plain-text description and gives users a direct
    route to a published vault page and Morini's strategy overview.

    :param metadata:
        Curated metadata for a vault.
    :return:
        Markdown note suitable for ``_notes`` export.
    """

    return f"""{metadata.name} is curated by Morini Capital, the curation arm of Piku Finance.

**Summary:** {metadata.description}

The published vault details are available on the [Piku vault page]({metadata.link}). Morini provides a [portfolio overview]({MORINI_VAULTS_OVERVIEW_URL}).
"""
