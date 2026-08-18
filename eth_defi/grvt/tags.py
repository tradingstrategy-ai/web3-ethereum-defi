"""Maintained strategy classifications for GRVT native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag, combine_strategy_tags

#: Most GRVT native vaults trade perpetual futures.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: These GRVT products are documented as tokenised RWA funds rather than
#: perpetual-futures vaults, so the native perpetual-futures default does not
#: apply. Keep the exception explicit and address-scoped.
NON_PERPETUAL_VAULTS: frozenset[str] = frozenset(
    {
        #: Vault: Balanced Bundle.
        #: Added: 2026-08-18.
        #: Decision material: The description identifies a tokenised CLO ETF
        #: and RWA credit exposure, not a perpetual-futures strategy.
        #: Sources: https://grvt.io/exchange/strategies/1662126310
        "vlt:3eesf9iphosimfc4szp2ixoqgiw",
    }
)

#: Vault-ID-specific classifications maintained in addition to the native
#: default. GRVT IDs are stored in lowercase.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: The Crypto Meerkat.
    #: Added: 2026-08-17.
    #: Decision material: The description describes sentiment factors, a
    #: trend-based model, Bollinger bands, and rolling linear regression for
    #: long/short signals, supporting algorithmic directional trend following.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1879036542
    #: - eth_defi/grvt/vault.py
    "vlt:2zqosukicgltfcjdet4kpmecvfg": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: Ampersan Research Limited.
    #: Added: 2026-08-17.
    #: Decision material: The description explicitly covers high-frequency
    #: market making, bid/ask spread capture, liquidity provision, and 24/7
    #: automated algorithmic risk monitoring.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/395939919
    #: - eth_defi/grvt/vault.py
    "vlt:2zqttakxz5jkdjuyjpbwudibkbw": {
        StrategyTag.algorithmic_trading,
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
    },
    #: Vault: AI Alpha Strategy.
    #: Added: 2026-08-17.
    #: Decision material: AI-generated signals are explicitly human-verified,
    #: so the binary execution tag is discretionary. The strategy library
    #: names trend following, mean reversion, and multiple model families.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1997719750
    #: - eth_defi/grvt/vault.py
    "vlt:2zrj92gtwomgi8yddq8hntbf2lz": {
        StrategyTag.directional_trading,
        StrategyTag.discretionary_trading,
        StrategyTag.mean_reversion,
        StrategyTag.multistrategy,
        StrategyTag.trend_following,
    },
    #: Vault: MizerXBT Strategy.
    #: Added: 2026-08-17.
    #: Decision material: The investment philosophy explicitly calls the
    #: approach discretionary long/short trading and describes selecting two
    #: related assets and managing their relative spread, supporting pair and
    #: directional trading.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/2122856772
    #: - eth_defi/grvt/vault.py
    "vlt:2zrkgkmfjqjlezllk24ledpwqbc": {
        StrategyTag.directional_trading,
        StrategyTag.discretionary_trading,
        StrategyTag.pair_trading,
    },
    #: Vault: AllDeFi Quant Directional Strategy.
    #: Added: 2026-08-17.
    #: Decision material: The description names proprietary algorithms,
    #: machine-learning pattern recognition, high-frequency trading,
    #: arbitrage, and short-term trend capture, supporting algorithmic
    #: directional, arbitrage, and trend-following tags.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/794368819
    #: - eth_defi/grvt/vault.py
    "vlt:2ztqfqdo7iipsg2ki99glseyfmb": {
        StrategyTag.algorithmic_trading,
        StrategyTag.arbitrage,
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: Rogue Trader Discretionary.
    #: Added: 2026-08-17.
    #: Decision material: The vault is explicitly named discretionary and is
    #: led by full-time traders. Its description describes fading overbought
    #: and oversold conditions in mean-reversion plays and breakouts.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/771186006
    #: - eth_defi/grvt/vault.py
    "vlt:2zu1ohpgg3e3bef9h9gwsj8crd9": {
        StrategyTag.directional_trading,
        StrategyTag.discretionary_trading,
        StrategyTag.mean_reversion,
    },
    #: Vault: Silentist BALANCE - 50% Reversion / 50% Trend.
    #: Added: 2026-08-17.
    #: Decision material: Silentist describes 10+ sub-algorithms, trend-
    #: following and mean-reversion signals, and trading that is 100%
    #: automated with predefined logic, supporting a multistrategy
    #: algorithmic directional classification.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1123551761
    #: - eth_defi/grvt/vault.py
    "vlt:302flw8mveki8wc4e7sf4fwczto": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.mean_reversion,
        StrategyTag.multistrategy,
        StrategyTag.trend_following,
    },
    #: Vault: Ignight Inverse-Trend (INACTIVE).
    #: Added: 2026-08-17.
    #: Decision material: The description calls the counter-trend
    #: accumulation strategy automated, with predefined price intervals and a
    #: fixed schedule; this supports algorithmic directional mean reversion.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/925359668
    #: - eth_defi/grvt/vault.py
    "vlt:328ljdq3lxhyoccqi3mtxhhq5u3": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.mean_reversion,
    },
    #: Vault: Ignight Inverse-Trend.
    #: Added: 2026-08-17.
    #: Updated: 2026-08-18.
    #: Decision material: The description calls the counter-trend accumulation
    #: strategy automated, with predefined price intervals and a fixed
    #: schedule. Its maintained category explicitly says Grid Trading. This
    #: supports algorithmic directional grid trading and mean reversion.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1756934834
    #: - eth_defi/grvt/vault.py
    "vlt:32aef5qkjhkibvf8inqq9oqsyep": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.mean_reversion,
    },
    #: Vault: Grvt Liquidity Provider (GLP).
    #: Added: 2026-08-17.
    #: Decision material: GRVT describes GLP as a delta-neutral market-making
    #: strategy for liquidity providers managed by a veteran trading team;
    #: without an automation claim, the binary tag is discretionary.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1463215095
    #: - eth_defi/grvt/vault.py
    "vlt:34dtzyg6lhkgm49je5aabi9tebw": {
        StrategyTag.delta_neutral,
        StrategyTag.discretionary_trading,
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
    },
    #: Vault: ProjectBTC - Liquidity Cycle Protocol.
    #: Added: 2026-08-17.
    #: Decision material: The description repeatedly identifies a structured,
    #: rules-based, pre-defined, mechanically managed trading system with
    #: data-driven position sizing, supporting algorithmic directional trading.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/376732940
    #: - eth_defi/grvt/vault.py
    "vlt:34vl6upbsdhmiebvnragnnkucfk": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Fisher8 Vault.
    #: Added: 2026-08-17.
    #: Decision material: Fisher8 describes directional mean-reversion bets
    #: and delta-neutral market making. Its system continuously rebalances and
    #: states that every decision is rule-based and fully automated.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1804793701
    #: - eth_defi/grvt/vault.py
    "vlt:35j0gsfvpviwtto6hamblg9rexg": {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
        StrategyTag.directional_trading,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.mean_reversion,
    },
    #: Vault: AI Grid Trading Strategy [INACTIVE].
    #: Added: 2026-08-17.
    #: Decision material: The description says the dual-stage grid is
    #: automatically executed at predefined intervals and filtered by a
    #: proprietary AI directional model, supporting algorithmic directional,
    #: grid-trading, and multistrategy tags.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1207500521
    #: - eth_defi/grvt/vault.py
    "vlt:35jpjdfrwaeuj0w7he4wi5vboag": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.multistrategy,
    },
    #: Vault: Adaptive Trend Reversal System (INACTIVE).
    #: Added: 2026-08-17.
    #: Decision material: The manager describes using adaptive price-action
    #: judgement and a framework that is not rigidly rule-based; the binary
    #: classification is therefore discretionary, with directional reversal
    #: signals supporting the directional tag.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/928368995
    #: - eth_defi/grvt/vault.py
    "vlt:36qtkmzkjcerbi0fmibxwltroij": {
        StrategyTag.directional_trading,
        StrategyTag.discretionary_trading,
    },
    #: Vault: Warwolf: Kinetic Volatility Capture.
    #: Added: 2026-08-17.
    #: Decision material: Warwolf is described as a market-making system using
    #: z-score mean reversion, regime detection, ATR controls, and hardcoded
    #: safety limits, supporting algorithmic market making and mean reversion.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1825085055
    #: - eth_defi/grvt/vault.py
    "vlt:371sgbnors7u71g6wpoo1lrst4p": {
        StrategyTag.algorithmic_trading,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.mean_reversion,
    },
    #: Vault: Pharos Market Maker (PMM).
    #: Added: 2026-08-17.
    #: Decision material: PMM is explicitly systematic and uses adaptive
    #: quoting, hanging orders, auto-refresh, position limits, and a liquidity
    #: kill switch, supporting algorithmic CLOB market making and liquidity
    #: provision with a mean-reversion bias.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/328543061
    #: - eth_defi/grvt/vault.py
    "vlt:386cnqkc6tb186ix2mtgayhbfrz": {
        StrategyTag.algorithmic_trading,
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.market_making_clob,
        StrategyTag.mean_reversion,
    },
    #: Vault: AI Follower.
    #: Added: 2026-08-17.
    #: Decision material: The live listing has no narrative description, but
    #: its maintained categories are Machine Learning-Driven and Trend
    #: Following; those categories support an algorithmic trend-following tag
    #: to satisfy complete binary coverage.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/332549738
    #: - eth_defi/grvt/vault.py
    "vlt:38enbl9m2f4almsegttr2irlhia": {
        StrategyTag.algorithmic_trading,
        StrategyTag.trend_following,
    },
    #: Vault: KangCFA.
    #: Added: 2026-08-17.
    #: Decision material: The description calls the strategy delta-neutral
    #: arbitrage and says it is managed systematically with tight controls,
    #: supporting algorithmic delta-neutral arbitrage.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1789596527
    #: - eth_defi/grvt/vault.py
    "vlt:38exn6urqqrjk6hnypihgxo2oro": {
        StrategyTag.algorithmic_trading,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
    },
    #: Vault: Ethereum Moving Average Long/Short.
    #: Added: 2026-08-17.
    #: Decision material: The strategy executes on a daily schedule using a
    #: specified moving average and backtest-calibrated leverage for long and
    #: short positions, supporting algorithmic directional trend following.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1457145751
    #: - eth_defi/grvt/vault.py
    "vlt:38t5xhyuxmzunwkud80clxhitok": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: RoboNet AI Long/Short Strategy.
    #: Added: 2026-08-17.
    #: Decision material: RoboNet explicitly describes a systematic,
    #: AI-powered long/short perpetual portfolio with model forecasts and
    #: rules-based rebalancing every eight hours, supporting algorithmic
    #: directional trading.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1604562259
    #: - eth_defi/grvt/vault.py
    "vlt:38wg4abwh126ay9l3fjhhem73co": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    },
    #: Vault: Show Me The BTC (Breakout).
    #: Added: 2026-08-17.
    #: Decision material: The description explicitly calls the SK System a
    #: trend-following algorithm and lists systematic volatility-breakout,
    #: momentum filtering, and one-signal-per-trend rules.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/651347046
    #: - eth_defi/grvt/vault.py
    "vlt:3a483mupgtm3v22dalkd16spqfg": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: BTC Maxi Lab.
    #: Added: 2026-08-17.
    #: Decision material: The fund describes proprietary quantitative models,
    #: high-frequency gamma-flow scalping, pinning-based trend following, and
    #: cross-asset model research, supporting algorithmic directional
    #: trend-following multistrategy.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1141168323
    #: - eth_defi/grvt/vault.py
    "vlt:3b9tvtvisaumciyw14znklwgnq3": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.multistrategy,
        StrategyTag.trend_following,
    },
    #: Vault: Solana Moving Average Long/Short.
    #: Added: 2026-08-17.
    #: Decision material: The strategy uses fixed golden/death-cross moving
    #: average signals and calibrated long/short leverage, supporting
    #: algorithmic directional trend following.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/657290673
    #: - eth_defi/grvt/vault.py
    "vlt:3brdxa1w6s5uhqkzc3jvravw1d9": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.trend_following,
    },
    #: Vault: [Archived] Test Vault.
    #: Added: 2026-08-17.
    #: Decision material: The source description only says "test vault" and
    #: provides no automation or strategy evidence. Per the requested complete
    #: binary coverage, it receives the conservative discretionary tag and no
    #: additional strategy tags.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1049342704
    #: - eth_defi/grvt/vault.py
    "vlt:3e4ebobovxfmmukmpnltcxtvtfp": {StrategyTag.discretionary_trading},
    #: Vault: Balanced Bundle.
    #: Added: 2026-08-17.
    #: Decision material: The description provides exposure to an actively
    #: managed AAA-rated CLO ETF, a real-world credit asset. No automation
    #: claim is made, so the binary tag is discretionary; the additional tags
    #: capture RWA and RWA credit exposure. This documented non-perpetual
    #: product is excluded from the native perpetual-futures default.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/1662126310
    #: - eth_defi/grvt/vault.py
    "vlt:3eesf9iphosimfc4szp2ixoqgiw": {
        StrategyTag.discretionary_trading,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
    #: Vault: Opportunistic Bundle.
    #: Added: 2026-08-17.
    #: Decision material: The description identifies a tokenised real-world
    #: asset-backed fund investing in Brazilian credit-card receivables and a
    #: market-neutral basis-trade sleeve. It provides no automation claim, so
    #: the binary tag is discretionary, with RWA credit, delta-neutral, and
    #: arbitrage tags for the documented exposures. The basis-trade sleeve
    #: retains the native perpetual-futures default.
    #: Sources:
    #: - https://edge.grvt.io/query
    #: - https://grvt.io/exchange/strategies/744299587
    #: - eth_defi/grvt/vault.py
    "vlt:3eexdzmz72tqcinpbu4f4i0dm2z": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.discretionary_trading,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a GRVT native vault.

    :param address:
        Lowercase-compatible GRVT vault ID.
    :return:
        New tag set containing the native perpetual-futures default when
        applicable and any address-specific classifications.
    """
    key = address.lower()
    defaults = () if key in NON_PERPETUAL_VAULTS else DEFAULT_STRATEGY_TAGS
    return combine_strategy_tags(defaults, STRATEGY_TAGS, key)
