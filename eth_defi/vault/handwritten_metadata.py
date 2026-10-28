"""Reviewed, address-scoped vault descriptions.

The mappings cover products with missing adapter metadata or descriptions that
need a researched explanation of their underlying exposure. Adapters use these
as address-scoped overrides, preserving their normal behaviour for other vaults.
"""

from dataclasses import dataclass
from typing import Final

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class HandwrittenVaultMetadata:
    """Curated display metadata for one known vault.

    Keep researched copy separate from mutable names and incomplete protocol
    metadata. Adapters use the descriptions while retaining their onchain name.
    """

    #: Research display name; does not replace the adapter's onchain name.
    name: str

    #: One-sentence strategy summary for listings.
    short_description: str

    #: Longer strategy explanation, optionally containing Markdown links.
    description: str

    #: Canonical vault or manager-maintained product page.
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


def _make_alphaping_morpho_metadata(
    chain_id: int,
    address: HexAddress,
    name: str,
    asset: str,
    *,
    short_description: str,
    strategy: str,
    additional_risks: str = "",
) -> HandwrittenVaultMetadata:
    """Build source-linked copy for one reviewed AlphaPing Morpho deployment.

    The strategy text describes the published mandate rather than promising
    that current collateral allocations or liquidity remain constant.

    :param chain_id: Deployment chain ID.
    :param address: Reviewed share-token address.
    :param name: Research display name; adapters retain their onchain names.
    :param asset: Denomination token symbol.
    :param short_description: One-sentence listing summary.
    :param strategy: Researched explanation of the vault's lending exposure.
    :param additional_risks: Strategy-specific risks beyond ordinary lending.
    :return: Source-linked display metadata.
    """
    chain = {1: "ethereum", 8453: "base", 42161: "arbitrum", 999: "hyperevm"}[chain_id]
    link = f"https://app.morpho.org/{chain}/vault/{address}"
    risks = "Risks include collateral price changes, oracle errors, smart contract failures and insufficient liquidation liquidity."
    if additional_risks:
        risks = f"{risks} {additional_risks}"
    return HandwrittenVaultMetadata(
        name=name,
        short_description=short_description,
        description=(f"This AlphaPing-associated [Morpho lending vault]({link}) is denominated in {asset}. {strategy}\n\nDepositors earn interest paid by borrowers who post collateral to access loans. {risks} Withdrawals depend on available {asset} liquidity."),
        link=link,
    )


