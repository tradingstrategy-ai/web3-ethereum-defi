# Lighter public pool census

The census covers the 349 pools listed on 19 November 2026. The historical review covers 13 October to 11 November 2026 UTC. [Analysis and methodology](lighter-public-pools.md)

- [Strategy classifications](#strategy-classifications)
- [Archive coverage](#archive-coverage)
- [Portfolio exposure](#portfolio-exposure)
- [Trading and rebalances](#trading-and-rebalances)
- [Share-price performance](#share-price-performance)

An em dash indicates that a value is unavailable. TVL, volume, fees and realised P&L are in USDC on Core and USDG on Robinhood. The account index identifies a pool within its deployment.

## Strategy classifications

The synthetic address is `lighter-pool-{account index}` for Core and `lighter-pool-robinhood-{account index}` for Robinhood. The API status is the listing value. Robinhood LLP has a separate deposit-closure override.

### Pools with new tags

| Pool | Account index | Deployment | API status | TVL | Tags added | Full tag set |
| --- | --- | --- | --- | --- | --- | --- |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | `281474976688087` | Core | Active | 576,346.778225 | Leveraged portfolio, Long-short portfolio, 3x portfolio | Algorithmic trading, Delta neutral, Leveraged portfolio, Long-short portfolio, Perpetual futures, 3x portfolio |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | `281474976622846` | Core | Active | 559,918.387467 | Leveraged portfolio, Long-short portfolio | Directional trading, Leveraged portfolio, Long-short portfolio, Perpetual futures |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | `281474976656586` | Core | Active | 408,720.031121 | Long-short portfolio | Delta neutral, Directional trading, Long-short portfolio, Perpetual futures |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | `281474976624925` | Core | Active | 187,535.125669 | Directional trading | Directional trading, Perpetual futures |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | `281474976498676` | Core | Active | 135,954.516614 | Directional trading, Discretionary trading, Unlevered portfolio | Directional trading, Discretionary trading, Perpetual futures, Unlevered portfolio |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | `281474976543116` | Core | Active | 70,755.110995 | Directional trading | Algorithmic trading, Directional trading, High-frequency, high-win-rate trading, Perpetual futures |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | `281474976651007` | Core | Active | 50,450.040471 | Directional trading, Leveraged portfolio, Long-short portfolio, 3x portfolio | Directional trading, Leveraged portfolio, Long-short portfolio, Perpetual futures, 3x portfolio |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | `281474976684763` | Core | Active | 29,303.282381 | Directional trading, Unlevered portfolio | Directional trading, Perpetual futures, Unlevered portfolio |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | `281474976488851` | Core | Active | 6,536.780561 | Unlevered portfolio | Directional trading, Discretionary trading, Perpetual futures, Sentiment trading, Social trading, Unlevered portfolio |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | `281474976489165` | Core | Active | 4,830.154868 | Unlevered portfolio | Directional trading, Discretionary trading, Perpetual futures, Sentiment trading, Social trading, Unlevered portfolio |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | `281474976466894` | Core | Active | 4,471.793931 | Algorithmic trading, Grid trading | Algorithmic trading, Grid trading, Perpetual futures |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | `281474976694250` | Core | Inactive | 4,199.378976 | Leveraged portfolio | Directional trading, Discretionary trading, Leveraged portfolio, Perpetual futures |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | `281474976489166` | Core | Active | 3,749.533038 | Leveraged portfolio | Directional trading, Discretionary trading, Leveraged portfolio, Perpetual futures, Sentiment trading, Social trading |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | `281474976488848` | Core | Active | 3,707.734978 | Leveraged portfolio, Long-short portfolio, 2x portfolio | Directional trading, Discretionary trading, Leveraged portfolio, Long-short portfolio, Perpetual futures, 2x portfolio, Sentiment trading, Social trading |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | `281474976488853` | Core | Active | 3,418.477979 | Unlevered portfolio | Directional trading, Discretionary trading, Perpetual futures, Sentiment trading, Social trading, Unlevered portfolio |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | `281474976655272` | Core | Active | 2,020.181791 | Directional trading, Unlevered portfolio | Directional trading, Perpetual futures, Unlevered portfolio |
| [AlphaMIX Global](https://app.lighter.xyz/public-pools/281474976415828) | `281474976415828` | Core | Active | 1,200.131483 | Algorithmic trading, Directional trading | Algorithmic trading, Directional trading, Perpetual futures |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | `281474976501540` | Core | Active | 1,073.396119 | Daily rebalance, Leveraged portfolio, Long-short portfolio | Algorithmic trading, Daily rebalance, Delta neutral, Leveraged portfolio, Long-short portfolio, Perpetual futures |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | `281474976492148` | Core | Active | 1,015.232917 | Algorithmic trading, Directional trading, Trend following | Algorithmic trading, Directional trading, Perpetual futures, Trend following |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | `281474976486477` | Core | Active | 955.444394 | Algorithmic trading, Daily rebalance, Delta neutral, Long-short portfolio | Algorithmic trading, Daily rebalance, Delta neutral, Long-short portfolio, Perpetual futures |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | `281474976656876` | Core | Inactive | 955.436817 | Long-short portfolio | Long-short portfolio, Perpetual futures |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | `281474976505621` | Core | Active | 755.821666 | Algorithmic trading, Directional trading | Algorithmic trading, Directional trading, Perpetual futures |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | `281474976643895` | Core | Active | 301.647646 | Directional trading, Discretionary trading, Leveraged portfolio | Directional trading, Discretionary trading, Leveraged portfolio, Perpetual futures |
| [Pikachu Pool](https://app.lighter.xyz/public-pools/281474976643789) | `281474976643789` | Core | Inactive | 220.131786 | Algorithmic trading, Discretionary trading | Algorithmic trading, Discretionary trading, Perpetual futures |
| [Alleged Alpha](https://app.lighter.xyz/public-pools/281474976625478) | `281474976625478` | Core | Active | 49.746991 | Algorithmic trading, Long-short portfolio, Multi-strategy, Trend following | Algorithmic trading, Long-short portfolio, Multi-strategy, Perpetual futures, Trend following |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | `281474976639900` | Core | Inactive | 33.84341 | Unlevered portfolio | Algorithmic trading, Multi-strategy, Perpetual futures, Unlevered portfolio |
| [scanner.money - TrendSend](https://app.lighter.xyz/public-pools/281474976490589) | `281474976490589` | Core | Active | 28.778969 | Algorithmic trading, Directional trading, Trend following | Algorithmic trading, Directional trading, Perpetual futures, Trend following |
| [BTC Trading Pool](https://app.lighter.xyz/public-pools/281474976644793) | `281474976644793` | Core | Active | 8.322349 | Directional trading, Discretionary trading, Trend following | Directional trading, Discretionary trading, Perpetual futures, Trend following |
| [cp0x Delta Majors](https://app.lighter.xyz/public-pools/281474976644089) | `281474976644089` | Core | Inactive | 6.993706 | Long-short portfolio | Directional trading, Long-short portfolio, Perpetual futures |
| [Short Coins](https://app.lighter.xyz/public-pools/281474976666853) | `281474976666853` | Core | Inactive | 4.986119 | Directional trading | Directional trading, Perpetual futures |
| [Shrimp Liquidity Provider (SLP)](https://app.lighter.xyz/public-pools/281474976666704) | `281474976666704` | Core | Active | 4.285461 | Long-short portfolio | Algorithmic trading, Delta neutral, Grid trading, Long-short portfolio, Perpetual futures |
| [BTC H1 EMA Strategy](https://app.lighter.xyz/public-pools/281474976656541) | `281474976656541` | Core | Active | 0.216159 | Bitcoin only, Directional trading | Algorithmic trading, Bitcoin only, Directional trading, Perpetual futures, Trend following |
| [Moonshot](https://app.lighter.xyz/public-pools/281474976638939) | `281474976638939` | Core | Active | 0.010473 | Directional trading, Trend following | Directional trading, Perpetual futures, Trend following |
| [Majors Correlated Pool!](https://app.lighter.xyz/public-pools/281474976626327) | `281474976626327` | Core | Inactive | 0.003755 | Algorithmic trading, Statistical arbitrage | Algorithmic trading, Perpetual futures, Statistical arbitrage |
| [BULBUL Vault](https://app.lighter.xyz/public-pools/281474976687595) | `281474976687595` | Core | Inactive | 0.003396 | Long-short portfolio | Directional trading, Long-short portfolio, Perpetual futures |
| [Symphony](https://app.lighter.xyz/public-pools/281474976626973) | `281474976626973` | Core | Inactive | 0.000909 | Delta neutral, Long-short portfolio, Multi-strategy | Delta neutral, Long-short portfolio, Multi-strategy, Perpetual futures |
| [Storm](https://app.lighter.xyz/public-pools/281474976623750) | `281474976623750` | Core | Active | 0 | Algorithmic trading | Algorithmic trading, Perpetual futures |
| [pool1](https://app.lighter.xyz/public-pools/281474976637751) | `281474976637751` | Core | Active | 0 | Directional trading | Directional trading, Perpetual futures |
| [Core4-Long Basket-Short](https://app.lighter.xyz/public-pools/281474976643102) | `281474976643102` | Core | Active | 0 | Long-short portfolio | Algorithmic trading, Delta neutral, Long-short portfolio, Perpetual futures |
| [\[Long Value & Short Dogshit\]](https://app.lighter.xyz/public-pools/281474976644348) | `281474976644348` | Core | Inactive | 0 | Long-short portfolio | Directional trading, Discretionary trading, Long-short portfolio, Mean reversion, Perpetual futures |
| [The Pool Party](https://app.lighter.xyz/public-pools/281474976680236) | `281474976680236` | Core | Inactive | 0 | Directional trading, Discretionary trading, Trend following | Directional trading, Discretionary trading, Perpetual futures, Trend following |
| [Agentic Trading](https://app.lighter.xyz/public-pools/281474976688779) | `281474976688779` | Core | Active | 0 | Social trading | Perpetual futures, Social trading |

### Evidence for tag changes

| Pool | Evidence source | Decision |
| --- | --- | --- |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | Window measurements | Both sides each reached 5% of equity in all 172 native snapshots. Median gross exposure was 294.6% of equity. |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | Window measurements | The operator describes a long-atoms, short-bits trade. Archived holdings include long precious-metals contracts and short BTC and equity-index contracts. Median gross exposure was 865.4% of equity. |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | Published mandate | The operator describes a BTC long hedged by an equally weighted short basket, alongside directional news trades. |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | Window measurements | All 720 qualified archived hours held BTC longs alone, with 70 ordinary fills in the review window. |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | Window measurements | The operator identifies discretionary trading. The observed book held a median of four long markets and 19.0% of equity across 172 native snapshots. |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | Window measurements and prior fill reconstruction | Research reconstructed mostly long directional inventory with some short cycles. Both sides reached 5% of equity in none of the native snapshots. The strategy trades through short-lived averaging cycles, so the review does not assign a portfolio leverage band. |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | Window measurements | The operator describes a majors-versus-alts sleeve alongside directional trades. Both sides each reached 5% of equity in all 172 native snapshots, with 315.2% median gross exposure and a persistent long tilt. |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | Window measurements | The observed book held a median of four long markets, with 116.6% median and 146.1% 90th-percentile gross exposure. Only one qualified archive hour included a short position. |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | Window measurements | The observed short book held a median of four markets and 100.1% of vault equity across 172 native snapshots. |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | Window measurements | The observed book had 66.5% median and 134.7% 90th-percentile gross exposure across 172 native snapshots. Both sides were material in 37% of those observations. |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | Published mandate | The operator describes a systematic grid strategy. |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | Window measurements | The observed long book held a median of three markets and 440.3% of vault equity across 172 native snapshots. |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | Window measurements | The long-only observed book held a median of three markets and 135.9% of vault equity across 172 native snapshots. |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | Window measurements | Centaur documents an equity and macro long-short book sourced from public signals. Both sides each reached 5% of equity in all 172 native snapshots, with 210.1% median gross exposure. |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | Window measurements | The observed long book held a median of seven markets and 90.6% of vault equity across 172 native snapshots. |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | Window measurements | All 720 qualified archived hours showed long exposure alone. Two markets at the median accounted for 93.9% of equity across 172 native snapshots. |
| [AlphaMIX Global](https://app.lighter.xyz/public-pools/281474976415828) | Published mandate | The operator describes a systematic directional model that uses market structure and probabilistic scenarios to adjust positions. |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | Window measurements | Both sides each reached 5% of equity in all 172 native snapshots, with 379.0% median gross exposure. The 29 multi-market bursts had a 24.0-hour median start gap. |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | Window measurements | The operator describes a systematic long-only breakout strategy. The archived positions were long throughout the observed open hours. |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | Window measurements | The operator describes a systematic delta-neutral book. Both sides were open in each of the 720 qualified archive hours. The median gap between 20 multi-market bursts was 24.0 hours, with 18 starts at minute 41 of the hour. |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | Published mandate | The operator describes a long basket of BTC and other market leaders hedged by a broad altcoin short basket. |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | Published mandate | The operator describes a systematic altcoin short strategy. |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | Window measurements | The operator describes discretionary trading. Median exposures were 656.5% long and 137.9% short across 154 native snapshots, with 789.0% median gross exposure. |
| [Pikachu Pool](https://app.lighter.xyz/public-pools/281474976643789) | Published mandate | The operator describes combining trading systems with manual trades. |
| [Alleged Alpha](https://app.lighter.xyz/public-pools/281474976625478) | Published mandate | The operator describes an automated long-short mandate combining cross-sectional, momentum and reversal strategies. The published reversal sleeve does not specify a mean-reversion target. |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | Window measurements | The current Lighter mandate describes a smart-trader benchmark portfolio. Both sides each reached 5% of equity in 80% of 127 native snapshots, with 70.6% median gross exposure. The purpose of the two books remains unspecified. |
| [scanner.money - TrendSend](https://app.lighter.xyz/public-pools/281474976490589) | Window measurements | The operator describes trading daily model signals with breakout entries and mechanical stop reversals. The archive confirms long positions in the review window. |
| [BTC Trading Pool](https://app.lighter.xyz/public-pools/281474976644793) | Published mandate | The operator describes discretionary directional trades, mostly in BTC, using events and trends. The stated mandate allows both sides, while the review window had no observed concurrent books. |
| [cp0x Delta Majors](https://app.lighter.xyz/public-pools/281474976644089) | Published mandate | The published strategy holds a majors long basket and an altcoin short basket and rebalances them. |
| [Short Coins](https://app.lighter.xyz/public-pools/281474976666853) | Published mandate | The operator describes shorting selected tokens. |
| [Shrimp Liquidity Provider (SLP)](https://app.lighter.xyz/public-pools/281474976666704) | Published mandate | The operator describes simultaneous hedged baskets as the basis of its grid strategy. |
| [BTC H1 EMA Strategy](https://app.lighter.xyz/public-pools/281474976656541) | Published mandate | The operator identifies BTC as the sole market for its hourly exponential moving average strategy. |
| [Moonshot](https://app.lighter.xyz/public-pools/281474976638939) | Published mandate | The operator describes directional long or short trades based on momentum. |
| [Majors Correlated Pool!](https://app.lighter.xyz/public-pools/281474976626327) | Published mandate | The operator describes a rule-based system that trades relative price dislocations between major crypto assets. The available evidence leaves the trade legs unspecified. |
| [BULBUL Vault](https://app.lighter.xyz/public-pools/281474976687595) | Published mandate | The operator specifies a BTC and HYPE long book against shorts in other assets. |
| [Symphony](https://app.lighter.xyz/public-pools/281474976626973) | Published mandate | The operator identifies a market-neutral, multi-strategy mandate. The archive shows no open positions in the review window. |
| [Storm](https://app.lighter.xyz/public-pools/281474976623750) | Published mandate | The operator describes multi-factor systematic rebalancing to capture BTC relative strength. The published strategy leaves the use of short positions unspecified. |
| [pool1](https://app.lighter.xyz/public-pools/281474976637751) | Published mandate | The operator describes taking only long positions when market conditions permit. |
| [Core4-Long Basket-Short](https://app.lighter.xyz/public-pools/281474976643102) | Published mandate | The operator specifies a BTC, ETH, SOL and HYPE long basket against an altcoin short basket. |
| [\[Long Value & Short Dogshit\]](https://app.lighter.xyz/public-pools/281474976644348) | Published mandate | The operator explicitly contrasts a long book of fundamentally strong assets with a short book of weaker assets. |
| [The Pool Party](https://app.lighter.xyz/public-pools/281474976680236) | Published mandate | The operator identifies discretionary swing trades in trending markets. |
| [Agentic Trading](https://app.lighter.xyz/public-pools/281474976688779) | Published mandate | The operator describes copying trades from onchain perpetual futures traders. |

### Pools with existing strategy tags

| Pool | Account index | Deployment | API status | TVL | Full tag set |
| --- | --- | --- | --- | --- | --- |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | `281474976710654` | Core | Active | 81,116,513.747764 | Liquidity provider, Market making, Orderbook market making, Perpetual futures |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | `281474976552443` | Core | Active | 83,492.861525 | Algorithmic trading, Directional trading, Grid trading, High-frequency, high-win-rate trading, Martingale-like, Perpetual futures |
| [Experimental Liquidity Provider (XLP)](https://app.lighter.xyz/public-pools/281474976680784) | `281474976680784` | Core | Active | 40,429.134901 | Liquidity provider, Market making, Orderbook market making, Perpetual futures |
| [Centaur Strategies: Lazy Villager](https://app.lighter.xyz/public-pools/281474976488856) | `281474976488856` | Core | Active | 7,982.734363 | Directional trading, Discretionary trading, Perpetual futures, Sentiment trading, Social trading |
| [Algorithmic L/S \| Big Brain Research](https://app.lighter.xyz/public-pools/281474976690488) | `281474976690488` | Core | Active | 1,976.577884 | Algorithmic trading, Directional trading, Perpetual futures, Trend following |
| [systematic breakout long-only](https://app.lighter.xyz/public-pools/281474976496745) | `281474976496745` | Core | Active | 1,288.264314 | Algorithmic trading, Directional trading, Perpetual futures, Trend following |
| [GCB Capital](https://app.lighter.xyz/public-pools/281474976690376) | `281474976690376` | Core | Active | 472.414668 | Algorithmic trading, Directional trading, Discretionary trading, Perpetual futures |
| [Popo BTC Only Low Frequency Algo](https://app.lighter.xyz/public-pools/281474976623846) | `281474976623846` | Core | Inactive | 229.598256 | Algorithmic trading, Perpetual futures |
| [PAWNZI ALGO – PROFITABLE (REAL)](https://app.lighter.xyz/public-pools/281474976680234) | `281474976680234` | Core | Inactive | 180.865494 | Algorithmic trading, Mean reversion, Perpetual futures |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | `281474976552918` | Core | Active | 149.76796 | Algorithmic trading, Discretionary trading, Pair trading, Perpetual futures |
| [DSC - L Strategy](https://app.lighter.xyz/public-pools/281474976644517) | `281474976644517` | Core | Active | 15.046853 | Directional trading, Perpetual futures |
| [Cross-Sectional-Relative-Strength](https://app.lighter.xyz/public-pools/281474976656474) | `281474976656474` | Core | Active | 8.60207 | Algorithmic trading, Perpetual futures, Trend following |
| [Bellagio](https://app.lighter.xyz/public-pools/281474976690371) | `281474976690371` | Core | Inactive | 7.040977 | Algorithmic trading, Discretionary trading, Perpetual futures |
| [Quant + Discretionary](https://app.lighter.xyz/public-pools/281474976644732) | `281474976644732` | Core | Inactive | 5.883834 | Algorithmic trading, Discretionary trading, Perpetual futures |
| [Valkyrie of the Faint Smile( L/S)](https://app.lighter.xyz/public-pools/281474976651220) | `281474976651220` | Core | Inactive | 0.140923 | Directional trading, Discretionary trading, Perpetual futures |
| [Long BTC/ETH/HYPE + Gold](https://app.lighter.xyz/public-pools/281474976644427) | `281474976644427` | Core | Inactive | 0.003338 | Directional trading, Perpetual futures |
| [YOLO Pool](https://app.lighter.xyz/public-pools/281474976689753) | `281474976689753` | Core | Active | 0.002798 | Directional trading, Perpetual futures |
| [gLighter Pool by NamasteWeb3](https://app.lighter.xyz/public-pools/281474976689660) | `281474976689660` | Core | Inactive | 0.002378 | Directional trading, Discretionary trading, Perpetual futures |
| [TrippleS Capital](https://app.lighter.xyz/public-pools/281474976686233) | `281474976686233` | Core | Active | 0.000106 | Algorithmic trading, Directional trading, Perpetual futures |
| [\[Systematic Strategies\] Long / Short Bot](https://app.lighter.xyz/public-pools/281474976549878) | `281474976549878` | Core | Active | 0 | Algorithmic trading, Perpetual futures |
| [\[Robonet\] - Allora Libra](https://app.lighter.xyz/public-pools/281474976576705) | `281474976576705` | Core | Active | 0 | Algorithmic trading, Directional trading, Perpetual futures |
| [CopyCAT](https://app.lighter.xyz/public-pools/281474976587033) | `281474976587033` | Core | Active | 0 | Algorithmic trading, Perpetual futures |
| [Candle Effect v1](https://app.lighter.xyz/public-pools/281474976620676) | `281474976620676` | Core | Inactive | 0 | Algorithmic trading, High-frequency, high-win-rate trading, Perpetual futures |
| [BTC 35x Long](https://app.lighter.xyz/public-pools/281474976644581) | `281474976644581` | Core | Inactive | 0 | Directional trading, Perpetual futures |
| [Trend Trading](https://app.lighter.xyz/public-pools/281474976681281) | `281474976681281` | Core | Active | 0 | Directional trading, Perpetual futures, Trend following |
| [YOLO Pool](https://app.lighter.xyz/public-pools/281474976690279) | `281474976690279` | Core | Inactive | 0 | Directional trading, Perpetual futures |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | `281474976710654` | Robinhood | Active | 1,782,204.244489 | Liquidity provider, Market making, Orderbook market making, Perpetual futures |

### Pools with the default tag

The pools below carry the Perpetual futures tag. The available mandates and observed histories support that level of classification.

| Pool | Account index | Deployment | API status | TVL |
| --- | --- | --- | --- | --- |
| [K Pool](https://app.lighter.xyz/public-pools/281474976680237) | `281474976680237` | Core | Active | 2,043.623572 |
| [Silentist Public 2nd](https://app.lighter.xyz/public-pools/281474976436927) | `281474976436927` | Core | Active | 1,000 |
| [EdgePool](https://app.lighter.xyz/public-pools/281474976466567) | `281474976466567` | Core | Active | 1,000 |
| [LAZY Pool](https://app.lighter.xyz/public-pools/281474976473729) | `281474976473729` | Core | Active | 1,000 |
| [Hanabi pool](https://app.lighter.xyz/public-pools/281474976678968) | `281474976678968` | Core | Inactive | 440.754103 |
| [DTK Pool](https://app.lighter.xyz/public-pools/281474976644086) | `281474976644086` | Core | Inactive | 426.891643 |
| [KUY Strategy](https://app.lighter.xyz/public-pools/281474976624682) | `281474976624682` | Core | Inactive | 387.459769 |
| [Index of Profitable Traders \| CloneX (Depreciated)](https://app.lighter.xyz/public-pools/281474976690931) | `281474976690931` | Core | Inactive | 283.82938 |
| [Vorcaro](https://app.lighter.xyz/public-pools/281474976621130) | `281474976621130` | Core | Active | 207.317474 |
| [SMTrading (Temporarily Closed)](https://app.lighter.xyz/public-pools/281474976680682) | `281474976680682` | Core | Inactive | 179.614463 |
| [BIT755](https://app.lighter.xyz/public-pools/281474976563230) | `281474976563230` | Core | Active | 150.944442 |
| [Trust me bro (diditrading)](https://app.lighter.xyz/public-pools/281474976690774) | `281474976690774` | Core | Inactive | 126.177518 |
| [Lighter Quantum](https://app.lighter.xyz/public-pools/281474976623571) | `281474976623571` | Core | Active | 93.08 |
| [LIT ALPHA POOL](https://app.lighter.xyz/public-pools/281474976490593) | `281474976490593` | Core | Active | 85.259692 |
| [old-pool](https://app.lighter.xyz/public-pools/281474976688801) | `281474976688801` | Core | Active | 70.056554 |
| [Soufflé Pool](https://app.lighter.xyz/public-pools/281474976680506) | `281474976680506` | Core | Inactive | 61.947083 |
| [paba and amigos](https://app.lighter.xyz/public-pools/281474976689945) | `281474976689945` | Core | Inactive | 51.27252 |
| [Pool](https://app.lighter.xyz/public-pools/281474976644417) | `281474976644417` | Core | Inactive | 50.376687 |
| [Capital Vault](https://app.lighter.xyz/public-pools/281474976690600) | `281474976690600` | Core | Inactive | 46.209656 |
| [Goon Cave](https://app.lighter.xyz/public-pools/281474976656239) | `281474976656239` | Core | Active | 18.541138 |
| [ABCapital](https://app.lighter.xyz/public-pools/281474976678339) | `281474976678339` | Core | Active | 12.469383 |
| [PHP Trading Desk (BTC/USDC)](https://app.lighter.xyz/public-pools/281474976593834) | `281474976593834` | Core | Active | 10.632618 |
| [Dragon](https://app.lighter.xyz/public-pools/281474976534461) | `281474976534461` | Core | Inactive | 8.728691 |
| [Aesthetic Pool](https://app.lighter.xyz/public-pools/281474976644611) | `281474976644611` | Core | Active | 8.474713 |
| [lefty’s btc pool](https://app.lighter.xyz/public-pools/281474976644615) | `281474976644615` | Core | Active | 7.058503 |
| [Popo Pool](https://app.lighter.xyz/public-pools/281474976690411) | `281474976690411` | Core | Inactive | 6.339615 |
| [Perpetua](https://app.lighter.xyz/public-pools/281474976644635) | `281474976644635` | Core | Active | 6.097587 |
| [ffadff](https://app.lighter.xyz/public-pools/281474976669598) | `281474976669598` | Core | Inactive | 5.463272 |
| [VB Trading](https://app.lighter.xyz/public-pools/281474976642013) | `281474976642013` | Core | Active | 5.190051 |
| [Syzygy Strategy](https://app.lighter.xyz/public-pools/281474976539113) | `281474976539113` | Core | Inactive | 5.121528 |
| [Shadow](https://app.lighter.xyz/public-pools/281474976643341) | `281474976643341` | Core | Active | 3.910635 |
| [Ghost](https://app.lighter.xyz/public-pools/281474976688577) | `281474976688577` | Core | Inactive | 2.387392 |
| [rekt](https://app.lighter.xyz/public-pools/281474976623731) | `281474976623731` | Core | Inactive | 2.320249 |
| [Experimental Pool](https://app.lighter.xyz/public-pools/281474976644304) | `281474976644304` | Core | Active | 1.20549 |
| [alt mania](https://app.lighter.xyz/public-pools/281474976644562) | `281474976644562` | Core | Inactive | 0.966212 |
| [Vegeta's Ultra Ego Pool](https://app.lighter.xyz/public-pools/281474976675532) | `281474976675532` | Core | Active | 0.730948 |
| [HLP](https://app.lighter.xyz/public-pools/281474976643920) | `281474976643920` | Core | Active | 0.559695 |
| [Dead pool](https://app.lighter.xyz/public-pools/281474976680683) | `281474976680683` | Core | Inactive | 0.160088 |
| [Failure](https://app.lighter.xyz/public-pools/281474976503569) | `281474976503569` | Core | Active | 0.041055 |
| [Better Than Yours](https://app.lighter.xyz/public-pools/281474976642928) | `281474976642928` | Core | Inactive | 0.014939 |
| [BTC/ETH x20 LONG](https://app.lighter.xyz/public-pools/281474976637687) | `281474976637687` | Core | Inactive | 0.007268 |
| [FastBit Pool](https://app.lighter.xyz/public-pools/281474976643399) | `281474976643399` | Core | Active | 0.005832 |
| [Aaa](https://app.lighter.xyz/public-pools/281474976663862) | `281474976663862` | Core | Inactive | 0.003112 |
| [Xiaohei](https://app.lighter.xyz/public-pools/281474976644412) | `281474976644412` | Core | Inactive | 0.002691 |
| [Vui Pool](https://app.lighter.xyz/public-pools/281474976689795) | `281474976689795` | Core | Inactive | 0.002561 |
| [miner](https://app.lighter.xyz/public-pools/281474976644575) | `281474976644575` | Core | Active | 0.002115 |
| [DivinelyRejected](https://app.lighter.xyz/public-pools/281474976643900) | `281474976643900` | Core | Inactive | 0.000568 |
| [Phantom](https://app.lighter.xyz/public-pools/281474976644012) | `281474976644012` | Core | Active | 0.000113 |
| [Degenerate Pool](https://app.lighter.xyz/public-pools/281474976644059) | `281474976644059` | Core | Active | 0.000019 |
| [Unnamed pool](https://app.lighter.xyz/public-pools/281474976415701) | `281474976415701` | Core | Active | 0 |
| [Unnamed pool](https://app.lighter.xyz/public-pools/281474976415757) | `281474976415757` | Core | Active | 0 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473724) | `281474976473724` | Core | Inactive | 0 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473725) | `281474976473725` | Core | Inactive | 0 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473728) | `281474976473728` | Core | Inactive | 0 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473730) | `281474976473730` | Core | Inactive | 0 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473731) | `281474976473731` | Core | Inactive | 0 |
| [GGez.trade](https://app.lighter.xyz/public-pools/281474976486923) | `281474976486923` | Core | Active | 0 |
| [Nebula Trading](https://app.lighter.xyz/public-pools/281474976492019) | `281474976492019` | Core | Active | 0 |
| [ICT_SMT](https://app.lighter.xyz/public-pools/281474976499136) | `281474976499136` | Core | Inactive | 0 |
| [\[RES\] Range Extension Strategies](https://app.lighter.xyz/public-pools/281474976527697) | `281474976527697` | Core | Active | 0 |
| [Wombat](https://app.lighter.xyz/public-pools/281474976528250) | `281474976528250` | Core | Inactive | 0 |
| [EAGLE](https://app.lighter.xyz/public-pools/281474976528251) | `281474976528251` | Core | Inactive | 0 |
| [Cabbage Gang Pool#1](https://app.lighter.xyz/public-pools/281474976587880) | `281474976587880` | Core | Active | 0 |
| [Candy from a Baby](https://app.lighter.xyz/public-pools/281474976617249) | `281474976617249` | Core | Active | 0 |
| [zxcsolomid](https://app.lighter.xyz/public-pools/281474976617289) | `281474976617289` | Core | Active | 0 |
| [Mars Gambols](https://app.lighter.xyz/public-pools/281474976624514) | `281474976624514` | Core | Inactive | 0 |
| [KUY Strategy](https://app.lighter.xyz/public-pools/281474976624683) | `281474976624683` | Core | Inactive | 0 |
| [Equity Trader](https://app.lighter.xyz/public-pools/281474976626656) | `281474976626656` | Core | Active | 0 |
| [Unicorn](https://app.lighter.xyz/public-pools/281474976628387) | `281474976628387` | Core | Active | 0 |
| [Miracle](https://app.lighter.xyz/public-pools/281474976640438) | `281474976640438` | Core | Active | 0 |
| [Miracles](https://app.lighter.xyz/public-pools/281474976640440) | `281474976640440` | Core | Inactive | 0 |
| [unnu_eth](https://app.lighter.xyz/public-pools/281474976643294) | `281474976643294` | Core | Active | 0 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976644413) | `281474976644413` | Core | Inactive | 0 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976644745) | `281474976644745` | Core | Inactive | 0 |
| [GOAT Pool](https://app.lighter.xyz/public-pools/281474976645219) | `281474976645219` | Core | Inactive | 0 |
| [Test](https://app.lighter.xyz/public-pools/281474976645732) | `281474976645732` | Core | Inactive | 0 |
| [MHM](https://app.lighter.xyz/public-pools/281474976646196) | `281474976646196` | Core | Active | 0 |
| [Majors vs Alts (old)](https://app.lighter.xyz/public-pools/281474976656935) | `281474976656935` | Core | Inactive | 0 |
| [Wife Money](https://app.lighter.xyz/public-pools/281474976656990) | `281474976656990` | Core | Inactive | 0 |
| [Test](https://app.lighter.xyz/public-pools/281474976675594) | `281474976675594` | Core | Inactive | 0 |
| [Test](https://app.lighter.xyz/public-pools/281474976675641) | `281474976675641` | Core | Inactive | 0 |
| [experiment2](https://app.lighter.xyz/public-pools/281474976677272) | `281474976677272` | Core | Inactive | 0 |
| [experiment](https://app.lighter.xyz/public-pools/281474976678305) | `281474976678305` | Core | Inactive | 0 |
| [Custard Pool](https://app.lighter.xyz/public-pools/281474976680565) | `281474976680565` | Core | Inactive | 0 |
| [degencrypt](https://app.lighter.xyz/public-pools/281474976686367) | `281474976686367` | Core | Active | 0 |
| [IZ labs](https://app.lighter.xyz/public-pools/281474976690877) | `281474976690877` | Core | Active | 0 |
| [Vikings Strategy](https://app.lighter.xyz/public-pools/281474976690978) | `281474976690978` | Core | Inactive | 0 |

All 193 fixed-exposure products carry Directional leverage, Directional trading and Perpetual futures. They rebalance to a stated exposure in one market. Each had 720 qualified archive hours with zero observed positions and no returned position change in the review window.

### Fixed long exposure products

| Pool | Account index | Deployment | API status | TVL |
| --- | --- | --- | --- | --- |
| [ETH 3x long](https://app.lighter.xyz/public-pools/281474976710653) | `281474976710653` | Core | Active | 132.448746 |
| [XAG 2x long](https://app.lighter.xyz/public-pools/281474976647177) | `281474976647177` | Core | Active | 100 |
| [BTC 3x long](https://app.lighter.xyz/public-pools/281474976710651) | `281474976710651` | Core | Active | 98.379108 |
| [HYPE 2x long](https://app.lighter.xyz/public-pools/281474976710564) | `281474976710564` | Core | Active | 96.889454 |
| [DOGE 2x long](https://app.lighter.xyz/public-pools/281474976710647) | `281474976710647` | Core | Active | 12.554555 |
| [AVAX 2x long](https://app.lighter.xyz/public-pools/281474976710634) | `281474976710634` | Core | Active | 3.621078 |
| [WLFI 2x long](https://app.lighter.xyz/public-pools/281474976692955) | `281474976692955` | Core | Active | 3.383885 |
| [SOL 3x long](https://app.lighter.xyz/public-pools/281474976710649) | `281474976710649` | Core | Active | 1.759931 |
| [SUI 2x long](https://app.lighter.xyz/public-pools/281474976710606) | `281474976710606` | Core | Active | 0.93113 |
| [XPL 2x long](https://app.lighter.xyz/public-pools/281474976694732) | `281474976694732` | Core | Active | 0.858779 |
| [MKR 2x long](https://app.lighter.xyz/public-pools/281474976710542) | `281474976710542` | Core | Inactive | 0.822469 |
| [APT 2x long](https://app.lighter.xyz/public-pools/281474976710417) | `281474976710417` | Core | Active | 0.431925 |
| [XRP 2x long](https://app.lighter.xyz/public-pools/281474976710638) | `281474976710638` | Core | Active | 0.162423 |
| [TAO 2x long](https://app.lighter.xyz/public-pools/281474976710626) | `281474976710626` | Core | Active | 0.092101 |
| [PUMP 1x long](https://app.lighter.xyz/public-pools/281474976703226) | `281474976703226` | Core | Active | 0.012543 |
| [VIRTUAL 2x long](https://app.lighter.xyz/public-pools/281474976707000) | `281474976707000` | Core | Inactive | 0.010442 |
| [LINK 2x long](https://app.lighter.xyz/public-pools/281474976710636) | `281474976710636` | Core | Active | 0.006187 |
| [AVNT 2x long](https://app.lighter.xyz/public-pools/281474976687812) | `281474976687812` | Core | Active | 0.006162 |
| [FARTCOIN 2x long](https://app.lighter.xyz/public-pools/281474976710580) | `281474976710580` | Core | Active | 0.003937 |
| [WIF 2x long](https://app.lighter.xyz/public-pools/281474976710643) | `281474976710643` | Core | Inactive | 0.003023 |
| [AAVE 2x long](https://app.lighter.xyz/public-pools/281474976710544) | `281474976710544` | Core | Inactive | 0.002802 |
| [SHIB 2x long](https://app.lighter.xyz/public-pools/281474976710604) | `281474976710604` | Core | Active | 0.002414 |
| [WLD 2x long](https://app.lighter.xyz/public-pools/281474976710641) | `281474976710641` | Core | Inactive | 0.002092 |
| [PAXG 2x long](https://app.lighter.xyz/public-pools/281474976698602) | `281474976698602` | Core | Active | 0.001905 |
| [JUP 2x long](https://app.lighter.xyz/public-pools/281474976710560) | `281474976710560` | Core | Inactive | 0.001156 |
| [SPX 2x long](https://app.lighter.xyz/public-pools/281474976706998) | `281474976706998` | Core | Inactive | 0.001136 |
| [POPCAT 2x long](https://app.lighter.xyz/public-pools/281474976710576) | `281474976710576` | Core | Inactive | 0.000949 |
| [BONK 2x long](https://app.lighter.xyz/public-pools/281474976710602) | `281474976710602` | Core | Active | 0.000836 |
| [PEPE 2x long](https://app.lighter.xyz/public-pools/281474976710645) | `281474976710645` | Core | Inactive | 0.000754 |
| [BNB 2x long](https://app.lighter.xyz/public-pools/281474976710562) | `281474976710562` | Core | Active | 0.000357 |
| [UNI 2x long](https://app.lighter.xyz/public-pools/281474976710538) | `281474976710538` | Core | Active | 0.000341 |
| [ONDO 2x long](https://app.lighter.xyz/public-pools/281474976708831) | `281474976708831` | Core | Active | 0.000312 |
| [ADA 2x long](https://app.lighter.xyz/public-pools/281474976708413) | `281474976708413` | Core | Active | 0.000279 |
| [PENDLE 2x long](https://app.lighter.xyz/public-pools/281474976708833) | `281474976708833` | Core | Inactive | 0.00012 |
| [LTC 2x long](https://app.lighter.xyz/public-pools/281474976710315) | `281474976710315` | Core | Inactive | 0.000012 |
| [ASTER 2x long](https://app.lighter.xyz/public-pools/281474976687563) | `281474976687563` | Core | Active | 0.000006 |
| [ZRO 2x long](https://app.lighter.xyz/public-pools/281474976696956) | `281474976696956` | Core | Inactive | 0.000004 |
| [KAITO 2x long](https://app.lighter.xyz/public-pools/281474976710319) | `281474976710319` | Core | Active | 0.000003 |
| [ICP 2x long](https://app.lighter.xyz/public-pools/281474976641577) | `281474976641577` | Core | Active | 0.000002 |
| [BERA 2x long](https://app.lighter.xyz/public-pools/281474976710582) | `281474976710582` | Core | Inactive | 0.000002 |
| [SYRUP 2x long](https://app.lighter.xyz/public-pools/281474976706165) | `281474976706165` | Core | Active | 0.000001 |
| [STRK 2x long](https://app.lighter.xyz/public-pools/281474976641573) | `281474976641573` | Core | Active | 0 |
| [FIL 2x long](https://app.lighter.xyz/public-pools/281474976641575) | `281474976641575` | Core | Active | 0 |
| [MET 2x long](https://app.lighter.xyz/public-pools/281474976644848) | `281474976644848` | Core | Active | 0 |
| [XAU 2x long](https://app.lighter.xyz/public-pools/281474976647201) | `281474976647201` | Core | Inactive | 0 |
| [MONAD 2x long](https://app.lighter.xyz/public-pools/281474976656643) | `281474976656643` | Core | Inactive | 0 |
| [ZEC 2x long](https://app.lighter.xyz/public-pools/281474976664630) | `281474976664630` | Core | Active | 0 |
| [EDEN 2x long](https://app.lighter.xyz/public-pools/281474976665434) | `281474976665434` | Core | Inactive | 0 |
| [2Z 2x long](https://app.lighter.xyz/public-pools/281474976665436) | `281474976665436` | Core | Active | 0 |
| [FF 2x long](https://app.lighter.xyz/public-pools/281474976667674) | `281474976667674` | Core | Active | 0 |
| [APEX 2x long](https://app.lighter.xyz/public-pools/281474976670932) | `281474976670932` | Core | Active | 0 |
| [STBL 2x long](https://app.lighter.xyz/public-pools/281474976680298) | `281474976680298` | Core | Inactive | 0 |
| [0G 2x long](https://app.lighter.xyz/public-pools/281474976680300) | `281474976680300` | Core | Active | 0 |
| [TOSHI 2x long](https://app.lighter.xyz/public-pools/281474976687808) | `281474976687808` | Core | Inactive | 0 |
| [MYX 2x long](https://app.lighter.xyz/public-pools/281474976687810) | `281474976687810` | Core | Inactive | 0 |
| [SKY 2x long](https://app.lighter.xyz/public-pools/281474976687814) | `281474976687814` | Core | Active | 0 |
| [PYTH 2x long](https://app.lighter.xyz/public-pools/281474976691058) | `281474976691058` | Core | Active | 0 |
| [XMR 2x long](https://app.lighter.xyz/public-pools/281474976691060) | `281474976691060` | Core | Inactive | 0 |
| [LINEA 2x long](https://app.lighter.xyz/public-pools/281474976691921) | `281474976691921` | Core | Inactive | 0 |
| [DOLO 2x long](https://app.lighter.xyz/public-pools/281474976692287) | `281474976692287` | Core | Active | 0 |
| [NMR 2x long](https://app.lighter.xyz/public-pools/281474976692289) | `281474976692289` | Core | Active | 0 |
| [CRO 2x long](https://app.lighter.xyz/public-pools/281474976692348) | `281474976692348` | Core | Active | 0 |
| [YZY 2x long](https://app.lighter.xyz/public-pools/281474976695152) | `281474976695152` | Core | Inactive | 0 |
| [VVV 2x long](https://app.lighter.xyz/public-pools/281474976695499) | `281474976695499` | Core | Active | 0 |
| [MORPHO 2x long](https://app.lighter.xyz/public-pools/281474976695501) | `281474976695501` | Core | Inactive | 0 |
| [TIA 2x long](https://app.lighter.xyz/public-pools/281474976695503) | `281474976695503` | Core | Inactive | 0 |
| [USELESS 2x long](https://app.lighter.xyz/public-pools/281474976695505) | `281474976695505` | Core | Inactive | 0 |
| [AERO 2x long](https://app.lighter.xyz/public-pools/281474976696149) | `281474976696149` | Core | Inactive | 0 |
| [ETHFI 2x long](https://app.lighter.xyz/public-pools/281474976696151) | `281474976696151` | Core | Inactive | 0 |
| [GMX 2x long](https://app.lighter.xyz/public-pools/281474976696153) | `281474976696153` | Core | Inactive | 0 |
| [DYDX 2x long](https://app.lighter.xyz/public-pools/281474976696155) | `281474976696155` | Core | Inactive | 0 |
| [MNT 2x long](https://app.lighter.xyz/public-pools/281474976696157) | `281474976696157` | Core | Inactive | 0 |
| [HBAR 2x long](https://app.lighter.xyz/public-pools/281474976696958) | `281474976696958` | Core | Active | 0 |
| [BCH 2x long](https://app.lighter.xyz/public-pools/281474976696960) | `281474976696960` | Core | Inactive | 0 |
| [PROVE 2x long](https://app.lighter.xyz/public-pools/281474976697777) | `281474976697777` | Core | Active | 0 |
| [ZK 2x long](https://app.lighter.xyz/public-pools/281474976698539) | `281474976698539` | Core | Inactive | 0 |
| [OP 2x long](https://app.lighter.xyz/public-pools/281474976698541) | `281474976698541` | Core | Inactive | 0 |
| [LAUNCHCOIN 2x long](https://app.lighter.xyz/public-pools/281474976698543) | `281474976698543` | Core | Inactive | 0 |
| [ZORA 2x long](https://app.lighter.xyz/public-pools/281474976698545) | `281474976698545` | Core | Active | 0 |
| [GRASS 2x long](https://app.lighter.xyz/public-pools/281474976698547) | `281474976698547` | Core | Inactive | 0 |
| [RESOLV 2x long](https://app.lighter.xyz/public-pools/281474976698549) | `281474976698549` | Core | Active | 0 |
| [ARB 2x long](https://app.lighter.xyz/public-pools/281474976698597) | `281474976698597` | Core | Inactive | 0 |
| [EIGEN 2x long](https://app.lighter.xyz/public-pools/281474976698600) | `281474976698600` | Core | Active | 0 |
| [PENGU 2x long](https://app.lighter.xyz/public-pools/281474976698775) | `281474976698775` | Core | Inactive | 0 |
| [LDO 2x long](https://app.lighter.xyz/public-pools/281474976698777) | `281474976698777` | Core | Inactive | 0 |
| [TRX 2x long](https://app.lighter.xyz/public-pools/281474976706167) | `281474976706167` | Core | Active | 0 |
| [S 2x long](https://app.lighter.xyz/public-pools/281474976708411) | `281474976708411` | Core | Active | 0 |
| [CRV 2x long](https://app.lighter.xyz/public-pools/281474976708835) | `281474976708835` | Core | Active | 0 |
| [IP 2x long](https://app.lighter.xyz/public-pools/281474976710317) | `281474976710317` | Core | Inactive | 0 |
| [SEI 2x long](https://app.lighter.xyz/public-pools/281474976710415) | `281474976710415` | Core | Active | 0 |
| [ENA 2x long](https://app.lighter.xyz/public-pools/281474976710540) | `281474976710540` | Core | Inactive | 0 |
| [AI16Z 2x long](https://app.lighter.xyz/public-pools/281474976710578) | `281474976710578` | Core | Inactive | 0 |
| [FLOKI 2x long](https://app.lighter.xyz/public-pools/281474976710600) | `281474976710600` | Core | Inactive | 0 |
| [TRUMP 2x long](https://app.lighter.xyz/public-pools/281474976710609) | `281474976710609` | Core | Active | 0 |
| [POL 2x long](https://app.lighter.xyz/public-pools/281474976710624) | `281474976710624` | Core | Inactive | 0 |
| [TON 2x long](https://app.lighter.xyz/public-pools/281474976710628) | `281474976710628` | Core | Inactive | 0 |
| [DOT 2x long](https://app.lighter.xyz/public-pools/281474976710630) | `281474976710630` | Core | Inactive | 0 |
| [NEAR 2x long](https://app.lighter.xyz/public-pools/281474976710632) | `281474976710632` | Core | Inactive | 0 |

### Fixed short exposure products

| Pool | Account index | Deployment | API status | TVL |
| --- | --- | --- | --- | --- |
| [APT 2x short](https://app.lighter.xyz/public-pools/281474976710416) | `281474976710416` | Core | Active | 124.691608 |
| [BTC 3x short](https://app.lighter.xyz/public-pools/281474976710650) | `281474976710650` | Core | Active | 85.475938 |
| [ETH 3x short](https://app.lighter.xyz/public-pools/281474976710652) | `281474976710652` | Core | Active | 52.006206 |
| [ASTER 2x short](https://app.lighter.xyz/public-pools/281474976687562) | `281474976687562` | Core | Active | 13.021256 |
| [SOL 3x short](https://app.lighter.xyz/public-pools/281474976710648) | `281474976710648` | Core | Active | 12.884868 |
| [PUMP 1x short](https://app.lighter.xyz/public-pools/281474976703225) | `281474976703225` | Core | Active | 4.062566 |
| [DOGE 2x short](https://app.lighter.xyz/public-pools/281474976710646) | `281474976710646` | Core | Active | 3.59849 |
| [BONK 2x short](https://app.lighter.xyz/public-pools/281474976710601) | `281474976710601` | Core | Active | 1.720842 |
| [PENDLE 2x short](https://app.lighter.xyz/public-pools/281474976708832) | `281474976708832` | Core | Active | 0.728383 |
| [WLFI 2x short](https://app.lighter.xyz/public-pools/281474976692953) | `281474976692953` | Core | Active | 0.311236 |
| [PEPE 2x short](https://app.lighter.xyz/public-pools/281474976710644) | `281474976710644` | Core | Active | 0.075316 |
| [WLD 2x short](https://app.lighter.xyz/public-pools/281474976710640) | `281474976710640` | Core | Active | 0.044728 |
| [HYPE 2x short](https://app.lighter.xyz/public-pools/281474976710563) | `281474976710563` | Core | Inactive | 0.036083 |
| [TAO 2x short](https://app.lighter.xyz/public-pools/281474976710625) | `281474976710625` | Core | Active | 0.031099 |
| [NMR 2x short](https://app.lighter.xyz/public-pools/281474976692288) | `281474976692288` | Core | Active | 0.01956 |
| [POL 2x short](https://app.lighter.xyz/public-pools/281474976710623) | `281474976710623` | Core | Active | 0.007576 |
| [TRUMP 2x short](https://app.lighter.xyz/public-pools/281474976710608) | `281474976710608` | Core | Active | 0.004326 |
| [FARTCOIN 2x short](https://app.lighter.xyz/public-pools/281474976710579) | `281474976710579` | Core | Active | 0.003649 |
| [ICP 2x short](https://app.lighter.xyz/public-pools/281474976641576) | `281474976641576` | Core | Active | 0.002623 |
| [ZEC 2x short](https://app.lighter.xyz/public-pools/281474976664629) | `281474976664629` | Core | Active | 0.001003 |
| [POPCAT 2x short](https://app.lighter.xyz/public-pools/281474976710575) | `281474976710575` | Core | Inactive | 0.000843 |
| [UNI 2x short](https://app.lighter.xyz/public-pools/281474976710537) | `281474976710537` | Core | Active | 0.000035 |
| [SUI 2x short](https://app.lighter.xyz/public-pools/281474976710605) | `281474976710605` | Core | Active | 0.000001 |
| [STRK 2x short](https://app.lighter.xyz/public-pools/281474976641572) | `281474976641572` | Core | Active | 0 |
| [FIL 2x short](https://app.lighter.xyz/public-pools/281474976641574) | `281474976641574` | Core | Active | 0 |
| [XAG 2x short](https://app.lighter.xyz/public-pools/281474976647176) | `281474976647176` | Core | Inactive | 0 |
| [XAU 2x short](https://app.lighter.xyz/public-pools/281474976647178) | `281474976647178` | Core | Inactive | 0 |
| [MONAD 2x short](https://app.lighter.xyz/public-pools/281474976656642) | `281474976656642` | Core | Inactive | 0 |
| [EDEN 2x short](https://app.lighter.xyz/public-pools/281474976665433) | `281474976665433` | Core | Active | 0 |
| [2Z 2x short](https://app.lighter.xyz/public-pools/281474976665435) | `281474976665435` | Core | Active | 0 |
| [FF 2x short](https://app.lighter.xyz/public-pools/281474976667673) | `281474976667673` | Core | Active | 0 |
| [APEX 2x short](https://app.lighter.xyz/public-pools/281474976670931) | `281474976670931` | Core | Active | 0 |
| [0G 2x short](https://app.lighter.xyz/public-pools/281474976680299) | `281474976680299` | Core | Active | 0 |
| [TOSHI 2x short](https://app.lighter.xyz/public-pools/281474976687807) | `281474976687807` | Core | Active | 0 |
| [MYX 2x short](https://app.lighter.xyz/public-pools/281474976687809) | `281474976687809` | Core | Inactive | 0 |
| [AVNT 2x short](https://app.lighter.xyz/public-pools/281474976687811) | `281474976687811` | Core | Inactive | 0 |
| [SKY 2x short](https://app.lighter.xyz/public-pools/281474976687813) | `281474976687813` | Core | Inactive | 0 |
| [PYTH 2x short](https://app.lighter.xyz/public-pools/281474976691057) | `281474976691057` | Core | Active | 0 |
| [XMR 2x short](https://app.lighter.xyz/public-pools/281474976691059) | `281474976691059` | Core | Inactive | 0 |
| [LINEA 2x short](https://app.lighter.xyz/public-pools/281474976691920) | `281474976691920` | Core | Inactive | 0 |
| [DOLO 2x short](https://app.lighter.xyz/public-pools/281474976692286) | `281474976692286` | Core | Active | 0 |
| [CRO 2x short](https://app.lighter.xyz/public-pools/281474976692347) | `281474976692347` | Core | Active | 0 |
| [XPL 2x short](https://app.lighter.xyz/public-pools/281474976694731) | `281474976694731` | Core | Active | 0 |
| [YZY 2x short](https://app.lighter.xyz/public-pools/281474976695151) | `281474976695151` | Core | Inactive | 0 |
| [VVV 2x short](https://app.lighter.xyz/public-pools/281474976695498) | `281474976695498` | Core | Active | 0 |
| [MORPHO 2x short](https://app.lighter.xyz/public-pools/281474976695500) | `281474976695500` | Core | Inactive | 0 |
| [TIA 2x short](https://app.lighter.xyz/public-pools/281474976695502) | `281474976695502` | Core | Inactive | 0 |
| [USELESS 2x short](https://app.lighter.xyz/public-pools/281474976695504) | `281474976695504` | Core | Active | 0 |
| [AERO 2x short](https://app.lighter.xyz/public-pools/281474976696148) | `281474976696148` | Core | Inactive | 0 |
| [ETHFI 2x short](https://app.lighter.xyz/public-pools/281474976696150) | `281474976696150` | Core | Inactive | 0 |
| [GMX 2x short](https://app.lighter.xyz/public-pools/281474976696152) | `281474976696152` | Core | Inactive | 0 |
| [DYDX 2x short](https://app.lighter.xyz/public-pools/281474976696154) | `281474976696154` | Core | Inactive | 0 |
| [MNT 2x short](https://app.lighter.xyz/public-pools/281474976696156) | `281474976696156` | Core | Inactive | 0 |
| [ZRO 2x short](https://app.lighter.xyz/public-pools/281474976696955) | `281474976696955` | Core | Inactive | 0 |
| [HBAR 2x short](https://app.lighter.xyz/public-pools/281474976696957) | `281474976696957` | Core | Active | 0 |
| [BCH 2x short](https://app.lighter.xyz/public-pools/281474976696959) | `281474976696959` | Core | Inactive | 0 |
| [PROVE 2x short](https://app.lighter.xyz/public-pools/281474976697776) | `281474976697776` | Core | Active | 0 |
| [ZK 2x short](https://app.lighter.xyz/public-pools/281474976698538) | `281474976698538` | Core | Inactive | 0 |
| [OP 2x short](https://app.lighter.xyz/public-pools/281474976698540) | `281474976698540` | Core | Active | 0 |
| [LAUNCHCOIN 2x short](https://app.lighter.xyz/public-pools/281474976698542) | `281474976698542` | Core | Active | 0 |
| [ZORA 2x short](https://app.lighter.xyz/public-pools/281474976698544) | `281474976698544` | Core | Inactive | 0 |
| [GRASS 2x short](https://app.lighter.xyz/public-pools/281474976698546) | `281474976698546` | Core | Inactive | 0 |
| [RESOLV 2x short](https://app.lighter.xyz/public-pools/281474976698548) | `281474976698548` | Core | Active | 0 |
| [ARB 2x short](https://app.lighter.xyz/public-pools/281474976698596) | `281474976698596` | Core | Active | 0 |
| [PAXG 2x short](https://app.lighter.xyz/public-pools/281474976698601) | `281474976698601` | Core | Active | 0 |
| [PENGU 2x short](https://app.lighter.xyz/public-pools/281474976698774) | `281474976698774` | Core | Active | 0 |
| [LDO 2x short](https://app.lighter.xyz/public-pools/281474976698776) | `281474976698776` | Core | Inactive | 0 |
| [SYRUP 2x short](https://app.lighter.xyz/public-pools/281474976706164) | `281474976706164` | Core | Active | 0 |
| [TRX 2x short](https://app.lighter.xyz/public-pools/281474976706166) | `281474976706166` | Core | Active | 0 |
| [SPX 2x short](https://app.lighter.xyz/public-pools/281474976706997) | `281474976706997` | Core | Inactive | 0 |
| [VIRTUAL 2x short](https://app.lighter.xyz/public-pools/281474976706999) | `281474976706999` | Core | Inactive | 0 |
| [S 2x short](https://app.lighter.xyz/public-pools/281474976708410) | `281474976708410` | Core | Active | 0 |
| [ADA 2x short](https://app.lighter.xyz/public-pools/281474976708412) | `281474976708412` | Core | Active | 0 |
| [ONDO 2x short](https://app.lighter.xyz/public-pools/281474976708830) | `281474976708830` | Core | Active | 0 |
| [CRV 2x short](https://app.lighter.xyz/public-pools/281474976708834) | `281474976708834` | Core | Active | 0 |
| [LTC 2x short](https://app.lighter.xyz/public-pools/281474976710314) | `281474976710314` | Core | Active | 0 |
| [IP 2x short](https://app.lighter.xyz/public-pools/281474976710316) | `281474976710316` | Core | Inactive | 0 |
| [KAITO 2x short](https://app.lighter.xyz/public-pools/281474976710318) | `281474976710318` | Core | Active | 0 |
| [SEI 2x short](https://app.lighter.xyz/public-pools/281474976710414) | `281474976710414` | Core | Active | 0 |
| [ENA 2x short](https://app.lighter.xyz/public-pools/281474976710539) | `281474976710539` | Core | Inactive | 0 |
| [MKR 2x short](https://app.lighter.xyz/public-pools/281474976710541) | `281474976710541` | Core | Inactive | 0 |
| [AAVE 2x short](https://app.lighter.xyz/public-pools/281474976710543) | `281474976710543` | Core | Inactive | 0 |
| [JUP 2x short](https://app.lighter.xyz/public-pools/281474976710559) | `281474976710559` | Core | Inactive | 0 |
| [BNB 2x short](https://app.lighter.xyz/public-pools/281474976710561) | `281474976710561` | Core | Active | 0 |
| [AI16Z 2x short](https://app.lighter.xyz/public-pools/281474976710577) | `281474976710577` | Core | Inactive | 0 |
| [BERA 2x short](https://app.lighter.xyz/public-pools/281474976710581) | `281474976710581` | Core | Inactive | 0 |
| [FLOKI 2x short](https://app.lighter.xyz/public-pools/281474976710599) | `281474976710599` | Core | Inactive | 0 |
| [SHIB 2x short](https://app.lighter.xyz/public-pools/281474976710603) | `281474976710603` | Core | Active | 0 |
| [TON 2x short](https://app.lighter.xyz/public-pools/281474976710627) | `281474976710627` | Core | Inactive | 0 |
| [DOT 2x short](https://app.lighter.xyz/public-pools/281474976710629) | `281474976710629` | Core | Inactive | 0 |
| [NEAR 2x short](https://app.lighter.xyz/public-pools/281474976710631) | `281474976710631` | Core | Active | 0 |
| [AVAX 2x short](https://app.lighter.xyz/public-pools/281474976710633) | `281474976710633` | Core | Inactive | 0 |
| [LINK 2x short](https://app.lighter.xyz/public-pools/281474976710635) | `281474976710635` | Core | Inactive | 0 |
| [XRP 2x short](https://app.lighter.xyz/public-pools/281474976710637) | `281474976710637` | Core | Active | 0 |
| [WIF 2x short](https://app.lighter.xyz/public-pools/281474976710642) | `281474976710642` | Core | Inactive | 0 |

## Archive coverage

The table shows pools with observed positions or position changes, a missing hourly value or a detailed position reconciliation. The remaining pools each had 720 qualified hours with zero observed positions and no returned position change.

| Pool | Qualified hours | Invested hours | Two-sided hours | Position change returned | Reconciled hours | Median long / short markets | Median top-five share |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | 719 | 719 | 719 | Yes | — | — / — | — |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | 720 | 720 | 720 | Yes | 720 | 26 / 28 | 29.1346% |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | 720 | 720 | 720 | No | 720 | 6 / 3 | 89.7248% |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | 720 | 720 | 0 | Yes | 720 | 1 / 0 | 100% |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | 720 | 720 | 0 | Yes | 680 | 4 / 0 | 100% |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | 720 | 632 | 0 | Yes | 720 | 1 / 0 | 100% |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | 720 | 656 | 5 | Yes | 720 | 2 / 0 | 100% |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | 720 | 720 | 720 | Yes | 720 | 15 / 2 | 67.8072% |
| [Experimental Liquidity Provider (XLP)](https://app.lighter.xyz/public-pools/281474976680784) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | 720 | 612 | 1 | Yes | 720 | 4 / 0 | 100% |
| [Centaur Strategies: Lazy Villager](https://app.lighter.xyz/public-pools/281474976488856) | 720 | 299 | 253 | Yes | 720 | 0 / 0 | 91.6977% |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | 720 | 720 | 0 | Yes | 720 | 0 / 4 | 100% |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | 720 | 720 | 277 | Yes | 720 | 6 / 0 | 97.1361% |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | 720 | 261 | 0 | Yes | 720 | 0 / 0 | 100% |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | 720 | 650 | 0 | Yes | 720 | 3 / 0 | 100% |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | 720 | 720 | 0 | Yes | 720 | 3 / 0 | 100% |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | 720 | 720 | 720 | Yes | 720 | 5 / 4 | 80.1589% |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | 720 | 720 | 0 | Yes | 720 | 7 / 0 | 85.3936% |
| [K Pool](https://app.lighter.xyz/public-pools/281474976680237) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | 720 | 720 | 0 | No | 720 | 2 / 0 | 100% |
| [Algorithmic L/S \| Big Brain Research](https://app.lighter.xyz/public-pools/281474976690488) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [systematic breakout long-only](https://app.lighter.xyz/public-pools/281474976496745) | 720 | 361 | 0 | Yes | 720 | 1 / 0 | 100% |
| [AlphaMIX Global](https://app.lighter.xyz/public-pools/281474976415828) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | 720 | 720 | 720 | Yes | 720 | 34 / 29 | 14.0938% |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | 720 | 67 | 0 | Yes | 720 | 0 / 0 | 100% |
| [Silentist Public 2nd](https://app.lighter.xyz/public-pools/281474976436927) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [EdgePool](https://app.lighter.xyz/public-pools/281474976466567) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [LAZY Pool](https://app.lighter.xyz/public-pools/281474976473729) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | 720 | 720 | 720 | Yes | 720 | 26 / 20 | 19.9982% |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | 720 | 0 | 0 | No | 720 | 0 / 0 | — |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | 720 | 646 | 499 | Yes | 720 | 5 / 5 | 90.8239% |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | 720 | 720 | 545 | Yes | 720 | 1 / 1 | 100% |
| [LIT ALPHA POOL](https://app.lighter.xyz/public-pools/281474976490593) | 720 | 386 | 0 | Yes | 720 | 1 / 0 | 100% |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | 720 | 508 | 495 | Yes | 716 | 16 / 3 | 46.766% |
| [scanner.money - TrendSend](https://app.lighter.xyz/public-pools/281474976490589) | 720 | 720 | 0 | Yes | 720 | 1 / 0 | 100% |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | 720 | 720 | 720 | Yes | 720 | 26 / 36 | 42.5622% |

## Portfolio exposure

### Native position history

Each observation pairs native position values with positive equity from the same snapshot. A two-sided snapshot has at least 5% of equity on each side.

| Pool | Snapshots / days | Invested snapshots | Two-sided share | Median long / short | Median gross / net | 90th-percentile gross | Median long / short markets | Median top-five share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | 172 / 30 | 172 | 29.6512% | 4.2073% / 7.2938% | 11.5013% / -2.9679% | 15.9209% | 93 / 86 | 53.5386% |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | 172 / 30 | 172 | 100% | 156.6745% / 138.8818% | 294.629% / 16.8199% | 304.0753% | 26 / 28 | 29.2739% |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | 172 / 30 | 172 | 100% | 434.0807% / 432.6582% | 865.388% / 3.2791% | 960.8925% | 6 / 3 | 89.7222% |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | 172 / 30 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | 172 / 30 | 172 | 0% | 124.5116% / 0% | 124.5116% / 124.5116% | 126.991% | 1 / 0 | 100% |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | 172 / 30 | 172 | 0% | 18.9774% / 0% | 18.9774% / 18.9774% | 57.0181% | 4 / 0 | 100% |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | 172 / 30 | 152 | 0% | 2.7226% / 0% | 2.7226% / 2.7226% | 72.41% | 1 / 0 | 100% |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | 172 / 30 | 156 | 0% | 0.9536% / 0% | 1.0121% / 0.9536% | 12.8309% | 2 / 0 | 100% |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | 172 / 30 | 172 | 100% | 297.8524% / 17.4795% | 315.1735% / 280.0959% | 355.8499% | 15 / 2 | 67.7723% |
| [Experimental Liquidity Provider (XLP)](https://app.lighter.xyz/public-pools/281474976680784) | 172 / 30 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | 172 / 30 | 146 | 0% | 116.645% / 0% | 116.645% / 116.645% | 146.074% | 4 / 0 | 100% |
| [Centaur Strategies: Lazy Villager](https://app.lighter.xyz/public-pools/281474976488856) | 172 / 30 | 75 | 36.6279% | 0% / 0% | 0% / 0% | 127.8206% | 0 / 0 | 91.7016% |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | 172 / 30 | 172 | 0% | 0% / 100.0684% | 100.0684% / -100.0684% | 107.6975% | 0 / 4 | 100% |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | 172 / 30 | 172 | 36.6279% | 65.5456% / 0% | 66.4887% / 65.5456% | 134.667% | 6 / 0 | 97.1624% |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | 120 / 22 | 58 | 0% | 0% / 0% | 0% / 0% | 6.5886% | 0 / 0 | 100% |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | 172 / 30 | 156 | 0% | 440.2867% / 0% | 440.2867% / 440.2867% | 1,596.9957% | 3 / 0 | 100% |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | 172 / 30 | 172 | 0% | 135.8701% / 0% | 135.8701% / 135.8701% | 137.0754% | 3 / 0 | 100% |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | 172 / 30 | 172 | 100% | 81.7834% / 93.7783% | 210.1186% / 2.2634% | 263.2122% | 5 / 5 | 69.3827% |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | 172 / 30 | 172 | 0% | 90.5724% / 0% | 90.5724% / 90.5724% | 104.0972% | 7 / 0 | 85.2971% |
| [K Pool](https://app.lighter.xyz/public-pools/281474976680237) | 172 / 30 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | 172 / 30 | 172 | 0% | 93.9006% / 0% | 93.9006% / 93.9006% | 94.001% | 2 / 0 | 100% |
| [Algorithmic L/S \| Big Brain Research](https://app.lighter.xyz/public-pools/281474976690488) | 172 / 30 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [systematic breakout long-only](https://app.lighter.xyz/public-pools/281474976496745) | 172 / 30 | 89 | 0% | 17.4382% / 0% | 17.4382% / 17.4382% | 18.7233% | 1 / 0 | 100% |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | 172 / 30 | 172 | 100% | 193.2473% / 185.8018% | 378.9893% / 8.0747% | 397.1644% | 33.5 / 29 | 14.1183% |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | 51 / 11 | 12 | 0% | 0% / 0% | 0% / 0% | 181.5266% | 0 / 0 | 100% |
| [Silentist Public 2nd](https://app.lighter.xyz/public-pools/281474976436927) | 49 / 9 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [EdgePool](https://app.lighter.xyz/public-pools/281474976466567) | 120 / 22 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [LAZY Pool](https://app.lighter.xyz/public-pools/281474976473729) | 134 / 24 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | 133 / 23 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [KUY Strategy](https://app.lighter.xyz/public-pools/281474976624682) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | 154 / 27 | 154 | 77.9221% | 656.4854% / 137.8991% | 789.0026% / 484.5971% | 1,654.7567% | 5 / 5 | 90.7918% |
| [Popo BTC Only Low Frequency Algo](https://app.lighter.xyz/public-pools/281474976623846) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [PAWNZI ALGO – PROFITABLE (REAL)](https://app.lighter.xyz/public-pools/281474976680234) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [BIT755](https://app.lighter.xyz/public-pools/281474976563230) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | 172 / 30 | 172 | 7.5581% | 67.5659% / 2.1162% | 69.679% / 62.9902% | 93.1093% | 1 / 1 | 100% |
| [HYPE 2x long](https://app.lighter.xyz/public-pools/281474976710564) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [BTC 3x short](https://app.lighter.xyz/public-pools/281474976710650) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [ETH 3x short](https://app.lighter.xyz/public-pools/281474976710652) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Alleged Alpha](https://app.lighter.xyz/public-pools/281474976625478) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | 127 / 22 | 122 | 80.315% | 55.7786% / 12.2435% | 70.5819% / 43.366% | 100.4529% | 18 / 4 | 46.8578% |
| [DSC - L Strategy](https://app.lighter.xyz/public-pools/281474976644517) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [SOL 3x short](https://app.lighter.xyz/public-pools/281474976710648) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [PHP Trading Desk (BTC/USDC)](https://app.lighter.xyz/public-pools/281474976593834) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Dragon](https://app.lighter.xyz/public-pools/281474976534461) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Cross-Sectional-Relative-Strength](https://app.lighter.xyz/public-pools/281474976656474) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Syzygy Strategy](https://app.lighter.xyz/public-pools/281474976539113) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [rekt](https://app.lighter.xyz/public-pools/281474976623731) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Failure](https://app.lighter.xyz/public-pools/281474976503569) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Better Than Yours](https://app.lighter.xyz/public-pools/281474976642928) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [LINK 2x long](https://app.lighter.xyz/public-pools/281474976710636) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Symphony](https://app.lighter.xyz/public-pools/281474976626973) | 1 / 1 | 0 | 0% | 0% / 0% | 0% / 0% | 0% | 0 / 0 | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | 172 / 30 | 172 | 100% | 49.7368% / 87.914% | 125.0693% / -29.3755% | 255.9008% | 26 / 36 | 42.1862% |

### Positions on 19 November

The table includes all pools with positive equity in the native account response.

| Pool | Long exposure | Short exposure | Gross exposure |
| --- | --- | --- | --- |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | 7.2045% | 11.7086% | 18.9131% |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | 142.3654% | 148.0338% | 290.3991% |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | 314.0173% | 297.2443% | 611.2617% |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | 0% | 0% | 0% |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | 643.1107% | 0% | 643.1107% |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | 31.466% | 0% | 31.466% |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | 1.2085% | 0% | 1.2085% |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | 0.1078% | 0% | 0.1078% |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | 386.896% | 0% | 386.896% |
| [Experimental Liquidity Provider (XLP)](https://app.lighter.xyz/public-pools/281474976680784) | 0% | 0% | 0% |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | 93.1819% | 0% | 93.1819% |
| [Centaur Strategies: Lazy Villager](https://app.lighter.xyz/public-pools/281474976488856) | 0% | 0% | 0% |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | 0% | 75.2013% | 75.2013% |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | 39.6128% | 0% | 39.6128% |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | 1.2065% | 0% | 1.2065% |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | 0% | 0% | 0% |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | 37.2498% | 0% | 37.2498% |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | 70.8844% | 142.8488% | 213.7332% |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | 79.1275% | 0% | 79.1275% |
| [K Pool](https://app.lighter.xyz/public-pools/281474976680237) | 0% | 0% | 0% |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | 289.0091% | 0% | 289.0091% |
| [Algorithmic L/S \| Big Brain Research](https://app.lighter.xyz/public-pools/281474976690488) | 0% | 0% | 0% |
| [systematic breakout long-only](https://app.lighter.xyz/public-pools/281474976496745) | 0% | 0% | 0% |
| [AlphaMIX Global](https://app.lighter.xyz/public-pools/281474976415828) | 98.224% | 0% | 98.224% |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | 190.3007% | 163.9614% | 354.2621% |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | 0% | 0% | 0% |
| [Silentist Public 2nd](https://app.lighter.xyz/public-pools/281474976436927) | 0% | 0% | 0% |
| [EdgePool](https://app.lighter.xyz/public-pools/281474976466567) | 0% | 0% | 0% |
| [LAZY Pool](https://app.lighter.xyz/public-pools/281474976473729) | 0% | 0% | 0% |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | 159.7425% | 158.8466% | 318.5891% |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | 0% | 0% | 0% |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | 0% | 0% | 0% |
| [GCB Capital](https://app.lighter.xyz/public-pools/281474976690376) | 0% | 0% | 0% |
| [Hanabi pool](https://app.lighter.xyz/public-pools/281474976678968) | 0% | 0% | 0% |
| [DTK Pool](https://app.lighter.xyz/public-pools/281474976644086) | 0% | 0% | 0% |
| [KUY Strategy](https://app.lighter.xyz/public-pools/281474976624682) | 0% | 0% | 0% |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | 0% | 0% | 0% |
| [Index of Profitable Traders \| CloneX (Depreciated)](https://app.lighter.xyz/public-pools/281474976690931) | 0% | 0% | 0% |
| [Popo BTC Only Low Frequency Algo](https://app.lighter.xyz/public-pools/281474976623846) | 0% | 0% | 0% |
| [Pikachu Pool](https://app.lighter.xyz/public-pools/281474976643789) | 0% | 0% | 0% |
| [Vorcaro](https://app.lighter.xyz/public-pools/281474976621130) | 0% | 0% | 0% |
| [PAWNZI ALGO – PROFITABLE (REAL)](https://app.lighter.xyz/public-pools/281474976680234) | 0% | 0% | 0% |
| [SMTrading (Temporarily Closed)](https://app.lighter.xyz/public-pools/281474976680682) | 0% | 0% | 0% |
| [BIT755](https://app.lighter.xyz/public-pools/281474976563230) | 0% | 0% | 0% |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | 121.4021% | 0% | 121.4021% |
| [ETH 3x long](https://app.lighter.xyz/public-pools/281474976710653) | 0% | 0% | 0% |
| [Trust me bro (diditrading)](https://app.lighter.xyz/public-pools/281474976690774) | 0% | 0% | 0% |
| [APT 2x short](https://app.lighter.xyz/public-pools/281474976710416) | 0% | 0% | 0% |
| [XAG 2x long](https://app.lighter.xyz/public-pools/281474976647177) | 0% | 0% | 0% |
| [BTC 3x long](https://app.lighter.xyz/public-pools/281474976710651) | 0% | 0% | 0% |
| [HYPE 2x long](https://app.lighter.xyz/public-pools/281474976710564) | 0% | 0% | 0% |
| [Lighter Quantum](https://app.lighter.xyz/public-pools/281474976623571) | 0% | 0% | 0% |
| [BTC 3x short](https://app.lighter.xyz/public-pools/281474976710650) | 0% | 0% | 0% |
| [LIT ALPHA POOL](https://app.lighter.xyz/public-pools/281474976490593) | 0% | 0% | 0% |
| [old-pool](https://app.lighter.xyz/public-pools/281474976688801) | 0% | 0% | 0% |
| [Soufflé Pool](https://app.lighter.xyz/public-pools/281474976680506) | 0% | 0% | 0% |
| [ETH 3x short](https://app.lighter.xyz/public-pools/281474976710652) | 0% | 0% | 0% |
| [paba and amigos](https://app.lighter.xyz/public-pools/281474976689945) | 0% | 0% | 0% |
| [Pool](https://app.lighter.xyz/public-pools/281474976644417) | 0% | 0% | 0% |
| [Alleged Alpha](https://app.lighter.xyz/public-pools/281474976625478) | 0% | 0% | 0% |
| [Capital Vault](https://app.lighter.xyz/public-pools/281474976690600) | 0% | 0% | 0% |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | 0% | 0% | 0% |
| [scanner.money - TrendSend](https://app.lighter.xyz/public-pools/281474976490589) | 0% | 0% | 0% |
| [Goon Cave](https://app.lighter.xyz/public-pools/281474976656239) | 0% | 0% | 0% |
| [DSC - L Strategy](https://app.lighter.xyz/public-pools/281474976644517) | 0% | 0% | 0% |
| [ASTER 2x short](https://app.lighter.xyz/public-pools/281474976687562) | 0% | 0% | 0% |
| [SOL 3x short](https://app.lighter.xyz/public-pools/281474976710648) | 0% | 0% | 0% |
| [DOGE 2x long](https://app.lighter.xyz/public-pools/281474976710647) | 0% | 0% | 0% |
| [ABCapital](https://app.lighter.xyz/public-pools/281474976678339) | 0% | 0% | 0% |
| [PHP Trading Desk (BTC/USDC)](https://app.lighter.xyz/public-pools/281474976593834) | 0% | 0% | 0% |
| [Dragon](https://app.lighter.xyz/public-pools/281474976534461) | 0% | 0% | 0% |
| [Cross-Sectional-Relative-Strength](https://app.lighter.xyz/public-pools/281474976656474) | 0% | 0% | 0% |
| [Aesthetic Pool](https://app.lighter.xyz/public-pools/281474976644611) | 0% | 0% | 0% |
| [BTC Trading Pool](https://app.lighter.xyz/public-pools/281474976644793) | 0% | 0% | 0% |
| [lefty’s btc pool](https://app.lighter.xyz/public-pools/281474976644615) | 0% | 0% | 0% |
| [Bellagio](https://app.lighter.xyz/public-pools/281474976690371) | 0% | 0% | 0% |
| [cp0x Delta Majors](https://app.lighter.xyz/public-pools/281474976644089) | 0% | 0% | 0% |
| [Popo Pool](https://app.lighter.xyz/public-pools/281474976690411) | 0% | 0% | 0% |
| [Perpetua](https://app.lighter.xyz/public-pools/281474976644635) | 0% | 0% | 0% |
| [Quant + Discretionary](https://app.lighter.xyz/public-pools/281474976644732) | 0% | 0% | 0% |
| [ffadff](https://app.lighter.xyz/public-pools/281474976669598) | 0% | 0% | 0% |
| [VB Trading](https://app.lighter.xyz/public-pools/281474976642013) | 0% | 0% | 0% |
| [Syzygy Strategy](https://app.lighter.xyz/public-pools/281474976539113) | 0% | 0% | 0% |
| [Short Coins](https://app.lighter.xyz/public-pools/281474976666853) | 0% | 0% | 0% |
| [Shrimp Liquidity Provider (SLP)](https://app.lighter.xyz/public-pools/281474976666704) | 0% | 0% | 0% |
| [PUMP 1x short](https://app.lighter.xyz/public-pools/281474976703225) | 0% | 0% | 0% |
| [Shadow](https://app.lighter.xyz/public-pools/281474976643341) | 0% | 0% | 0% |
| [AVAX 2x long](https://app.lighter.xyz/public-pools/281474976710634) | 0% | 0% | 0% |
| [DOGE 2x short](https://app.lighter.xyz/public-pools/281474976710646) | 0% | 0% | 0% |
| [WLFI 2x long](https://app.lighter.xyz/public-pools/281474976692955) | 0% | 0% | 0% |
| [Ghost](https://app.lighter.xyz/public-pools/281474976688577) | 0% | 0% | 0% |
| [rekt](https://app.lighter.xyz/public-pools/281474976623731) | 0% | 0% | 0% |
| [SOL 3x long](https://app.lighter.xyz/public-pools/281474976710649) | 0% | 0% | 0% |
| [BONK 2x short](https://app.lighter.xyz/public-pools/281474976710601) | 0% | 0% | 0% |
| [Experimental Pool](https://app.lighter.xyz/public-pools/281474976644304) | 0% | 0% | 0% |
| [alt mania](https://app.lighter.xyz/public-pools/281474976644562) | 0% | 0% | 0% |
| [SUI 2x long](https://app.lighter.xyz/public-pools/281474976710606) | 0% | 0% | 0% |
| [XPL 2x long](https://app.lighter.xyz/public-pools/281474976694732) | 0% | 0% | 0% |
| [MKR 2x long](https://app.lighter.xyz/public-pools/281474976710542) | 0% | 0% | 0% |
| [Vegeta's Ultra Ego Pool](https://app.lighter.xyz/public-pools/281474976675532) | 0% | 0% | 0% |
| [PENDLE 2x short](https://app.lighter.xyz/public-pools/281474976708832) | 0% | 0% | 0% |
| [HLP](https://app.lighter.xyz/public-pools/281474976643920) | 0% | 0% | 0% |
| [APT 2x long](https://app.lighter.xyz/public-pools/281474976710417) | 0% | 0% | 0% |
| [WLFI 2x short](https://app.lighter.xyz/public-pools/281474976692953) | 0% | 0% | 0% |
| [BTC H1 EMA Strategy](https://app.lighter.xyz/public-pools/281474976656541) | 0% | 0% | 0% |
| [XRP 2x long](https://app.lighter.xyz/public-pools/281474976710638) | 0% | 0% | 0% |
| [Dead pool](https://app.lighter.xyz/public-pools/281474976680683) | 0% | 0% | 0% |
| [Valkyrie of the Faint Smile( L/S)](https://app.lighter.xyz/public-pools/281474976651220) | 0% | 0% | 0% |
| [TAO 2x long](https://app.lighter.xyz/public-pools/281474976710626) | 0% | 0% | 0% |
| [PEPE 2x short](https://app.lighter.xyz/public-pools/281474976710644) | 0% | 0% | 0% |
| [WLD 2x short](https://app.lighter.xyz/public-pools/281474976710640) | 0% | 0% | 0% |
| [Failure](https://app.lighter.xyz/public-pools/281474976503569) | 0% | 0% | 0% |
| [HYPE 2x short](https://app.lighter.xyz/public-pools/281474976710563) | 0% | 0% | 0% |
| [TAO 2x short](https://app.lighter.xyz/public-pools/281474976710625) | 0% | 0% | 0% |
| [NMR 2x short](https://app.lighter.xyz/public-pools/281474976692288) | 0% | 0% | 0% |
| [Better Than Yours](https://app.lighter.xyz/public-pools/281474976642928) | 0% | 0% | 0% |
| [PUMP 1x long](https://app.lighter.xyz/public-pools/281474976703226) | 0% | 0% | 0% |
| [Moonshot](https://app.lighter.xyz/public-pools/281474976638939) | 0% | 0% | 0% |
| [VIRTUAL 2x long](https://app.lighter.xyz/public-pools/281474976707000) | 0% | 0% | 0% |
| [POL 2x short](https://app.lighter.xyz/public-pools/281474976710623) | 0% | 0% | 0% |
| [BTC/ETH x20 LONG](https://app.lighter.xyz/public-pools/281474976637687) | 0% | 0% | 0% |
| [LINK 2x long](https://app.lighter.xyz/public-pools/281474976710636) | 0% | 0% | 0% |
| [AVNT 2x long](https://app.lighter.xyz/public-pools/281474976687812) | 0% | 0% | 0% |
| [FastBit Pool](https://app.lighter.xyz/public-pools/281474976643399) | 0% | 0% | 0% |
| [TRUMP 2x short](https://app.lighter.xyz/public-pools/281474976710608) | 0% | 0% | 0% |
| [FARTCOIN 2x long](https://app.lighter.xyz/public-pools/281474976710580) | 0% | 0% | 0% |
| [Majors Correlated Pool!](https://app.lighter.xyz/public-pools/281474976626327) | 0% | 0% | 0% |
| [FARTCOIN 2x short](https://app.lighter.xyz/public-pools/281474976710579) | 0% | 0% | 0% |
| [BULBUL Vault](https://app.lighter.xyz/public-pools/281474976687595) | 0% | 0% | 0% |
| [Long BTC/ETH/HYPE + Gold](https://app.lighter.xyz/public-pools/281474976644427) | 0% | 0% | 0% |
| [Aaa](https://app.lighter.xyz/public-pools/281474976663862) | 0% | 0% | 0% |
| [WIF 2x long](https://app.lighter.xyz/public-pools/281474976710643) | 0% | 0% | 0% |
| [AAVE 2x long](https://app.lighter.xyz/public-pools/281474976710544) | 0% | 0% | 0% |
| [YOLO Pool](https://app.lighter.xyz/public-pools/281474976689753) | 0% | 0% | 0% |
| [Xiaohei](https://app.lighter.xyz/public-pools/281474976644412) | 0% | 0% | 0% |
| [ICP 2x short](https://app.lighter.xyz/public-pools/281474976641576) | 0% | 0% | 0% |
| [Vui Pool](https://app.lighter.xyz/public-pools/281474976689795) | 0% | 0% | 0% |
| [SHIB 2x long](https://app.lighter.xyz/public-pools/281474976710604) | 0% | 0% | 0% |
| [gLighter Pool by NamasteWeb3](https://app.lighter.xyz/public-pools/281474976689660) | 0% | 0% | 0% |
| [miner](https://app.lighter.xyz/public-pools/281474976644575) | 0% | 0% | 0% |
| [WLD 2x long](https://app.lighter.xyz/public-pools/281474976710641) | 0% | 0% | 0% |
| [PAXG 2x long](https://app.lighter.xyz/public-pools/281474976698602) | 0% | 0% | 0% |
| [JUP 2x long](https://app.lighter.xyz/public-pools/281474976710560) | 0% | 0% | 0% |
| [SPX 2x long](https://app.lighter.xyz/public-pools/281474976706998) | 0% | 0% | 0% |
| [ZEC 2x short](https://app.lighter.xyz/public-pools/281474976664629) | 0% | 0% | 0% |
| [POPCAT 2x long](https://app.lighter.xyz/public-pools/281474976710576) | 0% | 0% | 0% |
| [Symphony](https://app.lighter.xyz/public-pools/281474976626973) | 0% | 0% | 0% |
| [POPCAT 2x short](https://app.lighter.xyz/public-pools/281474976710575) | 0% | 0% | 0% |
| [BONK 2x long](https://app.lighter.xyz/public-pools/281474976710602) | 0% | 0% | 0% |
| [PEPE 2x long](https://app.lighter.xyz/public-pools/281474976710645) | 0% | 0% | 0% |
| [DivinelyRejected](https://app.lighter.xyz/public-pools/281474976643900) | 0% | 0% | 0% |
| [BNB 2x long](https://app.lighter.xyz/public-pools/281474976710562) | 0% | 0% | 0% |
| [UNI 2x long](https://app.lighter.xyz/public-pools/281474976710538) | 0% | 0% | 0% |
| [ONDO 2x long](https://app.lighter.xyz/public-pools/281474976708831) | 0% | 0% | 0% |
| [ADA 2x long](https://app.lighter.xyz/public-pools/281474976708413) | 0% | 0% | 0% |
| [PENDLE 2x long](https://app.lighter.xyz/public-pools/281474976708833) | 0% | 0% | 0% |
| [Phantom](https://app.lighter.xyz/public-pools/281474976644012) | 0% | 0% | 0% |
| [TrippleS Capital](https://app.lighter.xyz/public-pools/281474976686233) | 0% | 0% | 0% |
| [UNI 2x short](https://app.lighter.xyz/public-pools/281474976710537) | 0% | 0% | 0% |
| [Degenerate Pool](https://app.lighter.xyz/public-pools/281474976644059) | 0% | 0% | 0% |
| [LTC 2x long](https://app.lighter.xyz/public-pools/281474976710315) | 0% | 0% | 0% |
| [ASTER 2x long](https://app.lighter.xyz/public-pools/281474976687563) | 0% | 0% | 0% |
| [ZRO 2x long](https://app.lighter.xyz/public-pools/281474976696956) | 0% | 0% | 0% |
| [KAITO 2x long](https://app.lighter.xyz/public-pools/281474976710319) | 0% | 0% | 0% |
| [ICP 2x long](https://app.lighter.xyz/public-pools/281474976641577) | 0% | 0% | 0% |
| [BERA 2x long](https://app.lighter.xyz/public-pools/281474976710582) | 0% | 0% | 0% |
| [SYRUP 2x long](https://app.lighter.xyz/public-pools/281474976706165) | 0% | 0% | 0% |
| [SUI 2x short](https://app.lighter.xyz/public-pools/281474976710605) | 0% | 0% | 0% |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | 74.7827% | 87.9968% | 162.7795% |

## Trading and rebalances

Fill and order counts cover the 30-day window. The gap measures hours between consecutive multi-market bursts. Pools outside the table returned no position change and have no reported native volume for the window.

| Pool | Execution coverage | Fills / orders | Bursts / days with a burst | Median gap | 10th / 90th-percentile gap |
| --- | --- | --- | --- | --- | --- |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | Protocol pool | — / — | — / — | — | — / — |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | Acquisition capped | — / — | — / — | — | — / — |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | Qualified | 70 / 2 | 0 / 0 | — | — / — |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | Unqualified | — / — | — / — | — | — / — |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | Qualified | 2,295 / 382 | 25 / 15 | 15.205422 | 2.634509 / 51.064472 |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | Qualified | 14,791 / 11,288 | 134 / 30 | 3.517983 | 1.355596 / 11.269425 |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | Qualified | 37 / 17 | 2 / 2 | 150.349488 | 150.349488 / 150.349488 |
| [Experimental Liquidity Provider (XLP)](https://app.lighter.xyz/public-pools/281474976680784) | Protocol pool | — / — | — / — | — | — / — |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | Qualified | 636 / 61 | 9 / 6 | 26.306422 | 2.065957 / 215.739776 |
| [Centaur Strategies: Lazy Villager](https://app.lighter.xyz/public-pools/281474976488856) | Qualified | 20 / 17 | 2 / 2 | 102.719355 | 102.719355 / 102.719355 |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | Qualified | 1 / 1 | 0 / 0 | — | — / — |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | Qualified | 45 / 28 | 5 / 5 | 68.720524 | 34.835123 / 298.094631 |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | Qualified | 223 / 175 | 12 / 6 | 11.04206 | 1.840863 / 74.165245 |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | Qualified | 750 / 74 | 7 / 6 | 58.183876 | 21.021586 / 151.719562 |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | Qualified | 6 / 1 | 0 / 0 | — | — / — |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | Qualified | 113 / 68 | 10 / 8 | 29.251876 | 2.838672 / 75.813774 |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | Qualified | 29 / 25 | 4 / 3 | 17.959688 | 6.064837 / 369.41235 |
| [K Pool](https://app.lighter.xyz/public-pools/281474976680237) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [Algorithmic L/S \| Big Brain Research](https://app.lighter.xyz/public-pools/281474976690488) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [systematic breakout long-only](https://app.lighter.xyz/public-pools/281474976496745) | Qualified | 11 / 6 | 1 / 1 | — | — / — |
| [AlphaMIX Global](https://app.lighter.xyz/public-pools/281474976415828) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | Qualified | 672 / 639 | 29 / 28 | 23.999977 | 23.991228 / 25.008955 |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | Qualified | 45 / 25 | 4 / 2 | 2.000002 | 1.200101 / 17.199883 |
| [Silentist Public 2nd](https://app.lighter.xyz/public-pools/281474976436927) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [EdgePool](https://app.lighter.xyz/public-pools/281474976466567) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [LAZY Pool](https://app.lighter.xyz/public-pools/281474976473729) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | Qualified | 421 / 390 | 20 / 18 | 23.999789 | 16.141649 / 67.400346 |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | Qualified | 367 / 125 | 1 / 1 | — | — / — |
| [LIT ALPHA POOL](https://app.lighter.xyz/public-pools/281474976490593) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | Unqualified | — / — | — / — | — | — / — |
| [scanner.money - TrendSend](https://app.lighter.xyz/public-pools/281474976490589) | Qualified | 0 / 0 | 0 / 0 | — | — / — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | Protocol pool | — / — | — / — | — | — / — |

### Execution amounts

| Pool | Maker share | Fees | Gross realised P&L | Native volume |
| --- | --- | --- | --- | --- |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | — | — | — | 2,358,438,971.597248 |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | — | — | — | 90,780,863.957365 |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | — | 0 | 0 | 0 |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | — | 0 | 0 | 0 |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | 100% | 14.63756 | 0 | 772,970 |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | — | — | — | 391,145.63209 |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | 83.0501% | 69.521724 | 4,842.919828 | 1,545,939.098419 |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | 0% | 333.025234 | 3,656.505704 | 1,866,930.186736 |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | 27.027% | 7.033897 | -1,166.346875 | 36,001.850153 |
| [Experimental Liquidity Provider (XLP)](https://app.lighter.xyz/public-pools/281474976680784) | — | — | — | 0 |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | 65.7233% | 39.285667 | -4,255.727659 | 389,717.987599 |
| [Centaur Strategies: Lazy Villager](https://app.lighter.xyz/public-pools/281474976488856) | 0% | 2.413904 | 1,062.082061 | 12,315.834662 |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | 0% | 0.1764 | 0 | 899.998911 |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | 0% | 1.945766 | -198.68924 | 9,313.542575 |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | 78.4753% | 1.194622 | 179.338281 | — |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | 22.4% | 529.486248 | -113,312.848887 | 4,017,596.317663 |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | 0% | 0.323063 | -438.338312 | 1,648.280548 |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | 0% | 6.891785 | -476.855715 | 35,162.170845 |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | 0% | 1.637882 | -1,682.622977 | 8,356.538876 |
| [K Pool](https://app.lighter.xyz/public-pools/281474976680237) | — | 0 | 0 | 0 |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | — | 0 | 0 | 0 |
| [Algorithmic L/S \| Big Brain Research](https://app.lighter.xyz/public-pools/281474976690488) | — | 0 | 0 | 0 |
| [systematic breakout long-only](https://app.lighter.xyz/public-pools/281474976496745) | 0% | 0.256884 | -40.615688 | 1,335.527384 |
| [AlphaMIX Global](https://app.lighter.xyz/public-pools/281474976415828) | — | 0 | 0 | — |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | 0% | 2.957097 | 109.21144 | 14,577.674097 |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | 0% | 2.943948 | 115.420786 | 17,192.640885 |
| [Silentist Public 2nd](https://app.lighter.xyz/public-pools/281474976436927) | — | 0 | 0 | — |
| [EdgePool](https://app.lighter.xyz/public-pools/281474976466567) | — | 0 | 0 | — |
| [LAZY Pool](https://app.lighter.xyz/public-pools/281474976473729) | — | 0 | 0 | — |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | 0% | 2.149785 | -31.11712 | — |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | — | 0 | 0 | — |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | — | 0 | 0 | — |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | — | 0 | 0 | — |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | 54.2234% | 93.206251 | -29,613.616922 | 649,990.849714 |
| [LIT ALPHA POOL](https://app.lighter.xyz/public-pools/281474976490593) | — | 0 | 0 | — |
| [scanner.money - TrendSend](https://app.lighter.xyz/public-pools/281474976490589) | — | 0 | 0 | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | — | — | — | 629,375,858.175175 |

## Share-price performance

BTC fell 9.8559% over the aligned share-price observation window used for beta. Three pools had no recorded share-price history, including AlphaMIX Global and the two unnamed Core pools.

### Protocol and operator-run pools

| Pool | 30-day return | Maximum drawdown | BTC beta | First / last date | Days with a price | CAGR | Weekly Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | -1.6079% | -6.9911% | -0.19479 | 2025-09-18 / 2026-11-11 | 420 | 33.4673% | 0.712371 |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | -9.2557% | -32.0277% | -1.406559 | 2026-03-05 / 2026-11-11 | 252 | -84.4121% | -1.796499 |
| [BTC vs ALTS and NEWS/DIPS](https://app.lighter.xyz/public-pools/281474976656586) | 0% | 0% | 0 | 2025-10-08 / 2026-11-11 | 400 | 79.9788% | 0.948073 |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | -11.3473% | -14.8778% | 1.168896 | 2026-01-12 / 2026-11-11 | 304 | -18.5964% | -0.094322 |
| [Christ is King](https://app.lighter.xyz/public-pools/281474976498676) | 2.2209% | -2.3715% | 0.365694 | 2026-08-19 / 2026-11-11 | 85 | — | 3.822194 |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | 3.5231% | -0.5867% | 0.029003 | 2026-05-21 / 2026-11-11 | 175 | 46.1765% | 7.099229 |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | -38.9733% | -43.7095% | 3.25039 | 2025-10-15 / 2026-11-11 | 393 | 22.4847% | 0.707022 |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | -22.4558% | -26.8479% | 1.958444 | 2025-09-20 / 2026-11-11 | 418 | 38.2332% | 0.690518 |
| [Centaur Strategies: Inverse 1000x](https://app.lighter.xyz/public-pools/281474976488851) | 22.0048% | -6.1681% | -1.16915 | 2026-09-11 / 2026-11-11 | 62 | — | 5.115829 |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | -9.1202% | -16.4407% | 0.842696 | 2026-09-10 / 2026-11-11 | 63 | — | 0.398695 |
| [Systematic Strategy](https://app.lighter.xyz/public-pools/281474976466894) | — | — | — | 2026-10-21 / 2026-11-11 | 22 | — | — |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | -97.1018% | -97.1018% | 4.843244 | 2025-08-23 / 2026-11-11 | 446 | -95.5478% | -0.061109 |
| [Centaur Strategies: Kole World](https://app.lighter.xyz/public-pools/281474976489166) | -8.1949% | -18.3078% | 1.306681 | 2026-09-10 / 2026-11-11 | 63 | — | -1.480961 |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | -18.1065% | -18.3651% | 0.492362 | 2026-09-11 / 2026-11-11 | 62 | — | -10.307562 |
| [Centaur Strategies: Serenity](https://app.lighter.xyz/public-pools/281474976488853) | -16.0866% | -25.0144% | 1.411602 | 2026-09-11 / 2026-11-11 | 62 | — | -3.019383 |
| [5x  Traders](https://app.lighter.xyz/public-pools/281474976655272) | -7.9644% | -12.0995% | 0.799896 | 2025-10-10 / 2026-11-11 | 398 | -54.4953% | -1.13052 |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | -1.1513% | -6.8013% | -0.215131 | 2026-08-11 / 2026-11-11 | 93 | 19.7478% | 1.838724 |
| [systematic breakout ltf long only](https://app.lighter.xyz/public-pools/281474976492148) | 12.3852% | -5.3082% | 0.252743 | 2026-09-02 / 2026-11-11 | 71 | — | 0.84779 |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | -6.1366% | -11.9157% | 0.195981 | 2026-09-18 / 2026-11-11 | 55 | — | — |
| [Majors vs Alts](https://app.lighter.xyz/public-pools/281474976656876) | 0% | 0% | 0 | 2025-10-07 / 2026-11-11 | 401 | 45.8919% | 0.454584 |
| [Unwind](https://app.lighter.xyz/public-pools/281474976505621) | 0% | 0% | 0 | 2026-07-29 / 2026-11-11 | 106 | -67.9379% | -0.761184 |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | -99.8295% | -99.8299% | 12.553201 | 2025-10-29 / 2026-11-11 | 379 | -99.8455% | -0.912292 |
| [Pikachu Pool](https://app.lighter.xyz/public-pools/281474976643789) | 0% | 0% | 0 | 2025-10-29 / 2026-11-11 | 379 | -53.3392% | -0.896376 |
| [Alleged Alpha](https://app.lighter.xyz/public-pools/281474976625478) | 0% | 0% | 0 | 2026-01-03 / 2026-11-11 | 313 | -22.285% | -0.959841 |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | -8.1169% | -8.2366% | 0.302279 | 2025-11-13 / 2026-11-11 | 364 | -13.6265% | -0.806068 |
| [scanner.money - TrendSend](https://app.lighter.xyz/public-pools/281474976490589) | -63.9403% | -82.1228% | 8.248036 | 2026-09-06 / 2026-11-11 | 67 | — | -1.404606 |
| [BTC Trading Pool](https://app.lighter.xyz/public-pools/281474976644793) | 0% | 0% | 0 | 2025-10-25 / 2026-11-11 | 383 | -26.5494% | -0.565596 |
| [cp0x Delta Majors](https://app.lighter.xyz/public-pools/281474976644089) | 0% | 0% | 0 | 2025-10-28 / 2026-11-11 | 380 | 0.7684% | 0.044197 |
| [Short Coins](https://app.lighter.xyz/public-pools/281474976666853) | 0% | 0% | 0 | 2025-09-30 / 2026-11-11 | 408 | 29.3852% | 0.956445 |
| [Shrimp Liquidity Provider (SLP)](https://app.lighter.xyz/public-pools/281474976666704) | 0% | 0% | 0 | 2025-09-30 / 2026-11-11 | 408 | -99.96% | -2.263329 |
| [BTC H1 EMA Strategy](https://app.lighter.xyz/public-pools/281474976656541) | 0% | 0% | 0 | 2025-10-08 / 2026-11-11 | 400 | -55.4028% | -1.012108 |
| [Moonshot](https://app.lighter.xyz/public-pools/281474976638939) | 0% | 0% | 0 | 2025-11-19 / 2026-11-11 | 358 | -5.1459% | 0.523118 |
| [Majors Correlated Pool!](https://app.lighter.xyz/public-pools/281474976626327) | 0% | 0% | 0 | 2025-12-24 / 2026-11-11 | 323 | 28.9755% | 1.076454 |
| [BULBUL Vault](https://app.lighter.xyz/public-pools/281474976687595) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | -13.2817% | -0.076876 |
| [Symphony](https://app.lighter.xyz/public-pools/281474976626973) | 0% | 0% | 0 | 2025-12-10 / 2026-11-11 | 337 | -9.8455% | -0.914895 |
| [Storm](https://app.lighter.xyz/public-pools/281474976623750) | 0% | 0% | 0 | 2026-02-10 / 2026-11-11 | 275 | 0.7798% | 0.889036 |
| [pool1](https://app.lighter.xyz/public-pools/281474976637751) | 0% | 0% | 0 | 2025-11-21 / 2026-11-11 | 356 | 0% | 0.434517 |
| [Core4-Long Basket-Short](https://app.lighter.xyz/public-pools/281474976643102) | 0% | 0% | 0 | 2025-11-01 / 2026-11-11 | 376 | 0% | — |
| [\[Long Value & Short Dogshit\]](https://app.lighter.xyz/public-pools/281474976644348) | 0% | 0% | 0 | 2025-10-27 / 2026-11-11 | 381 | 5.1343% | — |
| [The Pool Party](https://app.lighter.xyz/public-pools/281474976680236) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | 0% | — |
| [Agentic Trading](https://app.lighter.xyz/public-pools/281474976688779) | — | — | — | 2025-09-17 / 2026-11-11 | 421 | — | — |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | 0.821% | -0.0384% | -0.020913 | 2025-01-16 / 2026-11-11 | 665 | 103.8998% | 2.416032 |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | 6.7917% | -8.2342% | 0.075189 | 2026-05-07 / 2026-11-11 | 189 | 184.2463% | 10.196316 |
| [Experimental Liquidity Provider (XLP)](https://app.lighter.xyz/public-pools/281474976680784) | 0% | 0% | 0 | 2025-09-23 / 2026-11-11 | 415 | -38.9951% | -0.737407 |
| [Centaur Strategies: Lazy Villager](https://app.lighter.xyz/public-pools/281474976488856) | 9.045% | -3.3293% | -0.155727 | 2026-09-11 / 2026-11-11 | 62 | — | 3.924022 |
| [Algorithmic L/S \| Big Brain Research](https://app.lighter.xyz/public-pools/281474976690488) | -0% | -0% | -0 | 2025-09-12 / 2026-11-11 | 426 | 1.6595% | 0.072502 |
| [systematic breakout long-only](https://app.lighter.xyz/public-pools/281474976496745) | -6.4964% | -6.4964% | 0.279677 | 2026-08-23 / 2026-11-11 | 81 | — | -1.035399 |
| [GCB Capital](https://app.lighter.xyz/public-pools/281474976690376) | 0% | 0% | 0 | 2025-09-13 / 2026-11-11 | 425 | -60.4888% | -0.992722 |
| [Popo BTC Only Low Frequency Algo](https://app.lighter.xyz/public-pools/281474976623846) | 0% | 0% | 0 | 2026-02-07 / 2026-11-11 | 278 | -98.2744% | -1.202259 |
| [PAWNZI ALGO – PROFITABLE (REAL)](https://app.lighter.xyz/public-pools/281474976680234) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | -13.835% | -0.428136 |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | -12.4252% | -12.9407% | 0.416686 | 2026-05-07 / 2026-11-11 | 189 | -40.7859% | -0.643471 |
| [DSC - L Strategy](https://app.lighter.xyz/public-pools/281474976644517) | 0% | 0% | 0 | 2025-10-26 / 2026-11-11 | 382 | -99.979% | -0.583747 |
| [Cross-Sectional-Relative-Strength](https://app.lighter.xyz/public-pools/281474976656474) | 0% | 0% | 0 | 2025-10-09 / 2026-11-11 | 399 | -32.4564% | -1.019182 |
| [Bellagio](https://app.lighter.xyz/public-pools/281474976690371) | 0% | 0% | 0 | 2025-09-13 / 2026-11-11 | 425 | — | — |
| [Quant + Discretionary](https://app.lighter.xyz/public-pools/281474976644732) | 0% | 0% | 0 | 2025-10-25 / 2026-11-11 | 383 | 12.2988% | — |
| [Valkyrie of the Faint Smile( L/S)](https://app.lighter.xyz/public-pools/281474976651220) | 0% | 0% | 0 | 2025-10-13 / 2026-11-11 | 395 | -7.5423% | -0.046989 |
| [Long BTC/ETH/HYPE + Gold](https://app.lighter.xyz/public-pools/281474976644427) | 0% | 0% | 0 | 2025-10-26 / 2026-11-11 | 382 | -42.9364% | -0.992551 |
| [YOLO Pool](https://app.lighter.xyz/public-pools/281474976689753) | 0% | 0% | 0 | 2025-09-15 / 2026-11-11 | 423 | -100% | -1.697834 |
| [gLighter Pool by NamasteWeb3](https://app.lighter.xyz/public-pools/281474976689660) | 0% | 0% | 0 | 2025-09-16 / 2026-11-11 | 422 | -93.3283% | -0.150494 |
| [TrippleS Capital](https://app.lighter.xyz/public-pools/281474976686233) | 0% | 0% | 0 | 2025-09-19 / 2026-11-11 | 419 | -99.9287% | -2.034056 |
| [\[Systematic Strategies\] Long / Short Bot](https://app.lighter.xyz/public-pools/281474976549878) | 0% | 0% | 0 | 2026-05-09 / 2026-11-11 | 187 | 0% | 0.164566 |
| [\[Robonet\] - Allora Libra](https://app.lighter.xyz/public-pools/281474976576705) | 0% | 0% | 0 | 2026-04-13 / 2026-11-11 | 213 | 0% | 0.09956 |
| [CopyCAT](https://app.lighter.xyz/public-pools/281474976587033) | 0% | 0% | 0 | 2026-04-06 / 2026-11-11 | 220 | -0.1999% | 0.242298 |
| [Candle Effect v1](https://app.lighter.xyz/public-pools/281474976620676) | 0% | 0% | 0 | 2026-03-19 / 2026-11-11 | 238 | -91.6332% | -1.221798 |
| [BTC 35x Long](https://app.lighter.xyz/public-pools/281474976644581) | 0% | 0% | 0 | 2025-10-26 / 2026-11-11 | 382 | 8.0836% | 0.95411 |
| [Trend Trading](https://app.lighter.xyz/public-pools/281474976681281) | 0% | 0% | 0 | 2025-09-22 / 2026-11-11 | 416 | 0.8022% | 0.394553 |
| [YOLO Pool](https://app.lighter.xyz/public-pools/281474976690279) | 0% | 0% | 0 | 2025-09-14 / 2026-11-11 | 424 | 0% | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | 11.531% | -2.0622% | -0.447219 | 2026-06-28 / 2026-11-11 | 137 | 787.5966% | 6.093826 |
| [K Pool](https://app.lighter.xyz/public-pools/281474976680237) | 0% | -0% | -0 | 2025-09-24 / 2026-11-11 | 414 | 303.7724% | 2.13724 |
| [Silentist Public 2nd](https://app.lighter.xyz/public-pools/281474976436927) | — | — | — | 2026-11-03 / 2026-11-11 | 9 | — | — |
| [EdgePool](https://app.lighter.xyz/public-pools/281474976466567) | — | — | — | 2026-10-21 / 2026-11-11 | 22 | — | — |
| [LAZY Pool](https://app.lighter.xyz/public-pools/281474976473729) | — | — | — | 2026-10-19 / 2026-11-11 | 24 | — | — |
| [Hanabi pool](https://app.lighter.xyz/public-pools/281474976678968) | 0% | 0% | 0 | 2025-09-25 / 2026-11-11 | 413 | 40.4624% | 0.467807 |
| [DTK Pool](https://app.lighter.xyz/public-pools/281474976644086) | 0% | 0% | -0 | 2025-10-28 / 2026-11-11 | 380 | 0.9578% | 1.631719 |
| [KUY Strategy](https://app.lighter.xyz/public-pools/281474976624682) | 0% | 0% | 0 | 2026-01-17 / 2026-11-11 | 299 | -54.0622% | -1.127731 |
| [Index of Profitable Traders \| CloneX (Depreciated)](https://app.lighter.xyz/public-pools/281474976690931) | 0% | 0% | 0 | 2025-09-09 / 2026-11-11 | 429 | -38.2499% | -1.326224 |
| [Vorcaro](https://app.lighter.xyz/public-pools/281474976621130) | 0% | 0% | 0 | 2026-03-11 / 2026-11-11 | 246 | -99.7632% | -1.206456 |
| [SMTrading (Temporarily Closed)](https://app.lighter.xyz/public-pools/281474976680682) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | -45.9076% | -0.542144 |
| [BIT755](https://app.lighter.xyz/public-pools/281474976563230) | 0% | 0% | 0 | 2026-04-23 / 2026-11-11 | 203 | 5.0331% | 0.547345 |
| [Trust me bro (diditrading)](https://app.lighter.xyz/public-pools/281474976690774) | 0% | 0% | 0 | 2025-09-10 / 2026-11-11 | 428 | -65.9832% | -1.405143 |
| [Lighter Quantum](https://app.lighter.xyz/public-pools/281474976623571) | 0% | 0% | 0 | 2026-02-16 / 2026-11-11 | 269 | 1.7845% | -1.187125 |
| [LIT ALPHA POOL](https://app.lighter.xyz/public-pools/281474976490593) | -78.6685% | -79.257% | 5.349098 | 2026-09-06 / 2026-11-11 | 67 | — | -1.979802 |
| [old-pool](https://app.lighter.xyz/public-pools/281474976688801) | 0% | 0% | 0 | 2025-09-17 / 2026-11-11 | 421 | 12.8568% | 0.550169 |
| [Soufflé Pool](https://app.lighter.xyz/public-pools/281474976680506) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | -30.9496% | -0.972705 |
| [paba and amigos](https://app.lighter.xyz/public-pools/281474976689945) | 0% | 0% | 0 | 2025-09-14 / 2026-11-11 | 424 | -41.6156% | -0.940094 |
| [Pool](https://app.lighter.xyz/public-pools/281474976644417) | 0% | 0% | 0 | 2025-10-27 / 2026-11-11 | 381 | -96.5847% | -1.418206 |
| [Capital Vault](https://app.lighter.xyz/public-pools/281474976690600) | 0% | 0% | 0 | 2025-09-11 / 2026-11-11 | 427 | -11.191% | -1.05588 |
| [Goon Cave](https://app.lighter.xyz/public-pools/281474976656239) | 0% | 0% | 0 | 2025-10-09 / 2026-11-11 | 399 | -10.289% | -0.167004 |
| [ABCapital](https://app.lighter.xyz/public-pools/281474976678339) | 0% | 0% | 0 | 2025-09-25 / 2026-11-11 | 413 | -99.9933% | -2.003106 |
| [PHP Trading Desk (BTC/USDC)](https://app.lighter.xyz/public-pools/281474976593834) | 0% | 0% | 0 | 2026-04-03 / 2026-11-11 | 223 | 5.2133% | -0.125301 |
| [Dragon](https://app.lighter.xyz/public-pools/281474976534461) | 0% | 0% | 0 | 2026-06-08 / 2026-11-11 | 157 | 60.5874% | 0.946162 |
| [Aesthetic Pool](https://app.lighter.xyz/public-pools/281474976644611) | 0% | 0% | 0 | 2025-10-26 / 2026-11-11 | 382 | -99.9172% | -1.058522 |
| [lefty’s btc pool](https://app.lighter.xyz/public-pools/281474976644615) | 0% | 0% | 0 | 2025-10-26 / 2026-11-11 | 382 | -56.0332% | -1.128415 |
| [Popo Pool](https://app.lighter.xyz/public-pools/281474976690411) | 0% | 0% | 0 | 2025-09-13 / 2026-11-11 | 425 | -96.2227% | 0.032762 |
| [Perpetua](https://app.lighter.xyz/public-pools/281474976644635) | 0% | 0% | 0 | 2025-10-25 / 2026-11-11 | 383 | -100% | -2.397778 |
| [ffadff](https://app.lighter.xyz/public-pools/281474976669598) | 0% | 0% | 0 | 2025-09-29 / 2026-11-11 | 409 | -99.9132% | 0.053684 |
| [VB Trading](https://app.lighter.xyz/public-pools/281474976642013) | 0% | 0% | 0 | 2025-11-06 / 2026-11-11 | 371 | -96.8757% | -1.281825 |
| [Syzygy Strategy](https://app.lighter.xyz/public-pools/281474976539113) | 0% | 0% | 0 | 2026-05-30 / 2026-11-11 | 166 | 27.4483% | 0.835228 |
| [Shadow](https://app.lighter.xyz/public-pools/281474976643341) | 0% | 0% | 0 | 2025-10-31 / 2026-11-11 | 377 | -12.2736% | -1.001373 |
| [Ghost](https://app.lighter.xyz/public-pools/281474976688577) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | -98.4275% | 0.017026 |
| [rekt](https://app.lighter.xyz/public-pools/281474976623731) | 0% | 0% | 0 | 2026-02-11 / 2026-11-11 | 274 | -53.8595% | -0.222687 |
| [Experimental Pool](https://app.lighter.xyz/public-pools/281474976644304) | 0% | 0% | 0 | 2025-10-27 / 2026-11-11 | 381 | -16.0356% | -0.374427 |
| [alt mania](https://app.lighter.xyz/public-pools/281474976644562) | 0.0001% | 0% | 0.000001 | 2025-10-26 / 2026-11-11 | 382 | -94.1875% | -1.608076 |
| [Vegeta's Ultra Ego Pool](https://app.lighter.xyz/public-pools/281474976675532) | 0% | 0% | 0 | 2025-09-27 / 2026-11-11 | 411 | -4.4556% | — |
| [HLP](https://app.lighter.xyz/public-pools/281474976643920) | 0% | 0% | 0 | 2025-10-29 / 2026-11-11 | 379 | -100% | -2.034045 |
| [Dead pool](https://app.lighter.xyz/public-pools/281474976680683) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | -84.4432% | -0.948164 |
| [Failure](https://app.lighter.xyz/public-pools/281474976503569) | 0% | 0% | 0 | 2026-08-04 / 2026-11-11 | 100 | -100% | -2.71985 |
| [Better Than Yours](https://app.lighter.xyz/public-pools/281474976642928) | 0% | 0% | 0 | 2025-11-02 / 2026-11-11 | 375 | -6.477% | -0.215044 |
| [BTC/ETH x20 LONG](https://app.lighter.xyz/public-pools/281474976637687) | 0% | 0% | 0 | 2025-11-21 / 2026-11-11 | 356 | 0.6546% | -1.462541 |
| [FastBit Pool](https://app.lighter.xyz/public-pools/281474976643399) | -0.0023% | -0.0023% | 0.000027 | 2025-10-31 / 2026-11-11 | 377 | -64.6034% | -0.810348 |
| [Aaa](https://app.lighter.xyz/public-pools/281474976663862) | 0% | 0% | 0 | 2025-10-03 / 2026-11-11 | 405 | 3.3274% | -0.964947 |
| [Xiaohei](https://app.lighter.xyz/public-pools/281474976644412) | 0% | 0% | 0 | 2025-10-27 / 2026-11-11 | 381 | 0% | 0.53488 |
| [Vui Pool](https://app.lighter.xyz/public-pools/281474976689795) | 0% | 0% | 0 | 2025-09-15 / 2026-11-11 | 423 | -9.7225% | -1.335113 |
| [miner](https://app.lighter.xyz/public-pools/281474976644575) | 0% | 0% | 0 | 2025-10-26 / 2026-11-11 | 382 | -0.8124% | 0.179781 |
| [DivinelyRejected](https://app.lighter.xyz/public-pools/281474976643900) | 0% | 0% | 0 | 2025-10-29 / 2026-11-11 | 379 | -28.8393% | 0.991881 |
| [Phantom](https://app.lighter.xyz/public-pools/281474976644012) | 0% | 0% | 0 | 2025-10-28 / 2026-11-11 | 380 | -93.6967% | -0.809423 |
| [Degenerate Pool](https://app.lighter.xyz/public-pools/281474976644059) | 0% | 0% | 0 | 2025-10-28 / 2026-11-11 | 380 | -97.528% | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473724) | — | — | — | 2026-10-19 / 2026-11-11 | 24 | — | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473725) | — | — | — | 2026-10-19 / 2026-11-11 | 24 | — | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473728) | — | — | — | 2026-10-19 / 2026-11-11 | 24 | — | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473730) | — | — | — | 2026-10-19 / 2026-11-11 | 24 | — | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976473731) | — | — | — | 2026-10-19 / 2026-11-11 | 24 | — | — |
| [GGez.trade](https://app.lighter.xyz/public-pools/281474976486923) | 0% | 0% | 0 | 2026-09-17 / 2026-11-11 | 56 | — | — |
| [Nebula Trading](https://app.lighter.xyz/public-pools/281474976492019) | 0% | 0% | 0 | 2026-09-03 / 2026-11-11 | 70 | — | 2.407 |
| [ICT_SMT](https://app.lighter.xyz/public-pools/281474976499136) | 0% | 0% | 0 | 2026-08-18 / 2026-11-11 | 86 | — | — |
| [\[RES\] Range Extension Strategies](https://app.lighter.xyz/public-pools/281474976527697) | 0% | 0% | 0 | 2026-06-19 / 2026-11-11 | 146 | 0% | 0.126265 |
| [Wombat](https://app.lighter.xyz/public-pools/281474976528250) | 0% | 0% | 0 | 2026-06-18 / 2026-11-11 | 147 | 0% | 0.109152 |
| [EAGLE](https://app.lighter.xyz/public-pools/281474976528251) | 0% | 0% | 0 | 2026-06-18 / 2026-11-11 | 147 | 0% | 0.71255 |
| [Cabbage Gang Pool#1](https://app.lighter.xyz/public-pools/281474976587880) | 0% | 0% | 0 | 2026-04-06 / 2026-11-11 | 220 | -0.1624% | -1.318368 |
| [Candy from a Baby](https://app.lighter.xyz/public-pools/281474976617249) | 0% | 0% | 0 | 2026-04-02 / 2026-11-11 | 224 | 0% | — |
| [zxcsolomid](https://app.lighter.xyz/public-pools/281474976617289) | 0% | 0% | 0 | 2026-04-01 / 2026-11-11 | 225 | 0.0051% | -0.724835 |
| [Mars Gambols](https://app.lighter.xyz/public-pools/281474976624514) | 0% | 0% | 0 | 2026-01-21 / 2026-11-11 | 295 | 2.626% | 0.70358 |
| [KUY Strategy](https://app.lighter.xyz/public-pools/281474976624683) | 0% | 0% | 0 | 2026-01-17 / 2026-11-11 | 299 | 0% | — |
| [Equity Trader](https://app.lighter.xyz/public-pools/281474976626656) | 0% | 0% | 0 | 2025-12-17 / 2026-11-11 | 330 | 0% | — |
| [Unicorn](https://app.lighter.xyz/public-pools/281474976628387) | 0% | 0% | 0 | 2025-11-28 / 2026-11-11 | 349 | -2.9197% | 0.82253 |
| [Miracle](https://app.lighter.xyz/public-pools/281474976640438) | 0% | 0% | 0 | 2025-11-12 / 2026-11-11 | 365 | -6.306% | — |
| [Miracles](https://app.lighter.xyz/public-pools/281474976640440) | 0% | 0% | 0 | 2025-11-12 / 2026-11-11 | 365 | 0% | — |
| [unnu_eth](https://app.lighter.xyz/public-pools/281474976643294) | 0% | 0% | 0 | 2025-11-01 / 2026-11-11 | 376 | 0% | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976644413) | 0% | 0% | 0 | 2025-10-27 / 2026-11-11 | 381 | 0% | — |
| [Lighter Robinhood Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976644745) | 0% | 0% | 0 | 2025-10-25 / 2026-11-11 | 383 | 0% | — |
| [GOAT Pool](https://app.lighter.xyz/public-pools/281474976645219) | 0% | 0% | 0 | 2025-10-23 / 2026-11-11 | 385 | -1.1558% | 0.982654 |
| [Test](https://app.lighter.xyz/public-pools/281474976645732) | 0% | 0% | 0 | 2025-10-22 / 2026-11-11 | 386 | 40.0006% | 0.982654 |
| [MHM](https://app.lighter.xyz/public-pools/281474976646196) | 0% | 0% | 0 | 2025-10-21 / 2026-11-11 | 387 | 0.7946% | 0.205775 |
| [Majors vs Alts (old)](https://app.lighter.xyz/public-pools/281474976656935) | 0% | 0% | 0 | 2025-10-07 / 2026-11-11 | 401 | 0% | — |
| [Wife Money](https://app.lighter.xyz/public-pools/281474976656990) | 0% | 0% | 0 | 2025-10-06 / 2026-11-11 | 402 | -0.7512% | 1.003761 |
| [Test](https://app.lighter.xyz/public-pools/281474976675594) | 0% | 0% | 0 | 2025-09-27 / 2026-11-11 | 411 | 0% | — |
| [Test](https://app.lighter.xyz/public-pools/281474976675641) | 0% | 0% | 0 | 2025-09-26 / 2026-11-11 | 412 | 0.228% | — |
| [experiment2](https://app.lighter.xyz/public-pools/281474976677272) | 0% | 0% | 0 | 2025-09-26 / 2026-11-11 | 412 | 0% | — |
| [experiment](https://app.lighter.xyz/public-pools/281474976678305) | 0% | 0% | 0 | 2025-09-26 / 2026-11-11 | 412 | 0% | — |
| [Custard Pool](https://app.lighter.xyz/public-pools/281474976680565) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | 0% | — |
| [degencrypt](https://app.lighter.xyz/public-pools/281474976686367) | 0% | 0% | 0 | 2025-09-19 / 2026-11-11 | 419 | 0% | — |
| [IZ labs](https://app.lighter.xyz/public-pools/281474976690877) | — | — | — | 2025-09-10 / 2026-11-11 | 428 | — | — |
| [Vikings Strategy](https://app.lighter.xyz/public-pools/281474976690978) | — | — | — | 2025-09-09 / 2026-11-11 | 429 | — | — |

### Fixed long exposure products

| Pool | 30-day return | Maximum drawdown | BTC beta | First / last date | Days with a price | CAGR | Weekly Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [ETH 3x long](https://app.lighter.xyz/public-pools/281474976710653) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -94.1929% | -2.003518 |
| [XAG 2x long](https://app.lighter.xyz/public-pools/281474976647177) | 0% | 0% | 0 | 2025-10-20 / 2026-11-11 | 388 | 0% | — |
| [BTC 3x long](https://app.lighter.xyz/public-pools/281474976710651) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -85.6353% | -1.721261 |
| [HYPE 2x long](https://app.lighter.xyz/public-pools/281474976710564) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -17.1492% | 0.468768 |
| [DOGE 2x long](https://app.lighter.xyz/public-pools/281474976710647) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -94.1316% | -1.833731 |
| [AVAX 2x long](https://app.lighter.xyz/public-pools/281474976710634) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -91.6618% | -1.970574 |
| [WLFI 2x long](https://app.lighter.xyz/public-pools/281474976692955) | 0% | 0% | 0 | 2025-08-26 / 2026-11-11 | 443 | -98.4676% | -1.969688 |
| [SOL 3x long](https://app.lighter.xyz/public-pools/281474976710649) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -97.4096% | -1.978843 |
| [SUI 2x long](https://app.lighter.xyz/public-pools/281474976710606) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.0392% | -2.669119 |
| [XPL 2x long](https://app.lighter.xyz/public-pools/281474976694732) | 0% | 0% | 0 | 2025-08-22 / 2026-11-11 | 447 | -99.8908% | -3.020602 |
| [MKR 2x long](https://app.lighter.xyz/public-pools/281474976710542) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -20.9793% | -0.757809 |
| [APT 2x long](https://app.lighter.xyz/public-pools/281474976710417) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -98.0764% | -2.177469 |
| [XRP 2x long](https://app.lighter.xyz/public-pools/281474976710638) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.8009% | -3.108873 |
| [TAO 2x long](https://app.lighter.xyz/public-pools/281474976710626) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.7309% | -2.648952 |
| [PUMP 1x long](https://app.lighter.xyz/public-pools/281474976703226) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -59.1737% | -0.40747 |
| [VIRTUAL 2x long](https://app.lighter.xyz/public-pools/281474976707000) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.886% | -1.002575 |
| [LINK 2x long](https://app.lighter.xyz/public-pools/281474976710636) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.911% | -2.936155 |
| [AVNT 2x long](https://app.lighter.xyz/public-pools/281474976687812) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | -99.9998% | -3.085657 |
| [FARTCOIN 2x long](https://app.lighter.xyz/public-pools/281474976710580) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9657% | -2.994419 |
| [WIF 2x long](https://app.lighter.xyz/public-pools/281474976710643) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9629% | -2.877459 |
| [AAVE 2x long](https://app.lighter.xyz/public-pools/281474976710544) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.7842% | -2.850276 |
| [SHIB 2x long](https://app.lighter.xyz/public-pools/281474976710604) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -89.561% | -2.205175 |
| [WLD 2x long](https://app.lighter.xyz/public-pools/281474976710641) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9713% | -1.37182 |
| [PAXG 2x long](https://app.lighter.xyz/public-pools/281474976698602) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -5.2122% | 0.151679 |
| [JUP 2x long](https://app.lighter.xyz/public-pools/281474976710560) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9762% | -2.712492 |
| [SPX 2x long](https://app.lighter.xyz/public-pools/281474976706998) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.975% | -2.413851 |
| [POPCAT 2x long](https://app.lighter.xyz/public-pools/281474976710576) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9907% | -2.947496 |
| [BONK 2x long](https://app.lighter.xyz/public-pools/281474976710602) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9901% | -2.899578 |
| [PEPE 2x long](https://app.lighter.xyz/public-pools/281474976710645) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9896% | -2.352312 |
| [BNB 2x long](https://app.lighter.xyz/public-pools/281474976710562) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -100% | -2.950832 |
| [UNI 2x long](https://app.lighter.xyz/public-pools/281474976710538) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -98.0524% | -2.198 |
| [ONDO 2x long](https://app.lighter.xyz/public-pools/281474976708831) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.987% | -2.88998 |
| [ADA 2x long](https://app.lighter.xyz/public-pools/281474976708413) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9706% | -2.89335 |
| [PENDLE 2x long](https://app.lighter.xyz/public-pools/281474976708833) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.987% | -3.6006 |
| [LTC 2x long](https://app.lighter.xyz/public-pools/281474976710315) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.909% | -3.224467 |
| [ASTER 2x long](https://app.lighter.xyz/public-pools/281474976687563) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | -99.7843% | -3.409974 |
| [ZRO 2x long](https://app.lighter.xyz/public-pools/281474976696956) | 0% | 0% | 0 | 2025-08-08 / 2026-11-11 | 461 | -99.7461% | -3.046865 |
| [KAITO 2x long](https://app.lighter.xyz/public-pools/281474976710319) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.8867% | -1.803574 |
| [ICP 2x long](https://app.lighter.xyz/public-pools/281474976641577) | 0% | 0% | 0 | 2025-11-08 / 2026-11-11 | 369 | -99.9964% | -3.209316 |
| [BERA 2x long](https://app.lighter.xyz/public-pools/281474976710582) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.5958% | -1.594853 |
| [SYRUP 2x long](https://app.lighter.xyz/public-pools/281474976706165) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9446% | -2.350594 |
| [STRK 2x long](https://app.lighter.xyz/public-pools/281474976641573) | 0% | 0% | 0 | 2025-11-08 / 2026-11-11 | 369 | 1.3187% | 0.948786 |
| [FIL 2x long](https://app.lighter.xyz/public-pools/281474976641575) | — | — | — | 2025-11-08 / 2026-11-11 | 369 | — | — |
| [MET 2x long](https://app.lighter.xyz/public-pools/281474976644848) | 0% | 0% | 0 | 2025-10-24 / 2026-11-11 | 384 | -3.4901% | 0.933158 |
| [XAU 2x long](https://app.lighter.xyz/public-pools/281474976647201) | 0% | 0% | 0 | 2025-10-20 / 2026-11-11 | 388 | 0% | — |
| [MONAD 2x long](https://app.lighter.xyz/public-pools/281474976656643) | 0% | 0% | 0 | 2025-10-08 / 2026-11-11 | 400 | 58.192% | 0.964928 |
| [ZEC 2x long](https://app.lighter.xyz/public-pools/281474976664630) | 0% | 0% | 0 | 2025-10-03 / 2026-11-11 | 405 | 25.8956% | 0.933007 |
| [EDEN 2x long](https://app.lighter.xyz/public-pools/281474976665434) | 0% | 0% | 0 | 2025-10-02 / 2026-11-11 | 406 | 2.9487% | 0.956412 |
| [2Z 2x long](https://app.lighter.xyz/public-pools/281474976665436) | 0% | 0% | 0 | 2025-10-02 / 2026-11-11 | 406 | -12.5811% | 0.956218 |
| [FF 2x long](https://app.lighter.xyz/public-pools/281474976667674) | 0% | 0% | 0 | 2025-09-29 / 2026-11-11 | 409 | 49.4436% | 0.956359 |
| [APEX 2x long](https://app.lighter.xyz/public-pools/281474976670932) | 0% | 0% | 0 | 2025-09-28 / 2026-11-11 | 410 | -13.0225% | 0.955992 |
| [STBL 2x long](https://app.lighter.xyz/public-pools/281474976680298) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | -24.6542% | 0.948121 |
| [0G 2x long](https://app.lighter.xyz/public-pools/281474976680300) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | -5.3829% | 0.928106 |
| [TOSHI 2x long](https://app.lighter.xyz/public-pools/281474976687808) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | — | — |
| [MYX 2x long](https://app.lighter.xyz/public-pools/281474976687810) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | 26.2147% | 0.940065 |
| [SKY 2x long](https://app.lighter.xyz/public-pools/281474976687814) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | -3.1691% | 0.832152 |
| [PYTH 2x long](https://app.lighter.xyz/public-pools/281474976691058) | 0% | 0% | 0 | 2025-09-08 / 2026-11-11 | 430 | 1.5114% | 0.932182 |
| [XMR 2x long](https://app.lighter.xyz/public-pools/281474976691060) | 0% | 0% | 0 | 2025-09-08 / 2026-11-11 | 430 | 0.8056% | 0.930366 |
| [LINEA 2x long](https://app.lighter.xyz/public-pools/281474976691921) | 0% | 0% | 0 | 2025-09-01 / 2026-11-11 | 437 | — | — |
| [DOLO 2x long](https://app.lighter.xyz/public-pools/281474976692287) | 0% | 0% | 0 | 2025-08-29 / 2026-11-11 | 440 | 2.9985% | 0.924458 |
| [NMR 2x long](https://app.lighter.xyz/public-pools/281474976692289) | 0% | 0% | 0 | 2025-08-29 / 2026-11-11 | 440 | -2.1984% | 0.923488 |
| [CRO 2x long](https://app.lighter.xyz/public-pools/281474976692348) | 0% | 0% | 0 | 2025-08-29 / 2026-11-11 | 440 | 0.396% | 0.924325 |
| [YZY 2x long](https://app.lighter.xyz/public-pools/281474976695152) | 0% | 0% | 0 | 2025-08-21 / 2026-11-11 | 448 | — | — |
| [VVV 2x long](https://app.lighter.xyz/public-pools/281474976695499) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | 1.1853% | 1.316756 |
| [MORPHO 2x long](https://app.lighter.xyz/public-pools/281474976695501) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | — | — |
| [TIA 2x long](https://app.lighter.xyz/public-pools/281474976695503) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | — | — |
| [USELESS 2x long](https://app.lighter.xyz/public-pools/281474976695505) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | -0.3481% | 0.909725 |
| [AERO 2x long](https://app.lighter.xyz/public-pools/281474976696149) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | 0% | 0.902244 |
| [ETHFI 2x long](https://app.lighter.xyz/public-pools/281474976696151) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | — | — |
| [GMX 2x long](https://app.lighter.xyz/public-pools/281474976696153) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | 0% | 0.902004 |
| [DYDX 2x long](https://app.lighter.xyz/public-pools/281474976696155) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | — | — |
| [MNT 2x long](https://app.lighter.xyz/public-pools/281474976696157) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | 0% | 0.901272 |
| [HBAR 2x long](https://app.lighter.xyz/public-pools/281474976696958) | 0% | 0% | 0 | 2025-08-08 / 2026-11-11 | 461 | -3.8435% | 0.883363 |
| [BCH 2x long](https://app.lighter.xyz/public-pools/281474976696960) | 0% | 0% | 0 | 2025-08-08 / 2026-11-11 | 461 | -2.7551% | 0.901231 |
| [PROVE 2x long](https://app.lighter.xyz/public-pools/281474976697777) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [ZK 2x long](https://app.lighter.xyz/public-pools/281474976698539) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [OP 2x long](https://app.lighter.xyz/public-pools/281474976698541) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 20.9784% | 0.895629 |
| [LAUNCHCOIN 2x long](https://app.lighter.xyz/public-pools/281474976698543) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 36.8806% | 0.894398 |
| [ZORA 2x long](https://app.lighter.xyz/public-pools/281474976698545) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 59.6365% | 0.895237 |
| [GRASS 2x long](https://app.lighter.xyz/public-pools/281474976698547) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 50.6731% | 0.895482 |
| [RESOLV 2x long](https://app.lighter.xyz/public-pools/281474976698549) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 54.0893% | 0.8956 |
| [ARB 2x long](https://app.lighter.xyz/public-pools/281474976698597) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 33.3072% | 0.895628 |
| [EIGEN 2x long](https://app.lighter.xyz/public-pools/281474976698600) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 47.9448% | 0.895578 |
| [PENGU 2x long](https://app.lighter.xyz/public-pools/281474976698775) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [LDO 2x long](https://app.lighter.xyz/public-pools/281474976698777) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [TRX 2x long](https://app.lighter.xyz/public-pools/281474976706167) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -20.6715% | -0.816853 |
| [S 2x long](https://app.lighter.xyz/public-pools/281474976708411) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 335.4812% | 0.895633 |
| [CRV 2x long](https://app.lighter.xyz/public-pools/281474976708835) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 33.1375% | 0.895619 |
| [IP 2x long](https://app.lighter.xyz/public-pools/281474976710317) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [SEI 2x long](https://app.lighter.xyz/public-pools/281474976710415) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 14.804% | 0.894331 |
| [ENA 2x long](https://app.lighter.xyz/public-pools/281474976710540) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [AI16Z 2x long](https://app.lighter.xyz/public-pools/281474976710578) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [FLOKI 2x long](https://app.lighter.xyz/public-pools/281474976710600) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [TRUMP 2x long](https://app.lighter.xyz/public-pools/281474976710609) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [POL 2x long](https://app.lighter.xyz/public-pools/281474976710624) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [TON 2x long](https://app.lighter.xyz/public-pools/281474976710628) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [DOT 2x long](https://app.lighter.xyz/public-pools/281474976710630) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [NEAR 2x long](https://app.lighter.xyz/public-pools/281474976710632) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |

### Fixed short exposure products

| Pool | 30-day return | Maximum drawdown | BTC beta | First / last date | Days with a price | CAGR | Weekly Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [APT 2x short](https://app.lighter.xyz/public-pools/281474976710416) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -23.2637% | 0.670337 |
| [BTC 3x short](https://app.lighter.xyz/public-pools/281474976710650) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 80.0948% | 1.20404 |
| [ETH 3x short](https://app.lighter.xyz/public-pools/281474976710652) | -0% | -0% | -0 | 2025-08-05 / 2026-11-11 | 464 | 285.5579% | 1.404677 |
| [ASTER 2x short](https://app.lighter.xyz/public-pools/281474976687562) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | -99.5321% | -0.299484 |
| [SOL 3x short](https://app.lighter.xyz/public-pools/281474976710648) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 99.5101% | 1.108402 |
| [PUMP 1x short](https://app.lighter.xyz/public-pools/281474976703225) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -62.9442% | -0.283948 |
| [DOGE 2x short](https://app.lighter.xyz/public-pools/281474976710646) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -24.7848% | 0.559198 |
| [BONK 2x short](https://app.lighter.xyz/public-pools/281474976710601) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -90.1431% | -0.184133 |
| [PENDLE 2x short](https://app.lighter.xyz/public-pools/281474976708832) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -79.4946% | 0.105805 |
| [WLFI 2x short](https://app.lighter.xyz/public-pools/281474976692953) | 0% | 0% | 0 | 2025-08-26 / 2026-11-11 | 443 | -85.7478% | -0.749111 |
| [PEPE 2x short](https://app.lighter.xyz/public-pools/281474976710644) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -64.8378% | 0.38438 |
| [WLD 2x short](https://app.lighter.xyz/public-pools/281474976710640) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -98.1896% | -0.706112 |
| [HYPE 2x short](https://app.lighter.xyz/public-pools/281474976710563) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.8919% | -2.251706 |
| [TAO 2x short](https://app.lighter.xyz/public-pools/281474976710625) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.7116% | -1.668085 |
| [NMR 2x short](https://app.lighter.xyz/public-pools/281474976692288) | 0% | 0% | 0 | 2025-08-29 / 2026-11-11 | 440 | -99.9995% | -4.077037 |
| [POL 2x short](https://app.lighter.xyz/public-pools/281474976710623) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -59.3476% | 0.037527 |
| [TRUMP 2x short](https://app.lighter.xyz/public-pools/281474976710608) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -16.3526% | 0.691592 |
| [FARTCOIN 2x short](https://app.lighter.xyz/public-pools/281474976710579) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.9039% | -2.102191 |
| [ICP 2x short](https://app.lighter.xyz/public-pools/281474976641576) | 0% | 0% | 0 | 2025-11-08 / 2026-11-11 | 369 | -3.1191% | 0.065707 |
| [ZEC 2x short](https://app.lighter.xyz/public-pools/281474976664629) | 0% | 0% | 0 | 2025-10-03 / 2026-11-11 | 405 | -100% | -3.153269 |
| [POPCAT 2x short](https://app.lighter.xyz/public-pools/281474976710575) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.8663% | -1.899402 |
| [UNI 2x short](https://app.lighter.xyz/public-pools/281474976710537) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -64.3196% | 0.228791 |
| [SUI 2x short](https://app.lighter.xyz/public-pools/281474976710605) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -99.7958% | -2.344474 |
| [STRK 2x short](https://app.lighter.xyz/public-pools/281474976641572) | 0% | 0% | 0 | 2025-11-08 / 2026-11-11 | 369 | -0.6565% | 1.710521 |
| [FIL 2x short](https://app.lighter.xyz/public-pools/281474976641574) | 0% | 0% | 0 | 2025-11-08 / 2026-11-11 | 369 | -0.9139% | 0.361235 |
| [XAG 2x short](https://app.lighter.xyz/public-pools/281474976647176) | 0% | 0% | 0 | 2025-10-20 / 2026-11-11 | 388 | 0% | — |
| [XAU 2x short](https://app.lighter.xyz/public-pools/281474976647178) | 0% | 0% | 0 | 2025-10-20 / 2026-11-11 | 388 | 0% | — |
| [MONAD 2x short](https://app.lighter.xyz/public-pools/281474976656642) | 0% | 0% | 0 | 2025-10-08 / 2026-11-11 | 400 | 23.9183% | 0.964927 |
| [EDEN 2x short](https://app.lighter.xyz/public-pools/281474976665433) | 0% | 0% | 0 | 2025-10-02 / 2026-11-11 | 406 | -1.8084% | 0.952321 |
| [2Z 2x short](https://app.lighter.xyz/public-pools/281474976665435) | 0% | 0% | 0 | 2025-10-02 / 2026-11-11 | 406 | 34.4157% | 0.716395 |
| [FF 2x short](https://app.lighter.xyz/public-pools/281474976667673) | 0% | 0% | 0 | 2025-09-29 / 2026-11-11 | 409 | -21.3364% | 0.862988 |
| [APEX 2x short](https://app.lighter.xyz/public-pools/281474976670931) | 0% | 0% | 0 | 2025-09-28 / 2026-11-11 | 410 | 42.5669% | 0.956047 |
| [0G 2x short](https://app.lighter.xyz/public-pools/281474976680299) | 0% | 0% | 0 | 2025-09-24 / 2026-11-11 | 414 | 7.2337% | 0.907487 |
| [TOSHI 2x short](https://app.lighter.xyz/public-pools/281474976687807) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | 2.108% | 0.839017 |
| [MYX 2x short](https://app.lighter.xyz/public-pools/281474976687809) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | — | — |
| [AVNT 2x short](https://app.lighter.xyz/public-pools/281474976687811) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | -2.3271% | 0.939871 |
| [SKY 2x short](https://app.lighter.xyz/public-pools/281474976687813) | 0% | 0% | 0 | 2025-09-18 / 2026-11-11 | 420 | 4.0071% | 0.938659 |
| [PYTH 2x short](https://app.lighter.xyz/public-pools/281474976691057) | 0% | 0% | 0 | 2025-09-08 / 2026-11-11 | 430 | -1.1859% | 0.742433 |
| [XMR 2x short](https://app.lighter.xyz/public-pools/281474976691059) | 0% | 0% | 0 | 2025-09-08 / 2026-11-11 | 430 | -0.4428% | 0.9302 |
| [LINEA 2x short](https://app.lighter.xyz/public-pools/281474976691920) | 0% | 0% | 0 | 2025-09-01 / 2026-11-11 | 437 | -17.3308% | 0.631316 |
| [DOLO 2x short](https://app.lighter.xyz/public-pools/281474976692286) | 0% | 0% | 0 | 2025-08-29 / 2026-11-11 | 440 | -1.9119% | 0.884698 |
| [CRO 2x short](https://app.lighter.xyz/public-pools/281474976692347) | 0% | 0% | 0 | 2025-08-29 / 2026-11-11 | 440 | 0.8453% | 0.495595 |
| [XPL 2x short](https://app.lighter.xyz/public-pools/281474976694731) | 0% | 0% | 0 | 2025-08-22 / 2026-11-11 | 447 | 0% | 0.906848 |
| [YZY 2x short](https://app.lighter.xyz/public-pools/281474976695151) | — | — | — | 2025-08-21 / 2026-11-11 | 448 | — | — |
| [VVV 2x short](https://app.lighter.xyz/public-pools/281474976695498) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | -0.3589% | 0.909236 |
| [MORPHO 2x short](https://app.lighter.xyz/public-pools/281474976695500) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | -0.4953% | 0.908262 |
| [TIA 2x short](https://app.lighter.xyz/public-pools/281474976695502) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | 1.708% | 0.909578 |
| [USELESS 2x short](https://app.lighter.xyz/public-pools/281474976695504) | 0% | 0% | 0 | 2025-08-20 / 2026-11-11 | 449 | 1.0169% | 0.909731 |
| [AERO 2x short](https://app.lighter.xyz/public-pools/281474976696148) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | — | — |
| [ETHFI 2x short](https://app.lighter.xyz/public-pools/281474976696150) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | 0% | 0.90239 |
| [GMX 2x short](https://app.lighter.xyz/public-pools/281474976696152) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | 0% | 0.902066 |
| [DYDX 2x short](https://app.lighter.xyz/public-pools/281474976696154) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | — | — |
| [MNT 2x short](https://app.lighter.xyz/public-pools/281474976696156) | 0% | 0% | 0 | 2025-08-13 / 2026-11-11 | 456 | 0% | 0.902077 |
| [ZRO 2x short](https://app.lighter.xyz/public-pools/281474976696955) | 0% | 0% | 0 | 2025-08-08 / 2026-11-11 | 461 | 5.5567% | 0.901271 |
| [HBAR 2x short](https://app.lighter.xyz/public-pools/281474976696957) | 0% | 0% | 0 | 2025-08-08 / 2026-11-11 | 461 | 4.187% | 0.397614 |
| [BCH 2x short](https://app.lighter.xyz/public-pools/281474976696959) | 0% | 0% | 0 | 2025-08-08 / 2026-11-11 | 461 | 3.0601% | 0.900463 |
| [PROVE 2x short](https://app.lighter.xyz/public-pools/281474976697776) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 65.8755% | 0.895458 |
| [ZK 2x short](https://app.lighter.xyz/public-pools/281474976698538) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -9.823% | 0.895532 |
| [OP 2x short](https://app.lighter.xyz/public-pools/281474976698540) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -9.7575% | 0.902654 |
| [LAUNCHCOIN 2x short](https://app.lighter.xyz/public-pools/281474976698542) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 4.4366% | 0.895589 |
| [ZORA 2x short](https://app.lighter.xyz/public-pools/281474976698544) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -1.6048% | 0.895545 |
| [GRASS 2x short](https://app.lighter.xyz/public-pools/281474976698546) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [RESOLV 2x short](https://app.lighter.xyz/public-pools/281474976698548) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -21.8532% | 0.895345 |
| [ARB 2x short](https://app.lighter.xyz/public-pools/281474976698596) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -17.6687% | 0.950675 |
| [PAXG 2x short](https://app.lighter.xyz/public-pools/281474976698601) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 2.7104% | 0.507303 |
| [PENGU 2x short](https://app.lighter.xyz/public-pools/281474976698774) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -13.0663% | 0.887993 |
| [LDO 2x short](https://app.lighter.xyz/public-pools/281474976698776) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -23.4312% | 0.895568 |
| [SYRUP 2x short](https://app.lighter.xyz/public-pools/281474976706164) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 78.888% | 0.844775 |
| [TRX 2x short](https://app.lighter.xyz/public-pools/281474976706166) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 34.688% | 0.756241 |
| [SPX 2x short](https://app.lighter.xyz/public-pools/281474976706997) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 381.9899% | 0.895568 |
| [VIRTUAL 2x short](https://app.lighter.xyz/public-pools/281474976706999) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [S 2x short](https://app.lighter.xyz/public-pools/281474976708410) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | -33.295% | 0.533643 |
| [ADA 2x short](https://app.lighter.xyz/public-pools/281474976708412) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 41.3057% | 0.99512 |
| [ONDO 2x short](https://app.lighter.xyz/public-pools/281474976708830) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 117.186% | 0.869268 |
| [CRV 2x short](https://app.lighter.xyz/public-pools/281474976708834) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 453.6247% | 0.887572 |
| [LTC 2x short](https://app.lighter.xyz/public-pools/281474976710314) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 202.7887% | 1.109954 |
| [IP 2x short](https://app.lighter.xyz/public-pools/281474976710316) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [KAITO 2x short](https://app.lighter.xyz/public-pools/281474976710318) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 1,335.0336% | 0.89563 |
| [SEI 2x short](https://app.lighter.xyz/public-pools/281474976710414) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 564.6962% | 0.940124 |
| [ENA 2x short](https://app.lighter.xyz/public-pools/281474976710539) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [MKR 2x short](https://app.lighter.xyz/public-pools/281474976710541) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 374.4% | 0.919716 |
| [AAVE 2x short](https://app.lighter.xyz/public-pools/281474976710543) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [JUP 2x short](https://app.lighter.xyz/public-pools/281474976710559) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [BNB 2x short](https://app.lighter.xyz/public-pools/281474976710561) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [AI16Z 2x short](https://app.lighter.xyz/public-pools/281474976710577) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [BERA 2x short](https://app.lighter.xyz/public-pools/281474976710581) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 280.2574% | 0.89563 |
| [FLOKI 2x short](https://app.lighter.xyz/public-pools/281474976710599) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [SHIB 2x short](https://app.lighter.xyz/public-pools/281474976710603) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 52.8973% | 1.086798 |
| [TON 2x short](https://app.lighter.xyz/public-pools/281474976710627) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [DOT 2x short](https://app.lighter.xyz/public-pools/281474976710629) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [NEAR 2x short](https://app.lighter.xyz/public-pools/281474976710631) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | 48.2122% | 0.877705 |
| [AVAX 2x short](https://app.lighter.xyz/public-pools/281474976710633) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [LINK 2x short](https://app.lighter.xyz/public-pools/281474976710635) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [XRP 2x short](https://app.lighter.xyz/public-pools/281474976710637) | — | — | — | 2025-08-05 / 2026-11-11 | 464 | — | — |
| [WIF 2x short](https://app.lighter.xyz/public-pools/281474976710642) | 0% | 0% | 0 | 2025-08-05 / 2026-11-11 | 464 | — | — |
