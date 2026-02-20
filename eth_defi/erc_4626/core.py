"""ERC-4626 core functions.

- Access ERC-4626 ABI
- Feature flags vaults can have

"""

import dataclasses
import datetime
import enum
from typing import Type

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract, get_deployed_contract
from eth_defi.vault.base import VaultSpec


class ERC4626Feature(enum.Enum):
    """Additional extensionsERc-4626 vault may have.

    Helps to classify for which protocol the vault belongs and then extract useful
    data out of it, like proprietary fee calls.

    - Flag ERC-4626 matches in the scan with features detected from the smart contract probes
    - Use name/known calls to flag the protocol for which the vault belongs
    """

    #: Failed when probing with multicall, Deposit() event likely for other protocol
    broken = "broken"

    #: Asynchronous vault extension (ERC-7540)
    #: https://eips.ethereum.org/EIPS/eip-7540
    erc_7540_like = "erc_7540_like"

    #: Multi-asset vault extension (ERC-7575)
    #: https://eips.ethereum.org/EIPS/eip-7575
    erc_7575_like = "erc_7575_like"

    #: Lagoon protocol
    #:
    #: https://app.lagoon.finance/
    lagoon_like = "lagoon_like"

    #: Ipor protocol
    #:
    #: https://app.ipor.io/fusion
    ipor_like = "ipor_like"

    #: Moonwell protocol
    moonwell_like = "moonwell_like"

    #: Morpho protocol (V1)
    #:
    #: Uses MORPHO() function for identification
    morpho_like = "morpho_like"

    #: Morpho Vault V2
    #:
    #: Newer version with adapter-based architecture for multi-protocol yield allocation.
    #: Uses adaptersLength() function for identification.
    #: https://docs.morpho.org/learn/concepts/vault-v2/
    #: https://github.com/morpho-org/vault-v2
    morpho_v2_like = "morpho_v2_like"

    #: Harvest Finance like protocol
    harvest_finance = "harvest_finance"

    #: Panoptic
    #: https://panoptic.xyz/
    panoptic_like = "panoptic_like"

    #: Baklavaf
    #: BRT2
    baklava_space_like = "baklava_space_like"

    #: https://astrolab.fi/
    astrolab_like = "astrolab_like"

    #: Gains network and Ostium
    #: gToken vaults
    #: https://github.com/GainsNetwork
    gains_like = "gains_like"

    #: ALmost like Gains, but Ostium LP
    ostium_like = "ostium_like"

    #: Gains tranche?
    #: https://basescan.org/address/0x2ac590a4a78298093e5bc7742685446af96d56e7#code
    gains_tranche_like = "gains_tranche_like"

    #: Return Finacne
    return_finance_like = "return_finance_like"

    #: Arcadia Finance
    #: https://defillama.com/protocol/arcadia-finance
    arcadia_finance_like = "arcadia_finance_like"

    #: SATS DAO
    #: https://github.com/satsDAO/Satoshi
    satoshi_stablecoin = "satoshi_stablecoin"

    #: Athena
    #: https://www.athenafinance.io/
    athena_like = "athena_like"

    #: Reserve
    #: https://reserve.org/
    reserve_like = "reserve_like"

    #: Fluid
    #: https://docs.fluid.instadapp.io/
    fluid_like = "fluid_like"

    #: Kiln metavault
    #: https://github.com/0xZunia/Kiln.MetaVault
    kiln_metavault_like = "kiln_metavault_like"

    #: Peopods
    #: https://beta.peapods.finance/
    peapods_like = "peapods_like"

    #: Yearn compounding vault.
    #: Written in Solidiy.
    #: https://yearn.fi/
    #: https://etherscan.io/address/0x4cE9c93513DfF543Bc392870d57dF8C04e89Ba0a#readProxyContract
    #: Contracts have both proxy and non-proxy functions.
    yearn_compounder_like = "yearn_compounder_like"

    #: Yearn v3
    #: Written in vyper.
    #: https://yearn.fi/
    #: https://etherscan.io/address/0xa10c40f9e318b0ed67ecc3499d702d8db9437228#readProxyContract
    yearn_v3_like = "yearn_v3_like"

    #: Yearn silo strategy
    #: By
    #:
    #: https://github.com/johnnyonline/yearn-v3-silo-lender/blob/34b35bd1649f746020f972844cc27cd6f2916374/src/strategies/silo/SiloStrategy.sol#L35
    yearn_tokenised_strategy = "yearn_tokenised_strategy"

    #: Superform
    #: Metavault - cross-chain yield.
    #: https://www.superform.xyz/vault/BB5FPH0VNwM1AxdvVnhn8/
    #: Non-metavault?
    #: https://www.superform.xyz/vault/b6XXUtR2K4ktxzAuDhZUI/
    #: https://etherscan.io//address/0x862c57d48becB45583AEbA3f489696D22466Ca1b#readProxyContract
    #: https://basescan.org/address/0x84d7549557f0fb69efbd1229d8e2f350b483c09b#code
    superform_like = "superform_like"

    #: Term Finance
    #: https://mytermfinance.com/
    #: https://etherscan.io/address/0xa10c40f9e318b0ed67ecc3499d702d8db9437228#readProxyContract
    term_finance_like = "term_finance_like"

    #: Euler
    #:
    #: In vault names EVK stands for "Euler Vault Kit"
    #: https://github.com/euler-xyz/euler-vault-kit/blob/master/docs/whitepaper.md
    #:
    #: https://app.euler.finance/vault/0xC063C3b3625DF5F362F60f35B0bcd98e0fa650fb?network=base
    #: https://basescan.org/address/0x30a9a9654804f1e5b3291a86e83eded7cf281618#code
    euler_like = "euler_like"

    #: EulerEarn
    #:
    #: EulerEarn is a Metamorpho-based metavault that aggregates deposits into multiple ERC-4626 vaults.
    #: Built on top of Euler Vault Kit (EVK).
    #: https://github.com/euler-xyz/euler-earn
    #: https://docs.euler.finance/developers/euler-earn/
    #:
    #: Example vault: https://snowtrace.io/address/0xE1A62FDcC6666847d5EA752634E45e134B2F824B
    euler_earn_like = "euler_earn_like"

    #: Umami DAO
    #:
    #: gmUSDC vault - ERC-4626 custom in-house, no Github repo
    #: https://arbiscan.io/address/0x5f851f67d24419982ecd7b7765defd64fbb50a97#readContract
    #:
    #:
    #: deposit() custom signature
    umami_like = "umami_like"

    #: Plutus
    #:
    #: https://plutus.fi/Vaults
    #:
    plutus_like = "plutus_like"

    #: D2 Finance
    #:
    #:
    #: https://arbiscan.io/address/0x75288264fdfea8ce68e6d852696ab1ce2f3e5004#code
    #:
    d2_like = "d2_like"

    #: Untangled Finance
    #:
    #:
    #: https://arbiscan.io/address/0x4a3f7dd63077cde8d7eff3c958eb69a3dd7d31a9#readContract
    #:
    untangled_like = "untangled_like"

    #: Goat protocol
    #:
    #:
    #: https://github.com/goatfi/contracts/blob/main/src/infra/multistrategy/Multistrategy.sol
    goat_like = "goat_like"

    #: USD.ai
    #:
    #: https://usd.ai/
    usdai_like = "usdai_like"

    #: AUTO Finance
    #: Prev. Tokemak Autopilot
    #:
    #: https://app.auto.finance/
    #: https://github.com/Tokemak/v2-core-pub?tab=readme-ov-file
    autopool_like = "autopool_like"

    #: NashPoint
    #:
    #: https://nashpoint.gitbook.io/nashpoint
    nashpoint_like = "nashpoint_like"

    #: Llama Lend (powered by LLAMMA)
    #:
    #: Llama Lend is Curve Finance's lending protocol powered by the liquidation protection
    #: mechanism provided by LLAMMA (Lending Liquidating Automated Market Maker Algorithm).
    #: https://lend.curve.fi/
    llamma_like = "llamma_like"

    #: Summer Earn
    #:
    #: "FleerCommander" contract https://github.com/OasisDEX/summer-earn-protocol/blob/8a0eaa6e0ff420f4e574042855514590e0cc610e/packages/core-contracts/src/contracts/FleetCommander.sol
    summer_like = "summer_like"

    #: Silo Finance
    #:
    #: https://arbiscan.io/address/0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9#readContract
    silo_like = "silo_like"

    #: Savings GYD
    #:
    #: https://app.gyro.finance/sgyd/arbitrum/https://app.gyro.finance/sgyd/arbitrum/
    gyroscope = "gyroscope"

    #: TrueFi
    #:
    #: https://truefi.io/
    #: https://arbiscan.io/address/0x8626a4234721a605fc84bb49d55194869ae95d98#readContract
    truefi_like = "truefi_like"

    #: Covered Agent Protocol (CAP)
    #:
    #: https://cap.ag/
    #: Uses Yearn V3 vault infrastructure
    #: https://etherscan.io/address/0x3ed6aa32c930253fc990de58ff882b9186cd0072
    cap_like = "cap_like"

    #: Foxify
    #:
    #: https://www.foxify.trade/
    foxify_like = "foxify_like"

    #: Liquidity Royalty Tranching
    #:
    #: https://github.com/stratosphere-network/LiquidRoyaltyContracts
    liquidity_royalty_like = "liquidity_royalty_like"

    #: cSigma Finance
    #:
    #: https://csigma.finance
    csigma_like = "csigma_like"

    #: Spark
    #:
    #: https://spark.fi/
    spark_like = "spark_like"

    #: Yearn Morpho Compounder strategy
    #:
    #: A Yearn V3 vault that uses MorphoCompounder strategies to invest
    #: in Morpho vaults and compound rewards.
    #: https://yearn.fi/
    #: https://etherscan.io/address/0x6D2981FF9b8d7edbb7604de7A65BAC8694ac849F
    yearn_morpho_compounder_like = "yearn_morpho_compounder_like"

    #: Teller Protocol
    #:
    #: Long-tail lending pools where lenders deposit assets to earn yield
    #: from borrower interest payments. Uses time-based loan expiration
    #: instead of price-based liquidations.
    #: https://www.teller.org/
    #: https://basescan.org/address/0x13cd7cf42ccbaca8cd97e7f09572b6ea0de1097b
    teller_like = "teller_like"

    #: Deltr
    #:
    #: StakeddUSD vault for dUSD staking.
    #: https://etherscan.io/address/0xa7a31e6a81300120b7c4488ec3126bc1ad11f320
    deltr_like = "deltr_like"

    #: Upshift
    #:
    #: Institutional-grade DeFi yield strategies through non-custodial vaults.
    #: https://www.upshift.finance/
    upshift_like = "upshift_like"
    #: Sky (formerly MakerDAO)
    #:
    #: stUSDS vault for USDS staking.
    #: https://sky.money/
    #: https://etherscan.io/address/0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9
    sky_like = "sky_like"

    #: Maple Finance Syrup
    #:
    #: Institutional-grade DeFi lending protocol with Syrup yield products.
    #: https://maple.finance/
    maple_like = "maple_like"

    #: Maple Finance AQRU Pool
    #:
    #: AQRU Receivables Pool for IRS tax credit receivables on Maple Finance.
    #: Real-world receivables pool bridging DeFi with traditional assets.
    #: https://aqru.io/real-world-receivables/
    maple_aqru_like = "maple_aqru_like"

    #: Centrifuge
    #:
    #: Real-world asset (RWA) tokenisation and financing protocol.
    #: Each pool can have multiple tranches, and each tranche is a separate
    #: deployment of an ERC-7540 Vault and a Tranche Token.
    #: https://centrifuge.io/
    centrifuge_like = "centrifuge_like"

    #: Ethena
    #:
    #: Synthetic dollar protocol with sUSDe staking vault.
    #: https://ethena.fi/
    ethena_like = "ethena_like"

    #: Decentralized USD (USDD)
    #:
    #: Decentralized stablecoin protocol with sUSDD savings vault.
    #: https://usdd.io/
    usdd_like = "usdd_like"

    #: Royco Protocol
    #:
    #: Incentivised Action Market (IAM) Protocol with WrappedVault infrastructure.
    #: https://royco.org/
    royco_like = "royco_like"

    #: ZeroLend
    #:
    #: Multi-chain DeFi lending protocol built on Layer 2 solutions, based on Aave V3.
    #: Uses Royco WrappedVault infrastructure for incentivised vaults.
    #: https://zerolend.xyz/
    zerolend_like = "zerolend_like"

    #: ETH Strategy
    #:
    #: DeFi treasury protocol offering leveraged ETH exposure without liquidation risk.
    #: https://www.ethstrat.xyz/
    eth_strategy_like = "eth_strategy_like"

    #: Yuzu Money
    #:
    #: DeFi protocol packaging high-yield strategies into an overcollateralised stablecoin.
    #: Deployed on Plasma chain with yzUSD, syzUSD, and yzPP products.
    #: https://yuzu.money/
    yuzu_money_like = "yuzu_money_like"

    #: Altura
    #:
    #: Multi-strategy yield protocol on HyperEVM (Hyperliquid) with NAV oracle pricing.
    #: https://altura.trade/
    altura_like = "altura_like"

    #: Spectra USDN Wrapper
    #:
    #: Spectra ERC4626 wrapper for WUSDN (SmarDex delta-neutral synthetic dollar).
    #: This is a wrapper contract, not a core Spectra yield tokenisation vault.
    #: https://www.spectra.finance/
    #: https://smardex.io/usdn
    spectra_usdn_wrapper_like = "spectra_usdn_wrapper_like"

    #: Gearbox Protocol
    #:
    #: Composable leverage protocol with ERC-4626 compatible lending pools (PoolV3).
    #: https://gearbox.finance/
    gearbox_like = "gearbox_like"

    #: Mainstreet Finance
    #:
    #: Synthetic USD stablecoin ecosystem with smsUSD staking vault on Sonic.
    #: https://mainstreet.finance/
    mainstreet_like = "mainstreet_like"

    #: YieldFi
    #:
    #: Web3 asset management platform with vyToken vaults.
    #: https://yield.fi/
    yieldfi_like = "yieldfi_like"

    #: Resolv
    #:
    #: Delta-neutral stablecoin protocol with wstUSR wrapped staking vault.
    #: https://resolv.xyz/
    resolv_like = "resolv_like"

    #: Curvance
    #:
    #: Next-generation DeFi lending protocol with capital-efficient money markets.
    #: https://www.curvance.com/
    curvance_like = "curvance_like"

    #: Spectra ERC4626 Wrapper
    #:
    #: Generic Spectra ERC4626 wrapper for rebasing tokens on various chains.
    #: These wrappers make rebasing tokens compatible with Spectra's PT/YT system.
    #: https://www.spectra.finance/
    spectra_erc4626_wrapper_like = "spectra_erc4626_wrapper_like"

    #: Singularity Finance
    #:
    #: AI-powered DeFi yield vaults using DynaVaults framework.
    #: https://singularityfinance.ai/
    singularity_like = "singularity_like"

    #: Brink
    #:
    #: Brink vaults on Mantle and other chains.
    #: Uses modified events (DepositFunds/WithdrawFunds) instead of standard ERC-4626 events.
    #: https://brink.trade/
    brink_like = "brink_like"

    #: Accountable Capital
    #:
    #: Blockchain-based financial verification technology with ERC-7540 async redemption vaults.
    #: Primarily deployed on Monad.
    #: https://www.accountable.capital/
    accountable_like = "accountable_like"

    #: YieldNest
    #:
    #: https://www.yieldnest.finance
    yieldnest_like = "yieldnest_like"

    #: Dolomite
    #:
    #: Next-generation DeFi lending and borrowing platform on Arbitrum.
    #: https://dolomite.io/
    dolomite_like = "dolomite_like"

    #: HypurrFi
    #:
    #: Lending market on HyperEVM (Hyperliquid) for leveraged yield strategies.
    #: https://www.hypurr.fi/
    hypurrfi_like = "hypurrfi_like"

    #: USDX Money
    #:
    #: Synthetic USD stablecoin protocol with sUSDX staking vault.
    #: https://usdx.money/
    usdx_money_like = "usdx_money_like"

    #: Hyperlend
    #:
    #: Wrapped HLP vault for HyperLiquidity Provider on HyperEVM.
    #: https://app.hyperlend.finance/hlp
    hyperlend_like = "hyperlend_like"

    #: Sentiment
    #:
    #: Decentralised leverage lending protocol with SuperPool vault aggregators.
    #: https://www.sentiment.xyz/
    sentiment_like = "sentiment_like"

    #: infiniFi
    #:
    #: On-chain fractional reserve banking protocol with siUSD liquid staking.
    #: https://infinifi.xyz/
    infinifi_like = "infinifi_like"

    #: Renalta
    #:
    #: Unverified smart contract source code - treat with caution.
    #: https://renalta.com/
    renalta_like = "renalta_like"

    #: Avant Protocol
    #:
    #: Stablecoin protocol on Avalanche with savUSD staking vault.
    #: https://www.avantprotocol.com/
    avant_like = "avant_like"

    #: aarnâ
    #:
    #: Agentic Onchain Treasury (AOT) protocol using AI agents for DeFi management.
    #: https://www.aarna.ai/
    aarna_like = "aarna_like"

    #: Yo Protocol
    #:
    #: DeFi protocol with ERC-4626 vault and asynchronous redemption mechanism.
    #: https://www.yo.xyz/
    yo_like = "yo_like"

    #: Frax
    #:
    #: Decentralised stablecoin and lending protocol with Fraxlend lending pairs.
    #: https://frax.com/
    frax_like = "frax_like"

    #: Hyperdrive (HyperEVM)
    #:
    #: Stablecoin money market and yield hub on Hyperliquid (HyperEVM).
    #: https://hyperdrive.fi/
    hyperdrive_hl_like = "hyperdrive_hl_like"

    #: BaseVol
    #:
    #: Onchain options protocol with AI-managed yield vaults on Base.
    #: https://basevol.com/
    basevol_like = "basevol_like"

    #: sBOLD
    #:
    #: Yield-bearing tokenised representation of deposits into Liquity V2 Stability Pools.
    #: https://www.k3.capital/
    sbold_like = "sbold_like"

    #: Hypercore native vault
    #:
    #: Native Hyperliquid perpetuals trading vault on Hypercore (non-EVM).
    #: Not an ERC-4626 vault but shares the same metrics interface.
    #: https://app.hyperliquid.xyz/vaults
    hypercore_native = "hypercore_native"

    #: GRVT native vault
    #:
    #: Native GRVT (Gravity Markets) perpetuals trading vault.
    #: Not an ERC-4626 vault but shares the same metrics interface.
    #: https://grvt.io/exchange/strategies
    grvt_native = "grvt_native"

    #: Ember Protocol
    #:
    #: Investment platform for launching and distributing onchain financial products.
    #: Uses custom VaultDeposit/RequestRedeemed events instead of standard ERC-4626.
    #: https://ember.so/
    ember_like = "ember_like"


