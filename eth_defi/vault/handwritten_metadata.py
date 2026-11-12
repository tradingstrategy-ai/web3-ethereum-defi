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

    #: Longer strategy explanation in Markdown, shown as "About the vault".
    #:
    #: Use paragraphs of two or three sentences separated by blank lines.
    #: Bold text, inline links and ``####`` subheadings are allowed; see
    #: ``docs/README-vault-description-formatting.md``.
    description: str

    #: Public vault page URL; use Trading Strategy unless explicitly requested otherwise.
    link: str


#: Nest AXI Vault on Plume (98866), reviewed on 2026-11-02.
#:
#: Plain-language replacement for Nest's payment-financing marketing copy.
#: The published explanation describes "fiat and stablecoin liquidity" delivered
#: where needed, but does not specify borrower costs or income sharing.
#: Use the verified public production page rather than its development mirror.
#: Scope this override to the reviewed vault entrypoint, not its share token.
#: Nest's first-party catalogue explicitly names "Axiym AXI" as the target
#: composition and maps the XUPL route to the Plume token below. The public
#: transparency panel reports XUPL holdings. This establishes product exposure,
#: not the complete legal lending chain or the identities of ultimate borrowers.
#: Research evidence and remaining gaps: .claude/docs/nest-axi-research.md.
#: https://api.nest.credit/v1/vaults/nest-axi-vault
#: https://www.axiym.io/terms-conditions
#: https://www.avalanche.com/about/blog/how-axiym-is-rewiring-global-payments-on-avalanche
#: https://www.wearefreemarket.com/news/freemarket-and-axiym-partner-to-unlock-instant-liquidity-for-businesses
#: https://tether.io/news/global-industrial-conglomerate-hyundai-completes-enterprise-treasury-pilot-on-tether-usdt-moving-corporate-funds-across-global-borders/
#: https://tradingstrategy.ai/vaults/nest-axi-vault
#: https://explorer.plume.org/address/0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0
#: https://app.plume.org/vaults/nest-axi-vault?section=transparency
NEST_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (98866, HexAddress("0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0")): HandwrittenVaultMetadata(
        name="Nest AXI Vault",
        short_description="Financing for payment businesses so they can send money without keeping as much cash parked in advance.",
        description=(
            "Nest AXI Vault aims to earn income by financing businesses that send money for customers. "
            "This gives those businesses money to make payments without keeping as much of their own cash in payment accounts beforehand.\n\n"
            "#### Business relationships\n\n"
            "The vault's planned investment is [Axiym AXI payment financing](https://api.nest.credit/v1/vaults/nest-axi-vault). "
            "Axiym provides [financing for licensed money-transfer and payment businesses](https://www.avalanche.com/about/blog/how-axiym-is-rewiring-global-payments-on-avalanche). "
            "Axiym's operator is [Axi Labs AG, a Swiss company](https://www.axiym.io/terms-conditions).\n\n"
            "#### Examples of Axiym services\n\n"
            "In April 2025, payment platform Freemarket announced [access to Axiym financing in US dollars and euros](https://www.wearefreemarket.com/news/freemarket-and-axiym-partner-to-unlock-instant-liquidity-for-businesses) for business customers through their Freemarket accounts.\n\n"
            "In July 2026, Hyundai Motor America and Hyundai Motor de México used Axiym for a [$20,000 cross-border payment pilot](https://tether.io/news/global-industrial-conglomerate-hyundai-completes-enterprise-treasury-pilot-on-tether-usdt-moving-corporate-funds-across-global-borders/). "
            "This demonstrated a payment service; the announcement did not describe a loan.\n\n"
            "#### Transparency\n\n"
            "In November 2026, the [vault's transparency dashboard](https://app.plume.org/vaults/nest-axi-vault?section=transparency) showed almost all its assets in XUPL, "
            "a token named [USDC PayFi Lending Vault](https://explorer.plume.org/token/0x72ee4fb6ed94436438e9c45c39660d7c2b4152f5). "
            "The dashboard does not name the **businesses borrowing the money**, and neither example above is confirmed to have used this vault's money.\n\n"
            "Public disclosures do not include the full lending agreements, what borrowers pay for financing or how income is shared with depositors."
        ),
        link="https://app.plume.org/vaults/nest-axi-vault?section=transparency",
    ),
}


#: Passivbot Canon on Hypercore (9999), reviewed on 2026-11-01.
#:
#: The project README links this exact vault as its reference long-only
#: configuration. Hyperliquid vaultDetails describes an unaltered template
#: configuration and reports a 10% leader commission. Current project defaults
#: do not prove the vault's running software version or exact settings.
#: https://github.com/enarjord/passivbot
#: https://github.com/enarjord/passivbot/blob/master/docs/config.bot.md
#: https://app.hyperliquid.xyz/vaults/0x490af7d4a048a81db0f677517ed6373565b42349
#: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy
PASSIVBOT_VAULT_METADATA: Final[dict[tuple[int, str], HandwrittenVaultMetadata]] = {
    (9999, "0x490af7d4a048a81db0f677517ed6373565b42349"): HandwrittenVaultMetadata(
        name="Passivbot Canon",
        short_description="Automated long-only crypto futures trading using PassivBot's reference strategy.",
        description=(
            "Passivbot Canon pools USDC to trade cryptocurrency perpetual futures on Hyperliquid, contracts that follow crypto prices without an expiry date. "
            "The [PassivBot project](https://github.com/enarjord/passivbot) identifies this exact vault as a live example of its reference **long-only configuration**, which buys exposure to rising prices.\n\n"
            "#### How the strategy works\n\n"
            "PassivBot is open-source trading software that automatically places and manages orders. "
            "Its contrarian approach aims to buy during price falls and take profit on rebounds, adding to positions within configured exposure limits to lower their average entry price.\n\n"
            "The software supports staggered limit orders at different prices and trailing orders that wait for a price move and a reversal before acting. "
            "The vault describes its configuration as the unaltered project template; its running software version and exact settings have not been independently verified.\n\n"
            "Adding to a losing position increases the money exposed to further price falls. "
            "A sustained decline can leave positions at a large loss or cause liquidation, where the exchange closes positions because there is insufficient collateral; frequent small winning trades do not ensure an overall profit.\n\n"
            "#### Fees and withdrawals\n\n"
            "As checked on **1 November 2026**, the vault charges a **10% leader share of profits**. "
            "Hyperliquid's user-vault terms impose a **one-day lock-up after deposits**, and withdrawals can incur slippage when open positions are closed.\n\n"
            "#### Sources\n\n"
            "- [PassivBot strategy documentation](https://github.com/enarjord/passivbot/blob/master/docs/config.bot.md)\n"
            "- [Passivbot Canon on Hyperliquid](https://app.hyperliquid.xyz/vaults/0x490af7d4a048a81db0f677517ed6373565b42349)\n"
            "- [Hyperliquid user-vault fees and withdrawals](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy)"
        ),
        link="https://app.hyperliquid.xyz/vaults/0x490af7d4a048a81db0f677517ed6373565b42349",
    ),
}


#: Agent RockSteady on Hypercore (9999), reviewed on 2026-10-31.
#:
#: The operator supplied the 30-market mean-reversion remit and holding periods
#: in the NuWealth.ai curator request on 2026-10-30. The official check and
#: record pages corroborate the vault identity, fixed-rule execution, launch
#: date, fees and withdrawal timing. Keep this override scoped to Hypercore.
#: On 2026-11-03 the operator supplied 4 February 2026 as the public live-record
#: start date; this display correction does not change discovery timestamps.
#: https://tradingstrategy.ai/vaults/agent-rocksteady
#: https://nuwealth.ai/check
#: https://nuwealth.ai/record
#: https://nuwealth.ai/risk
#: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-leaders-legacy
ROCKSTEADY_VAULT_METADATA: Final[dict[tuple[int, str], HandwrittenVaultMetadata]] = {
    (9999, "0xd321ad79041bdad719ba5ff42fe950e1977df439"): HandwrittenVaultMetadata(
        name="Agent RockSteady",
        short_description="Automated crypto futures strategy that mostly buys sharp dips and takes profit on rebounds.",
        description=("Agent RockSteady accepts USDC for a mean-reversion strategy run by [NuWealth.ai](https://nuwealth.ai/). It trades perpetual futures on Hyperliquid, contracts that follow cryptocurrency prices, using fixed rules rather than live AI decisions.\n\nNuWealth describes trading across **30 markets**, mostly buying sharp dips and taking profit when prices rebound, with occasional short positions that benefit from falling prices. Trades usually last hours, and the strategy aims for many small winning trades rather than a few large moves.\n\nThe vault has traded live since **4 February 2026**. Its [published vault details](https://nuwealth.ai/check) specify a **10% profit share** for the leader, with no management fee.\n\nEach deposit locks the depositor's whole vault position for **24 hours** before withdrawals are available. The leader must retain at least **5% of the vault's capital**; depositors can use Hyperliquid directly to withdraw without the NuWealth app."),
        link="https://tradingstrategy.ai/vaults/agent-rocksteady",
    ),
}


