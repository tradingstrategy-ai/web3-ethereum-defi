---
name: add-stablecoin
description: Add a new stablecoin or staked/derivative stablecoin to feed metadata, classification sets, rich metadata YAML and logos. Use when the user wants a stablecoin symbol (e.g. a vault denomination token) to be recognised by is_stablecoin_like() and shown on the site.
---

# Add stablecoin

This skill adds a stablecoin — or a staked/wrapped derivative of one — to the
repository and wires it into every layer that consumes stablecoin data.

The most common trigger is a vault whose **denomination token** is a
yield-bearing stablecoin derivative (e.g. `sNUSD`, `savUSD`, `syrupUSDC`,
`eEARN`, `stkGHO`) that `is_stablecoin_like()` does not yet recognise, so the
vault is dropped by `filter_vaults_by_stablecoin()` in the price-cleaning
pipeline and never reaches `top_vaults_by_chain.json`.

## The four layers (what files we have)

Stablecoin support is spread across **four decoupled layers**. They are
maintained by hand and are **not auto-synced** — adding one does not update the
others. Decide up front which layers the task needs.

| #   | Layer                  | Path                                                                                      | Purpose                                                                               | Required?                                         |
| --- | ---------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------- |
| 1   | **Classification set** | `eth_defi/stablecoin_metadata.py`                                                         | `is_stablecoin_like()` membership; the **only** layer the price-cleaning filter reads | **Yes** — without it the vault stays filtered out |
| 2   | **Metadata YAML**      | `eth_defi/data/stablecoins/{slug}.yaml`                                                   | Name, description, links, contracts, category — exported to R2 / shown on site        | Strongly recommended                              |
| 3   | **Feed YAML**          | `eth_defi/data/feeds/stablecoins/{slug}.yaml`                                             | News/post scanner sources (Twitter, RSS)                                              | Recommended                                       |
| 4   | **Logos**              | `eth_defi/data/stablecoins/original_logos/{slug}/` and `formatted_logos/{slug}/light.png` | Site logo; only exported if `light.png` exists                                        | Optional                                          |

**Critical gotcha:** layer 1 is a hardcoded Python set of bare symbol strings.
The `category:` field in the YAML (layer 2) does **not** feed it. A YAML file
alone will not make a vault pass the filter — you must edit the Python set.

## Reference files (read these before editing)

Authoritative format docs and copy-from examples already in the repo:

- **Format spec** — the module docstring at the top of
  [`eth_defi/stablecoin_metadata.py`](../../../eth_defi/stablecoin_metadata.py)
  documents the YAML schema (standard + `entries:` shapes), the three symbol
  sets, the logo layout and the R2 export. Read it first.
- **Feed schema + canonical aliases** —
  [`eth_defi/data/feeds/README.md`](../../../eth_defi/data/feeds/README.md) and
  [`eth_defi/feed/README-feed.md`](../../../eth_defi/feed/README-feed.md)
  (see its "Canonical feeder aliases" section).
- **Metadata YAML examples** (`eth_defi/data/stablecoins/`):
  - `category: stablecoin` → [`gho.yaml`](../../../eth_defi/data/stablecoins/gho.yaml)
  - `category: yield_bearing` (staked, has long_description) → [`susde.yaml`](../../../eth_defi/data/stablecoins/susde.yaml)
  - `category: wrapped` → [`gmdusdc.yaml`](../../../eth_defi/data/stablecoins/gmdusdc.yaml)
  - `entries:` (shared ticker) → [`rusd.yaml`](../../../eth_defi/data/stablecoins/rusd.yaml)
- **Feed YAML examples** (`eth_defi/data/feeds/stablecoins/`):
  - full distinct-issuer entry → [`sbold.yaml`](../../../eth_defi/data/feeds/stablecoins/sbold.yaml)
  - `canonical-feeder-id` alias → [`sfrax.yaml`](../../../eth_defi/data/feeds/stablecoins/sfrax.yaml)
- **Logo skills** — [`extract-project-logo`](../extract-project-logo/SKILL.md) and
  [`post-process-logo`](../post-process-logo/SKILL.md).

Open the example that matches your token's category and mirror its field order
and style. Do not invent fields not present in these examples.

## Batch / parallel runs (read if multiple stablecoins at once)

When several stablecoins are added concurrently (e.g. one subagent each):

- **The Python file is shared state.** Every token's Step 3 edits the *same*
  file `eth_defi/stablecoin_metadata.py`. Parallel writes clobber each other.
  In a parallel run, a worker must **not** edit the Python file — instead it
  **returns its decision** `(symbol, target_set, justification)` and the
  orchestrator applies all set edits in one serial pass afterwards.
- The YAML and logo files are per-slug and do not collide — workers create those
  directly.