#: Features that identify lending protocol vaults.
#:
#: Lending protocols have borrowers and lenders, with utilisation-based liquidity.
#: These vaults support `fetch_available_liquidity()` and `fetch_utilisation_percent()` APIs.
LENDING_PROTOCOL_FEATURES: frozenset[ERC4626Feature] = frozenset(
    {
        ERC4626Feature.gearbox_like,
        ERC4626Feature.ipor_like,
        ERC4626Feature.euler_like,
        ERC4626Feature.euler_earn_like,
        ERC4626Feature.morpho_like,
        ERC4626Feature.morpho_v2_like,
        ERC4626Feature.fluid_like,
        ERC4626Feature.silo_like,
        ERC4626Feature.llamma_like,
    }
)


def is_lending_protocol(features: set[ERC4626Feature]) -> bool:
    """Check if the vault features indicate a lending protocol.

    Lending protocols have borrowers and lenders with utilisation-based liquidity.
    These vaults support `fetch_available_liquidity()` and `fetch_utilisation_percent()` APIs.

    :param features:
        Set of detected vault features.

    :return:
        True if the vault is a lending protocol vault.
    """
    return bool(features & LENDING_PROTOCOL_FEATURES)


def get_vault_protocol_name(features: set[ERC4626Feature]) -> str:
    """Deduct vault protocol name based on Vault smart contract features.

    At least one feature must match.

    See :py:func:`eth_defi.erc_4626.classification.identify_vault_features`.

    :param features:
        List of detected features for a vault
    """
    if ERC4626Feature.broken in features:
        return "<not ERC-4626>"
    elif ERC4626Feature.morpho_v2_like in features:
        return "Morpho"
    elif ERC4626Feature.morpho_like in features:
        return "Morpho"
    elif ERC4626Feature.fluid_like in features:
        return "Fluid"
    elif ERC4626Feature.harvest_finance in features:
        return "Harvest Finance"
    elif ERC4626Feature.ipor_like in features:
        return "IPOR Fusion"
    elif ERC4626Feature.lagoon_like in features:
        return "Lagoon Finance"
    elif ERC4626Feature.morpho_like in features:
        return "Morpho"
    elif ERC4626Feature.panoptic_like in features:
        return "Panoptic"
    elif ERC4626Feature.astrolab_like in features:
        return "Astrolab"
    elif ERC4626Feature.baklava_space_like in features:
        return "Baklava"
    elif ERC4626Feature.gains_like in features:
        return "Gains Network"
    elif ERC4626Feature.return_finance_like in features:
        return "Return Finance"
    elif ERC4626Feature.arcadia_finance_like in features:
        return "Arcadia Finance"
    elif ERC4626Feature.satoshi_stablecoin in features:
        return "SATS Token"
    elif ERC4626Feature.athena_like in features:
        return "Athena Finance"
    elif ERC4626Feature.reserve_like in features:
        return "Reserve"
    elif ERC4626Feature.kiln_metavault_like in features:
        return "Kiln Metavault"
    elif ERC4626Feature.peapods_like in features:
        return "Peapods"
    elif ERC4626Feature.term_finance_like in features:
        return "Term Finance"
    elif ERC4626Feature.euler_earn_like in features:
        return "Euler"
    elif ERC4626Feature.euler_like in features:
        return "Euler"
    elif ERC4626Feature.superform_like in features:
        return "Superform"
    elif ERC4626Feature.yearn_compounder_like in features:
        return "Yearn"
    elif ERC4626Feature.superform_like in features:
        return "Superform"
    elif ERC4626Feature.yearn_v3_like in features:
        return "Yearn"
    elif ERC4626Feature.yearn_tokenised_strategy in features:
        return "Yearn"
    elif ERC4626Feature.gains_like in features:
        return "gTrade"
    elif ERC4626Feature.ostium_like in features:
        return "Ostium"
    elif ERC4626Feature.umami_like in features:
        return "Umami"
    elif ERC4626Feature.plutus_like in features:
        return "Plutus"
    elif ERC4626Feature.d2_like in features:
        return "D2 Finance"
    elif ERC4626Feature.untangled_like in features:
        return "Untangle Finance"
    elif ERC4626Feature.goat_like in features:
        return "Goat Protocol"
    elif ERC4626Feature.usdai_like in features:
        return "USDai"
    elif ERC4626Feature.autopool_like in features:
        return "AUTO Finance"
    elif ERC4626Feature.nashpoint_like in features:
        return "NashPoint"

    elif ERC4626Feature.llamma_like in features:
        return "Llama Lend"

    elif ERC4626Feature.summer_like in features:
        return "Summer.fi"

    elif ERC4626Feature.silo_like in features:
        return "Silo Finance"

    elif ERC4626Feature.gyroscope in features:
        return "Gyroscope"

    elif ERC4626Feature.truefi_like in features:
        return "TrueFi"

    elif ERC4626Feature.superform_like in features:
        return "Superform"

    elif ERC4626Feature.cap_like in features:
        return "CAP"

    elif ERC4626Feature.foxify_like in features:
        return "Foxify"

    elif ERC4626Feature.liquidity_royalty_like in features:
        return "Liquidity Royalty Tranching"

    elif ERC4626Feature.csigma_like in features:
        return "cSigma Finance"

    elif ERC4626Feature.spark_like in features:
        return "Spark"

    elif ERC4626Feature.yearn_morpho_compounder_like in features:
        return "Yearn"

    elif ERC4626Feature.teller_like in features:
        return "Teller"

    elif ERC4626Feature.deltr_like in features:
        return "Deltr"

    elif ERC4626Feature.upshift_like in features:
        return "Upshift"
    elif ERC4626Feature.sky_like in features:
        return "Sky"

    elif ERC4626Feature.maple_like in features:
        return "Maple"

    elif ERC4626Feature.maple_aqru_like in features:
        return "Maple"

    elif ERC4626Feature.centrifuge_like in features:
        return "Centrifuge"

    elif ERC4626Feature.ethena_like in features:
        return "Ethena"

    elif ERC4626Feature.usdd_like in features:
        return "Decentralized USD"

    elif ERC4626Feature.zerolend_like in features:
        return "ZeroLend"

    elif ERC4626Feature.royco_like in features:
        return "Royco"

    elif ERC4626Feature.eth_strategy_like in features:
        return "ETH Strategy"

    elif ERC4626Feature.yuzu_money_like in features:
        return "Yuzu Money"

    elif ERC4626Feature.altura_like in features:
        return "Altura"

    elif ERC4626Feature.spectra_usdn_wrapper_like in features:
        return "Spectra"

    elif ERC4626Feature.gearbox_like in features:
        return "Gearbox"

    elif ERC4626Feature.mainstreet_like in features:
        return "Mainstreet Finance"

    elif ERC4626Feature.yieldfi_like in features:
        return "YieldFi"

    elif ERC4626Feature.resolv_like in features:
        return "Resolv"

    elif ERC4626Feature.curvance_like in features:
        return "Curvance"

    elif ERC4626Feature.spectra_erc4626_wrapper_like in features:
        return "Spectra"

    elif ERC4626Feature.singularity_like in features:
        return "Singularity Finance"

    elif ERC4626Feature.brink_like in features:
        return "Brink"

    elif ERC4626Feature.accountable_like in features:
        return "Accountable"

    elif ERC4626Feature.yieldnest_like in features:
        return "YieldNest"

    elif ERC4626Feature.dolomite_like in features:
        return "Dolomite"

    elif ERC4626Feature.hypurrfi_like in features:
        return "HypurrFi"

    elif ERC4626Feature.usdx_money_like in features:
        return "USDX Money"

    elif ERC4626Feature.hyperlend_like in features:
        return "Hyperlend"

    elif ERC4626Feature.sentiment_like in features:
        return "Sentiment"

    elif ERC4626Feature.infinifi_like in features:
        return "infiniFi"

    elif ERC4626Feature.renalta_like in features:
        return "Renalta"

    elif ERC4626Feature.avant_like in features:
        return "Avant"

    elif ERC4626Feature.aarna_like in features:
        return "aarnâ"

    elif ERC4626Feature.yo_like in features:
        return "Yo"

    elif ERC4626Feature.frax_like in features:
        return "Frax"

    elif ERC4626Feature.hyperdrive_hl_like in features:
        return "Hyperdrive"

    elif ERC4626Feature.basevol_like in features:
        return "BaseVol"

    elif ERC4626Feature.sbold_like in features:
        return "sBOLD"

    elif ERC4626Feature.hypercore_native in features:
        return "Hyperliquid"

    elif ERC4626Feature.grvt_native in features:
        return "GRVT"

    elif ERC4626Feature.ember_like in features:
        return "Ember"

    # No idea
    if ERC4626Feature.erc_7540_like in features:
        return "<unknown ERC-7540>"
    else:
        return "<protocol not yet identified>"