#: Systemic Strategies L/S Grids on Hypercore (9999), reviewed on 2026-11-06.
#:
#: The live Hyperliquid vaultDetails response confirms the exact address, the
#: low-inflation long/high-inflation short grid mandate and 10% commission.
#: The operator's 3 November post describes exposure readjustments across its
#: vaults, not this vault's numerical risk limits. Do not import HyperGrowth's
#: HYPE-specific mandate or infer a verified win rate from the maintained tags.
#: Source access, announcement context and evidence gaps:
#: .claude/docs/systemic-strategies-vault-research.md
#: https://api.hyperliquid.xyz/info
#: https://x.com/SystemicStratHL/status/2117599240150987067
#: https://tradingstrategy.ai/vaults/systemic-strategies-l-s-grids
SYSTEMIC_STRATEGIES_VAULT_METADATA: Final[dict[tuple[int, str], HandwrittenVaultMetadata]] = {
    (9999, "0x07fd993f0fa3a185f7207adccd29f7a87404689d"): HandwrittenVaultMetadata(
        name="[ Systemic Strategies ] L/S Grids",
        short_description="Crypto futures grid trading that bets on tokens with low inflation or few unlocks and against tokens with higher inflation or unlocks.",
        description=("Pools USDC to trade [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts that follow cryptocurrency prices without an expiry date. The strategy combines **long positions in tokens with low supply inflation or few unlocks** with **short positions in tokens with higher inflation or unlocks**. Longs benefit from rising prices and shorts from falling prices; supply inflation creates new tokens, while unlocks release previously restricted tokens.\n\n#### How the strategy works\n\nGrid trading places buy and sell orders at different price levels, seeking to capture repeated price moves. The supply-based selection adds a directional view: returns depend on which tokens rise or fall as well as the trades made within each grid.\n\nHolding both longs and shorts does not ensure that their market exposures cancel out. Sustained price moves against open positions can produce large losses, even if many individual grid trades are profitable."),
        link="https://tradingstrategy.ai/vaults/systemic-strategies-l-s-grids",
    ),
}


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
NOON_YIELD_ENGINE_DESCRIPTION: Final[str] = "Across its products, Noon earns income from short-term US government debt, other loans and debt investments, lending through crypto platforms, and trading designed to reduce exposure to market price movements. Its sUSN token gives holders access to this mix of investments. Individual vaults can follow different strategies and withdrawal rules. Noon's public documents describe its wider business; they do not establish this vault's current investments."

#: Official public sources for Noon's yield model and vault-specific scope.
NOON_YIELD_ENGINE_SOURCES: Final[str] = "Noon explains its [sources of investment income](https://docs.noon.capital/1.-noon-the-basics/how-it-works) and the distinction between [individual vault strategies and sUSN](https://docs.noon.capital/1.-noon-the-basics/vaults)."

#: Accountable's launch announcement identifies SharpByte and Mu Digital as the
#: Asia-credit vault partners, and names staked AZND, muBOND and liquidity positions.
ASIA_CREDIT_STRATEGY_DESCRIPTION: Final[str] = "The Asia Credit Yield Vault accepts USDC to invest in Asian debt markets and provide funds for trading on crypto platforms. Accountable launched it with SharpByte Capital and Mu Digital. The launch announcement names staked AZND and muBOND among its investments, alongside funds supplied to trading pools. The aim is to earn income from debt investments and trading activity. Borrower defaults, software failures or difficulty selling investments could cause losses or delay withdrawals."

#: Neutral Trade's Solana Autopilot vault, the destination of its EVM feeder vaults.
NEUTRAL_AUTOPILOT_URL: Final[str] = "https://www.neutral.trade/strategies/neutral-autopilot"

#: Neutral Trade's update confirming the Ethereum feeder, Accountable's role and custody.
NEUTRAL_TRADE_UPDATE_URL: Final[str] = "https://www.neutral.trade/blog/september-october-2026-update"


#: Accountable vault pages for the Neutral Trade Autopilot feeders, keyed by
#: network name. Accountable uses the loan address, not the share-token address.
NEUTRAL_AUTOPILOT_ACCOUNTABLE_URLS: Final[dict[str, str]] = {
    "Ethereum": "https://yield.accountable.capital/vaults/0x56B935Fe5183cC0DE489233d032F9A4B8ec2f9Ff",
    "Monad": "https://yield.accountable.capital/vaults/0x5ee57E42DF67e5707F0CAE1a18DfeaDB4F0Df86c",
}


def _make_neutral_autopilot_metadata(network: str, other_network: str) -> HandwrittenVaultMetadata:
    """Build the reviewed copy for one Neutral Trade Autopilot feeder vault.

    Neutral Trade's Ethereum and Monad vaults, built with Accountable, are
    entry points to the same Solana Autopilot vault. Sharing one template keeps
    the explanation of that cross-linked capital identical on both networks,
    and each description links to the other network's vault. The description
    is Markdown with short paragraphs and ``####`` subheadings, following
    ``docs/README-vault-description-formatting.md``. Sources are recorded in
    ``docs/protocol-research/neutral-trade-autopilot-2026-10-29.md``.

    :param network: Network of the described feeder vault.
    :param other_network: Network of the other Accountable feeder vault.
    :return: Display metadata for the deployment.
    """
    link = NEUTRAL_AUTOPILOT_ACCOUNTABLE_URLS[network]
    other_link = NEUTRAL_AUTOPILOT_ACCOUNTABLE_URLS[other_network]
    description = f"""This {network} vault, built with [Accountable]({link}), is an entry point to [Neutral Autopilot]({NEUTRAL_AUTOPILOT_URL}), Neutral Trade's multi-strategy vault on Solana. It accepts USDC and passes it into the Solana vault, where Neutral Trade says most of the strategy's money is held. Accountable [calculates the vault's value independently](https://x.com/accountabledata/status/2106036446503047195) of Neutral Trade.

#### How the strategy works

Autopilot spreads deposits across strategies run by Neutral Trade and by outside trading firms, such as arbitrage between exchanges, collecting funding payments between futures traders, market making and options trading. It shifts the mix as results change.

Most of these strategies balance their trades to limit their dependence on whether prices rise or fall. Neutral Trade [caps strategies that bet on price direction](https://www.neutral.trade/blog/neutral-autopilot-launch) at **5% of the portfolio**.

#### Shared capital and custody

**This vault and a matching [{other_network} vault]({other_link}) both feed the same Solana vault.** Depositors on all three networks therefore share the same positions and the same centralised exchange accounts. Neutral Trade says this exchange capital is [held by the custodians Ceffu or Copper]({NEUTRAL_TRADE_UPDATE_URL}), and that trading firms can trade but cannot withdraw deposits.

#### Fees, withdrawals and risks

As of 29 October 2026, the vault's onchain fee settings charge **no management fee and a 3% performance fee** on gains, all of which goes to Accountable. Neutral Trade does not state whether its own Autopilot fees also apply to money arriving through these vaults.

Withdrawals are not instant: since 12 October 2026, Neutral Autopilot has settled withdrawal requests **weekly on Saturdays**. Depositors can still lose money through trading losses or the failure of an exchange or custodian, and withdrawals can be delayed.

#### Company

Neutral Trade was founded in 2024. Its [FAQ](https://www.neutral.trade/faq) names Solana Labs, YZi Labs, DRW Labs, Skyland Ventures, BlackPine and Monke Ventures as investors in the company; they do not guarantee vault returns."""
    return HandwrittenVaultMetadata(
        name="Neutral Trade Autopilot",
        short_description=f"{network} entry point to Neutral Trade's Solana-based Autopilot vault, which spreads USDC across several trading strategies designed to limit exposure to market direction.",
        description=description,
        link=link,
    )


#: Neutral Trade Autopilot feeder vaults built with Accountable.
NEUTRAL_AUTOPILOT_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (1, HexAddress("0x909dadbca7955614a455d9e7447ad4adb4902c8e")): _make_neutral_autopilot_metadata("Ethereum", "Monad"),
    (143, HexAddress("0xaabab7598be3c4fe58c593e73c2f5934b73b573e")): _make_neutral_autopilot_metadata("Monad", "Ethereum"),
}

#: Source-linked note explaining the cross-linked Autopilot feeder vaults.
NEUTRAL_AUTOPILOT_NOTE: Final[str] = f"This Accountable vault is a wrapper, or entry point, for Neutral Trade's Solana-based [Neutral Autopilot]({NEUTRAL_AUTOPILOT_URL}) vault, where Neutral Trade says the main capital is held. Neutral Trade's Ethereum and Monad Autopilot vaults are cross-linked: both feed the same Solana vault, so their depositors share its positions and its centralised exchange accounts. Neutral Trade says exchange capital is held in off-exchange custody with Ceffu or Copper and that trading firms cannot withdraw deposits, in its [September and October 2026 update]({NEUTRAL_TRADE_UPDATE_URL}). Accountable [calculates the vault's value independently](https://x.com/accountabledata/status/2106036446503047195). Neutral Trade's [FAQ](https://www.neutral.trade/faq) describes its redemption schedule and company investors."

