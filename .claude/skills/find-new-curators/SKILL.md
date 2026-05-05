---
name: find-new-curators
description: Identify missing vault curators from vault databases and suggest curator feeder YAML entries
---

# Find new curators

Use this skill when asked to discover new vault curators, improve curator coverage, or audit whether vaults in generic protocols are missing curator metadata.

The goal is to produce a reviewed shortlist of curator candidates, not to blindly add every unknown string found in vault names.

## Required inputs

Before starting, clarify the scope if the user did not provide it:

1. **Protocols**: Which vault protocols to inspect, e.g. Morpho, Euler, IPOR Fusion, Lagoon Finance, Hyperliquid, GRVT, Lighter, Hibachi, or all.
2. **Data source**: Local pipeline data in `~/.tradingstrategy/vaults`, production R2 metadata, or a specific DuckDB/parquet file.
3. **Output**: Candidate report only, or also create new `eth_defi/data/feeds/curators/*.yaml` files.

If the user asks for "all", inspect the local data sources that are present and report any missing databases instead of failing the whole task.

## 1. Print existing curators

Start by printing the existing machine-readable curator list:

```shell
poetry run python .claude/skills/find-new-curators/scripts/print-existing-curators.py
```

Useful environment variables:

```shell
FEEDS_DIR=eth_defi/data/feeds poetry run python .claude/skills/find-new-curators/scripts/print-existing-curators.py
INCLUDE_ALIASES=false poetry run python .claude/skills/find-new-curators/scripts/print-existing-curators.py
```

Also inspect the human reference list:

```shell
sed -n '1,140p' eth_defi/data/curators.md
```

Treat `eth_defi/data/feeds/curators/` as the source of truth for code paths. `eth_defi/data/curators.md` is useful background and should be updated only when the user asks for documentation changes or the new curator is strategically important.

## 2. Load vault data

Use the narrowest available source for the protocols in scope.

### ERC-4626 and generic vault protocols

Read `scripts/erc-4626/README-vault-scripts.md` before running pipeline scripts.

Usual local files live under:

- `~/.tradingstrategy/vaults/vault-metadata-db.pickle`
- `~/.tradingstrategy/vaults/vault-prices-1h-cleaned.parquet`
- `~/.tradingstrategy/vaults/top_vaults_by_chain.json`

For Morpho, Euler, IPOR Fusion, Lagoon Finance, and other ERC-4626-like protocols, prefer the metadata database or exported top vault JSON over live chain calls. Pull columns or fields that contain:

- chain id
- vault address
- vault name
- vault token symbol
- protocol slug or protocol name
- TVL or assets under management
- existing curator slug, if present
- any offchain description or manager metadata

### Lagoon Finance

Lagoon curator data can come from offchain metadata fetched from `app.lagoon.finance/api/vault`. See:

- `eth_defi/erc_4626/vault_protocol/lagoon/offchain_metadata.py`
- `tests/lagoon/test_lagoon_metadata.py`

Use cached data if present. If fetching live data, be explicit that GitHub Actions/datacentre IPs may receive failures.

### Hyperliquid, GRVT, Lighter, and native vaults

Use the native DuckDB databases when present:

- `~/.tradingstrategy/vaults/hyperliquid-vaults.duckdb`
- `~/.tradingstrategy/vaults/grvt-vaults.duckdb`
- `~/.tradingstrategy/vaults/lighter-pools.duckdb`
- other native vault databases added by the current repo, e.g. Hibachi

Relevant references:

- `scripts/hyperliquid/README-hyperliquid-vaults.md`
- `scripts/grvt/README-grvt-vaults.md`
- `scripts/lighter/README-lighter-vaults.md`

Prefer DuckDB SQL for exploration. Use Pandas DataFrame output in notebooks and `tabulate.tabulate()` in scripts.

## 3. Identify curator candidates

Run the current curator matcher before inventing new names:

- `eth_defi.vault.curator.identify_curator()`
- `eth_defi.vault.curator.CURATOR_NAME_PATTERNS`
- `eth_defi.vault.curator.PROTOCOL_CURATED_SLUGS`
- `eth_defi.vault.curator.ALL_PROTOCOL_CURATOR_SLUGS`

Then inspect unmatched or low-confidence vaults by protocol and TVL.

Good candidate signals:

- A repeated proper noun appears across multiple vault names or symbols.
- A vault name includes a known asset manager, risk manager, trading firm, or strategy operator.
- A native vault description names a manager or company.
- A protocol UI/API has a curator, manager, allocator, owner, guardian, trader, or strategy operator field.
- The candidate has a real website, X/Twitter profile, LinkedIn company profile, or RSS/blog source.

