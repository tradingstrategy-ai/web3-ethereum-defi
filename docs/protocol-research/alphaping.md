# AlphaPing vault descriptions

Research date: 2026-10-28 (UTC). This is a frozen review of 33 Morpho and
three IPOR deployments, including legacy and partner products. Association
with AlphaPing does not guarantee that governance roles, mandates or portfolio
allocations will remain unchanged. Names below are research labels; adapters
continue to use onchain names.

## Sources and methodology

The [Morpho GraphQL API](https://api.morpho.org/graphql) supplied the Morpho
inventory, denomination tokens and per-vault `metadata.description` fields.
Take the union of `vaults` and `vaultV2s` queries matching either
`ownerAddress_in` or `curatorAddress_in` against:

- `0x6788c8ad65e85cca7224a0b46d061ef7d81f9da5`
- `0xeb4af6fa3afa08b10d593ec8ff87efb03bc04645`

The owner and curator unions contain 38 deployments. Exclude these five
installer/test deployments, leaving the 33 reviewed Morpho vaults:

| Chain | Address | Name |
|---|---|---|
| 1 | `0xce341182ad323b9cb5ad45a60859dbc356dc3ddd` | zzzz |
| 8453 | `0x4952482927542e7bf75f024e7e06b239227a6f87` | zzzz |
| 1 | `0xd50b9bbf136d1bd5cd5ac6ed9b3f26c458a6d4a6` | (Deployer) |
| 1 | `0xf62f6060cb3833daeb61b6ad960d6d531a0867d2` | Alpha WETH (installer) |
| 1 | `0x8986cbe8b98c342888aa2787c2979b6e32a40149` | Alpha USDC (Installer) |

[AlphaPing's catalogue](https://alphaping.ch/vaults) lists only four featured
products. Its [Core mandate](https://alphaping.ch/core-vaults) and
[Enhanced mandate](https://alphaping.ch/enhanced-vaults) supplement individual
Morpho descriptions. Marketing claims of safety, guaranteed liquidity or
investment-grade collateral are not repeated as established facts. Sparse
entries describe lending and denomination without guessing specific collateral.

The [IPOR Fusion app](https://app.ipor.io/fusion) public configuration identifies
three Ethereum deployments with `atomist:"Alphaping"`. All were configured as
hidden at review time; only `0x29d322dd088e9b9d1416f43188954f08748fafbb` was
explicitly named deprecated. The inspected JavaScript bundle was
`https://app.ipor.io/assets/index-sX7ExjHi.js`; future bundles may have different
filenames. The published configuration explains the Falcon/sUSDf borrowing
loops. Ethereum RPC reads of each vault's denomination token confirmed USDC,
sUSDf and sUSDf respectively. This was a manual read-only check using
`JSON_RPC_ETHEREUM` from `.local-test.env`, not CI.

## Forex strategy and allocation snapshot

The contract `0x153bd1abe60104bd46aa05a27fa12d1346d64a57` was published as
Alpha USDC Forex V2 (`alphaForexV2`), but Morpho returned the name and symbol
Acuto on the review date. Two other renamed deployments are ALPHA USDC Core
at `0xb0f05e4de970a1aaf77f8c2f823953a367504ba9` (Hawkwood) and Alpha WETH
Core at `0x47fe8ab9ee47dd65c24df52324181790b9f47efc` (Villandrando).

[Morini's strategy overview](https://morini.capital/) explains the collateral:
leveraged TRY overnight carry and USD/TRY futures pricing differences;
Turkish cash equity versus single-stock-futures basis trades; and hedged
USD/TRY arbitrage between Turkish crypto exchanges and fiat rails.
[Piku's account of the partnership](https://piku.co/blog/alphaping-piku-morpho-usp-vault)
describes USP as a yield-bearing token backed by diversified DeFi, real-world
asset and FX strategies, and explains how borrowers can post USP, borrow USDC
and reinvest to increase exposure. The lending vault earns borrower interest;
it does not directly execute all of those underlying strategies itself.

For Alpha ZCHF Safe Vault, the manual Morpho `vaultByAddress` check on
2026-10-28 confirmed that its published description specifies a Bitcoin-only
lending and borrowing environment denominated in ZCHF. Both maintained
descriptions attribute that scope to the published mandate rather than
independently certifying current collateral or implying protection from loss.

The manual GraphQL read of `vaultV2ByAddress.caps.items` returned these
`MarketV1` allocations on 2026-10-28. Values are USDC after converting its six
decimal places; collateral-level and adapter-level caps must not be added again
because they describe the same assets at different levels.

| Collateral | Allocated USDC | LLTV |
|---|---:|---:|
| CarryTradeUSDTRYLeverage | 562,496.801408 | 62.5% |
| StockMarketTRBasisTrade | 241,102.708471 | 86% |
| USP | 3.017178 | 86% |
| aFXArbUSDTRY | 0 | 86% |

This is approximately 70% carry and 30% equity basis exposure. Allocations,
liquidity and APYs change; they are intentionally omitted from maintained
public descriptions. Oracle/NAV values do not establish that collateral can
be sold at that value. Existing review flags and risk notes remain independent
of descriptive copy.

## Reviewed deployment inventory

Each link below identifies the address-scoped product and its primary source.
The maintained text is in `eth_defi/vault/handwritten_metadata.py`.

| Chain | Protocol | Address | Research name |
|---|---|---|---|
| 1 | Morpho | `0x0bf0164d17469241b6e086da4016dcc54feaa334` | [Alpha USDC Delta V2](https://app.morpho.org/ethereum/vault/0x0bf0164d17469241b6e086da4016dcc54feaa334) |
| 1 | Morpho | `0x153bd1abe60104bd46aa05a27fa12d1346d64a57` | [Alpha USDC Forex V2 / Acuto](https://app.morpho.org/ethereum/vault/0x153bd1abe60104bd46aa05a27fa12d1346d64a57) |
| 1 | Morpho | `0x35cbe8542e70fa2f7f9cdf129f19e593f4b4f560` | [Alpha USDC Asia V2](https://app.morpho.org/ethereum/vault/0x35cbe8542e70fa2f7f9cdf129f19e593f4b4f560) |
| 1 | Morpho | `0x3c124fb80e6eedd88d108c2f04013ae5cd571abc` | [Falcon USDf Core](https://app.morpho.org/ethereum/vault/0x3c124fb80e6eedd88d108c2f04013ae5cd571abc) |
| 1 | Morpho | `0x47fe8ab9ee47dd65c24df52324181790b9f47efc` | [Alpha WETH Core / Villandrando](https://app.morpho.org/ethereum/vault/0x47fe8ab9ee47dd65c24df52324181790b9f47efc) |
| 1 | Morpho | `0x52317a47585a6acdfbd7a29b494c3e2baae96abc` | [Alpha MUSD](https://app.morpho.org/ethereum/vault/0x52317a47585a6acdfbd7a29b494c3e2baae96abc) |
| 1 | Morpho | `0x610f5b68bd1eed68af649a3fd3dc2caa1ee4ae7e` | [Alpha LINK Enhanced V2](https://app.morpho.org/ethereum/vault/0x610f5b68bd1eed68af649a3fd3dc2caa1ee4ae7e) |
| 1 | Morpho | `0x6619f92861c760ad11ba0d56e8ed63a33ecce22b` | [Alpha ZCHF Safe Vault](https://app.morpho.org/ethereum/vault/0x6619f92861c760ad11ba0d56e8ed63a33ecce22b) |
| 1 | Morpho | `0x6701957aaa39e9352a42729cc65436a4c945cb90` | [AlphaPing PT Frontier](https://app.morpho.org/ethereum/vault/0x6701957aaa39e9352a42729cc65436a4c945cb90) |
| 1 | Morpho | `0x76dedaf4ec78777716bdeba484037c9d5fbf5d11` | [Alpha USDT Core](https://app.morpho.org/ethereum/vault/0x76dedaf4ec78777716bdeba484037c9d5fbf5d11) |
| 1 | Morpho | `0x874e7fb1abb4c27a7bbeeefa39e888b63181939c` | [AlphaPing USDC Prime](https://app.morpho.org/ethereum/vault/0x874e7fb1abb4c27a7bbeeefa39e888b63181939c) |
| 1 | Morpho | `0x89610f2125a54547a7fb2a1b7926be734a632255` | [Nova WETH Vault](https://app.morpho.org/ethereum/vault/0x89610f2125a54547a7fb2a1b7926be734a632255) |
| 1 | Morpho | `0x951a9f4a2ce19b9dea6b37e691d076a345b6c0f8` | [Alpha USDC Enhanced V2](https://app.morpho.org/ethereum/vault/0x951a9f4a2ce19b9dea6b37e691d076a345b6c0f8) |
| 1 | Morpho | `0x9f39b13bb472126d6937bf25a39338e664eefa82` | [Alpha USDC Prime V2](https://app.morpho.org/ethereum/vault/0x9f39b13bb472126d6937bf25a39338e664eefa82) |
| 1 | Morpho | `0xa79a5fb6f619da3ed71087fb5cd33b16a5b666b5` | [Nova fxUSD Vault](https://app.morpho.org/ethereum/vault/0xa79a5fb6f619da3ed71087fb5cd33b16a5b666b5) |
| 1 | Morpho | `0xb0f05e4de970a1aaf77f8c2f823953a367504ba9` | [ALPHA USDC Core / Hawkwood](https://app.morpho.org/ethereum/vault/0xb0f05e4de970a1aaf77f8c2f823953a367504ba9) |
| 1 | Morpho | `0xbb5ad5d522684de9dd3f545fa6d6ac5bbca4710e` | [Alpha WETH Core V2](https://app.morpho.org/ethereum/vault/0xbb5ad5d522684de9dd3f545fa6d6ac5bbca4710e) |
| 1 | Morpho | `0xce22b5fb17ccbc0c5d87dc2e0df47dd71e3adc0a` | [USDU Core](https://app.morpho.org/ethereum/vault/0xce22b5fb17ccbc0c5d87dc2e0df47dd71e3adc0a) |
| 1 | Morpho | `0xd0634bf57921ef4068f284f5e78a3f2623e20129` | [Chaince USDC Yield](https://app.morpho.org/ethereum/vault/0xd0634bf57921ef4068f284f5e78a3f2623e20129) |
| 1 | Morpho | `0xd8cd72a2eb7a4312404c00aed5172c0026da0c27` | [Alpha Frax USD Enhanced V2](https://app.morpho.org/ethereum/vault/0xd8cd72a2eb7a4312404c00aed5172c0026da0c27) |
| 1 | Morpho | `0xda4d1a3187eba9ecaa137867bd4541f78d51638e` | [Alpha MUSD Core](https://app.morpho.org/ethereum/vault/0xda4d1a3187eba9ecaa137867bd4541f78d51638e) |
| 1 | Morpho | `0xeb6719c14ba01bf1d6bfb78a65f63f12fabf202c` | [Alpha APXUSD V2](https://app.morpho.org/ethereum/vault/0xeb6719c14ba01bf1d6bfb78a65f63f12fabf202c) |
| 1 | Morpho | `0xf1ca44eea3a4effcb195a970a2f1d8553f76f9a1` | [Alpha USDC Core V2](https://app.morpho.org/ethereum/vault/0xf1ca44eea3a4effcb195a970a2f1d8553f76f9a1) |
| 1 | Morpho | `0xf9b85b19f6885511165505299648e6d052e8d4c8` | [AlphaPing USDC Core](https://app.morpho.org/ethereum/vault/0xf9b85b19f6885511165505299648e6d052e8d4c8) |
| 1 | Morpho | `0xfa7ed49eb24a6117d8a3168eee69d26b45c40c63` | [Alpha ZCHF Vault](https://app.morpho.org/ethereum/vault/0xfa7ed49eb24a6117d8a3168eee69d26b45c40c63) |
| 999 | Morpho | `0x242572d6f1af7111bca807ecdd0f74108ceaed5d` | [Alpha USDT Prime](https://app.morpho.org/hyperevm/vault/0x242572d6f1af7111bca807ecdd0f74108ceaed5d) |
| 8453 | Morpho | `0x2365975ca06f99d5da5acfc70f2581b8d194f8aa` | [Alpha USDC Core](https://app.morpho.org/base/vault/0x2365975ca06f99d5da5acfc70f2581b8d194f8aa) |
| 8453 | Morpho | `0x44dd77d51629987d9555c762bf09903928c70206` | [Alpha USDC Prime V2](https://app.morpho.org/base/vault/0x44dd77d51629987d9555c762bf09903928c70206) |
| 8453 | Morpho | `0x4a4de4757d2a04d1e12bcc21068288caad6e19a7` | [Alpha cbBTC Core](https://app.morpho.org/base/vault/0x4a4de4757d2a04d1e12bcc21068288caad6e19a7) |
| 8453 | Morpho | `0xea47a35aaa60302b4816d920856ddc708ad181f6` | [Alpha wETH Core](https://app.morpho.org/base/vault/0xea47a35aaa60302b4816d920856ddc708ad181f6) |
| 42161 | Morpho | `0x0f841bd670eaeb6be0a173c06efefbad09abb0f4` | [Alpha USDT Core](https://app.morpho.org/arbitrum/vault/0x0f841bd670eaeb6be0a173c06efefbad09abb0f4) |
| 42161 | Morpho | `0x23faff4f253b85834d9d37f97ea807ec46d1ec9e` | [Alpha wETH Core](https://app.morpho.org/arbitrum/vault/0x23faff4f253b85834d9d37f97ea807ec46d1ec9e) |
| 42161 | Morpho | `0x4c289073eb6679393890b85eec9d604271c18c28` | [Alpha USDC Core](https://app.morpho.org/arbitrum/vault/0x4c289073eb6679393890b85eec9d604271c18c28) |
| 1 | IPOR Fusion | `0x29d322dd088e9b9d1416f43188954f08748fafbb` | [Leveraged sUSDf (Deprecated)](https://app.ipor.io/fusion/ethereum/0x29d322dd088e9b9d1416f43188954f08748fafbb) |
| 1 | IPOR Fusion | `0xb4790cfdf1df71c2e4ac2f5c43dbcb975ad447ae` | [Leveraged Falcon USDC Core](https://app.ipor.io/fusion/ethereum/0xb4790cfdf1df71c2e4ac2f5c43dbcb975ad447ae) |
| 1 | IPOR Fusion | `0xb530a1b5259a71187f1d69acf0488f102637a3ed` | [Leveraged sUSDf](https://app.ipor.io/fusion/ethereum/0xb530a1b5259a71187f1d69acf0488f102637a3ed) |

## Metadata-only repair

The repair reads an existing metadata pickle and changes only `_description`
and `_short_description` for matching reviewed deployments. Missing rows are
reported and skipped. A protocol mismatch aborts before any changes. The
persistent invocation holds the scanner writer lock and writes atomically;
rerunning it without changed copy leaves the file untouched.

```shell
DRY_RUN=true poetry run python scripts/erc-4626/migrate-alphaping-descriptions.py
DRY_RUN=false poetry run python scripts/erc-4626/migrate-alphaping-descriptions.py
```

Use `VAULT_DB_PATH` to select a copy for review. Otherwise the script follows
`PIPELINE_DATA_DIR`, defaulting to `~/.tradingstrategy/vaults`. Deploy the adapter
changes before running the repair so subsequent scans
retain the reviewed copy. Normal metadata analysis/export must run afterwards
to publish the changed descriptions.

Production execution is a separate operator action. Inspect `docker-compose.yml`
and use the mounted production state and maintenance entrypoint documented in
`AGENTS.md`. The repair itself performs no price scanning or network requests.
It preserves vault names, notes, flags, listing status, reader state and prices.
