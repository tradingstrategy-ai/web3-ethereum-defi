"""Maintained strategy classifications for Lighter native pools."""

from eth_defi.vault.strategy_tag import StrategyTag, combine_strategy_tags

#: Lighter native pools trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Synthetic-address-specific classifications maintained in addition to the
#: native perpetual-futures default.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: STRK 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced STRK 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976641572
    "lighter-pool-281474976641572": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: STRK 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced STRK 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976641573
    "lighter-pool-281474976641573": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FIL 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FIL 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976641574
    "lighter-pool-281474976641574": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FIL 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FIL 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976641575
    "lighter-pool-281474976641575": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ICP 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ICP 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976641576
    "lighter-pool-281474976641576": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ICP 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ICP 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976641577
    "lighter-pool-281474976641577": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MET 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MET 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976644848
    "lighter-pool-281474976644848": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XAG 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XAG 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976647176
    "lighter-pool-281474976647176": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XAG 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XAG 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976647177
    "lighter-pool-281474976647177": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XAU 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XAU 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976647178
    "lighter-pool-281474976647178": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XAU 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XAU 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976647201
    "lighter-pool-281474976647201": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MONAD 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MONAD 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976656642
    "lighter-pool-281474976656642": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MONAD 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MONAD 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976656643
    "lighter-pool-281474976656643": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZEC 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZEC 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976664629
    "lighter-pool-281474976664629": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZEC 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZEC 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976664630
    "lighter-pool-281474976664630": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: EDEN 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced EDEN 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976665433
    "lighter-pool-281474976665433": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: EDEN 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced EDEN 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976665434
    "lighter-pool-281474976665434": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: 2Z 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced 2Z 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976665435
    "lighter-pool-281474976665435": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: 2Z 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced 2Z 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976665436
    "lighter-pool-281474976665436": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FF 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FF 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976667673
    "lighter-pool-281474976667673": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FF 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FF 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976667674
    "lighter-pool-281474976667674": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: APEX 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced APEX 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976670931
    "lighter-pool-281474976670931": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: APEX 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced APEX 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976670932
    "lighter-pool-281474976670932": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: STBL 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced STBL 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976680298
    "lighter-pool-281474976680298": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: 0G 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced 0G 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976680299
    "lighter-pool-281474976680299": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: 0G 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced 0G 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976680300
    "lighter-pool-281474976680300": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ASTER 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ASTER 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687562
    "lighter-pool-281474976687562": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ASTER 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ASTER 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687563
    "lighter-pool-281474976687563": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TOSHI 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TOSHI 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687807
    "lighter-pool-281474976687807": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TOSHI 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TOSHI 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687808
    "lighter-pool-281474976687808": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MYX 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MYX 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687809
    "lighter-pool-281474976687809": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MYX 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MYX 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687810
    "lighter-pool-281474976687810": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AVNT 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AVNT 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687811
    "lighter-pool-281474976687811": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AVNT 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AVNT 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687812
    "lighter-pool-281474976687812": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SKY 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SKY 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687813
    "lighter-pool-281474976687813": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SKY 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SKY 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687814
    "lighter-pool-281474976687814": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PYTH 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PYTH 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976691057
    "lighter-pool-281474976691057": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PYTH 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PYTH 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976691058
    "lighter-pool-281474976691058": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XMR 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XMR 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976691059
    "lighter-pool-281474976691059": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XMR 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XMR 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976691060
    "lighter-pool-281474976691060": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LINEA 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LINEA 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976691920
    "lighter-pool-281474976691920": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LINEA 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LINEA 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976691921
    "lighter-pool-281474976691921": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DOLO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DOLO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692286
    "lighter-pool-281474976692286": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DOLO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DOLO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692287
    "lighter-pool-281474976692287": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: NMR 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced NMR 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692288
    "lighter-pool-281474976692288": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: NMR 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced NMR 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692289
    "lighter-pool-281474976692289": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: CRO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced CRO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692347
    "lighter-pool-281474976692347": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: CRO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced CRO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692348
    "lighter-pool-281474976692348": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: WLFI 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced WLFI 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692953
    "lighter-pool-281474976692953": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: WLFI 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced WLFI 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976692955
    "lighter-pool-281474976692955": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XPL 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XPL 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976694731
    "lighter-pool-281474976694731": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XPL 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XPL 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976694732
    "lighter-pool-281474976694732": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: YZY 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced YZY 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695151
    "lighter-pool-281474976695151": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: YZY 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced YZY 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695152
    "lighter-pool-281474976695152": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: VVV 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced VVV 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695498
    "lighter-pool-281474976695498": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: VVV 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced VVV 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695499
    "lighter-pool-281474976695499": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MORPHO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MORPHO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695500
    "lighter-pool-281474976695500": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MORPHO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MORPHO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695501
    "lighter-pool-281474976695501": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TIA 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TIA 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695502
    "lighter-pool-281474976695502": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TIA 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TIA 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695503
    "lighter-pool-281474976695503": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: USELESS 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced USELESS 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695504
    "lighter-pool-281474976695504": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: USELESS 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced USELESS 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976695505
    "lighter-pool-281474976695505": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AERO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AERO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696148
    "lighter-pool-281474976696148": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AERO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AERO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696149
    "lighter-pool-281474976696149": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ETHFI 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ETHFI 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696150
    "lighter-pool-281474976696150": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ETHFI 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ETHFI 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696151
    "lighter-pool-281474976696151": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: GMX 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced GMX 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696152
    "lighter-pool-281474976696152": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: GMX 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced GMX 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696153
    "lighter-pool-281474976696153": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DYDX 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DYDX 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696154
    "lighter-pool-281474976696154": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DYDX 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DYDX 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696155
    "lighter-pool-281474976696155": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MNT 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MNT 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696156
    "lighter-pool-281474976696156": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MNT 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MNT 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696157
    "lighter-pool-281474976696157": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZRO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZRO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696955
    "lighter-pool-281474976696955": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZRO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZRO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696956
    "lighter-pool-281474976696956": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: HBAR 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced HBAR 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696957
    "lighter-pool-281474976696957": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: HBAR 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced HBAR 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696958
    "lighter-pool-281474976696958": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BCH 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BCH 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696959
    "lighter-pool-281474976696959": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BCH 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BCH 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976696960
    "lighter-pool-281474976696960": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PROVE 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PROVE 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976697776
    "lighter-pool-281474976697776": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PROVE 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PROVE 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976697777
    "lighter-pool-281474976697777": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZK 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZK 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698538
    "lighter-pool-281474976698538": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZK 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZK 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698539
    "lighter-pool-281474976698539": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: OP 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced OP 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698540
    "lighter-pool-281474976698540": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: OP 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced OP 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698541
    "lighter-pool-281474976698541": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LAUNCHCOIN 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LAUNCHCOIN 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698542
    "lighter-pool-281474976698542": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LAUNCHCOIN 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LAUNCHCOIN 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698543
    "lighter-pool-281474976698543": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZORA 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZORA 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698544
    "lighter-pool-281474976698544": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ZORA 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ZORA 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698545
    "lighter-pool-281474976698545": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: GRASS 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced GRASS 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698546
    "lighter-pool-281474976698546": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: GRASS 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced GRASS 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698547
    "lighter-pool-281474976698547": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: RESOLV 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced RESOLV 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698548
    "lighter-pool-281474976698548": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: RESOLV 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced RESOLV 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698549
    "lighter-pool-281474976698549": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ARB 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ARB 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698596
    "lighter-pool-281474976698596": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ARB 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ARB 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698597
    "lighter-pool-281474976698597": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: EIGEN 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced EIGEN 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698599
    "lighter-pool-281474976698599": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: EIGEN 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced EIGEN 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698600
    "lighter-pool-281474976698600": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PAXG 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PAXG 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698601
    "lighter-pool-281474976698601": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PAXG 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PAXG 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698602
    "lighter-pool-281474976698602": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PENGU 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PENGU 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698774
    "lighter-pool-281474976698774": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PENGU 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PENGU 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698775
    "lighter-pool-281474976698775": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LDO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LDO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698776
    "lighter-pool-281474976698776": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LDO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LDO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976698777
    "lighter-pool-281474976698777": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PUMP 1x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PUMP 1x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976703225
    "lighter-pool-281474976703225": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PUMP 1x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PUMP 1x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976703226
    "lighter-pool-281474976703226": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SYRUP 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SYRUP 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976706164
    "lighter-pool-281474976706164": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SYRUP 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SYRUP 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976706165
    "lighter-pool-281474976706165": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TRX 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TRX 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976706166
    "lighter-pool-281474976706166": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TRX 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TRX 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976706167
    "lighter-pool-281474976706167": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SPX 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SPX 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976706997
    "lighter-pool-281474976706997": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SPX 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SPX 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976706998
    "lighter-pool-281474976706998": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: VIRTUAL 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced VIRTUAL 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976706999
    "lighter-pool-281474976706999": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: VIRTUAL 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced VIRTUAL 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976707000
    "lighter-pool-281474976707000": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: S 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced S 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708410
    "lighter-pool-281474976708410": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: S 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced S 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708411
    "lighter-pool-281474976708411": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ADA 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ADA 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708412
    "lighter-pool-281474976708412": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ADA 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ADA 2x long product. It maintains fixed leveraged ADA exposure without trade-selection intelligence.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708413
    "lighter-pool-281474976708413": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ONDO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ONDO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708830
    "lighter-pool-281474976708830": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ONDO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ONDO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708831
    "lighter-pool-281474976708831": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PENDLE 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PENDLE 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708832
    "lighter-pool-281474976708832": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PENDLE 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PENDLE 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708833
    "lighter-pool-281474976708833": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: CRV 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced CRV 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708834
    "lighter-pool-281474976708834": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: CRV 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced CRV 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976708835
    "lighter-pool-281474976708835": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LTC 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LTC 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710314
    "lighter-pool-281474976710314": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LTC 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LTC 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710315
    "lighter-pool-281474976710315": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: IP 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced IP 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710316
    "lighter-pool-281474976710316": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: IP 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced IP 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710317
    "lighter-pool-281474976710317": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: KAITO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced KAITO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710318
    "lighter-pool-281474976710318": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: KAITO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced KAITO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710319
    "lighter-pool-281474976710319": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SEI 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SEI 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710414
    "lighter-pool-281474976710414": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SEI 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SEI 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710415
    "lighter-pool-281474976710415": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: APT 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced APT 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710416
    "lighter-pool-281474976710416": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: APT 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced APT 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710417
    "lighter-pool-281474976710417": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: UNI 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced UNI 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710537
    "lighter-pool-281474976710537": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: UNI 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced UNI 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710538
    "lighter-pool-281474976710538": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ENA 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ENA 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710539
    "lighter-pool-281474976710539": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ENA 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ENA 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710540
    "lighter-pool-281474976710540": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MKR 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MKR 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710541
    "lighter-pool-281474976710541": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: MKR 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced MKR 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710542
    "lighter-pool-281474976710542": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AAVE 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AAVE 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710543
    "lighter-pool-281474976710543": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AAVE 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AAVE 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710544
    "lighter-pool-281474976710544": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: JUP 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced JUP 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710559
    "lighter-pool-281474976710559": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: JUP 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced JUP 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710560
    "lighter-pool-281474976710560": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BNB 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BNB 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710561
    "lighter-pool-281474976710561": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BNB 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BNB 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710562
    "lighter-pool-281474976710562": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: HYPE 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced HYPE 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710563
    "lighter-pool-281474976710563": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: HYPE 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced HYPE 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710564
    "lighter-pool-281474976710564": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: POPCAT 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced POPCAT 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710575
    "lighter-pool-281474976710575": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: POPCAT 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced POPCAT 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710576
    "lighter-pool-281474976710576": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AI16Z 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AI16Z 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710577
    "lighter-pool-281474976710577": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AI16Z 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AI16Z 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710578
    "lighter-pool-281474976710578": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FARTCOIN 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FARTCOIN 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710579
    "lighter-pool-281474976710579": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FARTCOIN 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FARTCOIN 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710580
    "lighter-pool-281474976710580": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BERA 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BERA 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710581
    "lighter-pool-281474976710581": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BERA 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BERA 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710582
    "lighter-pool-281474976710582": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FLOKI 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FLOKI 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710599
    "lighter-pool-281474976710599": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: FLOKI 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced FLOKI 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710600
    "lighter-pool-281474976710600": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BONK 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BONK 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710601
    "lighter-pool-281474976710601": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BONK 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BONK 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710602
    "lighter-pool-281474976710602": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SHIB 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SHIB 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710603
    "lighter-pool-281474976710603": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SHIB 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SHIB 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710604
    "lighter-pool-281474976710604": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SUI 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SUI 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710605
    "lighter-pool-281474976710605": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SUI 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SUI 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710606
    "lighter-pool-281474976710606": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TRUMP 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TRUMP 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710608
    "lighter-pool-281474976710608": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TRUMP 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TRUMP 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710609
    "lighter-pool-281474976710609": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: POL 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced POL 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710623
    "lighter-pool-281474976710623": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: POL 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced POL 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710624
    "lighter-pool-281474976710624": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TAO 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TAO 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710625
    "lighter-pool-281474976710625": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TAO 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TAO 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710626
    "lighter-pool-281474976710626": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TON 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TON 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710627
    "lighter-pool-281474976710627": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: TON 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced TON 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710628
    "lighter-pool-281474976710628": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DOT 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DOT 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710629
    "lighter-pool-281474976710629": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DOT 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DOT 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710630
    "lighter-pool-281474976710630": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: NEAR 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced NEAR 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710631
    "lighter-pool-281474976710631": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: NEAR 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced NEAR 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710632
    "lighter-pool-281474976710632": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AVAX 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AVAX 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710633
    "lighter-pool-281474976710633": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: AVAX 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced AVAX 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710634
    "lighter-pool-281474976710634": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LINK 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LINK 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710635
    "lighter-pool-281474976710635": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: LINK 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced LINK 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710636
    "lighter-pool-281474976710636": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XRP 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XRP 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710637
    "lighter-pool-281474976710637": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: XRP 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced XRP 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710638
    "lighter-pool-281474976710638": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: WLD 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced WLD 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710640
    "lighter-pool-281474976710640": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: WLD 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced WLD 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710641
    "lighter-pool-281474976710641": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: WIF 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced WIF 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710642
    "lighter-pool-281474976710642": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: WIF 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced WIF 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710643
    "lighter-pool-281474976710643": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PEPE 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PEPE 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710644
    "lighter-pool-281474976710644": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: PEPE 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced PEPE 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710645
    "lighter-pool-281474976710645": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DOGE 2x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DOGE 2x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710646
    "lighter-pool-281474976710646": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: DOGE 2x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced DOGE 2x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710647
    "lighter-pool-281474976710647": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SOL 3x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SOL 3x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710648
    "lighter-pool-281474976710648": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: SOL 3x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced SOL 3x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710649
    "lighter-pool-281474976710649": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BTC 3x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BTC 3x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710650
    "lighter-pool-281474976710650": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: BTC 3x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced BTC 3x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710651
    "lighter-pool-281474976710651": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ETH 3x short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ETH 3x short product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710652
    "lighter-pool-281474976710652": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: ETH 3x long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter public-pool metadata describes this as an automatically rebalanced ETH 3x long product.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710653
    "lighter-pool-281474976710653": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Lighter Liquidity Provider (LLP).
    #: Added: 2026-08-26.
    #: Decision material: The integration documentation describes LLP as the protocol-operated liquidity and insurance pool that provides market-making liquidity and handles liquidations.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976710654
    "lighter-pool-281474976710654": {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
    },
    #: Vault: Lighter Robinhood Liquidity Provider (LLP).
    #: Added: 2026-08-26.
    #: Decision material: The integration documentation identifies the configured Robinhood LLP as the protocol liquidity and insurance pool.
    #: Sources:
    #: - https://robinhoodchain.lighter.xyz/public-pools/281474976710654
    "lighter-pool-robinhood-281474976710654": {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
    },
    #: Vault: Guinea Pool.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly says discretionary long/short trading across majors and alts.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976694250
    "lighter-pool-281474976694250": {
        StrategyTag.discretionary_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Algorithmic L/S | Big Brain Research.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies an algorithmic long/short momentum strategy.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976690488
    "lighter-pool-281474976690488": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: GCB Capital.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description says it is semi-discretionary, semi-quant-supported and longs strength while shorting weakness.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976690376
    "lighter-pool-281474976690376": {
        StrategyTag.algorithmic_trading,
        StrategyTag.discretionary_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Bellagio.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description says algo trading with manual tweaks.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976690371
    "lighter-pool-281474976690371": {
        StrategyTag.algorithmic_trading,
        StrategyTag.discretionary_trading,
    },
    #: Vault: YOLO Pool.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly describes long/short contrarian trading.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976690279
    "lighter-pool-281474976690279": {
        StrategyTag.directional_trading,
    },
    #: Vault: YOLO Pool.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly describes long/short contrarian trading.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976689753
    "lighter-pool-281474976689753": {
        StrategyTag.directional_trading,
    },
    #: Vault: gLighter Pool by NamasteWeb3.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies discretionary high-conviction long/short and scalp trades.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976689660
    "lighter-pool-281474976689660": {
        StrategyTag.discretionary_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Edge & Hedge (L/S Factors).
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies a quantitative delta-neutral long/short strategy.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976688087
    "lighter-pool-281474976688087": {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
    },
    #: Vault: BULBUL Vault.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description says HYPE and BTC long, others short.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976687595
    "lighter-pool-281474976687595": {
        StrategyTag.directional_trading,
    },
    #: Vault: TrippleS Capital.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly says directional conviction and long/short algo.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976686233
    "lighter-pool-281474976686233": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Trend Trading.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description says the strategy follows market direction to capture upward and downward moves.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976681281
    "lighter-pool-281474976681281": {
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: Experimental Liquidity Provider (XLP).
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description states that XLP provides liquidity, runs market-making strategies and handles liquidations.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976680784
    "lighter-pool-281474976680784": {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
    },
    #: Vault: PAWNZI ALGO - PROFITABLE (REAL).
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly calls this a BTC and majors multi-timeframe reversal algo.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976680234
    "lighter-pool-281474976680234": {
        StrategyTag.algorithmic_trading,
        StrategyTag.mean_reversion,
    },
    #: Vault: Shrimp Liquidity Provider (SLP).
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies a quantitative semi-delta-neutral grid-trading strategy with hedged baskets.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976666704
    "lighter-pool-281474976666704": {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
        StrategyTag.grid_trading,
    },
    #: Vault: BTC vs ALTS and NEWS/DIPS.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description identifies a delta-neutral BTC-versus-altcoin main position plus directional news-based trades.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976656586
    "lighter-pool-281474976656586": {
        StrategyTag.delta_neutral,
        StrategyTag.directional_trading,
    },
    #: Vault: BTC H1 EMA Strategy.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies a BTC H1 EMA strategy.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976656541
    "lighter-pool-281474976656541": {
        StrategyTag.algorithmic_trading,
        StrategyTag.trend_following,
    },
    #: Vault: Cross-Sectional-Relative-Strength.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description says the strategy is algorithmic and rules-based, rotating into the strongest coins by cross-sectional relative strength.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976656474
    "lighter-pool-281474976656474": {
        StrategyTag.algorithmic_trading,
        StrategyTag.trend_following,
    },
    #: Vault: Valkyrie of the Faint Smile (L/S).
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly says discretionary L/S.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976651220
    "lighter-pool-281474976651220": {
        StrategyTag.discretionary_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Quant + Discretionary.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly combines quantitative models with discretionary trading.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976644732
    "lighter-pool-281474976644732": {
        StrategyTag.algorithmic_trading,
        StrategyTag.discretionary_trading,
    },
    #: Vault: DSC - L Strategy.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description says it is long selected tokens with low leverage.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976644517
    "lighter-pool-281474976644517": {
        StrategyTag.directional_trading,
    },
    #: Vault: Long BTC/ETH/HYPE + Gold.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly specifies risk-on crypto longs and risk-off gold exposure.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976644427
    "lighter-pool-281474976644427": {
        StrategyTag.directional_trading,
    },
    #: Vault: Long Value & Short Dogshit.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly calls the strategy discretionary long/short and says it bets on reversion.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976644348
    "lighter-pool-281474976644348": {
        StrategyTag.discretionary_trading,
        StrategyTag.directional_trading,
        StrategyTag.mean_reversion,
    },
    #: Vault: cp0x Delta Majors.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description specifies long majors and short alts with rebalancing.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976644089
    "lighter-pool-281474976644089": {
        StrategyTag.directional_trading,
    },
    #: Vault: Core4-Long Basket-Short.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies an automated market-neutral strategy.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976643102
    "lighter-pool-281474976643102": {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
    },
    #: Vault: Silentist.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly calls this a systematic multi-strategy combining technical trading, fundamental long/short and a smart-trader portfolio.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976639900
    "lighter-pool-281474976639900": {
        StrategyTag.algorithmic_trading,
        StrategyTag.multistrategy,
    },
    #: Vault: Popo BTC Only Low Frequency Algo.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies a low-frequency trading algo.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976623846
    "lighter-pool-281474976623846": {
        StrategyTag.algorithmic_trading,
    },
    #: Vault: Peter Schiff's vault.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly says short Bits and long Atoms.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976622846
    "lighter-pool-281474976622846": {
        StrategyTag.directional_trading,
    },
    #: Vault: CopyCAT.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly calls the strategy semi-systematic.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976587033
    "lighter-pool-281474976587033": {
        StrategyTag.algorithmic_trading,
    },
    #: Vault: Allora Libra.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description says it aggregates AI-network model forecasts into risk-adjusted long/short perpetual positions.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976576705
    "lighter-pool-281474976576705": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Systematic Strategies Long / Short Bot.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter name and description explicitly identify a systematic long/short bot.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976549878
    "lighter-pool-281474976549878": {
        StrategyTag.algorithmic_trading,
    },
    #: Vault: dima systematic long/short bot.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies a delta-neutral long/short bot.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976501540
    "lighter-pool-281474976501540": {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
    },
    #: Vault: 20d Breakout Long Binary (Automated).
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter description explicitly identifies an automated 20-day breakout, long-only strategy.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976496745
    "lighter-pool-281474976496745": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: BTC 35x Long.
    #: Added: 2026-08-26.
    #: Decision material: The current Lighter name and description explicitly identify a 35x long BTC position.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976644581
    "lighter-pool-281474976644581": {
        StrategyTag.directional_trading,
    },
    #: Vault: pmalt.
    #: Added: 2026-08-17.
    #: Decision material: Maintainer classification marks pmalt's public
    #: Lighter pool as algorithmic trading and pair trading.
    #: Sources:
    #: - eth_defi/data/feeds/curators/pmalt.yaml
    #: - https://app.lighter.xyz/public-pools/281474976552918
    "lighter-pool-281474976552918": {
        StrategyTag.algorithmic_trading,
        StrategyTag.pair_trading,
    },
    #: Vault: Steady Wealth Builder.
    #: Added: 2026-08-18.
    #: Decision material: The current Lighter description explicitly calls
    #: this a long-only DCA grid strategy, supporting directional grid trading.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976552443
    #: - https://mainnet.zklighter.elliot.ai/api/v1/account?by=index&value=281474976552443
    "lighter-pool-281474976552443": {
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
    },
}

#: Lighter's documented auto-rebalanced 2x long/short products maintain a
#: fixed leveraged exposure to one asset. Their automatic rebalancing is an
#: implementation mechanism, not trade intelligence, so they use
#: ``directional_leverage`` instead of ``algorithmic_trading``. ADA 2x long
#: (``lighter-pool-281474976708413``) is a representative example.
#:
#: Each identifier has an address-specific source block in
#: :py:data:`STRATEGY_TAGS` above. Keep this set in sync when adding or
#: removing a documented Lighter direct-leverage product.
DIRECTIONAL_LEVERAGE_VAULTS: frozenset[str] = frozenset(
    """
    lighter-pool-281474976641572
    lighter-pool-281474976641573
    lighter-pool-281474976641574
    lighter-pool-281474976641575
    lighter-pool-281474976641576
    lighter-pool-281474976641577
    lighter-pool-281474976644848
    lighter-pool-281474976647176
    lighter-pool-281474976647177
    lighter-pool-281474976647178
    lighter-pool-281474976647201
    lighter-pool-281474976656642
    lighter-pool-281474976656643
    lighter-pool-281474976664629
    lighter-pool-281474976664630
    lighter-pool-281474976665433
    lighter-pool-281474976665434
    lighter-pool-281474976665435
    lighter-pool-281474976665436
    lighter-pool-281474976667673
    lighter-pool-281474976667674
    lighter-pool-281474976670931
    lighter-pool-281474976670932
    lighter-pool-281474976680298
    lighter-pool-281474976680299
    lighter-pool-281474976680300
    lighter-pool-281474976687562
    lighter-pool-281474976687563
    lighter-pool-281474976687807
    lighter-pool-281474976687808
    lighter-pool-281474976687809
    lighter-pool-281474976687810
    lighter-pool-281474976687811
    lighter-pool-281474976687812
    lighter-pool-281474976687813
    lighter-pool-281474976687814
    lighter-pool-281474976691057
    lighter-pool-281474976691058
    lighter-pool-281474976691059
    lighter-pool-281474976691060
    lighter-pool-281474976691920
    lighter-pool-281474976691921
    lighter-pool-281474976692286
    lighter-pool-281474976692287
    lighter-pool-281474976692288
    lighter-pool-281474976692289
    lighter-pool-281474976692347
    lighter-pool-281474976692348
    lighter-pool-281474976692953
    lighter-pool-281474976692955
    lighter-pool-281474976694731
    lighter-pool-281474976694732
    lighter-pool-281474976695151
    lighter-pool-281474976695152
    lighter-pool-281474976695498
    lighter-pool-281474976695499
    lighter-pool-281474976695500
    lighter-pool-281474976695501
    lighter-pool-281474976695502
    lighter-pool-281474976695503
    lighter-pool-281474976695504
    lighter-pool-281474976695505
    lighter-pool-281474976696148
    lighter-pool-281474976696149
    lighter-pool-281474976696150
    lighter-pool-281474976696151
    lighter-pool-281474976696152
    lighter-pool-281474976696153
    lighter-pool-281474976696154
    lighter-pool-281474976696155
    lighter-pool-281474976696156
    lighter-pool-281474976696157
    lighter-pool-281474976696955
    lighter-pool-281474976696956
    lighter-pool-281474976696957
    lighter-pool-281474976696958
    lighter-pool-281474976696959
    lighter-pool-281474976696960
    lighter-pool-281474976697776
    lighter-pool-281474976697777
    lighter-pool-281474976698538
    lighter-pool-281474976698539
    lighter-pool-281474976698540
    lighter-pool-281474976698541
    lighter-pool-281474976698542
    lighter-pool-281474976698543
    lighter-pool-281474976698544
    lighter-pool-281474976698545
    lighter-pool-281474976698546
    lighter-pool-281474976698547
    lighter-pool-281474976698548
    lighter-pool-281474976698549
    lighter-pool-281474976698596
    lighter-pool-281474976698597
    lighter-pool-281474976698599
    lighter-pool-281474976698600
    lighter-pool-281474976698601
    lighter-pool-281474976698602
    lighter-pool-281474976698774
    lighter-pool-281474976698775
    lighter-pool-281474976698776
    lighter-pool-281474976698777
    lighter-pool-281474976703225
    lighter-pool-281474976703226
    lighter-pool-281474976706164
    lighter-pool-281474976706165
    lighter-pool-281474976706166
    lighter-pool-281474976706167
    lighter-pool-281474976706997
    lighter-pool-281474976706998
    lighter-pool-281474976706999
    lighter-pool-281474976707000
    lighter-pool-281474976708410
    lighter-pool-281474976708411
    lighter-pool-281474976708412
    lighter-pool-281474976708413
    lighter-pool-281474976708830
    lighter-pool-281474976708831
    lighter-pool-281474976708832
    lighter-pool-281474976708833
    lighter-pool-281474976708834
    lighter-pool-281474976708835
    lighter-pool-281474976710314
    lighter-pool-281474976710315
    lighter-pool-281474976710316
    lighter-pool-281474976710317
    lighter-pool-281474976710318
    lighter-pool-281474976710319
    lighter-pool-281474976710414
    lighter-pool-281474976710415
    lighter-pool-281474976710416
    lighter-pool-281474976710417
    lighter-pool-281474976710537
    lighter-pool-281474976710538
    lighter-pool-281474976710539
    lighter-pool-281474976710540
    lighter-pool-281474976710541
    lighter-pool-281474976710542
    lighter-pool-281474976710543
    lighter-pool-281474976710544
    lighter-pool-281474976710559
    lighter-pool-281474976710560
    lighter-pool-281474976710561
    lighter-pool-281474976710562
    lighter-pool-281474976710563
    lighter-pool-281474976710564
    lighter-pool-281474976710575
    lighter-pool-281474976710576
    lighter-pool-281474976710577
    lighter-pool-281474976710578
    lighter-pool-281474976710579
    lighter-pool-281474976710580
    lighter-pool-281474976710581
    lighter-pool-281474976710582
    lighter-pool-281474976710599
    lighter-pool-281474976710600
    lighter-pool-281474976710601
    lighter-pool-281474976710602
    lighter-pool-281474976710603
    lighter-pool-281474976710604
    lighter-pool-281474976710605
    lighter-pool-281474976710606
    lighter-pool-281474976710608
    lighter-pool-281474976710609
    lighter-pool-281474976710623
    lighter-pool-281474976710624
    lighter-pool-281474976710625
    lighter-pool-281474976710626
    lighter-pool-281474976710627
    lighter-pool-281474976710628
    lighter-pool-281474976710629
    lighter-pool-281474976710630
    lighter-pool-281474976710631
    lighter-pool-281474976710632
    lighter-pool-281474976710633
    lighter-pool-281474976710634
    lighter-pool-281474976710635
    lighter-pool-281474976710636
    lighter-pool-281474976710637
    lighter-pool-281474976710638
    lighter-pool-281474976710640
    lighter-pool-281474976710641
    lighter-pool-281474976710642
    lighter-pool-281474976710643
    lighter-pool-281474976710644
    lighter-pool-281474976710645
    lighter-pool-281474976710646
    lighter-pool-281474976710647
    lighter-pool-281474976710648
    lighter-pool-281474976710649
    lighter-pool-281474976710650
    lighter-pool-281474976710651
    lighter-pool-281474976710652
    lighter-pool-281474976710653
    """.split()
)

assert DIRECTIONAL_LEVERAGE_VAULTS <= STRATEGY_TAGS.keys()

for _address in DIRECTIONAL_LEVERAGE_VAULTS:
    STRATEGY_TAGS[_address] = {
        StrategyTag.directional_trading,
        StrategyTag.directional_leverage,
    }


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a Lighter native pool.

    :param address:
        Lowercase-compatible synthetic Lighter pool address.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return combine_strategy_tags(DEFAULT_STRATEGY_TAGS, STRATEGY_TAGS, address)