#: Saturn PT-USDat on Monad, reviewed on 2026-11-02.
#:
#: Accountable's cached strategy describes the loop and withdrawal policy.
#: Saturn's current homepage distinguishes USDat from the separate sUSDat product.
#: See ``docs/protocol-research/saturn-pt-usdat-description.md`` for evidence limits.
SATURN_PT_USDAT_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (143, HexAddress("0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3")): HandwrittenVaultMetadata(
        name="Saturn PT-USDat Loop Vault",
        short_description="This vault invests USDC in tokens redeemable at a future date and borrows money to increase the investment, magnifying potential gains and losses.",
        description="""#### What the vault invests in

This vault invests USDC in PT-USDat and borrows more USDC to increase the investment. It aims to earn more from the tokens than it pays in borrowing costs, while taking on the risk of larger losses.

[USDat](https://tradingstrategy.ai/glossary/usdat) is Saturn's digital dollar: a [stablecoin](https://tradingstrategy.ai/glossary/stablecoin), meaning a token intended to keep a stable price. USDat is [backed by PayPal USD through PYUSDx](https://saturn.credit/), a token representing that backing. USDat is distinct from sUSDat, Saturn's separate investment product that receives income from company securities.

[Pendle](https://tradingstrategy.ai/glossary/pendle) is a marketplace that separates a token's future income or rewards from its redemption value. A [principal token](https://tradingstrategy.ai/glossary/principal-token), or PT, represents the redemption part; PT-USDat is the version associated with USDat. Its [maturity](https://tradingstrategy.ai/glossary/maturity) is the date when that redemption becomes available.

#### How it earns returns

The vault buys principal tokens below their redemption value and aims to earn the difference by holding them until maturity. Redemption is [measured in the asset specified by each market](https://docs.pendle.finance/pendle-v2/ProtocolMechanics/YieldTokenization/PT), rather than a guaranteed cash payment in US dollars. Selling before maturity can produce a different return because the market price changes.

The vault uses its tokens as [collateral](https://tradingstrategy.ai/glossary/collateral), assets pledged to secure a loan, and borrows USDC through [Morpho](https://tradingstrategy.ai/glossary/morpho), a lending platform. It invests the borrowed money in more PT-USDat and can repeat this process, known as [recursive looping](https://tradingstrategy.ai/glossary/recursive-looping). This creates [leverage](https://tradingstrategy.ai/glossary/leverage): a larger investment than depositors' money alone could buy.

Returns depend on the gain from buying tokens below their redemption value, after borrowing costs and fees. **The vault's return is not fixed:** borrowing rates can rise, reducing profits or causing losses, and borrowed money magnifies the effect. The strategy involves moving into tokens with a later maturity as the existing ones approach redemption, so the vault does not have a single end date.

#### What can cause losses

USDat can fall below its intended dollar value, and problems with its backing or redemption process can affect the tokens the vault holds. PT prices can also fall before maturity, while borrowing interest increases the debt. If the collateral no longer sufficiently covers the loan, [liquidation](https://tradingstrategy.ai/glossary/liquidation), a forced sale to repay lenders, can cause losses.

[Morpho's lending markets](https://docs.morpho.org/learn/concepts/blue/) rely on an [oracle](https://tradingstrategy.ai/glossary/oracle), a price feed used to value collateral. Incorrect prices, unsuitable market settings, mistakes by the manager or failures in the software controlling the funds can cause losses. Limited [liquidity](https://tradingstrategy.ai/glossary/liquidity), meaning too little money available to trade or repay borrowing, can make it harder to reduce the investment or return deposits.

#### Withdrawals

**Withdrawals are not immediate.** Under the [published withdrawal terms](https://yield.accountable.capital/vaults/0x593784188369E5e5f484025A1940cf437620C3Fe), requests made near the tokens' maturity can be settled through redemption, avoiding a sale of those tokens on the market. Otherwise, the vault sells tokens on Pendle in smaller batches as buyers and trading funds become available; selling early can reduce the amount returned.

The stated processing time is one to two weeks in most cases, with a stated limit of the earlier of maturity or two months. However, shortages of USDC in the Morpho market or buyers for PT-USDat can delay processing. Treat these as the operator's stated timings, not a guaranteed payment deadline.""",
        link="https://yield.accountable.capital/vaults/0x593784188369E5e5f484025A1940cf437620C3Fe",
    ),
}


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
    **NEUTRAL_AUTOPILOT_VAULT_METADATA,
    **SATURN_PT_USDAT_VAULT_METADATA,
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
ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES.update(dict.fromkeys(NEUTRAL_AUTOPILOT_VAULT_METADATA, NEUTRAL_AUTOPILOT_NOTE))
# Use the dedicated source-linked Avalanche note instead of the generic Tauri note.
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


#: Unified avUSDC vault on Base, reviewed on 2026-11-05.
#:
#: The fee-allocation table and announcement agree on the 70% LP share;
#: the documentation's introductory 100% claim conflicts with its own table.
#: Liquidity limits are established by the reviewed implementations, not a
#: promise of current exit availability. Protocol and curator copy is separate.
#: https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc
#: https://docs.veranta.xyz/liquidity-providers/risk-management
#: https://www.veranta.xyz/blog/systematic-avnt-buybacks-and-burns
#: docs/protocol-research/veranta-0x944766f715b51967e56afde5f0aa76ceacc9e7f9.md
VERANTA_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (8453, HexAddress("0x944766f715b51967e56afde5f0aa76ceacc9e7f9")): HandwrittenVaultMetadata(
        name="Avantis USDC Vault",
        short_description="USDC supplied to back trading positions and earn a share of trading fees.",
        description=(
            "Deposits back traders' positions in cryptocurrency, currency, commodity and index markets. "
            "Depositors receive avUSDC tokens representing their share of the pool and earn "
            "[trading fee income](https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc).\n\n"
            "Idle capital may also be placed in other investments to earn income. "
            "The current investments, if any, have not been confirmed.\n\n"
            "#### Trading income\n\n"
            "- Vault depositors receive **70% of fees collected on opening, closing and profitable trades** under the "
            "protocol's [fee allocation](https://www.veranta.xyz/blog/systematic-avnt-buybacks-and-burns).\n"
            "- Vault depositors also receive **100% of margin fees**, which traders pay for using the pool's capital.\n"
            "- The remaining **30% of fees on opening, closing and profitable trades** buys the protocol's AVNT token "
            "and permanently removes it from circulation.\n"
            "- Fees from forced closures of underfunded trading positions go to the protocol treasury to fund operations.\n\n"
            "#### Payouts and withdrawals\n\n"
            "Trader losses build a reserve used to pay trader profits before drawing on deposited capital. "
            "Depositors earn trading fees rather than receiving those losses directly, and **deposits can lose value** if the reserve runs out.\n\n"
            "Withdrawals depend on money being available in the pool and limits on how much capital can be removed while backing trading positions. "
            "The displayed withdrawal fee is **0.5%**. "
            "The actual withdrawal fee is **dynamic** and follows the "
            "[reserve-based fee schedule](https://docs.veranta.xyz/liquidity-providers/risk-management)."
        ),
        link="https://tradingstrategy.ai/vaults/veranta-usdc-vault",
    ),
}


