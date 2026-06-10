# Stablecoin data fixes needed

Audit of `eth_defi/data/stablecoins/*.yaml` (metadata, 198 files),
`eth_defi/data/feeds/stablecoins/*.yaml` (feeds, 184 files) and
`eth_defi/stablecoin_metadata.py`, performed 2026-06-10.

This file lists issues that **could not be resolved automatically** and need a
human decision or research with on-chain verification. Items already fixed in
the same pass are listed first so reviewers know what changed.

## Already fixed in this pass (no action needed)

- **EIP-55 checksums** — 137 lowercase/mixed EVM addresses across 85 metadata
  files were rewritten to checksummed form via `eth_utils.to_checksum_address`.
  Addresses embedded in `long_description` prose and Etherscan URLs were
  normalised too (identical bytes, harmless). Non-EVM addresses were left alone
  (see below).
- **Name mismatches (metadata ↔ feed)** — 6 feed `name:` fields were aligned to
  the metadata `name:` (metadata is the single source of truth for display):
  `eure`, `gmdusdc`, `jchf`, `msusd`, `sausd`, `ysusdc`.

After these fixes all 198 metadata files load cleanly through
`build_stablecoin_metadata_json()` and all 9 Royco denominations still resolve
through `is_stablecoin_like()`.

## 1. Missing formatted logos (13)

No `formatted_logos/<slug>/light.png`. Use the `extract-project-logo` +
`post-process-logo` skills with the homepage in each metadata file as the source.

Obtainable (active projects, homepage known):

| slug | symbol | logo source (homepage) |
|------|--------|------------------------|
| aa-falconxusdc | AA_FalconXUSDC | Idle Finance / Falcon |
| apyusd | apyUSD | (see metadata homepage) |
| autousd | autoUSD | Auto Finance |
| bbqusdc | bbqUSDC | Steakhouse Financial |
| eearn | eEARN | Ember |
| savusd | savUSD | Avant / avUSD |
| snusd | sNUSD | Neutrl |
| stcusd | stcUSD | Cap |
| syrupusdc | syrupUSDC | Maple / Syrup |
| audt | AUDT | Anchored Coins |
| vusd | VUSD | (multi-issuer, confirm which) |

Not obtainable (unidentified placeholder tokens — leave without logo):
`mtusd`, `usxau`.

## 2. Missing contract addresses (genuinely active, ~40)

These metadata files have **no `contract_addresses`** but are active projects
that should have at least the canonical mainnet address. Needs lookup +
on-chain `symbol()` verification before adding (do **not** add unverified
addresses — hallucination risk):

`bvusd`, `djed`, `doladusd`, `eosdt`, `feusd`, `ftusd`, `ghst`, `iusd-indigo`,
`kdai`, `kusd`, `mtusdc`, `mtusdt`, `plusd`, `silk`, `susg`, `usc`, `usd-plus`,
`usd-t`, `usd-t0`, `usdai`, `usdc-e`, `usdh`, `usdm`, `usds-sperax`, `usdt-e`,
`usdt0`, `usdtb`, `usdxl`, `usg`, `ush`, `usk`, `ust`, `ustc`, `usx`, `uusd`,
`vbusdc`, `vbusdt`, `vusd-virtue`, `vusd-vow`, `ysusdc`, `zsd`.

**Correctly empty — do not "fix":**

- Native gas token, no ERC-20 contract: `xdai` (Gnosis Chain native).
- Defunct projects (address optional, low priority): `fei`, `flexusd`, `iron`,
  `plusd-polyquity`, `tor`, `usdv`.
- Unidentified / placeholder (cannot add): `dusd`, `mtusd`, `rusd`, `satusd`,
  `sosusdt`, `usdf`, `usdn`, `usdx`, `meusdt`, `usxau`, `usd8`, `usdh-hubble`.

## 3. Twitter & website drift (metadata ↔ feed) — RESOLVED

All 30 twitter handle drifts and 6 website drifts were researched (each issuer's
official site checked for the X handle / domain it links to) and the metadata
side was corrected to the verified canonical value. In every case the **feed
side was canonical** (the feed is actively curated) **except `msusd`**, where the
metadata `@Main_St_Finance` was the real handle and the feed `@MainSt_Finance`
was wrong — the feed was corrected and its stale `twitter-dead-at` marker (which
referred to the wrong handle) was removed.

Notable confirmations:

- **Rings → Trevee** rebrand confirmed (renamed "Trevee Earn", 2025-10-20).
  `scusd`/`plusd` metadata homepage → `trevee.xyz`, twitter → `@Trevee_xyz`,
  in-prose `app.rings.money`/`trevee.com` links updated too.
