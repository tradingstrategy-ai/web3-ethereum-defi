# FuturAI Labs

Reviewed on 9 November 2026. The user supplied the official
[X account](https://x.com/FuturAILabs) and [FM's profile](https://x.com/FMChain_),
not an individual tweet URL. The public name is FuturAI Labs, rather than
Futura Labs. The official profile bio links the low-volatility vault.

The [official website](https://www.futurailabs.xyz) describes fully automated,
24/7 mean reversion: identify deviations from historical averages, enter on
strong deviations, and close positions gradually. Its
[application configuration](https://www.futurailabs.xyz/app.js?v=10) publishes
both branded vault addresses. Sigma 7 is a separate partner strategy and must
not supply tags for the native vaults.

The website's partner section identifies a collaboration with
[Volpex](https://www.volpex.io) and features [Sigma 7](https://www.sigma7.io).
Both partner sites identify Sigma 7 as Volpex's automated liquidity-provision
strategy. The curator description links both sites and preserves this
organisation/product distinction.

The [complete Hyperliquid vault inventory](https://stats-data.hyperliquid.xyz/Mainnet/vaults)
and live `vaultDetails` calls to the [info endpoint](https://api.hyperliquid.xyz/info)
confirm three vaults led by `0x92f33bee92dd86db292e3bc8500ffc5619d50430`:

| Vault | Hypercore address | Published strategy |
| --- | --- | --- |
| FuturAI Labs - High Sharpe, Low Volatility | `0xb65dd7c56afbf3b272ab5fc49be44b47dca18003` | Systematic buying of dips and selling of rebounds; long-only, maximum 1.5x leverage |
| FuturAI Labs - High Sharpe, Medium Volatility | `0x53375ca9a649f337d83d0834fec0db97640a2a06` | Same engine; long-only, maximum 2x leverage |
| Breaking (closed) | `0xbefed76a71682b1dc48cca5a10f397cbc492a216` | Catch breakouts on daily timeframes; automation and leverage undisclosed |

Ownership here means attribution to the curator/operator, not ownership of
all depositor assets. Breaking is attributed through its matching leader,
not through a claim that it appears on the current website. Exact address
mappings preserve attribution if names change. No broad wallet rule is added.

Curator identity and profile metadata resolve during export; no curator
migration is needed. Migration 051 refreshes only persisted strategy tags.
The site and live API checks were read-only; no production refresh was run.

The local snapshot (last prices on 28 October 2026) contains both branded
vaults. Low volatility has first-seen metadata of 26 February 2026 and price
rows from 9 February; medium volatility has first-seen metadata of 1 April
2026 and price rows from 5 March. These price histories already predate their
recorded creation times; investigate that upstream timestamp discrepancy
separately rather than treating it as a missing-history gap. Breaking is absent
from this snapshot. No evidence here justifies a historical price backfill.

On 9 November 2026 the maintainer requested the high-frequency, high-win-rate
category for both branded vaults. This is a maintained classification, not a
measured win-rate or latency claim. Migration 052 refreshes their stored tags
after migration 051; Breaking retains its breakout classification.