#: Both publicly documented Birch Hill deployments on Base, reviewed 2026-11-06.
#: Exact identities: each vault book's technical appendix / onchain references.
#: The platform also shows unnamed coming-soon products without deployment addresses.
BIRCH_HILL_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (8453, HexAddress("0xb038cf3e233b78225f6d350ac8319bec8457db4a")): HandwrittenVaultMetadata(
        name="Birch Hill Groma Ecosystem USDC",
        short_description="USDC loans secured by tokenised shares in a residential property portfolio, earning interest from borrowers.",
        description=(
            "Deposited USDC funds loans to GromaCorp and verified holders of GRO, a token representing shares in GromaREIT's residential properties in Boston and Providence. "
            "Borrowers pledge their GRO shares as security and pay interest, which increases the value of depositors' vault shares after fees. "
            "The [vault book](https://docs.birchhill.io/vault-books/birch-hill-groma-ecosystem-usdc-vault) also describes a smaller allocation to loans secured by cbBTC, Coinbase's bitcoin-backed token, to help meet withdrawals.\n\n"
            "#### Access and fees\n\n"
            "**Direct deposits do not require identity verification**; the [October 2026 launch announcement](https://research.birchhill.io/p/introducing-birch-hill-ecosystem) distinguishes this from access through the operator's app, which requires identity checks. "
            "The vault charges [**10% of investment gains**](https://tradingstrategy.ai/vaults/birch-hill-rwa-usdc) as a [performance fee](https://tradingstrategy.ai/glossary/performance-fee) and no management fee.\n\n"
            "#### Withdrawals and losses\n\n"
            "Withdrawals depend on USDC being available rather than lent out. "
            "Most lending is secured by one issuer's property shares, so falling property values or insufficient recoveries from a default can reduce depositors' capital. "
            "GRO shares have transfer restrictions and limited quarterly redemptions, which can delay recovery when loans are not repaid."
        ),
        link="https://tradingstrategy.ai/vaults/birch-hill-rwa-usdc",
    ),
    (8453, HexAddress("0xa5afd3a598ef000a5edd96ed65929ac7a6feea39")): HandwrittenVaultMetadata(
        name="Birch Hill RWA USDC",
        short_description="USDC allocated to lending vaults backed by tokenised real-world assets, for verified accredited and institutional investors.",
        description=(
            "Deposited USDC is allocated to lending vaults that finance asset issuers and token holders. "
            "The [investment mandate](https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/1.-strategy-mandate) combines positions in Birch Hill Ecosystem vaults with uninvested USDC; borrowers' interest flows through those positions into the value of depositors' shares.\n\n"
            "The [current platform](https://birchhill.io/platforms) identifies this as the Flagship allocation product advised by BH Digital Asset Management. "
            "Its [Groma Ecosystem investment](https://tradingstrategy.ai/vaults/birch-hill-rwa-usdc) lends against GRO, shares in a residential property portfolio, with a smaller allocation to loans secured by cbBTC, Coinbase's bitcoin-backed token.\n\n"
            "#### Access and fees\n\n"
            "**Access is restricted to verified accredited and institutional investors.** "
            "The [access requirements](https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/4.-access-and-monitoring) include identity checks, investor eligibility verification and wallet screening, with approval checked before deposits and withdrawals. "
            "The [published Flagship fee](https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/2.-structure-and-loan-flow) is **10% of interest earned**; underlying Ecosystem vaults also publish their own fees, so this figure should not be read as a combined cost.\n\n"
            "#### Withdrawals and losses\n\n"
            "Uninvested USDC and the underlying vaults' available funds support withdrawals between borrower repayments; access approval does not guarantee immediate liquidity. "
            "Losses in the lending vaults reduce the value of this vault's shares, including shortfalls when assets pledged by defaulting borrowers cannot cover their debt."
        ),
        link="https://tradingstrategy.ai/vaults/birch-hill-rwa-usdc-2",
    ),
}


#: GLV [USDG-USDG] on Arbitrum (42161), marketed as the GMX Dollar Vault.
#: Reviewed on 2026-11-12 against GMX's own announcements and documentation.
#:
#: The seven-market list and the launch-boost terms are dated because GMX
#: plans to add markets and publish later boost rates. The GLV holds GM pool
#: tokens whose pools hold only USDG; depeg and issuer risk are our reading of
#: that structure, not a GMX statement. The Morpho post says shares can be pledged to borrow USDG in a
#: Gauntlet-curated market; its 28% looped-yield estimate is omitted.
#: No migration is needed: each successful GMX catalogue sync, run on every
#: Arbitrum scan cycle, rewrites the description fields of the products it returns.
#: https://gmxio.substack.com/p/deposit-dollars-earn-dollars-the
#: https://x.com/GMX_IO/status/2107456878548570618
#: https://x.com/GMX_IO/status/2118496905760272473
#: https://x.com/GMX_IO/status/2120494428213420489
#: https://docs.gmx.io/docs/providing-liquidity/
#: https://docs.gmx.io/docs/usdg-launch-boost/
#: https://tradingstrategy.ai/vaults/glv-usdg-usdg
GMX_VAULT_METADATA: Final[dict[tuple[int, str], HandwrittenVaultMetadata]] = {
    (42161, "0x4cd5a94a30876320ac65f2e192493ee476f13866"): HandwrittenVaultMetadata(
        name="GLV [USDG-USDG]",
        short_description="The vault supplies USDG, a dollar stablecoin, to pools that back leveraged crypto traders across several markets, earning fees and gaining when traders lose but paying out when they win.",
        description=(
            "#### Vault's trading strategy\n\n"
            "Deposits fund the pools that pay winning traders on [GMX](https://gmx.io/), an exchange for [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts with no expiry date that let traders bet on prices using borrowed money. "
            "The vault owns shares in several of these pools, one for each market, and every pool holds only **USDG**, a dollar stablecoin issued by Paxos. "
            "Deposits made in USDC are converted to USDG first.\n\n"
            "The vault takes the [other side of every trade](https://gmxio.substack.com/p/deposit-dollars-earn-dollars-the): it gains when traders lose and pays out when they win. "
            "In return, its pools keep 63% of the fees traders pay for opening and closing trades, for borrowing and on [liquidations](https://tradingstrategy.ai/glossary/liquidation), forced closures of losing bets. "
            "The fees are added to the share price instead of being paid out, and the rest goes to the exchange.\n\n"
            "#### How the money is allocated\n\n"
            "No manager picks trades. "
            "The vault, which GMX markets as the Dollar Vault, [opened in October 2026](https://x.com/GMX_IO/status/2107456878548570618) backing Bitcoin, ether and Solana markets, and one deposit was [spread across seven markets by November 2026](https://x.com/GMX_IO/status/2118496905760272473): Bitcoin, ether, Solana, NEAR, XRP, Zcash and HYPE. "
            "Software [shifts money between the pools automatically](https://docs.gmx.io/docs/providing-liquidity/), following how heavily each market is used, within limits per market that GMX's risk team sets.\n\n"
            "#### Risks\n\n"
            "The pools hold dollars instead of crypto assets, but the share price is still **exposed to crypto price moves through traders' winnings**. "
            "When bets lean heavily one way and the market moves in their favour, the payouts lower the share price.\n\n"
            "The exchange limits the total size of bets in each market, charges the more crowded side a [funding fee](https://tradingstrategy.ai/glossary/funding-rate) to pull bets back into balance, and can automatically shrink highly profitable positions. "
            "The vault also depends entirely on USDG keeping its dollar value and on Paxos honouring redemptions.\n\n"
            "#### Launch boost\n\n"
            "A **temporary** [launch boost](https://docs.gmx.io/docs/usdg-launch-boost/), funded by Arbitrum's USDG programme together with GMX, is added to the share price on top of trading fees. "
            "It targets 8% a year or more until the end of November 2026, and rates for later periods had not been published in November 2026. "
            "Returns during the boost are higher than trading fees alone would produce.\n\n"
            "#### Borrowing against vault shares\n\n"
            "Since November 2026, vault shares [can be pledged](https://x.com/GMX_IO/status/2120494428213420489) as [collateral](https://tradingstrategy.ai/glossary/collateral), assets securing a loan, to borrow USDG on the lending platform [Morpho](https://tradingstrategy.ai/glossary/morpho), in a market managed by Gauntlet. "
            "Borrowing to buy more shares multiplies both the returns and the losses described above."
        ),
        link="https://tradingstrategy.ai/vaults/glv-usdg-usdg",
    ),
}


