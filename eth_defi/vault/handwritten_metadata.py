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
#: Listing copy was supplied by the requester in the Centaur /add-curator request
#: on 2026-10-26 and lightly edited for publication. The supplied copy is the
#: source for the thematic/global equity remit of Post Fiat and the swing
#: trading across majors and memecoins described for Kole World; these
#: details are not quoted from the linked strategy pages. Official pages
#: corroborate identities, operator discretion, non-affiliation and fee use.
#: These are native pool identifiers on synthetic chain 9998, not EVM addresses.
#: The authoritative overview links each pool to its individual strategy page:
#: https://docs.centaur.io/strategies/lighter-pools/overview
CENTAUR_VAULT_METADATA: Final[dict[tuple[int, str], HandwrittenVaultMetadata]] = {
    (9998, "lighter-pool-281474976488848"): HandwrittenVaultMetadata(
        name="Post Fiat",
        short_description="Discretionary long/short equity and macro trading based on goodalexander's public posts.",
        description=("Discretionary long/short book across global equities and macro, built from @goodalexander's public posts. Thematic single-stock longs and shorts are held until he signals an exit or a level is hit. Operator fees fund [random, discretionary rebates](https://docs.centaur.io/strategies/lighter-pools/fees) for depositors with realised losses. Rebates are not insurance and do not guarantee recovery of losses or any return. No depositor has a right to a rebate; Centaur can alter, suspend or stop the programme at any time. @goodalexander is not affiliated with Centaur. See the [Post Fiat strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/post-fiat)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/post-fiat",
    ),
    (9998, "lighter-pool-281474976488851"): HandwrittenVaultMetadata(
        name="Inverse 1000x",
        short_description="Takes the opposite side of Jonah's public crypto and US technology calls.",
        description=("A fade vault taking the opposite side of Jonah's (@jvb_xyz) calls on crypto and US technology, sourced from his X posts and the 1000x livestream and podcast. Market-wide index calls are excluded. Operator fees fund [random, discretionary rebates](https://docs.centaur.io/strategies/lighter-pools/fees) for depositors with realised losses. Rebates are not insurance and do not guarantee recovery of losses or any return. No depositor has a right to a rebate; Centaur can alter, suspend or stop the programme at any time. Jonah is not affiliated with Centaur. See the [Inverse 1000x strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/inverse-1000x)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/inverse-1000x",
    ),
    (9998, "lighter-pool-281474976488853"): HandwrittenVaultMetadata(
        name="Serenity",
        short_description="Long exposure to semiconductors, memory and Korean technology based on aleabitoreddit's public thesis.",
        description=("Long book built from @aleabitoreddit's AI-bottleneck thesis: semiconductors, memory and Korean technology. Operator fees fund [random, discretionary rebates](https://docs.centaur.io/strategies/lighter-pools/fees) for depositors with realised losses. Rebates are not insurance and do not guarantee recovery of losses or any return. No depositor has a right to a rebate; Centaur can alter, suspend or stop the programme at any time. @aleabitoreddit is not affiliated with Centaur. See the [Serenity strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/serenity)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/serenity",
    ),
    (9998, "lighter-pool-281474976488856"): HandwrittenVaultMetadata(
        name="Lazy Villager",
        short_description="Concentrated, high-conviction crypto trading based on The Giver's public posts.",
        description=("Concentrated, high-conviction crypto book built from The Giver's (@lazyvillager1) posts, taking contrarian positions. Operator fees fund [random, discretionary rebates](https://docs.centaur.io/strategies/lighter-pools/fees) for depositors with realised losses. Rebates are not insurance and do not guarantee recovery of losses or any return. No depositor has a right to a rebate; Centaur can alter, suspend or stop the programme at any time. The Giver is not affiliated with Centaur. See the [Lazy Villager strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/lazy-villager)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/lazy-villager",
    ),
    (9998, "lighter-pool-281474976489166"): HandwrittenVaultMetadata(
        name="Kole World",
        short_description="Crypto swing trading across majors and memecoins based on Zoran Kole's public signals.",
        description=("Crypto swing book built from Zoran Kole's (@captain_kole) signals, spanning majors and memecoins. Operator fees fund [random, discretionary rebates](https://docs.centaur.io/strategies/lighter-pools/fees) for depositors with realised losses. Rebates are not insurance and do not guarantee recovery of losses or any return. No depositor has a right to a rebate; Centaur can alter, suspend or stop the programme at any time. Zoran Kole is not affiliated with Centaur. See the [Kole World strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/kole-world)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/kole-world",
    ),
    (9998, "lighter-pool-281474976489165"): HandwrittenVaultMetadata(
        name="Black Bull",
        short_description="Crypto and crypto-adjacent equity longs based on Ansem's public posts.",
        description=("Long book built from Ansem's (@blknoiz06) posts: high-conviction core holdings plus rotational positions across crypto and crypto-adjacent equities. Operator fees buy and permanently burn $ANSEM on Solana. Ansem is not affiliated with Centaur or the buy-and-burn programme. See the [Black Bull strategy](https://docs.centaur.io/strategies/lighter-pools/vaults/black-bull)."),
        link="https://docs.centaur.io/strategies/lighter-pools/vaults/black-bull",
    ),
}


#: Official Travessia page for the reviewed Avalanche USDT vault.
TRAVESSIA_TAURI_VAULT_URL: Final[str] = "https://www.travessiacredit.com/vaults/43114/tauri/0x9c3da70cD7feDEAeF528d060ea99AcE133E08Fd2"

#: Handwritten strategy metadata for Travessia's Avalanche deployment.
#:
#: Accountable's public listing omits Travessia. The manager's vault page and
#: https://docs.travessiacredit.com/pages/tauri-case-study.html describe the
#: underlying business; https://www.travessiacredit.com/ names its relationships.
TRAVESSIA_TAURI_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (43114, HexAddress("0x9c3da70cd7fedeaef528d060ea99ace133e08fd2")): HandwrittenVaultMetadata(
        name="Travessia Tauri Vault",
        short_description="USDT loans funding Tauri Agrícola's grain purchases and transport in Brazil.",
        description=("Travessia's Tauri Vault lends USDT to Tauri Agrícola, a Brazilian grain trader. The loans pay for grain purchases and transport while Tauri waits for buyers to pay their invoices. Tauri supplies soy, grain and rice to animal-feed producers, poultry farmers and industrial buyers. Returns come from Tauri's profits on these trades. Travessia says a typical trade takes 5 to 15 days, allowing the money to fund successive purchases. Under the published terms, Tauri must repay the loans, absorb initial losses and manage the risk of exchange-rate changes. Delayed payments or problems with Tauri's business could cause investor losses or delay withdrawals. Travessia names Rabobank, Avalanche, RedStone, Accountable and Royco among the organisations it works with."),
        link=TRAVESSIA_TAURI_VAULT_URL,
    ),
}

#: Source-linked descriptive note, without investment-backing or guarantee claims.
TRAVESSIA_TAURI_VAULT_NOTE: Final[str] = f"""The [Travessia Tauri Vault]({TRAVESSIA_TAURI_VAULT_URL}) lends USDT to Tauri Agrícola to pay for grain purchases and transport until buyers pay their invoices. Returns come from Tauri's profits on these trades. Travessia says a typical trade takes 5 to 15 days, allowing the money to fund successive purchases. Under the published terms, Tauri must repay the loans, absorb initial losses and manage the risk of exchange-rate changes. Delayed payments or problems with Tauri's business could cause investor losses or delay withdrawals.

Travessia's [Tauri case study](https://docs.travessiacredit.com/pages/tauri-case-study.html) describes a Brazilian grain trader supplying soy, grain and rice to animal-feed producers, poultry farmers and industrial buyers.

On its [homepage](https://www.travessiacredit.com/), Travessia names Rabobank, Avalanche, RedStone, Accountable and Royco among the organisations it works with.
"""


#: Shared business description for Tauri's other reviewed deposit assets.
TRAVESSIA_TAURI_STRATEGY_DESCRIPTION: Final[str] = "Travessia's Tauri Vault lends depositors' money to Tauri Agrícola, a Brazilian grain trader. The loans pay for grain purchases and transport while Tauri waits for buyers to pay their invoices. Tauri supplies soy, grain and rice to animal-feed producers, poultry farmers and industrial buyers. Returns come from Tauri's profits on these trades. Travessia says a typical trade takes 5 to 15 days, allowing the money to fund successive purchases. Under the published terms, Tauri must repay the loans, absorb initial losses and manage the risk of exchange-rate changes. Delayed payments or problems with Tauri's business could cause investor losses or delay withdrawals. Travessia names Rabobank, Avalanche, RedStone, Accountable and Royco among the organisations it works with."

#: The official application's deployment catalogue confirms each chain, address
#: and deposit asset: https://www.travessiacredit.com/vaults.
TRAVESSIA_TAURI_VAULT_METADATA.update(
    {
        (chain_id, HexAddress(address.lower())): HandwrittenVaultMetadata(
            name=name,
            short_description=f"{denomination} deposits funding loans for Tauri Agrícola's grain purchases and transport in Brazil.",
            description=TRAVESSIA_TAURI_STRATEGY_DESCRIPTION,
            link=f"https://www.travessiacredit.com/vaults/{chain_id}/tauri/{address}",
        )
        for chain_id, address, denomination, name in (
            (1, "0x4C18E2bb9942b12b28e780acF2D9EC2DDA126df9", "AUSD", "Travessia Tauri Vault"),
            (1, "0xf84A37545e313E8177d80039158ed478C7Dc9079", "USDC", "Travessia Tauri Vault - USDC"),
            (1, "0xe4EE0fe4B09021FCdF76B165AA1e8eAF7d447c35", "USDT", "Travessia Tauri Vault - USDT"),
            (143, "0x8D24B48Cb736addb5B7069565F192A052F42Ba61", "AUSD", "Travessia Tauri Vault"),
        )
    }
)

#: Protocol-level background, not a claim about a particular vault's holdings.
NOON_YIELD_ENGINE_DESCRIPTION: Final[str] = "Across its products, Noon earns income from short-term US government debt, other loans and debt investments, lending through crypto platforms, and trading designed to reduce exposure to market price movements. Its sUSN token gives holders access to this mix of investments. Individual vaults can follow different strategies and withdrawal rules. These public documents describe Noon's wider business; they do not establish this vault's current investments."

#: Official public sources for Noon's yield model and vault-specific scope.
NOON_YIELD_ENGINE_SOURCES: Final[str] = "Noon explains its [sources of investment income](https://docs.noon.capital/1.-noon-the-basics/how-it-works) and the distinction between [individual vault strategies and sUSN](https://docs.noon.capital/1.-noon-the-basics/vaults)."

#: Accountable's launch announcement identifies SharpByte and Mu Digital as the
#: Asia-credit vault partners, and names staked AZND, muBOND and liquidity positions.
ASIA_CREDIT_STRATEGY_DESCRIPTION: Final[str] = "The Asia Credit Yield Vault accepts USDC to invest in Asian debt markets and provide funds for trading on crypto platforms. Accountable launched it with SharpByte Capital and Mu Digital. The launch announcement names staked AZND and muBOND among its investments, alongside funds supplied to trading pools. The aim is to earn income from debt investments and trading activity. Borrower defaults, software failures or difficulty selling investments could cause losses or delay withdrawals."

#: Neutral Trade's public strategy page describes Autopilot as a rebalanced
#: portfolio; its strategy directory lists availability on Monad as well as Ethereum.
NEUTRAL_AUTOPILOT_DESCRIPTION: Final[str] = "Neutral Trade Autopilot spreads USDC deposits across trading strategies run by several managers. It adjusts the mix as conditions change. The strategies balance trades to limit their dependence on whether markets rise or fall. They seek to earn income from payments between traders and differences between buying and selling prices. Trading losses, exchange failures or withdrawal delays can still affect depositors. Neutral Trade identifies Solana Labs, DRW Labs, Skyland Ventures, BlackPine and Monke Ventures as company backers."

#: Other reviewed Accountable deployments with manually researched descriptions.
#:
#: Sources and the limits of deployment-specific evidence are recorded in
#: ``docs/protocol-research/accountable-description-fill-2026-10-28.md``.
ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (42161, HexAddress("0x9fe29a3fec55414f70454e2dbe6522f48ced3e85")): HandwrittenVaultMetadata(
        name="Noon Symphony Yield Vault",
        short_description="USDC vault linked to Symphony's savings and rewards app, with investments provided by Noon.",
        description=("The Noon Symphony Yield Vault accepts USDC and is associated with Symphony's savings app. Symphony offers investment returns alongside travel and lifestyle rewards, and names Noon as a provider of the investments that generate those returns. " + NOON_YIELD_ENGINE_DESCRIPTION),
        link="https://www.symphony.io/blog/symphony-is-live-get-rewarded-for-saving-not-spending",
    ),
    (1, HexAddress("0xa73424f1ac94b3ef0d0c9af4f2967c87d4af25d9")): HandwrittenVaultMetadata(
        name="sUSN Delta Neutral Yield Vault",
        short_description="USDC vault associated with Noon's sUSN investment products.",
        description=("The sUSN Delta Neutral Yield Vault accepts USDC and is associated with Noon's sUSN investment products. Its name refers to trading that aims to reduce the effect of market price movements on returns. " + NOON_YIELD_ENGINE_DESCRIPTION),
        link="https://docs.noon.capital/1.-noon-the-basics/vaults",
    ),
    (1, HexAddress("0x0963c0f17d77e336ff75ed36c27d3a1522847615")): HandwrittenVaultMetadata(
        name="Noon Sophon Yield Vault",
        short_description="USDC vault from Noon's earlier Sophon offering.",
        description=("The Noon Sophon Yield Vault accepts USDC and takes its name from Noon's earlier Sophon offering. " + NOON_YIELD_ENGINE_DESCRIPTION),
        link="https://docs.noon.capital/6.-resources/contract-addresses-and-oracles",
    ),
    (143, HexAddress("0x4c0d041889281531ff060290d71091401caa786d")): HandwrittenVaultMetadata(
        name="Asia Credit Yield Vault",
        short_description="USDC investments in Asian debt markets and trading pools, launched with SharpByte Capital and Mu Digital.",
        description=ASIA_CREDIT_STRATEGY_DESCRIPTION,
        link="https://www.linkedin.com/posts/accountablecapital_verifiable-yield-arrives-on-monad-foundation-activity-7398798395642982401-6WqP",
    ),
    (143, HexAddress("0xaabab7598be3c4fe58c593e73c2f5934b73b573e")): HandwrittenVaultMetadata(
        name="Neutral Trade Autopilot",
        short_description="USDC trading strategies managed by Neutral Trade to limit exposure to market price swings.",
        description=NEUTRAL_AUTOPILOT_DESCRIPTION,
        link="https://www.neutral.trade/strategies/neutral-autopilot",
    ),
}

#: Source citations for reviewed Accountable vaults. These descriptive notes
#: do not introduce exclusionary vault flags.
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES: Final[dict[tuple[int, HexAddress], str]] = {key: (f"{metadata.description}\n\nSee the [official Tauri vault page]({metadata.link}), [operator case study](https://docs.travessiacredit.com/pages/tauri-case-study.html) and [Travessia's reported relationships](https://www.travessiacredit.com/).") for key, metadata in TRAVESSIA_TAURI_VAULT_METADATA.items()}
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES.update(
    {
        key: f"{metadata.description}\n\n{NOON_YIELD_ENGINE_SOURCES}"
        for key, metadata in ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA.items()
        if key
        in {
            (42161, "0x9fe29a3fec55414f70454e2dbe6522f48ced3e85"),
            (1, "0xa73424f1ac94b3ef0d0c9af4f2967c87d4af25d9"),
            (1, "0x0963c0f17d77e336ff75ed36c27d3a1522847615"),
        }
    }
)
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES[42161, HexAddress("0x9fe29a3fec55414f70454e2dbe6522f48ced3e85")] += " Symphony identifies Noon in its [launch announcement](https://www.symphony.io/blog/symphony-is-live-get-rewarded-for-saving-not-spending)."
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES[1, HexAddress("0x0963c0f17d77e336ff75ed36c27d3a1522847615")] += " Noon lists its separate Sophon-network tokens as discontinued in its [token directory](https://docs.noon.capital/6.-resources/contract-addresses-and-oracles). This [vault is on Ethereum](https://etherscan.io/address/0x0963c0f17d77e336ff75ed36c27d3a1522847615#code); that notice does not establish whether this vault is still operating."
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES[143, HexAddress("0x4c0d041889281531ff060290d71091401caa786d")] = f"{ASIA_CREDIT_STRATEGY_DESCRIPTION}\n\nAccountable's [Monad launch announcement](https://www.linkedin.com/posts/accountablecapital_verifiable-yield-arrives-on-monad-foundation-activity-7398798395642982401-6WqP) names the partners and investments. [SharpByte Capital](https://www.sharpbyte.xyz/) describes its investment management business."
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES[143, HexAddress("0xaabab7598be3c4fe58c593e73c2f5934b73b573e")] = f"{NEUTRAL_AUTOPILOT_DESCRIPTION}\n\nSee Neutral Trade's [Autopilot strategy](https://www.neutral.trade/strategies/neutral-autopilot), [network availability](https://www.neutral.trade/strategies?tier=marketplace) and [company background and backers](https://www.neutral.trade/faq)."
# Preserve the existing source-linked Avalanche note.
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES[43114, HexAddress("0x9c3da70cd7fedeaef528d060ea99ace133e08fd2")] = TRAVESSIA_TAURI_VAULT_NOTE


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
    return CENTAUR_VAULT_METADATA.get(key) or MORINI_CAPITAL_VAULT_METADATA.get(key) or TRAVESSIA_TAURI_VAULT_METADATA.get(key) or ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA.get(key)


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