#: AlphaPing deployment inventory reviewed on 2026-10-28.
#:
#: Frozen from the union of Morpho V1/V2 owner and curator matches for
#: 0x6788c8ad65e85cca7224a0b46d061ef7d81f9da5 and
#: 0xeb4af6fa3afa08b10d593ec8ff87efb03bc04645. Excludes two ``zzzz``
#: deployments, ``(Deployer)`` and two installer contracts. Wallet association
#: establishes involvement, not a guarantee of unchanged curation or mandate.
#: Sources, aliases, exclusions and the allocation snapshot are recorded in
#: ``docs/protocol-research/alphaping.md``.
ALPHAPING_MORPHO_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (chain_id, address): _make_alphaping_morpho_metadata(chain_id, address, name, asset, short_description=summary, strategy=strategy, additional_risks=risks)
    for chain_id, address, name, asset, summary, strategy, risks in (
        (1, HexAddress("0x0bf0164d17469241b6e086da4016dcc54feaa334"), "Alpha USDC Delta V2", "USDC", "USDC lending with exposure to hedged carry, basis and arbitrage strategies.", "Its published Delta mandate targets hedged strategies, including funding spreads, basis trades and arbitrage across spot and derivatives markets.", "Hedges can fail and underlying strategies may use leverage or depend on offchain counterparties."),
        (1, HexAddress("0x153bd1abe60104bd46aa05a27fa12d1346d64a57"), "Alpha USDC Forex V2 / Acuto", "USDC", "USDC lending against tokenised Turkish currency and equity strategies.", "It lends USDC against tokens representing [Morini/Piku investment strategies](https://morini.capital/). At the October 2026 review, its configured markets accepted tokens for leveraged USD/TRY carry, Turkish equity basis trading and hedged currency arbitrage, as well as Piku's yield-bearing USP token. [Piku describes](https://piku.co/blog/alphaping-piku-morpho-usp-vault) how borrowers may use the loans to increase their underlying strategy exposure.", "These exposures add collateral valuation, offchain counterparty and leverage risks."),
        (999, HexAddress("0x242572d6f1af7111bca807ecdd0f74108ceaed5d"), "Alpha USDT Prime", "USD₮0", "USD₮0 lending across AlphaPing-selected credit markets on HyperEVM.", "Its published Prime mandate allocates across selected lending markets and strategies, with allocations adjusted as market conditions change.", ""),
        (1, HexAddress("0x35cbe8542e70fa2f7f9cdf129f19e593f4b4f560"), "Alpha USDC Asia V2", "USDC", "USDC lending with exposure to Asia-linked real-world asset strategies.", "Its published Asia mandate targets overcollateralised credit backed by Asia-linked real-world assets, potentially including private credit, trade finance and asset-backed strategies.", "Real-world assets add valuation, counterparty and legal enforcement risks."),
        (8453, HexAddress("0x44dd77d51629987d9555c762bf09903928c70206"), "Alpha USDC Prime V2", "USDC", "USDC lending across AlphaPing-selected credit markets on Base.", "Its published Prime mandate allocates across selected lending markets and strategies, with allocations adjusted as market conditions change.", ""),
        (1, HexAddress("0x610f5b68bd1eed68af649a3fd3dc2caa1ee4ae7e"), "Alpha LINK Enhanced V2", "LINK", "LINK lending with exposure to structured credit and liquidity strategies.", "Its published Enhanced mandate supplies LINK to strategy-driven credit and liquidity venues, including structured borrowing and incentive-supported markets. AlphaPing describes its [Enhanced mandates](https://alphaping.ch/enhanced-vaults) as allowing broader collateral and more complex exposures than Core.", "Structured exposures can add leverage and counterparty risks."),
        (1, HexAddress("0x951a9f4a2ce19b9dea6b37e691d076a345b6c0f8"), "Alpha USDC Enhanced V2", "USDC", "USDC lending against structured and strategy-based onchain assets.", "Its published mandate lends against a broader range of structured and strategy-based assets. AlphaPing's [Enhanced framework](https://alphaping.ch/enhanced-vaults) permits broader collateral eligibility and more complex exposure structures within mandate limits.", "Structured assets can add valuation, leverage and counterparty risks."),
        (1, HexAddress("0x9f39b13bb472126d6937bf25a39338e664eefa82"), "Alpha USDC Prime V2", "USDC", "USDC lending across AlphaPing-selected credit markets on Ethereum.", "Its published Prime mandate allocates across selected lending markets and strategies, with allocations adjusted as market conditions change.", ""),
        (1, HexAddress("0xbb5ad5d522684de9dd3f545fa6d6ac5bbca4710e"), "Alpha WETH Core V2", "WETH", "WETH lending through AlphaPing's Core vault range on Ethereum.", "It supplies WETH to selected Morpho lending markets. AlphaPing's published [Core framework](https://alphaping.ch/core-vaults) prioritises established liquid assets and overcollateralised credit; its eligible collateral is not detailed in public documentation.", ""),
        (1, HexAddress("0xd0634bf57921ef4068f284f5e78a3f2623e20129"), "Chaince USDC Yield", "USDC", "USDC lending operated in cooperation with Chaince Research and AlphaPing.", "The published vault description identifies Chaince Research LLC, a subsidiary of Chaince Digital, as operating the product in cooperation with AlphaPing. The mandate supplies USDC across a diversified set of overcollateralised lending markets.", ""),
        (1, HexAddress("0xd8cd72a2eb7a4312404c00aed5172c0026da0c27"), "Alpha Frax USD Enhanced V2", "frxUSD", "frxUSD lending across structured credit and liquidity strategies.", "Its published mandate deploys frxUSD across structured borrowing markets, incentive-supported pools and credit venues associated with the Frax ecosystem. AlphaPing's [Enhanced framework](https://alphaping.ch/enhanced-vaults) allows broader collateral and more complex exposures.", "Underlying structured strategies can add valuation, leverage and counterparty risks."),
        (1, HexAddress("0xeb6719c14ba01bf1d6bfb78a65f63f12fabf202c"), "Alpha APXUSD V2", "apxUSD", "apxUSD lending across selected overcollateralised credit markets.", "Its published mandate allocates apxUSD across overcollateralised lending opportunities based on risk, liquidity and market conditions.", ""),
        (1, HexAddress("0xf1ca44eea3a4effcb195a970a2f1d8553f76f9a1"), "Alpha USDC Core V2", "USDC", "USDC lending under AlphaPing's conservative Core credit mandate.", "Its published mandate targets short-duration, overcollateralised credit. AlphaPing's [Core framework](https://alphaping.ch/core-vaults) prioritises established liquid assets, conservative collateral thresholds and constrained market allocations.", ""),
        (1, HexAddress("0xfa7ed49eb24a6117d8a3168eee69d26b45c40c63"), "Alpha ZCHF Vault", "ZCHF", "ZCHF lending across selected liquid-collateral markets.", "Its published mandate supplies ZCHF across liquid-collateral markets and rebalances lending allocations as conditions change.", ""),
        (1, HexAddress("0xf9b85b19f6885511165505299648e6d052e8d4c8"), "AlphaPing USDC Core", "USDC", "USDC lending with a published focus on capital preservation and market liquidity.", "Its published Core mandate prioritises capital preservation, income from lending and market liquidity, with monitoring of collateral quality, utilisation and counterparty exposure.", ""),
        (8453, HexAddress("0xea47a35aaa60302b4816d920856ddc708ad181f6"), "Alpha wETH Core", "WETH", "WETH lending through AlphaPing's Core vault range on Base.", "It supplies WETH to selected Morpho lending markets. Public documentation does not identify this deployment's eligible collateral.", ""),
        (1, HexAddress("0xda4d1a3187eba9ecaa137867bd4541f78d51638e"), "Alpha MUSD Core", "MUSD", "MUSD-denominated lending through AlphaPing's Morpho Core vault.", "Its published mandate seeks lending returns denominated in Mezo's MUSD stablecoin. Its public description does not specify the eligible collateral.", ""),
        (1, HexAddress("0xce22b5fb17ccbc0c5d87dc2e0df47dd71e3adc0a"), "USDU Core", "USDU", "USDU lending through curated Morpho borrowing markets.", "Its published mandate allocates to USDU lending markets with curated borrower access and monitored credit exposures, supporting liquidity in the USDU ecosystem.", ""),
        (1, HexAddress("0xb0f05e4de970a1aaf77f8c2f823953a367504ba9"), "ALPHA USDC Core / Hawkwood", "USDC", "USDC lending through the AlphaPing deployment originally named ALPHA USDC Core.", "Originally published as ALPHA USDC Core and subsequently named Hawkwood, this deployment supplies USDC to selected Morpho markets. Its original mandate described lending to strategy-based assets and supporting market liquidity.", ""),
        (1, HexAddress("0x89610f2125a54547a7fb2a1b7926be734a632255"), "Nova WETH Vault", "WETH", "WETH lending through an AlphaPing-associated Nova vault.", "It supplies WETH to Morpho borrowing markets. Public documentation does not identify this deployment's eligible collateral.", ""),
        (1, HexAddress("0x874e7fb1abb4c27a7bbeeefa39e888b63181939c"), "AlphaPing USDC Prime", "USDC", "USDC lending combining AlphaPing's Core and Frontier credit opportunities.", "Its published Prime mandate combines Core lending exposures with higher-risk Frontier opportunities, adjusting allocations with liquidity, counterparty exposure and market conditions.", "Frontier exposures can introduce less liquid collateral and additional counterparty risks."),
        (1, HexAddress("0x6701957aaa39e9352a42729cc65436a4c945cb90"), "AlphaPing PT Frontier", "USDC", "USDC lending targeting higher-yield Frontier credit opportunities.", "Its published Frontier mandate targets emerging-market yield opportunities with monitoring of liquidity, counterparty exposure and protocol performance. Its public description does not identify the principal-token positions.", "Emerging credit markets can have less liquid collateral and additional counterparty risks."),
        (1, HexAddress("0x6619f92861c760ad11ba0d56e8ed63a33ecce22b"), "Alpha ZCHF Safe Vault", "ZCHF", "ZCHF lending under a published Bitcoin-only collateral mandate.", "Its published mandate describes a Bitcoin-only lending and borrowing environment denominated in ZCHF. The product name does not imply protection from loss.", ""),
        (1, HexAddress("0x52317a47585a6acdfbd7a29b494c3e2baae96abc"), "Alpha MUSD", "MUSD", "MUSD-denominated lending through AlphaPing's Morpho vault.", "Its published mandate seeks lending returns denominated in Mezo's MUSD stablecoin. Its public description does not specify the eligible collateral.", ""),
        (1, HexAddress("0x47fe8ab9ee47dd65c24df52324181790b9f47efc"), "Alpha WETH Core / Villandrando", "WETH", "WETH lending through the AlphaPing deployment originally named Alpha WETH Core.", "Originally published as Alpha WETH Core and subsequently named Villandrando, this deployment supplies WETH to selected Morpho markets. [AlphaPing's product page](https://alphaping.ch/vaults/0x47fe8ab9ee47dd65c24df52324181790b9f47efc) describes an overcollateralised lending mandate.", ""),
        (1, HexAddress("0x3c124fb80e6eedd88d108c2f04013ae5cd571abc"), "Falcon USDf Core", "USDf", "USDf lending against synthetic yield-bearing stablecoins and other selected collateral.", "Its published mandate lends Falcon's USDf against synthetic yield-bearing stablecoins and other selected assets.", "Synthetic stablecoins and their underlying strategies add issuer, hedging and valuation risks."),
        (1, HexAddress("0xa79a5fb6f619da3ed71087fb5cd33b16a5b666b5"), "Nova fxUSD Vault", "fxUSD", "fxUSD lending through an AlphaPing-associated Nova vault.", "It supplies fxUSD to Morpho borrowing markets. Public documentation does not identify this deployment's eligible collateral.", ""),
        (1, HexAddress("0x76dedaf4ec78777716bdeba484037c9d5fbf5d11"), "Alpha USDT Core", "USDT", "USDT lending through AlphaPing's Core vault range on Ethereum.", "It supplies USDT to selected Morpho lending markets. Public documentation does not identify this deployment's eligible collateral.", ""),
        (42161, HexAddress("0x4c289073eb6679393890b85eec9d604271c18c28"), "Alpha USDC Core", "USDC", "USDC lending through AlphaPing's Core vault range on Arbitrum.", "It supplies USDC to selected Morpho lending markets. Public documentation does not identify this deployment's eligible collateral.", ""),
        (8453, HexAddress("0x4a4de4757d2a04d1e12bcc21068288caad6e19a7"), "Alpha cbBTC Core", "cbBTC", "cbBTC lending through AlphaPing's Core vault range on Base.", "It supplies Coinbase Wrapped BTC to selected Morpho lending markets. Public documentation does not identify this deployment's eligible collateral.", "The denomination token also depends on its wrapped-Bitcoin issuer and redemption arrangements."),
        (42161, HexAddress("0x23faff4f253b85834d9d37f97ea807ec46d1ec9e"), "Alpha wETH Core", "WETH", "WETH lending through AlphaPing's Core vault range on Arbitrum.", "It supplies WETH to selected Morpho lending markets. Public documentation does not identify this deployment's eligible collateral.", ""),
        (8453, HexAddress("0x2365975ca06f99d5da5acfc70f2581b8d194f8aa"), "Alpha USDC Core", "USDC", "USDC lending through AlphaPing's Core vault range on Base.", "It supplies USDC to selected Morpho lending markets. Public documentation does not identify this deployment's eligible collateral.", ""),
        (42161, HexAddress("0x0f841bd670eaeb6be0a173c06efefbad09abb0f4"), "Alpha USDT Core", "USDT0", "USDT0 lending through AlphaPing's Core vault range on Arbitrum.", "It supplies USDT0 to selected Morpho lending markets. Public documentation does not identify this deployment's eligible collateral.", ""),
    )
}


