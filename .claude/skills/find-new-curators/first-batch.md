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

Updated 2026-05-05 after deeper source checks.

| Candidate | Local vault evidence | External evidence | Decision |
|-----------|----------------------|-------------------|----------|
| Greenhouse | Three Sonic Euler vaults: `Mainstreet Greenhouse USDC`, `Greenhouse scUSD`, and `Greenhouse USDC`. The Trading Strategy page for `Greenhouse USDC` is already labelled `illiquid`/`blacklisted`, with notes that Main Street Market related products were wiped out in the 2025-10-10 event. | No clean curator identity was found. The strongest source is the Trading Strategy vault page warning about Main Street-related products and nonsensical returns. | **Reject for curator metadata.** Do not add `greenhouse.yaml` until a live, redeemable vault and official curator identity can be verified. Treat these rows as bad data / risk flags rather than curator coverage gaps. |
| Felix | Seven HyperEVM Morpho vaults use the Felix brand: `Felix USDC`, `Felix USDH`, `Felix USDe`, `Felix USDT0`, and Frontier variants. | Conflicting primary sources: Felix terms say Felix Labs is Owner while Anthias Labs is Curator, Allocator, and Guardian for Felix Morpho Vaults; a later Morpho forum post says Felix would like to introduce itself as curator for Morpho on Hyperliquid. Anthias also says it supports Felix vault curation. | **Defer Felix as its own curator.** Current code maps Felix vault names to `anthias-labs`; keep that unless on-chain role checks show Felix now holds the curator role for a distinct newer vault set. |
| Trust Wallet | Thirty-plus `Trust Wallet ...` vaults mostly wrap underlying strategies on Kiln Metavault: Gauntlet, Steakhouse/Smokehouse, Spark, Aave, Compound, Angle, Fluid, etc. Existing curator matches already catch many underlying curators. | Kiln docs describe wallets, custodians, exchanges, and fintech platforms integrating Morpho through Kiln DeFi / OmniVaults; this fits Trust Wallet as distributor/integrator rather than vault curator. | **Reject as curator.** Continue matching the embedded underlying strategy names such as Gauntlet, Steakhouse Financial, Smokehouse, and Spark. |
| Lista | Euler/Lista vault names include `Lista USDT Savings Vault`, `Lista USDC Savings Vault`, `Lista USDT Vault`, `Lista USDC Vault`, and `Lista DAO USD1 Vault`. Existing feed coverage has `lisusd` stablecoin metadata, not a curator. | Lista docs say specialised curators manage each Lista Vault. Official Lista posts identify Gauntlet as primary curator/co-curator for selected vaults and RockawayX as curator for PT vaults. | **Reject Lista as a generic curator.** Do not add `lista.yaml`; map specific Lista vaults to Gauntlet or RockawayX only when the vault-level curator is known, or handle Lista self-operated vaults separately if the data proves protocol-managed operation. |
| Concrete | More than 40 local rows use Concrete names, mostly under `protocol-not-yet-identified`, e.g. `Concrete Decentralized Finance USDT`, `Concrete USDC MultiSig Strategy`, `Concrete-MorphoUSDC-Vault`, and movement/stable predeposit vaults. | Concrete audit material describes Concrete multi-strategy ERC-4626 vaults, VaultManager ownership, and automatic strategy allocation. Concrete app output shows many vaults with "Vault owner Concrete". This looks like an own protocol/vault platform rather than a curator of Morpho/Euler infrastructure. | **Defer as protocol metadata, not curator metadata.** First improve protocol detection for Concrete vaults and consider a protocol feeder. Add a curator alias only if a later export uses Concrete as curator on a third-party protocol. |
| Galaxy | Two live-looking Morpho vaults: `Galaxy USDC Quality` and `Galaxy USDT Quality`, plus one old `Galaxy Finance BUSD Vault Token`. | Trading Strategy and Staking Rewards pages confirm the Morpho vault names and sizeable TVL, but no official Galaxy/Morpho announcement or curator identity page was found. Search results also collide with unrelated Galaxy entities. | **Defer.** Plausible institutional curator, but do not add until an official Galaxy page, Morpho curator page, or governance/forum announcement confirms the entity and social links. |
| TermMax | Around 30 local rows use TermMax names across Euler and unidentified protocols, including `TermMax USDC Vault`, `TermMax USDC Prime`, `TermMax Stable ERC4626 USD Coin`, and `TermMax x Mezen Capital Vault`. | TermMax official site and docs describe TermMax as a fixed-rate protocol with curator-managed vaults. The docs say TermMax Vaults enable third-party fund management, with an assigned curator; this means "TermMax" is likely the protocol/product layer. Some local names point to `Mezen Capital` as the actual curator. | **Reject TermMax as curator; defer protocol work.** Add protocol detection/feed metadata for TermMax separately. Investigate `Mezen Capital` as a possible curator candidate in a future batch. |
| Mithras | One Sonic Euler vault: `Mithras`, with high apparent TVL. | Trading Strategy labels it `broken` and `blacklisted`: on-chain metrics do not make sense and the vault likely has a broken smart contract. External coverage around the Stream/xUSD contagion mentions Mithras exposure, but not a clean curator identity. | **Reject for now.** Do not add a curator for a single broken/blacklisted vault with no verified identity. Revisit only if an official Mithras curator source appears and the vault data becomes sane. |
| Hakutora | Current chain parquet has six Ethereum Morpho vaults: `Hakutora USDC`, `Hakutora USDT`, `Hakutora DAI`, `Hakutora WETH`, `Hakutora WBTC`, and `Hakutora cbBTC`. Morpho API currently lists `Hakutora USDC` and `Hakutora USDT` as featured/listed vaults with no warnings. | OneKey Help Centre says Morpho USDC is "managed by OneKey (Hakutora)" inside the OneKey App. Morpho has a Hakutora curator page. No official Hakutora standalone website/social source found yet. | **Promote to next curator batch, medium confidence.** Add `hakutora.yaml` using Morpho/OneKey evidence, but keep comments explicit that identity is presented as `OneKey (Hakutora)`. |
| Pangolins | Current exports contain Ethereum and Base Morpho vaults `Pangolins USDC`, `Pangolins USDT`, plus BNB `Pangolins USDT Vault` under Euler/unknown local protocol detection. Morpho API currently lists Base `Pangolins USDC` as featured/listed with no warnings. | Pangolins has an official website, a Morpho forum introduction with website and X/Twitter handle, and Morpho's November 2025 update says Pangolins is one of the latest curators on Morpho. | **Promote to next curator batch, high confidence.** Add `pangolins.yaml` as a third-party curator with `pangolins.io` and `PangolinsVault` metadata. |
| Api3 | Export has four Ethereum Morpho rows: `Api3 Core USDC` and `Api3 dCOMP USDC`, including older addresses. Morpho API currently returns live data for `Api3 Core USDC` and `Api3 dCOMP USDC`; `Api3 Core USDC` is listed with no warnings. | Morpho forum post `Introducing Api3-curated Vaults on Morpho` is direct primary evidence: Api3 says it is participating in Morpho as a curator. Api3 official/LinkedIn posts discuss Morpho vault curation, and Morpho has an Api3 curator page. | **Promote to next curator batch, high confidence.** Add `api3.yaml` as a third-party curator with official website, X/Twitter, LinkedIn, and RSS metadata. |