Weak candidate signals:

- Generic strategy words like "core", "prime", "stable", "yield", "max", "bluechip", "delta neutral", or "basis".
- Asset names, token symbols, chain names, or collateral names.
- Protocol-owned vault labels, unless these should become protocol-managed curators.
- One-off vault names with no external identity and low TVL.

## 4. Use name heuristics

Extract possible curator names from vault names with conservative heuristics.

Patterns worth checking:

- Prefix before asset or strategy suffix, e.g. `Gauntlet USDC Core` -> `Gauntlet`.
- Brand inside compound names, e.g. `RE7 Morpho USDC` -> `RE7`.
- Known company suffixes, e.g. `Capital`, `Labs`, `Finance`, `Strategies`, `Risk`, `Digital`, `Technologies`.
- Native vault manager fields, e.g. GRVT `manager_name`.
- Offchain descriptions with phrases like "managed by", "curated by", "operated by", "strategy by", "vault by", "led by".

Avoid single-word matches unless the word is distinctive and verified externally. If the match needs a short alias, add it to `CURATOR_NAME_PATTERNS` only after checking for false positives across the vault dataset.

## 5. Suggest generic protocol curators

For generic vault protocols, report candidates as third-party curators when the protocol is infrastructure and vaults are operated by outside parties.

Typical examples:

- Morpho vault curators
- Euler vault curators
- IPOR Fusion curators
- Lagoon Finance curators
- vault managers in native marketplace protocols

For each candidate, include:

- proposed slug
- display name
- evidence vaults with chain id, address, name, and TVL
- matched heuristic
- website/Twitter/LinkedIn/RSS if found
- whether to add a full source-bearing YAML or only a name pattern for an existing curator
- confidence: high, medium, or low

Do not add a candidate if the only evidence is a protocol name, token name, or an unverified social handle.

## 6. Suggest protocol-managed curators

Some vaults are managed by the protocol itself and should not be treated as external curators.

Check whether the protocol is already covered by:

- `PROTOCOL_CURATED_SLUGS` for protocols where all vaults are protocol-managed
- `ALL_PROTOCOL_CURATOR_SLUGS` for protocols with specific system vaults
- address constants such as Hyperliquid system vaults and Lighter system pools
- curator alias YAML files with `canonical-feeder-id` pointing to a protocol or stablecoin feeder

Suggest protocol-managed curators when:

- all vaults for a protocol are operated by the protocol itself
- a protocol has official system vaults mixed with third-party vaults
- a stablecoin/protocol issuer also acts as the curator for its staked or yield vaults

When the same organisation already has a protocol or stablecoin feeder, prefer an alias curator YAML:

```yaml
feeder-id: spark
name: Spark
role: curator
canonical-feeder-id: spark
```

When the protocol itself needs curator classification, update `eth_defi/vault/curator.py` rather than adding a fake third-party curator.

## 7. Verify candidates

For every proposed curator:

1. Search the repo first: `rg -n "{name}|{slug}" eth_defi/data eth_defi/vault docs scripts tests`.
2. Check whether a protocol, stablecoin, or curator YAML already represents the organisation.
3. Verify the official website and social links.
4. Check whether RSS exists; if not, leave it out rather than adding a dead URL.
5. Confirm the candidate appears in vault data, not just in marketing copy.

If web information is needed, use authoritative project pages first. For X/Twitter and LinkedIn, prefer links from the official website footer or docs.

## 8. Output format

When reporting only, use a compact table:

| Candidate | Type | Confidence | Evidence | Suggested action |
|-----------|------|------------|----------|------------------|

Use these type values:

- `third-party curator`
- `protocol-managed curator`
- `alias curator`
- `name-pattern update`
- `reject`

When making code/data changes:

- add or update `eth_defi/data/feeds/curators/{slug}.yaml`
- add evidence links under `other-links` when a protocol forum,
  documentation page, or vault launch post proves the curator role
- update `eth_defi/vault/curator.py` only for classification/name-pattern changes
- add tests when changing curator classification behaviour
- update `eth_defi/data/curators.md` only if requested or if the new curator belongs in the human reference list

## 9. Run checks

For data-only YAML changes, at minimum run:

```shell
poetry run python .claude/skills/find-new-curators/scripts/print-existing-curators.py
```

For curator classification changes, run focused tests:

```shell
source .local-test.env && poetry run pytest tests/feed/test_canonical_feeder.py tests/vault/test_curator.py
```

If `tests/vault/test_curator.py` does not exist, search for curator tests and run the closest focused test file instead.
