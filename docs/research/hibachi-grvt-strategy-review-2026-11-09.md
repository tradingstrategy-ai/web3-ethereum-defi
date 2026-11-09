# Hibachi and GRVT strategy review

Reviewed on 2026-11-09 using the current public [GRVT GraphQL listing](https://edge.grvt.io/query) with `first=100` and no discoverability filter (29 returned, totalCount 29), and the complete [Hibachi inventory](https://data-api.hibachi.xyz/vault/info) (3 returned). Inactive and hidden GRVT listings are included. Existing maintainer-requested classifications are retained unless the current strategy contradicts them.

Fire now appears as ID 4 (FLP2); ID 3 is retained as a historical classification, but is absent from the current Hibachi inventory. No production state was modified.

| Vault | Address | Tags added | Tags removed |
|---|---|---|---|
| [The Crypto Meerkat](https://grvt.io/exchange/strategies/1879036542) | `vlt:2zqosukicgltfcjdet4kpmecvfg` | None | None |
| [Ampersan Research Limited](https://grvt.io/exchange/strategies/395939919) | `vlt:2zqttakxz5jkdjuyjpbwudibkbw` | None | None |
| [AI Alpha Strategy](https://grvt.io/exchange/strategies/1997719750) | `vlt:2zrj92gtwomgi8yddq8hntbf2lz` | None | None |
| [MizerXBT Strategy](https://grvt.io/exchange/strategies/2122856772) | `vlt:2zrkgkmfjqjlezllk24ledpwqbc` | trend_following | None |
| [AllDeFi Quant Directional Strategy](https://grvt.io/exchange/strategies/794368819) | `vlt:2ztqfqdo7iipsg2ki99glseyfmb` | None | None |
| [Rogue Trader Discretionary](https://grvt.io/exchange/strategies/771186006) | `vlt:2zu1ohpgg3e3bef9h9gwsj8crd9` | None | None |
| [Silentist](https://grvt.io/exchange/strategies/1123551761) | `vlt:302flw8mveki8wc4e7sf4fwczto` | None | mean_reversion, trend_following |
| [Ignight Inverse-Trend (INACTIVE)](https://grvt.io/exchange/strategies/925359668) | `vlt:328ljdq3lxhyoccqi3mtxhhq5u3` | grid_trading | None |
| [Ignight Inverse-Trend](https://grvt.io/exchange/strategies/1756934834) | `vlt:32aef5qkjhkibvf8inqq9oqsyep` | None | None |
| [Grvt Liquidity Provider (GLP)](https://grvt.io/exchange/strategies/1463215095) | `vlt:34dtzyg6lhkgm49je5aabi9tebw` | None | None |
| [ProjectBTC - Liquidity Cycle Protocol](https://grvt.io/exchange/strategies/376732940) | `vlt:34vl6upbsdhmiebvnragnnkucfk` | None | None |
| [Fisher8 Vault](https://grvt.io/exchange/strategies/1804793701) | `vlt:35j0gsfvpviwtto6hamblg9rexg` | liquidity_provider, market_making_clob | None |
| [AI Grid Trading Strategy [INACTIVE]](https://grvt.io/exchange/strategies/1207500521) | `vlt:35jpjdfrwaeuj0w7he4wi5vboag` | None | None |
| [Adaptive Trend Reversal System (INACTIVE)](https://grvt.io/exchange/strategies/928368995) | `vlt:36qtkmzkjcerbi0fmibxwltroij` | None | None |
| [Warwolf: Kinetic Volatility Capture](https://grvt.io/exchange/strategies/1825085055) | `vlt:371sgbnors7u71g6wpoo1lrst4p` | liquidity_provider, market_making_clob | None |
| [Pharos Market Maker (PMM)](https://grvt.io/exchange/strategies/328543061) | `vlt:386cnqkc6tb186ix2mtgayhbfrz` | None | None |
| [AI Follower](https://grvt.io/exchange/strategies/332549738) | `vlt:38enbl9m2f4almsegttr2irlhia` | None | None |
| [KangCFA](https://grvt.io/exchange/strategies/1789596527) | `vlt:38exn6urqqrjk6hnypihgxo2oro` | None | None |
| [Ethereum Moving Average Long/Short](https://grvt.io/exchange/strategies/1457145751) | `vlt:38t5xhyuxmzunwkud80clxhitok` | None | None |
| [RoboNet AI Long/Short Strategy](https://grvt.io/exchange/strategies/1604562259) | `vlt:38wg4abwh126ay9l3fjhhem73co` | None | None |
| [Show Me The BTC (Breakout)](https://grvt.io/exchange/strategies/651347046) | `vlt:3a483mupgtm3v22dalkd16spqfg` | None | None |
| [BTC Maxi Lab](https://grvt.io/exchange/strategies/1141168323) | `vlt:3b9tvtvisaumciyw14znklwgnq3` | None | None |
| [Solana Moving Average Long/Short](https://grvt.io/exchange/strategies/657290673) | `vlt:3brdxa1w6s5uhqkzc3jvravw1d9` | None | None |
| [[Archived] Test Vault](https://grvt.io/exchange/strategies/1049342704) | `vlt:3e4ebobovxfmmukmpnltcxtvtfp` | None | None |
| [Balanced Bundle](https://grvt.io/exchange/strategies/1662126310) | `vlt:3eesf9iphosimfc4szp2ixoqgiw` | None | None |
| [Opportunistic Bundle](https://grvt.io/exchange/strategies/744299587) | `vlt:3eexdzmz72tqcinpbu4f4i0dm2z` | None | arbitrage, delta_neutral, perpetual_futures |
| [LBZ Trading Trend pullback](https://grvt.io/exchange/strategies/365795750) | `vlt:3i8nkgzoo173zwckkhmvo4fdrft` | algorithmic_trading, directional_trading, trend_following | None |
| [Global Macro Hedge Trading](https://grvt.io/exchange/strategies/1296921658) | `vlt:3l5macfjcmky91ckru4tejdzxpl` | None | None |
| [High Yield Earn](https://grvt.io/exchange/strategies/1712148808) | `vlt:3lpp5meo1g1pquz8ebwdee8sgpo` | None | None |
| [Growi Alpha Vault](https://data-api.hibachi.xyz/vault/info) | `hibachi-vault-2` | directional_trading | None |
| [Fire Liquidity Provider](https://data-api.hibachi.xyz/vault/info) | `hibachi-vault-4` | liquidity_provider, market_making, market_making_clob | None |
| [Robonet - Allora Woodpecker](https://data-api.hibachi.xyz/vault/info) | `hibachi-vault-5` | algorithmic_trading, directional_trading | None |
| Fire Liquidity Provider (historical) | `hibachi-vault-3` | None | None |

Global Macro Hedge Trading and High Yield Earn lack sufficient vault-specific strategy descriptions for additional tags. The archived test vault retains its earlier explicitly requested discretionary classification; the current description alone would not warrant adding it anew.

Silentist now describes technical, fundamental long/short and trader-benchmarking engines, with fully automated execution. Opportunistic Bundle now describes BlackOpal receivables and FACTOR business financing; its former basis-trade classification is removed. No price backfill is needed for a tag-only refresh.

## Run after merge

`migration/scripts/048-hibachi-grvt-strategy-tags.py` refreshes only `_strategy_tags` for the 33 fixed reviewed identities (32 current, one historical). Missing metadata rows are reported, never created. At most 33 rows change; actual counts depend on persisted tags. Follow `.claude/docs/migration-scripts.md`: inspect production Compose mounts, build the merged image, stop scanner and competing writers before preview and apply, and restart the same image for normal exports and uploads. Migration success alone does not publish the tags.