## Add-curator evidence prepared

Prepared 2026-05-05 for the `add-curator` skill. Existing curator inventory has no `api3`, `hakutora`, `onekey`, or `pangolins` curator feeder files. `rg` also found no matching existing protocol/stablecoin feeder metadata, so these should be source curator files unless a reviewer wants a separate OneKey canonical feeder later.

### Api3

- Curator name: `Api3`
- Curator slug: `api3`
- Curator type: third-party curator
- Suggested feed file: `eth_defi/data/feeds/curators/api3.yaml`
- Identity metadata:
  - website: `https://api3.org/`
  - twitter: `API3DAO`
  - linkedin: `api3`
  - rss: `https://blog.api3.org/rss/` (checked with HTTP 200 `application/rss+xml`)
- Local vault evidence:
  - `Api3 Core USDC`, Ethereum Morpho, `0xb3f4d94a209045ef35661e657db9adac584141f1`, USDC.
  - `Api3 dCOMP USDC`, Ethereum Morpho, `0xe6f0ce5394b3d15b4ab1216d84f544b8f38e4d69`, USDC.
  - Older export rows also include `0x36cfe1568461e499391ef0a555300f1ae2da2439` and `0xe2221aa07ec3266da87763e2b1e28d07a8a4e53b`, but the Morpho API no longer returned live vault metadata for those addresses on 2026-05-05.
- External curator evidence:
  - Morpho forum `Introducing Api3-curated Vaults on Morpho`: Api3 explicitly says it is participating in Morpho as a curator.
  - Morpho curator page exists for `api3`.
  - Api3 LinkedIn says Api3 launched Kabu USDC as a curated Morpho vault and says Api3 is doubling down on curation.
  - Api3 official blog explains OEV-Boosted Morpho Markets and the earlier Yearn-curated launch, giving useful background for Api3's Morpho-specific role.
