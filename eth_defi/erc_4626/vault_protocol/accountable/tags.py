"""Maintained chain-scoped strategy classifications for Accountable vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Chain containing the original Saturn and Glasgow Cosmic classifications.
MONAD_CHAIN_ID = 143

#: Chain containing the reviewed Vectis UltraX Arb deployment.
ETHEREUM_CHAIN_ID = 1

#: Reviewed Accountable share vaults deployed on Ethereum.
ETHEREUM_STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Vectis UltraX Arb, Ethereum (1).
    #: Added: 2026-11-09.
    #: Decision material: The current Accountable API maps loan 608280921 to
    #: this share token and identifies Vectis Finance as manager. Vectis's ETH
    #: documentation confirms Accountable infrastructure, automated allocation,
    #: hedged perpetual positions and funding-rate/basis arbitrage across venues.
    #: Sources:
    #: - https://yield.accountable.capital/api/loan/608280921
    #: - https://docs.vectis.finance/vaults/ultrax-arb-vault-v2-eth
    #: - https://tradingstrategy.ai/vaults/vectis-ultrax-arb
    "0x05ed3da1deeaaa57c8e09986e95ac271572dcf89": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
        StrategyTag.algorithmic_trading,
    },
}

#: Reviewed Accountable share vaults deployed on Monad.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Saturn PT-USDat Loop Vault, Monad (143).
    #: Added: 2026-11-02.
    #: Decision material: Current local vault-metadata-db.pickle row inspected
    #: on this date describes recursive USDC borrowing against PT-USDat,
    #: earning the fixed-yield/variable-borrow-cost spread and selling PT on
    #: Pendle's AMM. This supports looping, carry and AMM venue tags, not
    #: liquidity provision or lending interest earned from supplying USDC.
    #: Sources:
    #: - https://yield.accountable.capital/vaults/0x593784188369E5e5f484025A1940cf437620C3Fe
    #: - docs/protocol-research/saturn-pt-usdat-description.md
    "0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3": {
        StrategyTag.lending_looping,
        StrategyTag.carry_trade,
        StrategyTag.amm,
    },
    #: Vault: Glasgow Cosmic by Ryllo, Monad (143).
    #: Added: 2026-10-22.
    #: Decision material: The vault finances the cricket team's working capital
    #: through credit secured by its real-world receivables. Accountable's
    #: cached metadata identifies Ryllo as the manager and describes this
    #: strategy; the current public vault listing corroborates the description.
    #: Sources:
    #: - https://yield.accountable.capital/vaults/0x44DF31Ec2F1dD517268929903061a47edb7fbD1f
    #: - https://tradingstrategy.ai/vaults/glasgow-cosmic-by-ryllo
    #: - https://ryllo.co/
    "0x899e270df335287f687fdac8a9089059b042044f": {
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
        StrategyTag.rwa_lending,
    },
}


#: Reviewed Tauri deployments, keyed by chain to avoid cross-chain attribution.
TRAVESSIA_STRATEGY_TAGS: dict[int, dict[str, set[StrategyTag]]] = {
    1: {
        #: Vault: Travessia Tauri Vault, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: Grain purchases before sale support the inventory-financing tag.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/1/tauri/0x4C18E2bb9942b12b28e780acF2D9EC2DDA126df9
        "0x4c18e2bb9942b12b28e780acf2d9ec2dda126df9": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing},
        #: Vault: Travessia Tauri Vault - USDC, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: Grain purchases before sale support the inventory-financing tag.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/1/tauri/0xf84A37545e313E8177d80039158ed478C7Dc9079
        "0xf84a37545e313e8177d80039158ed478c7dc9079": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing},
        #: Vault: Travessia Tauri Vault - USDT, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: Grain purchases before sale support the inventory-financing tag.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/1/tauri/0xe4EE0fe4B09021FCdF76B165AA1e8eAF7d447c35
        "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing},
    },
    143: {
        #: Vault: Travessia Tauri Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: Grain purchases before sale support the inventory-financing tag.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/143/tauri/0x8D24B48Cb736addb5B7069565F192A052F42Ba61
        "0x8d24b48cb736addb5b7069565f192a052f42ba61": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing},
    },
    43114: {
        #: Vault: Travessia Tauri Vault, chain 43114.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: Grain purchases before sale support the inventory-financing tag.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/43114/tauri/0x9c3da70cD7feDEAeF528d060ea99AcE133E08Fd2
        "0x9c3da70cd7fedeaef528d060ea99ace133e08fd2": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing},
    },
    8453: {
        #: Vault: Travessia Tauri Vault - USDC, chain 8453.
        #: Added: 2026-11-09.
        #: Decision material: The current official address-specific page
        #: describes loans financing grain purchases before resale by Tauri Agricola.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/8453/tauri/0xb37cc5e891d87c5a3ef750e1bdf59c960575593c
        "0xb37cc5e891d87c5a3ef750e1bdf59c960575593c": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing},
    },
    137: {
        #: Vault: Travessia Tauri Vault - BRZ, chain 137.
        #: The current snapshot detects generic ERC-7540/7575 only, so this
        #: Accountable mapping is inactive there and excluded from migration 047.
        #: Added: 2026-11-09.
        #: Decision material: The current official address-specific page
        #: describes loans financing grain purchases before resale by Tauri Agricola.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/137/tauri/0x08dd5f2a0b107b8f93c4f524901994353bedfaf7
        "0x08dd5f2a0b107b8f93c4f524901994353bedfaf7": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing},
    },
}


#: Additional products reviewed against the live Accountable API on 2026-11-09.
REVIEWED_STRATEGY_TAGS: dict[int, dict[str, set[StrategyTag]]] = {
    1: {
        #: Vault: JUSD Credit Vault, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Combines CEX/DEX funding and spot arbitrage with onchain and
        #: offchain private credit; no recursive leverage.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/605251425
        "0x2b5895b716311a1eac8f93fb88c56af9ec8c1a69": {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures, StrategyTag.lending},
        #: Vault: OnRe Core Vault, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Holds ONyc reinsurance exposure, loops on Kamino, trades yield on
        #: Exponent and supplies USDC to Kamino.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/605211240
        "0xb9c317cae7dd05ecb0c0925020e529934c96f84d": {StrategyTag.rwa, StrategyTag.multistrategy, StrategyTag.lending, StrategyTag.lending_looping, StrategyTag.carry_trade},
        #: Vault: Morini FXArbUSDTRY, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Makes markets across Turkish crypto and banking FX rails, hedges
        #: TRY and earns overnight forward carry.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/605201359
        "0x99351baed3d8ab544ccb08af96a105910fda71e7": {StrategyTag.fx, StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.market_making, StrategyTag.carry_trade},
        #: Vault: SPICE Credit, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Allocates USDC into real-world-asset lending positions.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/602260898
        "0xb37cc5e891d87c5a3ef750e1bdf59c960575593c": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.lending},
        #: Vault: Tenbin tGLD, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Supplies to the tGLD-USDC Euler lending market for fixed returns;
        #: no directional gold position is established.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/606161431
        "0xc606d3f0e757f7c55ced29caff0cf53099b4bbea": {StrategyTag.lending},
        #: Vault: Neutral Trade Autopilot, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Combines funding arbitrage, hedged perpetual/options market
        #: making, systematic trading and a capped directional CTA sleeve; no high-win-rate
        #: claim.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/608250934
        "0x909dadbca7955614a455d9e7447ad4adb4902c8e": {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.market_making, StrategyTag.perpetual_futures, StrategyTag.options, StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
        #: Vault: Agra USPC Liquidity Vault, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Quotes two-sided USPC liquidity around NAV and supplies idle USDS
        #: to a Morpho lending vault.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/606172323
        "0x438d84e451decd8d4b12eadd7d186ca0bda947e7": {StrategyTag.market_making, StrategyTag.liquidity_provider, StrategyTag.lending},
        #: Vault: BIMA WBTC/USDC OCT26-27, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610021271
        "0x934a801d516ef34c519c793fe0606570e732fc6d": {StrategyTag.options},
        #: Vault: BIMA WBTC/USDC OCTNOV26, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610051081
        "0x5164363574b0136d60479f0a5a33cf49871f3bb4": {StrategyTag.options},
        #: Vault: BIMA WBTC/USDC NOVDEC26, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610211251
        "0xe9e54004556b9b55c59a36294688ff7081671944": {StrategyTag.options},
        #: Vault: BIMA WBTC/USDC NOV26-27, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610211260
        "0x0434d3b6e6c0a53c04fbb2d3d2a5cc61316cbb75": {StrategyTag.options},
    },
    56: {
        #: Vault: BIMA WBNB/USDC OCTNOV26, chain 56.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610051025
        "0xe1f7c0b46c02374ef9a77e40a6cec31ca5a15313": {StrategyTag.options},
        #: Vault: BIMA WBNB/USDC OCT26-27, chain 56.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610021254
        "0x0e51a7f836f0cf058a0f13160e21eee38ab221c0": {StrategyTag.options},
        #: Vault: BIMA WBNB/USDC NOV26-27, chain 56.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610211317
        "0x40591d6ec4e7013e39b7e10fc02caace79fc6e98": {StrategyTag.options},
        #: Vault: BIMA WBNB/USDC NOVDEC26, chain 56.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610211419
        "0x8c6b6a1cb41b618bc2efe2539efa9830d8b2242b": {StrategyTag.options},
    },
    143: {
        #: Vault: Asia Credit Yield Vault, Monad (143).
        #: Added: 2026-11-09.
        #: Decision material: The current local vault-metadata-db.pickle row
        #: describes Asian debt investments (staked AZND and muBOND) alongside
        #: capital allocated to crypto trading pools. No specific trading
        #: instrument or execution method is established.
        #: Sources:
        #: - eth_defi/vault/handwritten_metadata.py (ASIA_CREDIT_STRATEGY_DESCRIPTION)
        #: - https://www.linkedin.com/posts/accountablecapital_verifiable-yield-arrives-on-monad-foundation-activity-7398798395642982401-6WqP
        "0x4c0d041889281531ff060290d71091401caa786d": {StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
        #: Vault: RWA Backed Lending by Valos, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Institutional private loans are backed by receivables owed to
        #: Valos.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/602061541
        "0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.lending},
        #: Vault: Hyperithm Delta Neutral Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Combines funding arbitrage, leveraged lending arbitrage, lending
        #: supply, Pendle and DEX liquidity provision.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/601091250
        "0x7cd231120a60f500887444a9baf5e1bd753a5e59": {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.amm},
        #: Vault: Hyperithm Delta Neutral cbBTC Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Uses cbBTC collateral across the documented Hyperithm funding,
        #: lending and DEX liquidity strategies.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/603161016
        "0xc6de1dc0b59682be906e5bca14e385e74ee88168": {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.amm},
        #: Vault: K3 x Galaxy Lending, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Fixed-term institutional loans exclusively to Galaxy Digital; no
        #: real-world collateral is specified.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/606111140
        "0x0143c3ef3a76ed825fd5201953f65b52acefd799": {StrategyTag.lending},
        #: Vault: Tori Ecosystem Monad, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Combines stablecoin LP positions with PT-strUSD productive
        #: collateral. Looping is not stated, so no looping tag.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/607231303
        "0x9e98bda72ced55e562bed1dcaf2b123e95eb181e": {StrategyTag.multistrategy, StrategyTag.liquidity_provider, StrategyTag.amm},
        #: Vault: aHYPER Looping Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Recursively borrows USDC against aHYPER to earn the leveraged
        #: underlying yield less borrowing cost, including its funding arbitrage.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/602061589
        #: - https://yield.accountable.capital/api/loan/601091250
        "0x23b148d8f389c5821739381f1ff87bb7e1162566": {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures},
        #: Vault: sUSN Delta Neutral Yield Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Holds sUSN and may recursively borrow USDC against it to buy more
        #: sUSN. Its underlying portfolio is described in loan 512021430
        #: as US Treasuries, private credit and delta-neutral strategies.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/512021430
        "0x58ba69b289de313e66a13b7d1f822fc98b970554": {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.lending, StrategyTag.delta_neutral},
        #: Vault: Salt Markets Alpha, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Holds compartment notes financing SME loans repaid from
        #: contractual receivables.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/607281660
        "0x41661c2b112263c93fdd9346c4a950476265af7a": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.lending},
        #: Vault: Yuzu Money Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Holds syzUSD; Yuzu Alpha documents leveraged loops, lending,
        #: tokenised T-bills/CLOs and stablecoin arbitrage through Curve,
        #: Balancer and Pendle pools on its official Alpha page.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/512191476
        #: - https://www.yuzu.money/alpha
        #: - https://app.yuzu.money/docs
        "0x3a2c4aaae6776dc1c31316de559598f2f952e2cb": {StrategyTag.multistrategy, StrategyTag.lending, StrategyTag.lending_looping, StrategyTag.rwa, StrategyTag.arbitrage, StrategyTag.amm},
        #: Vault: aHyperBTC Looping Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Recursively borrows cbBTC against aHyperBTC to earn leveraged
        #: underlying yield less borrowing cost, including its funding arbitrage.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/609151158
        #: - https://yield.accountable.capital/api/loan/603161016
        "0x721928108fa84ae8a13545bfee3e6958626cee60": {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures},
        #: Vault: Ouroboros 90D Fixed Term Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Allocates across proprietary onchain and offchain delta-neutral
        #: strategies; specific instruments are not disclosed.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/604081476
        "0x03ef37626bc7c7b3f6cea4bcbfeb7764b51e22fb": {StrategyTag.delta_neutral, StrategyTag.multistrategy},
        #: Vault: Aegis Yield Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Holds sYUSD and supplies the sYUSD/YUSD Curve pool; Aegis
        #: documents hedged BTC perpetual funding income.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/512221757
        #: - https://aegis.im/
        "0x0a4afb907672279926c73dc1f77151931c2a55cc": {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures, StrategyTag.liquidity_provider, StrategyTag.amm},
        #: Vault: Neutral Trade Autopilot, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Monad product documents funding arbitrage, perpetual/options
        #: market making, systematic trading and a capped directional CTA sleeve.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/608250958
        "0xaabab7598be3c4fe58c593e73c2f5934b73b573e": {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.market_making, StrategyTag.perpetual_futures, StrategyTag.options, StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    },
    4114: {
        #: Vault: Noon wcBTC Yield Vault, chain 4114.
        #: Added: 2026-11-09.
        #: Decision material: Borrows USDC against wcBTC to buy sUSN and may recursively loop
        #: sUSN. Its underlying portfolio is described in loan 512021430
        #: as US Treasuries, private credit and delta-neutral strategies.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/603161240
        #: - https://yield.accountable.capital/api/loan/512021430
        "0x69bf9d488b0cbb9288b25f83ef815c2dc14d76de": {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.lending, StrategyTag.delta_neutral},
    },
    4663: {
        #: Vault: Meridian Liquidity Provider, chain 4663.
        #: Added: 2026-11-09.
        #: Decision material: Published mandate stages USDe in pre-deposit, then makes cross-
        #: exchange perpetual markets with offsetting exposure. Tags describe the mandate, not
        #: activation.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/607290214
        "0x24b84023c8e4da635be228c380c09bfe5271bf9d": {StrategyTag.market_making, StrategyTag.liquidity_provider, StrategyTag.delta_neutral, StrategyTag.perpetual_futures},
    },
    8453: {
        #: Vault: BIMA cbBTC/USDC OCT26-27, chain 8453.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610021318
        "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35": {StrategyTag.options},
        #: Vault: BIMA CBBTC/USDC OCTNOV26, chain 8453.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610051085
        "0xee381a87bd54796f471f79cea87132f2869dc37c": {StrategyTag.options},
        #: Vault: BIMA cbBTC/USDC NOV26-27, chain 8453.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610211347
        "0x67e9faebcd6429817653ff5391f26325ea0714be": {StrategyTag.options},
        #: Vault: BIMA CBBTC/USDC NOVDEC26, chain 8453.
        #: Added: 2026-11-09.
        #: Decision material: Uses a short-call/long-put collar through a prime broker or OTC
        #: counterparty to subsidise collateralised borrowing.
        #: Sources:
        #: - https://yield.accountable.capital/api/loan/610211355
        "0x37153aac7cac0c9e9c0fa2b8e7abb371cbfe70d8": {StrategyTag.options},
    },
}


def get_strategy_tags(chain_id: int, address: HexAddress) -> set[StrategyTag] | None:
    """Resolve the reviewed Accountable strategy for a chain and share address.

    Keep the original Monad-only classifications scoped to Monad while allowing
    separately researched products on other chains. Classifications describe
    published mandates, including underlying yield exposure and planned phases. Lookups return
    copies so consumers cannot mutate the maintained tables.

    :param chain_id: Chain containing the share token.
    :param address: Share token address, normalised by the lookup helper.
    :return: Maintained tags or ``None`` for an unreviewed deployment.
    """
    reviewed_tags = lookup_strategy_tags(REVIEWED_STRATEGY_TAGS.get(chain_id, {}), address)
    if reviewed_tags is not None:
        return reviewed_tags
    tags = lookup_strategy_tags(TRAVESSIA_STRATEGY_TAGS.get(chain_id, {}), address)
    if tags is not None:
        return tags
    if chain_id == ETHEREUM_CHAIN_ID:
        return lookup_strategy_tags(ETHEREUM_STRATEGY_TAGS, address)
    if chain_id == MONAD_CHAIN_ID:
        return lookup_strategy_tags(STRATEGY_TAGS, address)
    return None