- **Do not run `post-process-prices.py` inside a worker.** It is heavy and
  uploads to R2. The orchestrator runs it once, at the end, after all Python
  edits land (see Step 7).

### Layer 1: three disjoint symbol sets

In `eth_defi/stablecoin_metadata.py`, choose exactly one set for the symbol;
their union is `ALL_STABLECOIN_LIKE`, used by `is_stablecoin_like()`:

- `STABLECOIN_LIKE` — primary pegged stables (USDC, DAI, GHO, NUSD, avUSD). Paired YAML `category: stablecoin`.
- `YIELD_BEARING_STABLES` — "staked <peg>" tokens where a base stablecoin we list (USDe, frxUSD, BOLD, NUSD, avUSD…) is staked into an auto-appreciating token (sUSDe, sfrxUSD, sBOLD, sNUSD, savUSD). Paired YAML `category: yield_bearing`.
- `WRAPPED_STABLECOIN_LIKE` — money-market / vault-share wrappers whose underlying is a plain stablecoin like USDC/DAI (cUSDC, aDAI, gmdUSDC, eEARN). Paired YAML `category: wrapped`.

Use the **exact on-chain symbol string**, preserving case (e.g. `sUSDe`, not
`SUSDE`). The Python symbol may differ in case from the slug.

### Choosing yield_bearing vs wrapped (don't over-think it)

For the **filter**, the choice does not matter — all three sets feed
`ALL_STABLECOIN_LIKE` equally, so any of them unblocks the vault. The
yield_bearing/wrapped split only affects how the token is **grouped for display**.
Use this heuristic and move on:

- There is a clearly-named **base peg** and this is its staked form (`s`/`st`/`sav`
  prefix over a token we'd list on its own) → **yield_bearing**.
- It is an ERC-4626 receipt whose underlying is just plain USDC/DAI and there is
  no distinct intermediate peg → **wrapped**.

If genuinely on the line, pick `yield_bearing` and note the call in your report.
**Do not block on this distinction.**

**Ignore existing drift:** some already-committed files put a symbol in one
Python set while their YAML uses a different `category` (e.g. `aDAI`/`cUSDC` are
in `WRAPPED_STABLECOIN_LIKE` but their YAML says `category: yield_bearing`). That
is historical inconsistency — do **not** copy it. For a *new* token, keep the
Python set and the YAML `category` paired per the mapping above.

## Inputs

Gather or infer before editing:

- The stablecoin **symbol** exactly as it appears on-chain (e.g. `savUSD`).
- Whether it is a **base** peg or a **staked/wrapped derivative**.
- For a derivative: the **parent/underlying** stablecoin and the **issuer**.
- Issuer **website**, **Twitter/X** handle (no `@`), optional **LinkedIn**, **RSS/blog**.
- **Contract address(es)** per chain and a short + long description from primary sources.
- Logo source (brand kit, website, CoinGecko), if a logo is wanted.

If the symbol came from a vault denomination (e.g. via the Royco diagnosis),
note its chain and address as evidence.

## Slug convention

The **slug** is the lowercased symbol with non-alphanumeric characters replaced
by `-` — both `.` and `_` become `-` (`USDC.e` → `usdc-e`,
`AA_FalconXUSDC` → `aa-falconxusdc`). Tranche-style symbols with underscores and
uppercase (e.g. `AA_`/`BB_` prefixes) are common; only the slug is normalised —
the `symbol:` field and the Python-set entry keep the **exact** on-chain string
(`AA_FalconXUSDC`). The slug is the filename stem for both the metadata YAML and
the feed YAML, and the value of `slug:` / `feeder-id:`. **Base and derivative
get separate files** — never bundle a staked token into the base file. The
`token_symbols:` field is only for aliases of the _same_ token (casing/bridged
variants), not for derivatives.

## Step 1: Check existing coverage

Do not duplicate. Check all four layers for the symbol and its parent:

```shell
SYM=savUSD; SLUG=savusd
# Layer 1 — classification sets
poetry run python -c "from eth_defi.stablecoin_metadata import ALL_STABLECOIN_LIKE as A; print('$SYM in set:', '$SYM' in A)"
# Layers 2 + 3 — YAML files (symbol and likely parent)
ls eth_defi/data/stablecoins/${SLUG}.yaml eth_defi/data/feeds/stablecoins/${SLUG}.yaml 2>/dev/null
rg -n "$SYM" eth_defi/data/stablecoins eth_defi/data/feeds/stablecoins
# Layer 4 — logos
ls eth_defi/data/stablecoins/formatted_logos/${SLUG}/ 2>/dev/null
```

If the symbol is already in a set and has YAML, stop and report — likely only a
logo or a feed source is missing.

## Step 2: Resolve the parent (canonical) for derivatives

For a staked/wrapped derivative, identify its underlying stablecoin and issuer
from primary sources (issuer docs, DeFiLlama, the ERC-4626 `asset()`). Do **not**
guess the parent by stripping an `s`/`st`/`sav` prefix — verify it.

Then decide the feed-file shape (Step 4). **Prefer an alias over duplicating a
news source.** Before writing a full entry, search for any existing feeder for
the same issuer/curator across *all* roles, not just the parent peg:

```shell
rg -n "twitter:|feeder-id:|name:" eth_defi/data/feeds/protocols eth_defi/data/feeds/curators eth_defi/data/feeds/stablecoins | rg -i "{issuer or twitter handle}"
```

- **An existing feeder already carries this issuer's news source** (a parent peg
  under `stablecoins/`, OR the protocol under `protocols/`, OR the curator under
  `curators/`) → alias to it: `canonical-feeder-id: {that-feeder-id}` and omit
  `twitter`/`rss`. `canonical-feeder-id` may cross roles. Patterns:
  `sfrax` → `frax` (peg), `autousd` → `auto-finance` (protocol),
  `bbqusdc` → `steakhouse-financial` (curator). This avoids scanning the same
  account twice.