#: K3 wrappers reviewed against the official deployment registry.
#: https://github.com/K3-Capital/k3-vault-contracts/tree/main/deployments
K3_VAULT_METADATA: Final[dict[tuple[int, str], HandwrittenVaultMetadata]] = {
    (5042, "0x56fb80282bb565de67c196924dda0138e30323be"): HandwrittenVaultMetadata(
        name="K3 cirBTC Vault",
        short_description="Borrows USDC against cirBTC to fund lending and other permitted investments, aiming to earn more than the borrowing cost and pay the difference in cirBTC.",
        description=(
            "Deposited [cirBTC](https://www.circle.com/cirbtc), Circle's token backed one-for-one by bitcoin, "
            "secures USDC loans used to fund income-producing investments. "
            "It targets [returns paid in cirBTC](https://x.com/k3_capital/status/2119852350034895084) through a "
            "[delta-neutral](https://tradingstrategy.ai/glossary/delta-neutral) approach, aiming to earn income without an additional bet on market direction. "
            "**Depositors retain bitcoin price exposure**: both the deposit and any returns can lose dollar value.\n\n"
            "#### How income is earned\n\n"
            "The [borrowing strategy](https://x.com/k3_capital/status/2119852361879556335) uses cirBTC as "
            "[collateral](https://tradingstrategy.ai/glossary/collateral), assets pledged to secure a loan, on Morpho or Aave. "
            "Borrowed USDC funds investments whose income is intended to exceed the loan interest; the difference is converted into cirBTC. "
            "The target health factor is **1.3**, a measure of the buffer between the pledged assets and the point at which lenders can sell them to repay debt.\n\n"
            "The [investment mandate](https://x.com/k3_capital/status/2119852385367740877) permits borrowing only through separate markets, each with its own collateral and loan rules, "
            "lending to established lending markets, and holding selected "
            "[principal tokens](https://tradingstrategy.ai/glossary/principal-token), which represent an investment's redemption value at a future date. "
            "The launch allocation included USDC lending on Aave and Morpho, where borrowers pledge assets to secure their loans. "
            "Principal tokens may be harder to sell before their redemption date.\n\n"
            "#### Borrowing risk and withdrawals\n\n"
            "Borrowing costs above investment income, or losses on the funded investments, can reduce the cirBTC returned to depositors. "
            "A fall in the value of the pledged cirBTC can trigger "
            "[liquidation](https://tradingstrategy.ai/glossary/liquidation), a forced sale to repay lenders.\n\n"
            "Deposits and withdrawals require **settlement by the manager** before users can claim shares or cirBTC. "
            "The [settlement terms](https://github.com/K3-Capital/k3-vault-contracts/blob/main/ARCHITECTURE.md) "
            "rely on the manager's investment valuations and do not allow pending requests to be cancelled. "
            "There is no fixed withdrawal time, and management and performance fee rates are not publicly specified for this vault."
        ),
        link="https://app.k3.capital/",
    ),
    (1, "0x009c02a73706a68e0ae0209235408206e4f53709"): HandwrittenVaultMetadata(
        name="K3 cbBTC Vault",
        short_description="Borrows USDC against cbBTC to fund permitted investments, aiming to earn more than the borrowing cost and convert the difference into cbBTC.",
        description=(
            "Deposited [cbBTC](https://www.coinbase.com/cbbtc), a token backed one-for-one by bitcoin held by Coinbase, "
            "secures USDC loans used to fund income-producing investments. "
            "The [investment strategy](https://docs.k3.capital/vault-infra/introduction/cbbtc-vault.html) "
            "aims to earn more than the loan interest and convert the difference into cbBTC, increasing the cbBTC value of each share. "
            "**Depositors retain bitcoin price exposure**, so a gain in cbBTC terms can still be a loss in dollars.\n\n"
            "#### How income is earned\n\n"
            "The manager pledges cbBTC as [collateral](https://tradingstrategy.ai/glossary/collateral), assets securing a loan, "
            "and selects a permitted lending market based on borrowing costs while targeting a buffer against a forced sale. "
            "Borrowed USDC can fund fixed-rate investments, stablecoin products and lending through approved venues, "
            "including Pendle, Morpho and Euler; these are permitted investments rather than a list of current holdings.\n\n"
            "The strategy aims to earn income by accepting investment risks and collecting incentives paid for supplying capital to eligible markets. "
            "Borrowing costs above investment income, losses on those investments or a stablecoin losing its dollar value can reduce the cbBTC returned. "
            "A fall in the pledged cbBTC's value can trigger [liquidation](https://tradingstrategy.ai/glossary/liquidation), a forced sale to repay lenders.\n\n"
            "The manager sets investment valuations and monitors compliance with the investment mandate through operating policies. "
            "The vault contract does not independently verify those valuations or enforce the permitted investment list.\n\n"
            "#### Fees and withdrawals\n\n"
            "Published default fees are **0% management and 5% performance**, with vault-specific terms taking precedence. "
            "There is [no separate protocol-level fee](https://docs.k3.capital/vault-infra/introduction/faq.html), "
            "but borrowing and other investment costs still reduce returns.\n\n"
            "Deposits and withdrawals settle in batches using the manager's investment valuations, with a target schedule of **Mondays and Fridays**. "
            "This is not a guaranteed withdrawal time: settlement cannot proceed unless all withdrawals due in that batch can be fully funded. "
            "Requests cannot be cancelled once submitted, and users must wait for settlement before claiming shares or cbBTC."
        ),
        link="https://app.k3.capital/",
    ),
    (1, "0x333333c5def8368c3fcc9c378cedc0f709fa5ecb"): HandwrittenVaultMetadata(
        name="USDe Enhanced Yield",
        short_description="Pools USDe in a managed investment account whose allocations and sources of income are not publicly specified.",
        description=("Deposited [USDe](https://docs.ethena.fi/overview/ethena-overview), Ethena's token designed to track the US dollar, is pooled in a managed investment account. Depositors receive [USDe+ shares](https://github.com/K3-Capital/k3-vault-contracts/blob/main/deployments/1/0x333333c5DEF8368C3fCc9c378cedc0f709Fa5ECB/deployment.json) representing their claim on that account. **How the vault earns income is not publicly specified**, including whether the vault lends, borrows or invests in other products.\n\n[Deposits and withdrawals settle in batches](https://github.com/K3-Capital/k3-vault-contracts/blob/main/ARCHITECTURE.md), using investment values reported by the manager. Pending requests cannot be cancelled, and users must wait for settlement before claiming shares or USDe. There is no fixed withdrawal time, and management and performance fee rates are not publicly specified for this vault."),
        link="https://app.k3.capital/",
    ),
    (8453, "0xf508f0096b966f657a6bee55b341e9309d5871fb"): HandwrittenVaultMetadata(
        name="K3 USDC",
        short_description="Pools USDC in an underlying investment vault whose allocations and sources of income are not publicly specified.",
        description=("Deposited USDC buys a claim on a separate investment vault, represented by smvUSDC shares. The [vault terms](https://basescan.org/address/0x8c4f97ce5b2eea713728729aec08e48bce588216#code) make the value of these shares depend on the underlying vault's investments. **The underlying strategy is not publicly specified**, including the assets it holds, whether it borrows and how it earns income.\n\nDeposits and withdrawals require a request followed by a claim after the underlying vault settles. Pending requests may be reduced subject to that vault's rules, and the owner can block individual accounts. Share values reflect the last settlement; withdrawal times and management and performance fee rates are not publicly specified."),
        link="https://app.k3.capital/",
    ),
    (143, "0x37aef9d5b058382024ffb5c08e2f4c9ca4978751"): HandwrittenVaultMetadata(
        name="K3 AUSD",
        short_description="Pools AUSD in an underlying investment vault whose allocations and sources of income are not publicly specified.",
        description=("Deposited [AUSD](https://www.agora.finance/), Agora's dollar stablecoin, buys a claim on a separate investment vault, represented by k3AUSD shares. The [vault terms](https://monadscan.com/address/0x1cca0fc4ce416893fe5e74039c4c81a4d10e6806#code) make the value of these shares depend on the underlying vault's investments. **The underlying strategy is not publicly specified**, including the assets it holds, whether it borrows and how it earns income.\n\nDeposits and withdrawals require a request followed by a claim after the underlying vault settles. Pending requests may be reduced subject to that vault's rules, and the owner can block individual accounts. Share values reflect the last settlement; withdrawal times and management and performance fee rates are not publicly specified."),
        link="https://app.k3.capital/",
    ),
}


#: RockawayX Liminal USDC on HyperEVM (999), reviewed on 2026-11-10.
#: The live Morpho page identifies USDC lending against limUSD at 86% LLTV.
#: Borrower leverage and collateral trading do not make this vault a trading
#: or looping strategy; the Morpho adapter supplies its lending tag.
#: Sources:
#: - https://app.morpho.org/hyperevm/vault/0x83107E84F9C5EAAf31F6D2e2B06e0aa14121370e/rockawayx-liminal-usdc
#: - https://x.com/RockawayX/status/2119837798052098499
#: - https://docs.liminal.money/tokenized/limusd
#: - https://docs.liminal.money/tokenized/xtokens
#: - https://docs.liminal.money/tokenized/flows
#: - https://docs.liminal.money/tokenized/risks
ROCKAWAYX_LIMINAL_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (999, HexAddress("0x83107e84f9c5eaaf31f6d2e2b06e0aa14121370e")): HandwrittenVaultMetadata(
        name="RockawayX Liminal USDC",
        short_description="Lends USDC to borrowers who pledge Liminal's yield-bearing limUSD token as security for their loans.",
        description=(
            "Deposited USDC funds loans to holders of limUSD, a token representing Liminal's pooled trading and lending investments. "
            "Borrowers pledge limUSD as [collateral](https://tradingstrategy.ai/glossary/collateral), assets securing their loans, "
            "so they can borrow USDC without selling their tokens. "
            "Depositors earn the interest paid by these borrowers through the "
            "[lending market](https://app.morpho.org/hyperevm/vault/0x83107E84F9C5EAAf31F6D2e2B06e0aa14121370e/rockawayx-liminal-usdc).\n\n"
            "#### Collateral and risks\n\n"
            "The [limUSD portfolio](https://docs.liminal.money/tokenized/limusd) earns income from lending and trading on Hyperliquid. "
            "Its [trading strategies](https://docs.liminal.money/tokenized/xtokens) buy assets such as bitcoin or Hyperliquid's HYPE token and open matching short "
            "[perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts that follow the asset's price without an expiry date.\n\n"
            "The short positions aim to offset losses when asset prices fall. When funding rates are positive, they also collect "
            "[funding payments](https://tradingstrategy.ai/glossary/funding-rate) from traders holding long positions, which gain when prices rise.\n\n"
            "Lenders depend on the pledged limUSD retaining enough value to cover the loans, but its value can fall if the investments lose money. "
            "The [collateral risks](https://docs.liminal.money/tokenized/risks) include trading losses, failures by firms holding the assets and disruption to Hyperliquid, which can reduce that value or delay recovery. "
            "Selling the pledged tokens to repay a loan may not recover the full amount owed.\n\n"
            "Exchanging the collateral token itself for USDC follows Liminal's "
            "[redemption process](https://docs.liminal.money/tokenized/flows). "
            "Queued limUSD redemptions normally take up to three days, with longer waits possible for large exits. "
            "Instant limUSD redemption requires available reserves and costs 0.3%; these terms apply to the pledged token rather than to USDC deposits in this vault.\n\n"
            "#### Vault fees and withdrawals\n\n"
            "The vault charges a [performance fee](https://tradingstrategy.ai/glossary/performance-fee) of **10% of interest earned**, "
            "with no [management fee](https://tradingstrategy.ai/glossary/management-fee), a recurring charge for managing deposits. "
            "USDC available for withdrawal may be limited while funds are lent out to borrowers."
        ),
        link="https://app.morpho.org/hyperevm/vault/0x83107E84F9C5EAAf31F6D2e2B06e0aa14121370e/rockawayx-liminal-usdc",
    ),
}