- Migrations to shorter handles (site-verified): Tether `@tether_to`→`@tether`,
  Synthetix `@synthetix_io`→`@synthetix`, Ethena `@ethena_labs`→`@ethena`,
  Paxos `@PaxosGlobal`→`@Paxos`, Mento `@MentoProtocol`→`@MentoLabs`,
  TrueUSD `@TrueUSD`→`@tusdio` + `trueusd.com`→`tusd.io` (301 redirect),
  Native Markets `@native_markets`→`@nativemarkets` + `.xyz`→`.com`,
  OpenEden `@OpenEdenLabs`→`@OpenEden_X`, Mountain `@MountainPrtcl`→`@MountainUSDM`,
  Elixir `@ElixirProtocol`→`@elixir`, EUROe `@membrane_fi`→`@EUROemoney`,
  Fathom `@FathomProtocol`→`@Fathom_fi`, Indigo `@IndigoProtocol1`→`@Indigo_protocol`,
  Level `@leveldotmoney`→`@levelusd`, Mimo `@mimodefi`→`@mimo_labs`,
  VNX `@VNX_fi`→`@VNX_Platform`, TrueFi `@TrueFi_DAO`→`@TrueFiDAO`,
  YieldNest homepage `app.yieldnest.finance`→`yieldnest.finance`.

**Still open (1) — needs decision:**

- `usdm-megaeth`: metadata `@megaborealeth` vs feed `@megaeth`. Research could
  not corroborate `@megaborealeth` as the issuer's account, and `@megaeth` is the
  MegaETH **chain** account, not a distinct USDm issuer (USDm is MegaETH's native
  stablecoin on Ethena USDtb whitelabel rails). No clearly-correct dedicated
  issuer handle exists — left unchanged pending a human call. Canonical site is
  `megaeth.com`.

**Minor follow-up:** `scusd` metadata `name:` is still `Rings scUSD` while its
homepage/twitter/descriptions now reflect Trevee; consider renaming to
`Trevee scUSD` (and matching the feed name) for consistency with `plUSD`
(`Trevee Plasma USD`). Left as-is to avoid introducing a new name mismatch.

## 4. (merged into section 3)

## 5. Missing external listing links (content gap)

Lower priority — fill where the listing genuinely exists.

- Missing `coingecko` (42): `aa-falconxusdc, audt, autousd, bbqusdc, bvusd,
  cnht, csusd, eearn, ftusd, gmdusdc, gmusd, kdai, meusdt, msusd, mtusd, mtusdc,
  mtusdt, nusd, plusd-polyquity, rusd, satusd, sausd, sosusdt, susdc, susg,
  tfusdc, usc, usd-t0, usd8, usdcv, usdf, usdh, usdh-hubble, usdm-megaeth,
  usdt-e, usg, usxau, vbusdc, vbusdt, vusd-vesper, xusd, ysusdc`.
- Missing `defillama` (98): see audit script output — most older entries. Many
  yield-bearing/wrapped derivatives live under `defillama.com/rwa/asset/...` or
  have no DeFiLlama page at all; only add real URLs.

## 6. Non-EVM addresses (intentional — documented, no fix)

These look "malformed" to an EVM checksum validator but are correct native
formats. The structural audit script should be taught to skip non-EVM chains:

- `fxd` → chain `xdc`, address `xdc49d3f7543335cf38Fa10889CCFF10207e22110B5`
  (XDC Network uses the `xdc` address prefix).
- `tcnh` → chain `tron`, address `TBqsNXUtqaLptVK8AYvdPPctpqd8oBYWUC`
  (TRON Base58 address).

## 7. Intentionally feedless (NOT bugs)

14 metadata files have no matching feed YAML. All are explainable and should
**not** get a feed:

- Multi-project tickers (9, one slug = several distinct issuers, no single news
  source): `ausd`, `dusd`, `rusd`, `satusd`, `usdn`, `usdx`, `usdf`, `yusd`,
  `xusd`.
- Dead / unidentified single tokens with no scannable source (5): `meusdt`,
  `mtusd`, `onc`, `sosusdt`, `usxau`.

## Reproducing the audit

```shell
poetry run python scripts/stablecoins/audit-stablecoin-data.py
```

Exits non-zero on ERROR-level findings. After this pass it reports 0 errors,
13 warnings (all missing logos, section 1) and 50 info items (intentionally
feedless tokens in section 7 plus the twitter/website drift in sections 3–4).