- Suggested detection change:
  - YAML `name: Api3` should be enough for vault names containing `Api3`.
  - Add a `CURATOR_NAME_PATTERNS` entry only if case handling fails in tests.
- Suggested `other-links`:
  - `Morpho forum - Introducing Api3-curated Vaults on Morpho`
  - `Morpho app - Api3 curator page`
  - `Api3 blog - Introducing OEV-Boosted Morpho Markets`
  - `Api3 LinkedIn - Kabu USDC curated Morpho vault update`

### Hakutora

- Curator name: `Hakutora`
- Curator slug: `hakutora`
- Curator type: third-party curator, with OneKey-backed identity notes
- Suggested feed file: `eth_defi/data/feeds/curators/hakutora.yaml`
- Identity metadata:
  - No standalone Hakutora website/social source found.
  - Morpho uses `hakutora` as the curator slug.
  - OneKey Help Centre describes the product as `OneKey (Hakutora)`.
  - If identity metadata is desired, use comments rather than assigning OneKey social links to Hakutora unless the reviewer wants OneKey as the canonical organisation.
- Local vault evidence:
  - `Hakutora USDC`, Ethereum Morpho, `0x974c8fbf4fd795f66b85b73ebc988a51f1a040a9`, USDC. Morpho API listed/featured, no warnings on 2026-05-05.
  - `Hakutora USDT`, Ethereum Morpho, `0xa71d08a159258553a5ac190d60fa919425ff02ea`, USDT. Morpho API listed/featured, no warnings on 2026-05-05.
  - `Hakutora DAI`, Ethereum Morpho, `0x42d425fb918acbbd73b10b851979e8fc469b3e9a`, DAI. Morpho API returned `short_timelock`/`not_whitelisted` warnings.
  - Additional current parquet rows: `Hakutora WETH`, `Hakutora WBTC`, `Hakutora cbBTC`, all Ethereum Morpho with small current NAV and Morpho API warnings.
- External curator evidence:
  - OneKey Help Centre says Morpho USDC is managed by `OneKey (Hakutora)` inside the OneKey App.
  - Morpho curator page exists for `hakutora`.
  - Morpho API metadata describes Hakutora USDC/USDT as dynamically adjusting allocation while monitoring asset risks.
- Suggested detection change:
  - YAML `name: Hakutora` should match current vault names.
  - No protocol slug change needed; vaults are Morpho.
- Suggested `other-links`:
  - `OneKey Help Centre - Morpho USDC managed by OneKey (Hakutora)`
  - `Morpho app - Hakutora curator page`
  - `Morpho app - Hakutora USDC vault`

### Pangolins

- Curator name: `Pangolins`
- Curator slug: `pangolins`
- Curator type: third-party curator
- Suggested feed file: `eth_defi/data/feeds/curators/pangolins.yaml`
- Identity metadata:
  - website: `https://pangolins.io/`
  - twitter: `PangolinsVault`
  - linkedin: not found
  - rss: not found
- Local vault evidence:
  - `Pangolins USDC`, Ethereum Morpho, `0x1941ada601b91ea7538e73442a1a632e8f9ffb70`, USDC.
  - `Pangolins USDT`, Ethereum Morpho, `0xd73270593e2542e5a43b8c7fbe4f2d5c9c4a443c`, USDT.
  - `Pangolins USDC`, Base Morpho, `0x1401d1271c47648ac70cbcdfa3776d4a87ce006b`, USDC. Morpho API listed/featured, no warnings on 2026-05-05.
  - `Pangolins USDT Vault`, BNB local vault data, `0xeb4f6ffb1038e1cca701e7d53083b37ec5b6ba33`, USDT. Current chain parquet shows it as `<unknown ERC-4626>`; older top-vault export labelled it Euler.
- External curator evidence:
  - Pangolins official website says it curates top-tier DeFi protocols and executes automated risk control; it specifically mentions Morpho among selected lending/derivatives protocols.
  - Morpho forum `About Pangolins` says Pangolins focuses on DeFi risk management and lists website `pangolins.io` plus X/Twitter `PangolinsVault`.
  - Morpho November 2025 update says Pangolins is one of the latest curators on Morpho.
  - Morpho API metadata describes Base `Pangolins USDC` as curated by Pangolins to execute automated risk control and help users earn yield safely.
- Suggested detection change:
  - YAML `name: Pangolins` should match current vault names.
  - Add a name pattern only if tests show plural/singular normalisation trouble. Avoid matching singular `Pangolin` because it collides with unrelated Avalanche DEX/protocol naming.