def get_erc_4626_contract(web3: Web3) -> Type[Contract]:
    """Get IERC4626 interface."""
    return get_contract(
        web3,
        "lagoon/IERC4626.json",
    )


def get_deployed_erc_4626_contract(
    web3: Web3,
    address: HexAddress,
    abi_fname="lagoon/IERC4626.json",
) -> Contract:
    """Get IERC4626 deployed at some address."""
    return get_deployed_contract(
        web3,
        abi_fname,
        address=address,
    )


@dataclasses.dataclass(slots=True, frozen=True)
class ERC4262VaultDetection:
    """A ERC-4626 detection."""

    #: Chain
    chain: int

    #: Vault contract address
    address: HexAddress

    #: When this vault was first seen
    first_seen_at_block: int

    #: When this vault was first seen
    first_seen_at: datetime.datetime

    #: Detected features fo this vault
    features: set[ERC4626Feature]

    #: When this entry was scanned on chain
    updated_at: datetime.datetime

    #: Event counts
    deposit_count: int

    #: Event counts
    redeem_count: int

    def get_spec(self) -> VaultSpec:
        """Chain id/address tuple identifying this vault."""
        return VaultSpec(self.chain, self.address)

    def is_protocol_identifiable(self) -> bool:
        """Did we correctly identify the protocol?"""
        # TODO: Hackish
        protocol_name = get_vault_protocol_name(self.features)
        return "<" not in protocol_name

    def is_erc_7540(self) -> bool:
        """Are we asynchronous vault"""
        return ERC4626Feature.erc_7540_like in self.features

    def is_erc_7575(self) -> bool:
        """Are we asynchronous vault"""
        return ERC4626Feature.erc_7575_like in self.features