def _make_alphaping_ipor_metadata(address: HexAddress, name: str, asset: str, strategy: str, *, deprecated: bool = False) -> HandwrittenVaultMetadata:
    """Build copy for one AlphaPing-labelled IPOR Fusion deployment.

    IPOR's public app configuration supplies the strategy and atomist identity,
    including the explicitly deprecated deployment. The review snapshot is
    documented alongside the Morpho inventory.

    :param address: Reviewed Ethereum vault address.
    :param name: IPOR configuration display name.
    :param asset: Denomination token symbol.
    :param strategy: Published borrowing-loop mechanics.
    :param deprecated: Whether IPOR explicitly labels this deployment deprecated.
    :return: Source-linked description and one-sentence summary.
    """
    link = f"https://app.ipor.io/fusion/ethereum/{address}"
    status = "IPOR labels this deployment deprecated. " if deprecated else ""
    summary = "Deprecated sUSDf borrowing loop managed by AlphaPing on IPOR Fusion." if deprecated else f"Leveraged {asset} strategy using Falcon assets and Morpho borrowing loops."
    return HandwrittenVaultMetadata(
        name=name,
        short_description=summary,
        description=(f"{status}This AlphaPing-managed [IPOR Fusion vault]({link}) is denominated in {asset}. {strategy}\n\nReturns depend on the underlying Falcon yield exceeding borrowing and execution costs. Repeated borrowing amplifies losses as well as gains. Risks include liquidation, Falcon counterparty and stablecoin risks, oracle errors, smart contract failures and insufficient exit liquidity."),
        link=link,
    )