- Suggested `other-links`:
  - `Pangolins official website - Curates DeFi protocols and automated risk control`
  - `Morpho forum - About Pangolins`
  - `Morpho blog - Pangolins named as latest Morpho curator`
  - `Morpho app - Pangolins USDC Base vault`

## Sources checked

- Flowdesk Morpho forum: `https://forum.morpho.org/t/announcing-flowdesk-ausd-rwa-strategy/2213`
- Anthias Labs Morpho forum: `https://forum.morpho.org/t/anthias-labs-curator-introduction/2061`
- Felix docs: `https://usefelix.gitbook.io/felix-docs/terms/terms-and-conditions`
- Janus Henderson official release: `https://ir.janushenderson.com/News--Events/news/news-details/2024/Janus-Henderson-to-Partner-with-Anemoy-and-Centrifuge-on-Its-First-Tokenized-Fund/default.aspx`
- Anemoy JTRSY page: `https://www.anemoy.io/funds/jtrsy`
- TelosC/Euler external coverage: `https://t.signalplus.com/crypto-news/detail/peckshield-27m-defi-risk-euler-telosc-vaults`
- Greenhouse Trading Strategy warning: `https://tradingstrategy.ai/trading-view/vaults/greenhouse-usdc`
- Felix Morpho forum: `https://forum.morpho.org/t/introducing-felix-vaults/2047`
- Kiln Morpho integration docs: `https://docs.kiln.fi/v1/kiln-products/defi/how-to-integrate/morpho-via-kiln-defi`
- Lista vault docs: `https://docs.bsc.lista.org/introduction/lista-lending/vaults`
- Lista third-party vault risk docs: `https://docs.bsc.lista.org/introduction/lista-lending/third-party-vault-risk-management`
- Lista Gauntlet partnership: `https://blog.lista.org/lista-dao-partners-with-gauntlet-to-power-next-generation-vault-curation`
- Lista RockawayX-curated PT vault: `https://blog.lista.org/rockawayx-curated-pt-vaults-now-live-on-lista`
- Concrete audit report: `https://docs.concrete.xyz/assets/files/Zellic-Audit-Report-5dbb9d52d444adcd197dfbaa941a86ab.pdf/`
- Concrete app snapshot: `https://concretexyz.pro/`
- Galaxy USDC Quality Trading Strategy page: `https://tradingstrategy.ai/trading-view/vaults/galaxy-usdc-quality`
- TermMax official site: `https://termmax.org/`
- TermMax vault docs: `https://docs.ts.finance/protocol-mechanisms/components/vault`
- Mithras Trading Strategy warning: `https://tradingstrategy.ai/trading-view/vaults/mithras`
- Hakutora / OneKey help centre: `https://help.onekey.so/en/articles/12605538-resolv-season-2-rewards-distributed-to-onekey-app-defi-users`
- Hakutora Exponential page: `https://exponential.fi/pools/morpho-usd-lending-ethereum/33800868-86e1-4577-b18f-d1f2e5f41a20`
- Hakutora Morpho curator page: `https://app.morpho.org/curator/hakutora`
- Hakutora Morpho USDC vault page: `https://app.morpho.org/ethereum/vault/0x974c8FBf4fd795F66B85B73ebC988A51F1A040a9/hakutora-usdc`
- Pangolins Morpho update: `https://morpho.org/blog/morpho-effect-november-2025/`
- Pangolins official website: `https://pangolins.io/`
- Pangolins Morpho forum: `https://forum.morpho.org/t/about-pangolins/1996`
- Pangolins Morpho Base vault page: `https://app.morpho.org/base/vault/0x1401d1271C47648AC70cBcdfA3776D4A87CE006B/pangolins-usdc`
- Pangolins Morpho vault page: `https://app.morpho.org/unichain/vault/0x3e628F9089DD78D2B9C50Eb88ffb6D2b1014ff94/pangolins-usdc`
- Api3 Morpho forum: `https://forum.morpho.org/t/introducing-api3-curated-vaults-on-morpho/2111`
- Api3 Morpho curator page: `https://app.morpho.org/ethereum/curator/api3`
- Api3 official website: `https://api3.org/`
- Api3 blog RSS: `https://blog.api3.org/rss/`
- Api3 official blog - OEV-Boosted Morpho Markets: `https://blog.api3.org/introducing-oev-boosted-morpho-markets/`
- Api3 LinkedIn: `https://www.linkedin.com/company/api3`
- Morpho API docs: `https://legacy.docs.morpho.org/morpho/tutorials/api/`
