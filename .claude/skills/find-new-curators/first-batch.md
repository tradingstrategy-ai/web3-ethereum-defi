# First curator discovery batch

Report-only output from running the `find-new-curators` skill.

Local inputs used:

- Existing curator feeder YAMLs: 60 curator entries
- `~/.tradingstrategy/vaults/top_vaults_by_chain.json`, generated `2026-05-01T06:58:11Z`
- Native DuckDBs: Hyperliquid, GRVT, Lighter, Hibachi
- Obvious broken or impossible NAV outliers were filtered before ranking

## Summary

| Candidate | Type | Confidence | Evidence | Suggested action |
|-----------|------|------------|----------|------------------|
| Flowdesk | third-party curator | High | `Flowdesk AUSD Equity Strategy`, Morpho, about `$17.3M`. Morpho forum says Flowdesk is introducing itself as a curator and Flowdesk acts as vault curator. | Add `flowdesk.yaml`. |
| Anthias Labs | third-party curator | High | Felix Morpho vaults about `$53M`. Felix docs say Anthias Labs is Curator, Allocator, and Guardian, and Anthias says it supports Felix vault curation. | Add `anthias-labs.yaml`; consider a `Felix` name pattern only after false-positive checks. |
| Janus Henderson Anemoy | third-party/RWA asset manager | High | Seven Centrifuge/ERC-7540 vaults, about `$2.8B`. Janus official release says Janus partners with Anemoy/Centrifuge and serves as sub-advisor. | Add `janus-henderson-anemoy.yaml` or `anemoy.yaml` after deciding canonical naming. |
| TelosC / Telos Consilium | third-party curator | Medium | Nine Euler vaults, about `$318M`. External coverage calls TelosC a risk curator. | Add after verifying official website and social links. |
| Kappa Lab | third-party curator | Medium | Hibachi FLP, about `$538k`. Local Hibachi description says the vault is operated by Kappa Lab. | Add `kappa-lab.yaml`. |
| Smokehouse | name-pattern update | High | Smokehouse Morpho vaults about `$56M`. Morpho source refers to Steakhouse Financial's Smokehouse. | Do not add a new curator; add `Smokehouse` pattern to `steakhouse-financial`. |
| Gains Network / gTrade | protocol-managed curator | High | Twelve Gains vaults, about `$19.4M`. Current code has `gtrade`, but exported protocol slug is `gains-network`. | Add `gains-network` to protocol-curated handling or create an alias. |
| CAP | alias curator | Medium | Existing `cap` protocol feeder; protocol-owned `cap USDC` vault about `$61.6M`. | Consider curator alias to `cap`. |
| Upshift | alias curator | Medium | Existing `upshift` protocol feeder; three protocol-owned vault names about `$30.7M`. | Consider curator alias to `upshift`. |
| Curvance | alias curator | Medium | Existing `curvance` protocol feeder; seven protocol-owned vault names about `$26.6M`. | Consider curator alias to `curvance`. |
| GRVT | protocol-managed curator | Medium | Existing `grvt` protocol feeder; GRVT Liquidity Provider about `$19.6M`. | Consider curator alias to `grvt` or native protocol-managed handling. |
| Altura | alias curator | Medium | Existing `altura` protocol feeder; protocol-owned vault about `$17.5M`. | Consider curator alias to `altura`. |
| Summer.fi | alias curator | Medium | Existing `summer-fi` protocol feeder; five protocol-owned vault names about `$13.6M`. | Consider curator alias to `summer-fi`. |
| YieldNest | alias curator | Medium | Existing `yieldnest` protocol feeder; YieldNest RWA MAX about `$9.0M`. | Consider curator alias to `yieldnest`. |
| sBOLD | alias curator | Medium | Existing `sbold` stablecoin/protocol feeder; sBOLD vault about `$9.0M`. | Consider whether existing `k3-capital` alias already covers this well enough. |

## Defer or reject

| Candidate | Finding |
|-----------|---------|
| Greenhouse | High NAV, but already flagged locally as illiquid/Main Street related. Not a clean curator addition. |
| Felix | Vault name is not necessarily the curator. Anthias Labs appears to be the actual curator/guardian. |
| Trust Wallet | Looks like an integrator/distributor. Smokehouse maps better to Steakhouse Financial. |
| Lista | Recent source says Gauntlet and RockawayX are curators, so Lista itself is not a clean curator candidate. |
| Concrete | Plausible prefix, but needs stronger official curator evidence before adding. |
| Galaxy | Plausible institutional name, but needs direct vault curator evidence. |
| TermMax | Appears as protocol/product prefix in several vaults; needs verification. |
| Mithras | Single high-value Euler name, but local flags include broken-vault context. Needs verification. |
| Hakutora | Morpho prefix candidate, but needs external identity evidence. |
| Pangolins | Appears across Euler/Morpho names; needs external identity evidence. |
| Api3 | Likely protocol/project name rather than curator unless official vault curation is confirmed. |

## Sources checked

- Flowdesk Morpho forum: `https://forum.morpho.org/t/announcing-flowdesk-ausd-rwa-strategy/2213`
- Anthias Labs Morpho forum: `https://forum.morpho.org/t/anthias-labs-curator-introduction/2061`
- Felix docs: `https://usefelix.gitbook.io/felix-docs/terms/terms-and-conditions`
- Janus Henderson official release: `https://ir.janushenderson.com/News--Events/news/news-details/2024/Janus-Henderson-to-Partner-with-Anemoy-and-Centrifuge-on-Its-First-Tokenized-Fund/default.aspx`
- Anemoy JTRSY page: `https://www.anemoy.io/funds/jtrsy`
- TelosC/Euler external coverage: `https://t.signalplus.com/crypto-news/detail/peckshield-27m-defi-risk-euler-telosc-vaults`