#: Ethereum IPOR deployments labelled ``atomist: Alphaping`` in the public app
#: configuration reviewed on 2026-10-28. All three were hidden from its public
#: listing; only the middle deployment was explicitly labelled deprecated.
ALPHAPING_IPOR_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (1, HexAddress("0xb4790cfdf1df71c2e4ac2f5c43dbcb975ad447ae")): _make_alphaping_ipor_metadata(
        HexAddress("0xb4790cfdf1df71c2e4ac2f5c43dbcb975ad447ae"),
        "Leveraged Falcon USDC Core",
        "USDC",
        "The published strategy deposits USDC into Falcon USDC Core and uses the resulting vault shares as Morpho collateral to borrow USDC. Borrowed USDC is exchanged for USDf and staked for sUSDf, which is also used as collateral for further USDC borrowing and reinvestment.",
    ),
    (1, HexAddress("0x29d322dd088e9b9d1416f43188954f08748fafbb")): _make_alphaping_ipor_metadata(
        HexAddress("0x29d322dd088e9b9d1416f43188954f08748fafbb"),
        "Leveraged sUSDf (Deprecated)",
        "sUSDf",
        "The published strategy posts sUSDf as Morpho collateral, borrows USDC, exchanges it for USDf and stakes it for more sUSDf. The new sUSDf is supplied as collateral and the borrowing cycle repeats.",
        deprecated=True,
    ),
    (1, HexAddress("0xb530a1b5259a71187f1d69acf0488f102637a3ed")): _make_alphaping_ipor_metadata(
        HexAddress("0xb530a1b5259a71187f1d69acf0488f102637a3ed"),
        "Leveraged sUSDf",
        "sUSDf",
        "The published strategy posts sUSDf as Morpho collateral, borrows USDC, exchanges it for USDf and stakes it for more sUSDf. The new sUSDf is supplied as collateral and the borrowing cycle repeats.",
    ),
}

#: Reviewed AlphaPing copy shared by live adapters and the metadata-only repair.
ALPHAPING_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    **ALPHAPING_MORPHO_VAULT_METADATA,
    **ALPHAPING_IPOR_VAULT_METADATA,
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
    return ALPHAPING_VAULT_METADATA.get(key) or CENTAUR_VAULT_METADATA.get(key) or MORINI_CAPITAL_VAULT_METADATA.get(key) or TRAVESSIA_TAURI_VAULT_METADATA.get(key) or ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA.get(key)


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