#: Reviewed vaults of the 13 Lagoon curator profiles maintained in November 2026.
#: Exact deployment evidence and missing strategy disclosures are recorded in
#: docs/protocol-research/lagoon-curator-onboarding-2026-11-10.md.
LAGOON_CURATOR_VAULT_METADATA: Final[dict[tuple[int, HexAddress], HandwrittenVaultMetadata]] = {
    (1, HexAddress("0x2746f31096f23670caf4043f8b30d8d02405a257")): HandwrittenVaultMetadata(
        name="stETH Redemption Carry",
        short_description="The vault buys discounted staked ether tokens and redeems them for ether to earn the price difference.",
        description="#### Vault's trading strategy\n\nThe vault [buys stETH below its redemption value](https://app.lagoon.finance/api/vault?chainId=1&address=0x2746f31096f23670caf4043f8b30d8d02405a257) on [Curve](https://curve.finance/) and exchanges it for ether through [Lido's withdrawal queue](https://docs.lido.fi/token-guides/steth/). stETH represents ether deposited in Lido's staking system; the strategy earns the difference between its purchase price and the ether received, after costs.\n\nMoney awaiting a trade is placed in sigmaETH, an interest-earning lending portfolio on [Morpho](https://morpho.org/). A narrowing discount or a long redemption queue can reduce the return and delay access to money.",
        link="",
    ),
    (1, HexAddress("0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8")): HandwrittenVaultMetadata(
        name="VOLTA90 MULTI ASSET STRATEGY",
        short_description="The vault trades contracts tracking traditional and crypto markets using human decisions supported by automated screening and risk controls.",
        description=(
            "#### Vault's trading strategy\n\nThe vault takes [positions tied to price movements](https://volta90.trade/) in equities, "
            "commodities, indices and crypto markets through [Hyperliquid](https://hyperliquid.xyz/). These contracts can gain or los"
            "e value as the referenced markets move, rather than paying a predictable rate of interest.\n\nAutomated screeners and aler"
            "ts identify opportunities continuously, while VOLTA90's human operators choose and approve positions. Execution and risk"
            " controls are automated; exposures change with market conditions and operator judgement within concentration limits.\n\nTh"
            "e strategy uses [leverage](https://tradingstrategy.ai/glossary/leverage), taking positions larger than the capital set a"
            "side to support them. Total market exposure is capped at twice the portfolio value, with margin limits of three times th"
            "e capital shared across positions or twice the capital reserved for an individual position. Adverse moves can cause [liq"
            "uidation](https://tradingstrategy.ai/glossary/liquidation), the forced closure of positions when their supporting funds "
            "become insufficient.\n\n**Access is invite-only.** Applicants must request approval before investing.\n\n#### Share migratio"
            "n and charging basis\n\nThe [September 2026 migration](https://volta90.trade/blog/volta90-ethereum-migration) created a ne"
            "w share token and a new performance-fee reference price; holders do not inherit the old shares' previous peak value. The"
            " reference resets quarterly, so performance charges use this product's reset schedule rather than a single permanent hig"
            "h-water mark. A 2% redemption charge is refunded under the product terms when the holding period exceeds three months."
        ),
        link="https://tradingstrategy.ai/vaults/volta90-multi-asset-strategy-2",
    ),
    (1, HexAddress("0x4cb280e63251b9ab24a54def74bf5995d82ff398")): HandwrittenVaultMetadata(
        name="Rho X Liquidity Provider",
        short_description="The vault supplies trading capital to Rho markets to earn fees and differences between buying and selling prices.",
        description="#### Vault's trading strategy\n\nThe vault [supplies trading capital across Rho products](https://app.lagoon.finance/api/vault?chainId=1&address=0x4cb280e63251b9ab24a54def74bf5995d82ff398) through [Rho X](https://rho.trading/). It aims to earn trading fees and the difference between prices at which assets are bought and sold.\n\nAllocations are spread across markets within risk limits. The individual Rho products and numerical risk limits are not disclosed. Positions can lose money if prices move against the inventory held for traders.",
        link="https://tradingstrategy.ai/vaults/rho-x-liquidity-provider",
    ),
    (1, HexAddress("0x59b7942f7d2afd085691ce65c152e0d38d4eff22")): HandwrittenVaultMetadata(
        name="Gami lvlUSD",
        short_description="The vault invests lvlUSD across lending and trading pools and seeks additional token incentives.",
        description="#### Vault's trading strategy\n\nThe vault [invests lvlUSD, a US dollar-pegged stablecoin](https://app.lagoon.finance/api/vault?chainId=1&address=0x59b7942f7d2afd085691ce65c152e0d38d4eff22), in lending markets, pools that fund token exchanges and products offering fixed income. Returns can come from borrower interest, trading fees and incentive tokens; reward points have no assured cash value.\n\nGami Capital and Stake Capital jointly curate the strategy. The investment platforms and any borrowing arrangements are not disclosed.",
        link="https://tradingstrategy.ai/vaults/gami-capital-lvlusd",
    ),
    (1, HexAddress("0x7d2c2f54792ad72cb834d298f542145b06b703cb")): HandwrittenVaultMetadata(
        name="Murmurr USDC One",
        short_description="The vault allocates USDC among Morpho lending portfolios across four blockchains to earn borrower interest.",
        description=(
            "#### Vault's trading strategy\n\nThe vault [allocates money among lending portfolios](https://app.lagoon.finance/api/vault"
            "?chainId=1&address=0x7d2c2f54792ad72cb834d298f542145b06b703cb) on [Morpho](https://morpho.org/) across Ethereum, Base, H"
            "yperEVM and Monad. Borrowers pay interest and pledge assets that can be sold to repay loans; the strategy is restricted "
            "to USDC lending.\n\nMurmurr's allocator ranks markets using expected yield and the quality of pledged assets, subject to l"
            "imits on individual assets, portfolios and blockchains. Automated checks reject unsuitable markets and trigger exits whe"
            "n liquidity or borrowing levels breach limits.\n\nTransfers between blockchains use [Circle CCTP](https://developers.circl"
            "e.com/cctp), which burns tokens on one chain and issues them on another. Withdrawals depend on available lending liquidi"
            "ty; selling pledged assets may not recover everything owed. Older records use the DeTrade name, but a management-handove"
            "r date is not disclosed."
        ),
        link="https://tradingstrategy.ai/vaults/detrade-morpho-x-chain-usdc",
    ),
    (1, HexAddress("0x9a458370556a68bdc130a80c35b294d0ad947a6a")): HandwrittenVaultMetadata(
        name="Hub Capital crvUSD Vault",
        short_description="The vault targets income paid in readily tradeable tokens and excludes points and other illiquid rewards.",
        description="#### Vault's trading strategy\n\nThe vault [targets income paid in readily tradeable tokens](https://app.lagoon.finance/api/vault?chainId=1&address=0x9a458370556a68bdc130a80c35b294d0ad947a6a), excluding points and other rewards that cannot readily be sold. The investment platforms and the activities generating that income are not disclosed.",
        link="https://tradingstrategy.ai/vaults/hub-capital-crvusd-vault",
    ),
    (1, HexAddress("0xca790385506b790554571cbc9da73f0130cdcfd5")): HandwrittenVaultMetadata(
        name="Hub Capital USDC vault",
        short_description="The vault targets income paid in readily tradeable tokens and excludes points and other illiquid rewards.",
        description="#### Vault's trading strategy\n\nThe vault [targets income paid in readily tradeable tokens](https://app.lagoon.finance/api/vault?chainId=1&address=0xca790385506b790554571cbc9da73f0130cdcfd5), excluding points and other rewards that cannot readily be sold. The investment platforms and the activities generating that income are not disclosed.",
        link="https://tradingstrategy.ai/vaults/hub-capital-usdc-vault",
    ),
    (1, HexAddress("0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89")): HandwrittenVaultMetadata(
        name="Cross-chain USDC Lending",
        short_description="The vault lends USDC on Morpho and moves capital between blockchains when expected interest gains cover transfer costs.",
        description="#### Vault's trading strategy\n\nThe vault [lends directly into selected markets](https://app.lagoon.finance/api/vault?chainId=1&address=0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89) on [Morpho](https://morpho.org/) across Ethereum, Optimism, Base, Arbitrum, Polygon, Monad and HyperEVM. Borrower interest generates income, while pledged assets provide a source of repayment if borrowers default.\n\nAn automated allocator compares the expected extra interest with the cost of moving funds through [Circle CCTP](https://developers.circle.com/cctp), a system for transferring native stablecoins between chains. Transfers are triggered only when the expected gain over the planned holding period covers the cost; that period and any human approval requirements are undisclosed. Loan defaults, insufficient proceeds from selling pledged assets and transfer delays can reduce returns or delay withdrawals.",
        link="https://tradingstrategy.ai/vaults/cross-chain-usdc-lending",
    ),
    (143, HexAddress("0xe95074a86fead8647edff36af2f3f1dd7e8f7adb")): HandwrittenVaultMetadata(
        name="DeTrade Core AUSD",
        short_description="The vault rotates among stablecoin reward programmes and reinvests incentive tokens collected every eight hours.",
        description="#### Vault's trading strategy\n\nThe vault [allocates to stablecoin reward programmes](https://app.lagoon.finance/api/vault?chainId=143&address=0xe95074a86fead8647edff36af2f3f1dd7e8f7adb) distributed through [Merkl](https://merkl.xyz/). It can hold several dollar-pegged stablecoins, so deposits also take on the risks of the other tokens used.\n\nRewards are collected every eight hours and reinvested in participating programmes. The underlying lending or trading pools are not identified; Merkl distributes rewards rather than operating those investments. Incentive values can fall and stablecoins can trade below their intended value.",
        link="https://tradingstrategy.ai/vaults/detrade-core-ausd",
    ),
    (8453, HexAddress("0x0a63471da694fc822f5a0a0b65978dda8c8edbe5")): HandwrittenVaultMetadata(
        name="DeTrade Applefarm USDC",
        short_description="The vault supplies USDC and USDT to Curve pools on Etherlink to earn trading fees and token rewards.",
        description="#### Vault's trading strategy\n\nThe vault [supplies money to stablecoin trading pools](https://app.lagoon.finance/api/vault?chainId=8453&address=0x0a63471da694fc822f5a0a0b65978dda8c8edbe5) on [Curve](https://curve.finance/) on Etherlink. Returns come from exchange fees and reward programmes distributed through [Merkl](https://merkl.xyz/).\n\nThe portfolio holds both USDC and USDT. The route for moving money to Etherlink and the exact Curve pools are not disclosed. A stablecoin losing its dollar value or falling rewards can reduce the value returned to depositors.",
        link="https://tradingstrategy.ai/vaults/detrade-applefarm-usdc-2",
    ),
    (8453, HexAddress("0x4efc07dca8697792119484af33549f33ab11bf3c")): HandwrittenVaultMetadata(
        name="MoneyFi FlowForge Vault",
        short_description="The vault allocates stablecoins to investment pools across blockchains, including Aptos, using a risk and return scoring model.",
        description="#### Vault's trading strategy\n\nThe vault [allocates stablecoins to investment pools across blockchains](https://app.lagoon.finance/api/vault?chainId=8453&address=0x4efc07dca8697792119484af33549f33ab11bf3c), including Aptos. MoneyFi scores opportunities using returns, risk, the amount already invested and the availability of funds for withdrawals.\n\nThe exact platforms and the split of income between lending interest, trading fees and rewards are not disclosed. Stablecoin investments can still lose money; a scoring model does not guarantee repayment or preservation of capital.",
        link="https://tradingstrategy.ai/vaults/moneyfi-flowforge-vault",
    ),
    (8453, HexAddress("0x8092ca384d44260ea4feaf7457b629b8dc6f88f0")): HandwrittenVaultMetadata(
        name="DeTrade Core USDC",
        short_description="The vault earns stablecoin lending income and rewards, with optional borrowing to increase investments.",
        description="#### Vault's trading strategy\n\nThe vault's [Core strategy](https://detrade.fund/) lends through [Morpho](https://morpho.org/) and can use [Aave](https://aave.com/) and [Pendle](https://pendle.finance/), where rights to future yield can be traded. Incentive tokens distributed through [Merkl](https://merkl.xyz/) are reinvested.\n\nThe strategy may borrow and reinvest when expected income exceeds borrowing costs. This creates [leverage](https://tradingstrategy.ai/glossary/leverage), increasing both potential gains and losses. DeTrade actively manages allocations.",
        link="https://tradingstrategy.ai/vaults/detrade-core-usdc",
    ),
    (8453, HexAddress("0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523")): HandwrittenVaultMetadata(
        name="DeTrade Core ETH",
        short_description="The vault uses interest-earning ether tokens to secure borrowing for additional stablecoin investments.",
        description="#### Vault's trading strategy\n\nThe vault's [Core strategy](https://detrade.fund/) uses tokens representing staked ether as security for loans on [Morpho](https://morpho.org/) and [Aave](https://aave.com/). These tokens earn rewards for helping secure networks, while borrowed stablecoins fund additional investments, including Core USDC.\n\nIncome combines staking rewards, stablecoin investment returns and reinvested [Merkl](https://merkl.xyz/) incentives, after borrowing costs. Falling ether prices can trigger [liquidation](https://tradingstrategy.ai/glossary/liquidation), a forced sale of pledged assets to repay loans. DeTrade manages the allocations; borrowing limits are not disclosed.",
        link="",
    ),
    (8453, HexAddress("0xb09f761cb13baca8ec087ac476647361b6314f98")): HandwrittenVaultMetadata(
        name="722Capital-USDC",
        short_description="The vault combines offsetting trading positions, price-difference trades and repeated borrowing and reinvestment.",
        description="#### Vault's trading strategy\n\nThe [investment mandate](https://app.lagoon.finance/api/vault?chainId=8453&address=0xb09f761cb13baca8ec087ac476647361b6314f98) combines positions intended to offset market price movements, trades on price differences between related instruments and repeated borrowing and reinvestment. It also seeks incentive tokens and early participation rewards from newer investment platforms. The trading and lending venues are not disclosed.\n\nOffsetting positions can still lose money when the instruments diverge, and borrowing costs can exceed investment income. Reward points and potential token distributions have no assured cash value.",
        link="https://tradingstrategy.ai/vaults/722capital-usdc",
    ),
    (8453, HexAddress("0xb725f2277f3f62ad017a6dc418433b326a9f6334")): HandwrittenVaultMetadata(
        name="MSA - Portefeuille Défensif",
        short_description="The vault concentrates on bitcoin, with ether and selected smaller tokens plus additional income investments.",
        description="#### Vault's trading strategy\n\nThe vault [concentrates on bitcoin](https://app.lagoon.finance/api/vault?chainId=8453&address=0xb725f2277f3f62ad017a6dc418433b326a9f6334), alongside ether and selected smaller tokens. MSA chooses the assets; holdings may remain idle or be invested to earn additional income.\n\nPerformance is monitored continuously, and profits can be moved into stablecoin investments while awaiting new opportunities. The trading and income platforms are not disclosed. Scheduled reallocation is part of the mandate, but whether reviews occur twice monthly or every two months is unclear.\n\nThe portfolio remains exposed to token price declines; a defensive allocation is not a capital guarantee.",
        link="https://tradingstrategy.ai/vaults/msa-portefeuille-defensif",
    ),
    (8453, HexAddress("0xbe7db44f4ce20dac83b578b94fd35087f66e9754")): HandwrittenVaultMetadata(
        name="TruMarket",
        short_description="The vault finances agricultural shipments from Latin American exporters to buyers in Europe, the US, Asia and the UAE.",
        description="#### Vault's trading strategy\n\nDeposits [finance agricultural trade](https://app.lagoon.finance/api/vault?chainId=8453&address=0xbe7db44f4ce20dac83b578b94fd35087f66e9754) linking exporters in Latin America, including Peru and Chile, with buyers in Europe, the US, Asia and the UAE. Money is provided when goods ship, with repayment after delivery; borrower payments generate investor income.\n\n[TruMarket](https://www.trumarket.tech/) selects and structures deals, checks trade documents and monitors shipments and repayments. The individual borrowers and loan pricing are not disclosed. Delayed deliveries or unpaid invoices can delay withdrawals or cause losses.",
        link="https://tradingstrategy.ai/vaults/trumarket-2",
    ),
    (8453, HexAddress("0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550")): HandwrittenVaultMetadata(
        name="DeTrade Core EURC",
        short_description="The vault uses euro stablecoins to secure dollar borrowing and invests the proceeds for income above financing costs.",
        description="#### Vault's trading strategy\n\nThe vault's [Core strategy](https://detrade.fund/) pledges euro stablecoins on lending platforms such as [Aave](https://aave.com/) to borrow dollar stablecoins. It invests in Core USDC and euro-denominated lending portfolios on [Morpho](https://morpho.org/), aiming to earn more than its financing costs.\n\nBorrowing increases the portfolio's size and its sensitivity to losses. The strategy targets euro exposure, but the currency-hedging method is not disclosed; this should not be read as a guarantee against exchange-rate losses.",
        link="https://tradingstrategy.ai/vaults/detrade-core-eurc",
    ),
    (8453, HexAddress("0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4")): HandwrittenVaultMetadata(
        name="GUSDC",
        short_description="The vault combines stablecoin lending, funding token exchanges and positions intended to offset market price movements.",
        description="#### Vault's trading strategy\n\nThe vault [combines lending, funding token exchanges and offsetting trading positions](https://app.lagoon.finance/api/vault?chainId=8453&address=0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4) across several blockchains. Income can come from borrower interest, exchange fees and the trading positions, although the particular instruments, platforms and income split are not disclosed.\n\nOffsetting positions can reduce exposure to broad price movements but do not prevent losses if positions diverge or borrowers fail to repay.",
        link="https://tradingstrategy.ai/vaults/gusdc",
    ),
    (8453, HexAddress("0xf56bfe07b8d6e6d74258cdb6969a633629b06b08")): HandwrittenVaultMetadata(
        name="MSA - Portefeuille Dynamique",
        short_description="The vault holds bitcoin, ether and selected smaller tokens, with additional income investments and active profit-taking.",
        description="#### Vault's trading strategy\n\nThe vault [holds bitcoin, ether and smaller sector-specific tokens](https://app.lagoon.finance/api/vault?chainId=8453&address=0xf56bfe07b8d6e6d74258cdb6969a633629b06b08), including investments linked to financial applications and AI. MSA selects the assets and seeks additional income by putting holdings into investment platforms where possible.\n\nPerformance is monitored continuously, and profits can be moved into stablecoin investments while awaiting new opportunities. The trading and income platforms are not disclosed. Scheduled reallocation is part of the mandate, but whether reviews occur twice monthly or every two months is unclear.\n\nThe portfolio remains exposed to token price declines even when holdings also earn income.",
        link="https://tradingstrategy.ai/vaults/msa-portefeuille-dynamique",
    ),
    (42161, HexAddress("0x9e6de1f581d63129af453fe1ea7253b280b4dca8")): HandwrittenVaultMetadata(
        name="Volta90 Multi-asset trading strategy",
        short_description="The vault traded contracts tracking traditional and crypto markets using human investment decisions.",
        description=(
            "#### Vault's trading strategy\n\nThe portfolio took [positions tied to price movements](https://volta90.trade/) in equitie"
            "s, commodities, indices and crypto markets through [Hyperliquid](https://hyperliquid.xyz/). These contracts gained or lo"
            "st value as the referenced markets moved, rather than paying a predictable rate of interest.\n\nAutomated screeners and al"
            "erts supported VOLTA90's human operators in choosing and approving positions. The continued trading book uses [leverage]"
            "(https://tradingstrategy.ai/glossary/leverage), taking positions larger than the capital supporting them, with the risk "
            "of forced closure if that capital becomes insufficient. Historical leverage limits for this deployment are not disclosed"
            ".\n\n#### Share migration\n\nThe [migration announcement](https://volta90.trade/blog/volta90-ethereum-migration) identifies "
            "a separate Ethereum share token for ongoing subscriptions and redemptions and states that the legacy shares are valueles"
            "s. The trading book continued, but the two share tokens and their price histories are distinct."
        ),
        link="https://tradingstrategy.ai/vaults/volta90-multi-asset-trading-strategy",
    ),
    (1, HexAddress("0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac")): HandwrittenVaultMetadata(
        name="Murmurr USDC Blue-Chip",
        short_description="The vault earns interest on USDC loans backed by established crypto assets.",
        description="#### Vault's trading strategy\n\nThe vault lends on [Morpho](https://morpho.org/) to earn interest from borrowers who pledge assets as security. Its [published mandate](https://murmurr.xyz/) focuses on loans backed by established crypto assets.\n\nMurmurr ranks markets using expected interest income and the quality of pledged assets, subject to concentration and available-liquidity limits. The allocator combines automated checks with curator selection.\n\nWithdrawals depend on money available in the lending markets. If borrowers fail to repay, selling pledged assets may recover less than the amount owed.",
        link="",
    ),
    (1, HexAddress("0x29ca54dd3ae9e43abb10880f5e08da439bf25f99")): HandwrittenVaultMetadata(
        name="Murmurr USDC Frontier",
        short_description="The vault earns interest by lending USDC across selected Morpho markets.",
        description="#### Vault's trading strategy\n\nThe vault lends on [Morpho](https://morpho.org/) to earn interest from borrowers who pledge assets as security. Its [published mandate](https://murmurr.xyz/) covers a diversified selection of lending markets; the complete criteria for selecting pledged assets are not disclosed.\n\nMurmurr ranks markets using expected interest income and the quality of pledged assets, subject to concentration and available-liquidity limits. The allocator combines automated checks with curator selection.\n\nWithdrawals depend on money available in the lending markets. If borrowers fail to repay, selling pledged assets may recover less than the amount owed.",
        link="",
    ),
    (8453, HexAddress("0xcda5dbf58a016158ecd4a226109752cc3a627ec8")): HandwrittenVaultMetadata(
        name="Murmurr USDC cbAssets",
        short_description="The vault earns interest by lending USDC across a diversified selection of Morpho markets.",
        description="#### Vault's trading strategy\n\nThe vault lends on [Morpho](https://morpho.org/) to earn interest from borrowers who pledge assets as security. Its [published mandate](https://murmurr.xyz/) covers a diversified selection of lending markets; the complete criteria for selecting pledged assets are not disclosed.\n\nMurmurr ranks markets using expected interest income and the quality of pledged assets, subject to concentration and available-liquidity limits. The allocator combines automated checks with curator selection.\n\nWithdrawals depend on money available in the lending markets. If borrowers fail to repay, selling pledged assets may recover less than the amount owed.",
        link="",
    ),
    (143, HexAddress("0x9f209b43559b13b783eaa0a6686921c7b174f269")): HandwrittenVaultMetadata(
        name="Murmurr USDC aHYPER",
        short_description="The vault earns interest on USDC loans backed by a single token pledged by borrowers, aHYPER.",
        description="#### Vault's trading strategy\n\nThe vault lends on [Morpho](https://morpho.org/) to earn interest from borrowers who pledge assets as security. Its [published mandate](https://murmurr.xyz/) limits the portfolio to one pledged token, aHYPER, whose underlying investments are not explained in this mandate; losses affecting that token therefore concentrate the risk of non-repayment.\n\nMurmurr applies limits on the share of available market liquidity the portfolio can hold. Withdrawals depend on money available in the lending markets. If borrowers fail to repay, selling pledged assets may recover less than the amount owed.",
        link="",
    ),
    (999, HexAddress("0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10")): HandwrittenVaultMetadata(
        name="Murmurr USDC WHYPE",
        short_description="The vault earns interest on USDC loans backed by a single token pledged by borrowers, WHYPE.",
        description="#### Vault's trading strategy\n\nThe vault lends on [Morpho](https://morpho.org/) to earn interest from borrowers who pledge assets as security. Its [published mandate](https://murmurr.xyz/) limits the portfolio to one pledged token, WHYPE, a transferable version of HYPE; losses affecting that token therefore concentrate the risk of non-repayment.\n\nMurmurr applies limits on the share of available market liquidity the portfolio can hold. Withdrawals depend on money available in the lending markets. If borrowers fail to repay, selling pledged assets may recover less than the amount owed.",
        link="",
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
    return LAGOON_CURATOR_VAULT_METADATA.get(key) or K3_VAULT_METADATA.get(key) or GMX_VAULT_METADATA.get(key) or BIRCH_HILL_VAULT_METADATA.get(key) or NEST_VAULT_METADATA.get(key) or PASSIVBOT_VAULT_METADATA.get(key) or ROCKSTEADY_VAULT_METADATA.get(key) or SYSTEMIC_STRATEGIES_VAULT_METADATA.get(key) or ALPHAPING_VAULT_METADATA.get(key) or CENTAUR_VAULT_METADATA.get(key) or MORINI_CAPITAL_VAULT_METADATA.get(key) or TRAVESSIA_TAURI_VAULT_METADATA.get(key) or ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA.get(key) or VERANTA_VAULT_METADATA.get(key) or ROCKAWAYX_LIMINAL_VAULT_METADATA.get(key)


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
