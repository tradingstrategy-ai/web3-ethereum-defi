# D2 vault description research

Reviewed 5 November 2026. The [official vault catalogue](https://d2.finance/api/vaults?page=1&per_page=100&sort=recently_added&direction=desc)
returned 54 entries, including hidden/legacy products. The 47 non-empty
`explanation` fields support the maintained summaries in the adapter.
Seven entries have no strategy explanation; these and unlisted deployments
receive an explicit evidence-gap summary rather than a strategy inferred
from their names. Catalogue presence does not establish that a vault is active.
Addresses must be paired with chain IDs: Hot7 and Kimchi Premium share an
address on different chains, as do other deployments.

The following source excerpts preserve the strategy evidence for plain-text
summaries. They are research material, not public display copy. Risk ratings,
performance targets, balances, fees and transient incentives are omitted from
the summaries. No runtime API dependency is introduced.

## HyperBERA.USDC (80094)

- Address: `0x36b933554782b108bb9962ac00c498acbceb706d`

Goal: Generate consistent stable returns for conservative investors, through an ensemble of high quality alphas, while netting out market risk. Enhanced with Boyco incentives.

Strategy: Unlike standard yield farms, HyperBera.USDC combines funding arbitrage, yield farming, and structured options strategies to generate returns. Built on a proven model

## HyperBera.weETH (42161)

- Address: `0x0178b56fea3d7b5b9f9e0cdad486522de948730f`

Goal: To maximize weETH yield while maintaining 100% ETH exposure, utilizing Hyperliquid’s HLP vault, arbitrage opportunities, and farming incentives across Berachain, Boyco, and Derive.

Strategy: A dual-phase strategy utilizing weETH as collateral for yield generation in Hyperliquid first and after Boyco/Berachain launch, actively managed. Majority of flow utilized in Boyco and Berachain

## HyperBera (42161)

- Address: `0xc027ec28f76d92d4124fcbffcf6b25137a84968c`

Goal: To maximize BTC yield while maintaining 100% BTC exposure, utilizing Hyperliquid’s HLP vault, arbitrage opportunities, and farming incentives across Berachain, Boyco, and Derive.

Strategy: A dual-phase strategy utilizing SolvBTC as collateral for yield generation in Hyperliquid first and after Boyco/Berachain launch, actively managed.

## Alpha Strategies (42161)

- Address: `0x75288264fdfea8ce68e6d852696ab1ce2f3e5004`
- Announcement: https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9

Goal: The D2 Finance Alpha Strategies Fund, also known as HYPE++ for DeFi users, is a multi-strategy fund focused on generating risk-adjusted returns through Relative Value, Volatility Arbitrage, Event-driven Positioning, and Tactical Cross-asset Hedging in digital asset markets. Focused on trading blue-chip crypto assets BTC, ETH, the fund captures convex upside while actively hedging downside volatility.

## ETH++ (42161)

- Address: `0x27d22eb71f00495eccc89bb02c2b68e6988c6a42`
- Announcement: https://medium.com/@D2.Finance/d2-finance-eth-market-launching-on-camelot-5e420cff0181

Goal: ETH++ allocates on a risk adjusted basis for the longterm and employs opportunistic arbitrage strategies. Conservatively aligned with a smoother return profile.

Strategy: ETH++ employs volatility arbitrage-like strategies by buying or selling ETH and other blue-chip derivatives, then hedging and actively trading the delta on platforms such as Hyperliquid and GMX, typically extracting a funding arbitrage.

## d2USDC v2 (42161)

- Address: `0xd1d64daeed7504ef3eb056aa2d973bd064843a84`

Goal: d2USDC aims to achieve sustainable yield by capitalizing on market inefficiencies and strategic arbitrage opportunities. It is designed to allocate on a risk-adjusted basis, balancing yield generation with capital preservation, to offer investors a stable and lucrative return profile.

Strategy: d2USDC combines funding arbitrage, yield farming through tokens such as ARB, DOLO, and EDA, and volatility arbitrage. The strategy leverages USDC to farm yields, capitalize on funding rate disparities, and dynamically hedge against market movements. It employs platforms like GMX for active delta management to optimize returns.

## Kodiak++ (80094)

- Address: `0x2b8d0420996a2753ef21c25c94eae9fc0c0aed1e`

Goal: Maximize BGT yield accumulation in a robust, actively managed strategy that doesn’t just react to the market — it capitalizes on it. Enhanced with Boyco incentives.

Strategy: Looping and V3 management to dynamically optimize exposure and capital efficiency, with arbitrage yield and options based risk management. Built on a proven model.

## dgnBeraland (80094)

- Address: `0xbe75c8a7e58c7901d2e128dc8d3b6de2481f1f79`

Goal: Optimizing for upside potential on the BERA token (1-3x) exposure. Enhanced with Boyco incentives.

Strategy: This strategy offers a single click solution to a calibrated mix of leverage (through options), DeFi efficiency, and options-based yield.

## HyperTRUMP (42161)

- Address: `0xfddd73ecd0d0d75e902a567811a70e167a262fab`

Goal: HyperTRUMP aims to exploit short-lived dislocations and capitalize on volatile price action while maintaining partial exposure to structured, sustainable yield streams. This strategy balances extreme risk with DeFi efficiency, creating asymmetric return opportunities for high-risk investors.

Strategy: Up to 80% Trump-Focused Arbitrage: Allocated to event-driven opportunities around the Trump Token, emphasizing funding arbitrage and capturing early volatility-driven inefficiencies. 20% Yield Enhancement: Deployed in Hyperbera.WETH, offering multi-layered yield opportunities including Berachain PoL rewards, HyperLiquid arbitrage, and Boyco incentives.

## DRV++ (1)

- Address: `0x07dff4087b43c4a759f4fc69511c26d51929daf4`

Goal: DRV++ aims to generate superior risk-adjusted returns by leveraging DeFi’s most advanced derivatives infrastructure while capturing additional yield through protocol incentives, airdrop farming, and volatility-driven trading.

Strategy: 80% Yield Buffer: Allocated to stable, locked yields via Aave, Pendle, and Ethena, ensuring a secure foundation. 20% Risk-On Trading: Actively managed derivatives strategies, including Relative Value arbitrage between Hyperliquid and Derive options exposure

## dgnBTC v2 (8453)

- Address: `0x6c05a7d2c24b48fc3c615d294fec2eb068548897`

Goal: D2’s dgnBTC strategy is designed to provide an average exposure to btc’s upside of 2X. The strategy incorporates ‘implied puts’ as a protective measure when exposure exceeds 100%, designed to replicate the performance of directly holding BTC.

Strategy: DgnBTC is for those who looking for higher upside than holding BTC with a clear “risk-on” bias for maximizing BTC exposure.

## d2ETH (42161)

- Address: `0x195a9e0f29f96d4ab2139ee1272380a4aa352890`

Goal: d2ETH is a straight-up competitor to other methods of farming ETH and targets to beat Lido and other ETH staking solutions in the long run. It’s the ideal strategy for those looking to stay fully exposed to ETH’s price action while enhancing their yield.

Strategy: The d2ETH managed strategy borrows USDC against the ETH collateral and redeploys a portion of capital into ETH++ as portable alpha, with dynamic hedging, effectively boosting investor yield while maintaining full beta to ETH.

## dgnETH v2 (42161)

- Address: `0x0f76de33a3679a6065d14780618b54584a3907d4`

Goal: D2’s dgnETH strategy is designed to provide an average exposure to ETH’s upside of 2X, with the potential to reach up to 4X during bullish market conditions. The strategy incorporates ‘implied puts’ as a protective measure when exposure exceeds 100%, designed to replicate the performance of directly holding ETH.

Strategy: DgnETH is for those who strive for a balance between risk and reward, embodying the essence of D2’s philosophy, but with a clear “risk-on” bias for maximizing ETH exposure.

## iARB (42161)

- Address: `0xb0730aa7d6e880f901b5d71a971096db56895a0f`

Goal: The primary goal of the ARB++ Vault is to deliver superior performance compared to the average user’s allocation within the Arbitrum ecosystem, doing so on a risk-adjusted basis over the long term.

Strategy: This vault is designed to emulate the user experience of single-sided staking for ARB, targeting users who are bullish on ARB over the long term, desire lower volatility AND yield on top of their ARB.This means users will deposit ARB to receive an adjusted amount of ARB (+- the PNL) at the end of the epoch.

## ARB++ (42161)

- Address: `0x291344fbaac4fe14632061e4c336fe3b94c52320`

Goal: The primary goal of the ARB++ Vault is to deliver superior performance compared to the average user’s allocation within the Arbitrum ecosystem, doing so on a risk-adjusted basis over the long term.

Strategy: This vault is designed to emulate the user experience of single-sided staking for ARB, targeting users who are bullish on ARB over the long term, desire lower volatility AND yield on top of their ARB.This means users will deposit ARB to receive an adjusted amount of ARB (+- the PNL) at the end of the epoch.

## GMX++ (42161)

- Address: `0xab2743a3a2e06d457368e901f5f927f271fa1374`

Goal: Maximize the utility of the GMX token while maintaining full delta exposure and creating diversified revenue streams ontop, aiming to acquire more GMX.

Strategy: GMX++ focuses on GMX PERP basis trading and leveraging GMX as collateral across multiple lending platforms like Silo and Dolomite. By unlocking liquidity for additional yield opportunities, without losing delta exposure to GMX, this strategy allows maximized capital efficiency while adding beta (GLV ) + portable alpha (eth++) for diversified returns as GMX.

## wHYPE (42161)

- Address: `0xac75f0c46723432a2303f2a7c7769535a179ed56`

Goal: Exposure to Hype while farm EDA point on one hottest project of Berachain

Strategy: Wrapped wHYPE + EDA points

## wHLP (42161)

- Address: `0x907a9f69061736ad82811cccd6add9dc4a2352a9`

Goal: To achieve exposure to HLP yield leveraged 2 times while farming EDA points on one of the hottest projects on Berachain.

Strategy: By enabling community participation in liquidity provision, the HLP vault enhances Hyperliquid's overall liquidity and efficiency. Additionally, it allows farming of EDA points, provided there is an allocation to our d2 finance strategies.

## PlsDAO++ v2 (42161)

- Address: `0x36b1939adf539a4ac94b57dbad32faecd5bcf4d0`

Goal: Personalized for each institution

Strategy: Bespoke asset allocation solutions

## Dewhales v2 (42161)

- Address: `0x34f0fdd80a51dfd8ba42343c20f89217280d760e`

Goal: Personalized for each institution.

Strategy: Bespoke asset allocation solutions.

## xD2e (42161)

- Address: `0x999a57ae7694298126a5db2e44f778ca486b14fc`

Goal: D2 Rangers who actively participate in the platform and demonstrate long-term commitment will probabilistically receive significantly greater rewards than those who merely hold tokens or invest in vaults.

Strategy: OFT wrapper for xD2

## GTxD2 (42161)

- Address: `0x1176c3760af6a1dbaa5bbd0cc6cda8a2ed6b785e`

Goal: Transparent on-chain block trading.

Strategy: Guaranteed TWAP (Time-Weighted Average Price)

## Hype EVM++ (999)

- Address: `0x7da637df83778704464208f092dbdbf9f386df96`

No strategy explanation in the catalogue.

## LYRA++ (42161)

- Address: `0x183424d5ae5ec9fd486634bc566d0f75ad9c9109`

No strategy explanation in the catalogue.

## GEARPENDLE++ (42161)

- Address: `0x5b49d7fae00de64779ddcd6b067c8eb046bd9a0b`

No strategy explanation in the catalogue.

## renzoSilo++ (42161)

- Address: `0x80c403807b1032d7cb19b6d612ce23f05a213d36`

No strategy explanation in the catalogue.

## VERTEXMOBY++ (42161)

- Address: `0x23f556f4df7a263ce68ee29659aa4ea632a6a5bc`

No strategy explanation in the catalogue.

## dgnLYRA (42161)

- Address: `0x9aa9bd7b032e527b0820c85cee89093dc33df8bd`

No strategy explanation in the catalogue.

## Test (42161)

- Address: `0xeb3194d69201b04729f1a618d95519cdae24d6c0`

No strategy explanation in the catalogue.

## hBERA (80094)

- Address: `0xace42f7e3f4672607897bf1951468031f0214359`
- Announcement: https://medium.com/@D2.Finance/hedged-bera-hbera-betting-on-berachains-community-hedging-against-ghost-towns-72d6332e95e2

Goal: To provide exposure to the potential growth of the Berachain ecosystem while employing hedging techniques to reduce downside risks and market volatility.

Strategy: Hedge against BERA volatility by partially hedging with "ghost towns" chains, targeting 80% to 120% exposure to BERA. The strategy balances long exposure to Berachain’s growth with a short book to mitigate overall market beta.

## HyperEuler (80094)

- Address: `0xf650ba4303ce164e1f6b215d4cbb5e212d307056`
- Announcement: https://medium.com/@D2.Finance/hypereuler-x-pol-pol-is-here-is-time-to-supercharge-your-bera-4dfaa8805d23

Goal: To maximize returns from Berachain's PoL system by strategically utilizing RFA incentives and leveraging mechanisms, thereby optimizing BGT (Berachain Governance Token) accumulation for participants.

Strategy: HyperEuler is a capital-efficient farming strategy that combines Euler's Rebalancing Farm Allocation (RFA) incentives with Kodiak's Proof-of-Liquidity (PoL) layer and Hyperliquid funding arbitrage.

## 2xBERA (80094)

- Address: `0xcd18006cc69c6d5fa4fd4eaf99910b58464fa3ae`
- Announcement: https://medium.com/@D2.Finance/infrared-origami-d2-2xbera-df7e7129df9a

Goal: Maximize BERA upside and outsized PoL yield while eliminating liquidation risk through active management

Strategy: Farms oriBGT yield, longs BERA with 2x leverage while capitalizing on Hyperliquid funding rate, and uses synthetic puts to auto-deleverage below $6, avoiding liquidation.

## dgnUpside (80094)

- Address: `0xbf075980792f8cc89dfb74b553acf6750a7e941b`

Goal: Capture peak crypto upside with multi-asset convexity while earning native yield from PoL—designed for high volatility, zero-liquidation environments.

Strategy: Farms BGT via PoL, auto-deploys call options on ETH, BTC, and BERA with dynamic 2–4x leverage. Uses implied puts to cap downside and minimize theta drag. All built on Berachain.

## HGB (999)

- Address: `0x0f00008223b4d4c2c466b4456df1d483743cbd60`

Goal: A dynamic basket options strategy complete with embedded leverage which maintains HYPE exposure while borrowing initial cash exposure to facilitate 10% exposure to Gold and 10% exposure to BTC. 

Strategy: The strategy leverages HYPE as collateral while giving you exposure to the enduring strength of Gold and Bitcoin.

## hXXI BTC (999)

- Address: `0xf44f49e6577b3934f981c6f0629d15154d2606e6`
- Announcement: https://medium.com/@D2.Finance/introducing-hyperliquid-xxi-hxxi-the-future-of-bitcoin-accumulation-on-chain-499c488e67ca

Goal: The hXXI BTC strategy fund represents a sophisticated institutional gateway for capital-efficient Bitcoin accumulation and yield. hXXI delivers transparent, risk-adjusted returns through auditable on-chain execution profiting from discrepancies in the BTC volatility surface, harvesting premiums from over-writing strategies, exploiting BTC price differences across venues, and dynamic hedging vs internal vaults.

Strategy: Hyperliquid TwentyOne: Bitcoin Accumulation, built for the New On-Chain Era

## dgnOOGA (80094)

- Address: `0xc4fee8c68293a63241b64e5a2ef07fcf89005dd3`
- Announcement: https://medium.com/@D2.Finance/4f10c3fbf0a3

Goal: Acquires OOGA yield layer deploying across Kodiak, Infrared, Origami, Dolomite. Dynamic rebalancing which aims to profit after pump and derisk on drawdowns (not an option, but behaves like one)

Strategy: Designed to grow your BERA and capture yield, even in bear markets, thanks to OOOGA aggregator fee flows.

## USDT0 hRWA (999)

- Address: `0x7410e69958a8ece2a51c231c8528513d4d668c7a`
- Announcement: https://medium.com/@D2.Finance/introducing-usdt0-hrwa-the-ultimate-stablecoin-yield-engine-on-hyperevm-f7e8b58068db

Goal: USDT0 hRWA employs a multi-strategy framework, by integrating a diverse array of alpha-generating strategies — ranging from tokenized RWA strategies such as SPX skew, spread monetization, etc, and DeFi-native yield generation — in order to deliver superior, risk-adjusted returns. 

Strategy: Multi-strategy stablecoin yield engine

## USDT0 Hyperliquid-RWA Strategies (999)

- Address: `0xade27c7dec9211973278876f3819aedc28cd50ca`
- Announcement: https://medium.com/@D2.Finance/introducing-usdt0-hrwa-the-ultimate-stablecoin-yield-engine-on-hyperevm-f7e8b58068db

Goal: USDT0 hRWA employs a multi-strategy framework, by integrating a diverse array of alpha-generating strategies — ranging from tokenized RWA strategies such as SPX skew, spread monitization, etc,to DeFi-native yield generation — to deliver superior, risk-adjusted returns 

Strategy: Multi-strategy stablecoin yield engine

## d2HYPE (999)

- Address: `0x3ebb11ba6a5b61c04d1a703ea10728d519945440`
- Announcement: https://medium.com/@D2.Finance/d2hype-turn-hype-volatility-into-20-apr-yield-b570d72a4ec0

Goal: Multi-strategy fund trading HYPE volatility through vol arbitrage, structured covered calls (OTM weekly strikes), dynamic delta hedging with protective puts, and lending yield when rates allow — balancing premium income, upside participation, and capital efficiency across all market conditions.

Strategy: Institutional-grade HYPE returns.

## dgnVengeance (999)

- Address: `0x6bf9345b5d6b27b5cbf2e463dc5e0b2afcedc21c`
- Announcement: https://medium.com/@D2.Finance/dgnvengeance-dgnupsides-last-dance-969739fb2325

Goal: Uses ETH collateral to buy out-of-the-money ETH calls at low implied volatility, creating dynamic, nonlinear leverage. Downside is capped by construction at a maximum drawdown of 40% of principal per epoch. Asymmetric payoff: for users comfortable with premium bleed and underperformance in stagnant markets, but significantly higher returns in volatile and upward markets.

Strategy: Capture ETH upside with implied leverage via nonlinear options convexity, capped downside, and no liquidation risk.

## hKRAKEN (999)

- Address: `0x4de611ed46aaf54a6850f97542ce7a58e7c8d0fe`
- Announcement: https://medium.com/@D2.Finance/introducing-hkraken-pioneering-tokenized-pre-ipo-options-6292f2f3d03c

Goal: Using receipt tokens as collateral, the vault strategy writes a call options at a $40 strike price, earning premiums denominated in dgnUpside (D2’s next gen strategy delivering upto 4x upside on ETH/BTC with no liquidations, leveraging Unit ETH as the ultimate underlying collateral) from buyers seeking leveraged exposure to Kraken’s pre-IPO potential. The strategy is selling Kraken upside.

Strategy: On-Chain Access to Kraken’s Pre-IPO Equity, hKRAKEN serves as a true proof of concept for the next generation of groundbreaking on-chain structured products

## CoreSolvBTC Covered Call (1116)

- Address: `0xa6c99234ebcda225ef32cefb8472363e09a51aea`

Goal: The D2 BTC Covered Call Strategy enables family offices and institutional investors to generate consistent yield from Bitcoin holdings through systematic selling of synthetic call options. Partial repackage of option resting orders, partial internal portfolio buys, combined with on-chain incentives enable pricing options at a discount (better than IBIT/Derbit) vs selling at a premium to mid via portfolio strategies requiring BTC exposure. 

Strategy: Enhanced SolvBTC Covered Call Strategy for CoreDAO 

## hSOL (999)

- Address: `0x8ef30c5ce9a460bfae82f1f039f7c5e5427d7018`
- Announcement: https://medium.com/@D2.Finance/introducing-hsol-20-apr-target-on-sol-with-hyperliquid-5b10672f6369

Goal: hSOL combines advanced options structuring to generate premiums and redeploys capital into funding arbitrage and/or high performing strategies on HyperEVM, such as D2's hRWA strategy. The strategy also farms Hyper Unit points. hSOL combines innovative DeFi option selling, cross-yield strategies, and a Solana-centric approach, bringing outsized APR for users who want to remain on-chain with their SOL.

Strategy: Institutional-grade SOL returns targeting 20% APR

## Alpha Strategies (999)

- Address: `0x195eb4d088f222c982282b5dd495e76dba4bc7d1`
- Announcement: https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9

Goal: The D2 Finance Alpha Strategies Fund, also known as HYPE++ for DeFi users, is a multi-strategy fund focused on generating risk-adjusted returns through Relative Value, Volatility Arbitrage, Event-driven Positioning, and Tactical Cross-asset Hedging in digital asset markets. Focused on trading blue-chip crypto assets BTC, ETH, the fund captures convex upside while actively hedging downside volatility.

## dgnHYPE++ (999)

- Address: `0x140e81f8c033c0705036e55be2f2fbce43e4595c`

Goal: Enhanced upside potential leveraging the D2 Finance HYPE++ Pendle market for structured risk vs reward.

Strategy: Borrow stables against HYPE, buys D2 Finance HYPE++ PT if spreads are favorable 

## HYPE++ Ethena (1)

- Address: `0x2406aacbdf8463176deb285adaa81768415b6c7e`
- Announcement: https://medium.com/@D2.Finance/hype-strategy-faq-654bc8d0efc9

Goal: The HYPE++ strategy will focus around taking advantage of blue-chip volatility, mispricing (through Relative value trades), and other inefficiencies in a risk-adjusted manner. HYPE++ employs volatility, relative value, statistical, funding, basis, and cross-platform arbitrage.

## Alpha Strategies (8453)

- Address: `0x2406aacbdf8463176deb285adaa81768415b6c7e`
- Announcement: https://medium.com/@D2.Finance/hype-base-institutional-grade-defi-bdce160e66b6

Goal: The D2 Finance Alpha Strategies Fund, also known as HYPE++ for DeFi users, is a multi-strategy fund focused on generating risk-adjusted returns through Relative Value, Volatility Arbitrage, Event-driven Positioning, and Tactical Cross-asset Hedging in digital asset markets. Focused on trading blue-chip crypto assets BTC, ETH, the fund captures convex upside while actively hedging downside volatility.

## texasHedge (999)

- Address: `0x208f63a7f60c319597c05fa5ec67fde41839bad6`
- Announcement: https://medium.com/@D2.Finance/texas-hedge-epoch-2-elon-takes-us-to-space-x-fec66a354ad3

Goal: Sell the richest vol on the board (DRAM calls, ~90v) to fund cheap convexity — S&P crash puts (~13v) and Tesla/Mag-7 calls — and ride SpaceX into inclusion via the pre-IPO perp. Indicative payoffs, If SpaceX gets included and the complex rallies hard +triple digits. The home run the trade is built for. If the broader market melts up across the board solid gains, but less than the targeted squeeze. The Tesla Mag 7 calls and the SpaceX perp win, while the S&P puts expire worthless and the short DRAM calls work against you. If the broader market sells off but the complex holds up: roughly +100% to +120%, as the cheap S&P puts pay off in the crash. If markets stay quiet or the IPO is delayed: roughly 0%, minus a small carry cost. If everything falls together: about 40% loss, governed by the stop ladder.

Strategy: A multi-leg bet that SpaceX does well: a strong IPO, the index funds that are now forced to buy it, and the broader Elon complex Tesla, the Mag7 rising with it. Structured for convex upside with fixed downside.

## ftUSDC (1)

- Address: `0x09dfcf4731149582d163a763e7dd553ebc18852d`

Goal: Fixed Term (ftUSDC) is a lending vault where funds are lent to D2 Finance at a fixed rate. The counter party is our Alpha Strategies (HYPE++) which may use them for margin in its strategies when opportunities arise. Similar structure to traditional multi-strategy funds. Tarting ~2x Aave USDC lending rates. 

## Kimchi Corridor (10)

- Address: `0x20833f2e96ab236d66d18a0064e1e8171b70e22e`
- Announcement: https://medium.com/@D2.Finance/texas-hedge-got-twins-kimchi-premium-and-the-ibit-ewy-volatility-corridor-e88c72b03ec0

Goal: Epoch 1: Buy IBIT realized vol and sell EWY realized vol as a single spread swap, struck to par on Bloomberg DLIB so the trade starts at flat premium by construction. The strike is the Korea premium itself — the vol points Korea trades over Bitcoin, indicatively mid-30s. The vault wins point-for-point as the realized premium comes in under the strike and loses point-for-point as it blows wider, down to a hard floor and no further. Indicative payoffs per vault, gross: Korea premium realizing at 15 pays about +20%; at 25, about +10%; at the strike, zero; Bitcoin realizing as loud as Korea pays about +35%; Bitcoin out-realizing Korea pays beyond that, uncapped. The full pricing script ships with the ticket — both tickers, all 43 fixings, the annualization, the floor — so settlement is recomputable, no library call to trust. Hard floor inside the payoff formula: worst case −40% of vault NAV, not one point more.

Strategy: A bet that the widest Korea-over-Bitcoin vol gap on record snaps back: long Bitcoin realized vol at the 10th percentile, short Korea realized vol near its highs. One floored spread swap — convex upside, fixed downside.

## Global Macro (4663)

- Address: `0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8`
- Announcement: https://medium.com/@D2.Finance/globalmacro-gm-vault-on-robinhood-take-from-the-markets-that-sleep-give-to-the-one-that-never-a66b2c62f300

Goal: The Global Macro Vault is a systematic directional fund focused on generating absolute returns through Systematic Momentum, Volatility-Aware Expression, and Around-the-Clock Cross-Session Positioning in global macro markets. Focused on trading equity indices, major commodities, and FX through options and delta-one instruments, the fund takes conviction-sized directional risk under a hard 10% loss limit per epoch, while principal earns onchain credit yield between signals.

## Debasement++ (1)

- Address: `0x1f7bae0549f9f0baf15d0d637900056bb9434c36`
- Announcement: https://medium.com/@D2.Finance/debasement-do-not-fade-the-money-printer-8f39a12eb777

Goal: The Debasement++ Vault is a structured directional strategy focused on capturing monetary debasement through a fully funded gold base, convex Bitcoin upside, and a systematic short against the market's most expensive proxy expression.Deposited in XAUT on Ethereum, the vault runs four legs — a physical gold base, short GLD puts, long BTC calls, and a managed MSTR short — inside a 100% to 200% NAV-equivalent band, opening near 130%. The structure is long the debasement regime, long gold vol carry, long BTC convexity, and short the corporate proxy's mNAV premium, with the first epoch running to November 16, 2026, sized to carry through US midterm election noise and settle after it.

## Hot7 (8453)

- Address: `0xafd240a8e564ad34663b6c42475027ed083088a2`
- Announcement: https://medium.com/@D2.Finance/hot7-structured-products-rwas-are-boring-d2-brings-the-spice-7b8d14dcd662

Goal: The HOT7 Vault is a systematic structured-equity fund focused on generating absolute returns through Systematic Momentum, Structured Cushion, and Paid Carry in US single-name equities. Focused on the seven strongest-momentum US stocks with listed options and HIP-3 perps, the fund holds the basket equal-weighted at 1.3x gross, fixed monthly and not reset daily, with a call-spread overwrite funding an SPX put-spread cushion. Epoch 1 earns Aave incentives and HIP-3 funding on the same names, delta flat, until the signal is strong enough to earn the structure. The $HOT layer is coming later.

## Kimchi Premium (10)

- Address: `0xafd240a8e564ad34663b6c42475027ed083088a2`
- Announcement: https://medium.com/@D2.Finance/texas-hedge-got-twins-kimchi-premium-and-the-ibit-ewy-volatility-corridor-e88c72b03ec0

Goal: Sell a single KOSPI 200 realized-volatility swap, struck July 20 off an independent Bloomberg fixing, held to the September 18 expiry with no mid-epoch discretion. The thesis in one line: Korea won't realize ~75 vol for two months. Indicative payoffs at a 75 strike, gross: if Korea realizes 25 (its three-year average), roughly +50%; at 45, about +30%; at 55, about +20%; at the strike, zero, no carry to pay; if realized runs through the ~115 cap, the −40% floor. SK Hynix earnings land inside the window on July 23 — that event risk is priced into the strike, not hedged away. Max loss is written into the derivative itself: exactly −40% of vault NAV, recomputable from public closes.

Strategy: A pure short-volatility bet that Korea's panic is overpriced: sell KOSPI 200 vol at the top of its three-year range and collect the premium if realized comes in soft. No delta, no direction — just the richest listed vol on the board versus what actually prints.