- **No existing feeder for this issuer anywhere** → full entry with its own
  `twitter`/`rss`. Pattern: `sbold` → K3 Capital.

The canonical target must be a real, **source-bearing** feeder (it has
`twitter`/`rss` and no `canonical-feeder-id` of its own). Confirm it exists and
check its contents before pointing at it.

**Ticker-case / collision trap:** the parent peg's slug may be taken by an
*unrelated* project that shares the ticker — e.g. `cusd.yaml` is Mento Dollar
(Celo), which is **not** Cap's cUSD. Do not alias `stcUSD` to that file. When the
genuine parent has no feeder of its own, write a full entry; never point
`canonical-feeder-id` at a same-ticker stranger. Likewise the symbol sets are
case-sensitive: `CUSD` (Mento) and `cUSD` (Cap) are different members — match the
exact on-chain casing.

## Step 3: Update the classification set (layer 1 — required)

Edit `eth_defi/stablecoin_metadata.py` and add the exact symbol string to the
correct set (`STABLECOIN_LIKE`, `YIELD_BEARING_STABLES`, or
`WRAPPED_STABLECOIN_LIKE`). Keep the existing ordering/formatting style of the
set. This is the change that actually unblocks `filter_vaults_by_stablecoin()`.

**Parallel/batch run:** do not edit this file yourself — return
`(symbol, target_set, why)` and let the orchestrator apply it (see "Batch /
parallel runs" above).

## Step 4: Add the feed YAML (layer 3)

Save under `eth_defi/data/feeds/stablecoins/{slug}.yaml`.

Distinct-issuer (full) entry:

```yaml
feeder-id: {slug}
name: {Issuer} {Symbol}
role: stablecoin
website: https://example.org/
twitter: example
linkedin: example-company
rss: https://example.org/feed.xml
```

Same-issuer derivative (canonical alias — no feed sources):

```yaml
# {Symbol} - same issuer as {PARENT}
feeder-id: { slug }
name: { Human name }
role: stablecoin
canonical-feeder-id: { parent-slug }
```

Use a leading `# comment` to record handle changes or broken/disabled feeds, as
existing files do. Verify any RSS URL actually resolves before adding it.

## Step 5: Add the metadata YAML (layer 2)

Save under `eth_defi/data/stablecoins/{slug}.yaml`. Standard (one project per
symbol) shape:

```yaml
symbol: { Symbol } # exact on-chain ticker, matches the Python set entry
name: { Full human-readable name }
slug: { slug } # matches filename stem and feeder-id
category: yield_bearing # stablecoin | yield_bearing | wrapped — MUST match the set chosen in Step 3
short_description: |
  {One to three sentences. Describe the token and its peg/yield mechanism.}
long_description: |
  {Multi-paragraph Markdown with inline links to the issuer. Empty string if not yet written.}
links:
  homepage: https://example.org/
  coingecko: "" # empty string if not listed
  defillama: ""
  twitter: https://x.com/example
contract_addresses:
  - chain: ethereum # chain slug: ethereum, arbitrum, base, avalanche, …
    address: "0x..." # checksummed ERC-20 address
checks:
  twitter_last_post_at: ""
  domain_up_at: ""
  marked_dead_at: ""
  information_found_missing_at: ""
```

Field rules learned from real runs:

- **Verify every contract address on-chain before adding it.** Do not trust a
  block-explorer search result or a docs page alone — multi-chain "same address"
  claims are a common hallucination. Read `symbol()` at each `(chain, address)`
  and confirm it equals the token symbol:

  ```shell
  source .local-test.env && poetry run python - <<'PY'
  from web3 import Web3
  from eth_defi.provider.multi_provider import create_multi_provider_web3
  import os
  w3 = create_multi_provider_web3(os.environ["JSON_RPC_ETHEREUM"])  # pick the chain's JSON_RPC_* var
  abi = [{"name":"symbol","outputs":[{"type":"string"}],"inputs":[],"stateMutability":"view","type":"function"}]
  c = w3.eth.contract(address=Web3.to_checksum_address("0x..."), abi=abi)
  print(c.functions.symbol().call())
  PY
  ```

  Include only addresses you verified. Start from the evidenced address (the
  vault denomination), then add other chains only after the same check passes.
- **Confirming the underlying / USD-peg on non-standard wrappers:** read the
  ERC-4626 `asset()` to confirm the underlying is a dollar token. Tranche / CDO
  receipts (e.g. Pareto `AA_`/`BB_` tranches) often do **not** implement
  `asset()` — the call reverts. Fall back to the protocol's own accessor: read
  the CDO/minter contract (`minter()` → `token()`) or the docs to confirm the
  underlying (usually USDC/DAI). If you cannot confirm a USD underlying, STOP and
  report rather than classify it as a stablecoin.
- **Chain slugs** must be ones the codebase knows — see `eth_defi.chain.CHAIN_NAMES`
  (`ethereum`, `arbitrum`, `base`, `avalanche`, `linea`, …). Omit a deployment
  whose chain slug you cannot confirm rather than guessing it.
- **`defillama` link:** yield-bearing / RWA tokens often have no
  `/stablecoin/` page — a `/protocol/...` or `/rwa/...` page is acceptable, and an
  empty string is fine if none exists. Do not force a `/stablecoin/` URL.
- For a canonical-alias derivative, use the **same homepage domain** as the parent
  YAML for consistency unless you can prove the parent's domain is wrong.

Use an `entries:` file (top level holds only `symbol`/`slug`/`category`) only
when several unrelated projects share the same ticker — see the module docstring
in `stablecoin_metadata.py` for that shape. Write descriptions from primary
sources only; if evidence is too weak, stop and ask rather than invent.

## Step 6: Logos (layer 4 — optional)

Only needed if a site logo is wanted (export skips missing logos gracefully).

When subagents are available and the user allowed delegated work, use them:

- One subagent runs `extract-project-logo` to fetch the official logo into
  `eth_defi/data/stablecoins/original_logos/{slug}/`. Prefer brand kit → website
  header → GitHub asset → CoinGecko/Twitter avatar.
- Then a subagent runs `post-process-logo` with input
  `eth_defi/data/stablecoins/original_logos/{slug}/` and output
  `eth_defi/data/stablecoins/formatted_logos/` to produce
  `formatted_logos/{slug}/light.png` (256×256).

Otherwise run the two skills sequentially. A derivative may reuse the parent's
brand if it has no distinct mark.

## Step 7: Verify

Classification (the unblock test):

```shell
poetry run python -c "from eth_defi.stablecoin_metadata import is_stablecoin_like; print(is_stablecoin_like('{Symbol}'))"
```

Metadata + feed YAML load through the shared loaders:

```shell
source .local-test.env && poetry run python - <<'PY'
from pathlib import Path
from eth_defi.stablecoin_metadata import build_stablecoin_metadata_json
from eth_defi.feed.sources import load_feeder_metadata

m = build_stablecoin_metadata_json(Path("eth_defi/data/stablecoins/{slug}.yaml"))
print("metadata ok:", m[0]["symbol"], m[0]["category"])
f = load_feeder_metadata(Path("eth_defi/data/feeds/stablecoins/{slug}.yaml"))
print("feed ok:", f.get("feeder-id"), f.get("canonical-feeder-id"))
PY
```

If a vault was the motivation, re-run post-processing (no rescan needed — the
uncleaned parquet already holds correct prices) and confirm the vault now
survives cleaning. **Orchestrator-only, run once after all Python edits land —
never inside a parallel worker; it is heavy and uploads to R2:**

```shell
source .local-test.env && poetry run python scripts/erc-4626/post-process-prices.py
```

Format any Python edits:

```shell
poetry run ruff format eth_defi/stablecoin_metadata.py
poetry run ruff check eth_defi/stablecoin_metadata.py
```

## Step 8: Report

Summarise:

- Symbol and which classification set it was added to (and why that set).
- Metadata YAML created, with `category` and source pages used.
- Feed YAML created — full entry vs `canonical-feeder-id` and the resolved parent.
- Logo files added or reused, or noted as skipped.
- Verification output (`is_stablecoin_like` True, loaders pass, vault survives cleaning).
- Any inferred sources or unresolved identity/parent questions.
