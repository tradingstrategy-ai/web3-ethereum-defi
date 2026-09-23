# Vault export post-processing hooks plan

## Goal

Add a small post-processing hook stage to the vault export pipeline. Hooks run
after `VaultDatabase.read()` and before metrics and public vault JSON records
are built. The first hook corrects false Yearn attribution by checking Yearn
rows against Yearn's official offchain vault catalogue.

This stage is shared by `scan-vaults-all-chains.py` and
`post-process-prices.py` through their existing `run_post_processing()` and
`top_vaults_json.main()` path.

## Why the existing Yearn check misses these vaults

Yearn-compatible interfaces are not proof that Yearn operates a vault. The
current offchain check only adds `yearn_registry_excluded` when the official
registry contains an explicit negative or empty `inclusion` record. A
technically Yearn-compatible vault that is completely absent from the registry
keeps its Yearn protocol, curator and homepage link. This is why the Monad false
positives survive the existing check.

The export hook changes the rule for public attribution: a row currently
labelled Yearn must have a positive match in the official catalogue. Otherwise
it is exported as generic ERC-4626 while retaining its technical Yearn feature
for adapter behaviour.

## Implementation

### 1. Add a minimal hook runner

Create `eth_defi/vault/export_post_processing.py` with:

- a hook callable that receives the in-memory `VaultDatabase` and returns the
  corrected vault IDs;
- an ordered module-level tuple of registered hooks; and
- `run_vault_export_post_processors()` to run the hooks and log their names,
  corrected-row counts and elapsed times.

Call the runner in `eth_defi/vault/top_vaults_json.py` immediately after
`VaultDatabase.read()`. Hooks mutate only the exporter-owned in-memory database;
they do not introduce plugin discovery, dependency injection or a new
configuration format.

Corrected rows must replace stale sticky export records in the same build.
Before forcing a corrected ID through metrics, compare its cleaned attribution
with the existing exported record using the exporter's normalised values. Only
stale records bypass the normal freshness cadence, avoiding repeated price
work on later looped scans.

### 2. Add the Yearn cleanup hook

Create
`eth_defi/erc_4626/vault_protocol/yearn/export_cleanup.py` and reuse the
existing official Yearn Kong registry URL, request timeout and response
validation from `yearn/endorsement.py` where practical.

For one JSON build:

1. Fetch the official catalogue once and normalise positive Yearn entries to
   `(chain_id, lowercase_address)` keys.
2. Project current Yearn rows, plus already-generic rows carrying the
   exclusion marker, to a compact Pandas DataFrame so stale sticky attribution
   can be repaired.
3. Use a vectorised merge or membership operation to split those rows into
   official matches and non-matches.
4. Leave official matches as Yearn.
5. Add `yearn_registry_excluded` to non-matches and derive their replacement
   protocol from the remaining non-Yearn features. This is normally generic
   ERC-4626, but a more specific retained protocol feature may win. Update the
   slug and explorer link with existing helpers.
6. Preserve technical Yearn features and all unrelated manual flags. Do not
   change rows already attributed to another protocol and do not promote
   generic rows to Yearn.

If the catalogue request fails or the response fails its existing shape and
minimum-size sanity checks, log the failure and skip this hook for the current
build. The hook runner must catch the failure so the normal export continues
with the existing metadata; a broken offchain service must never crash the
scanner or prevent unrelated vaults from publishing. Do not add per-chain
policy tables, address hard-codes, a new database or a second cache layer.

### 3. Keep it fast

The hook performs one HTTP request and one vectorised metadata operation per
JSON build. It must not make per-vault API or RPC calls, instantiate adapters,
or scan the price Parquet.

Pandas is sufficient because the metadata table and Yearn catalogue contain
only thousands of rows. Use PyArrow only if a benchmark shows that the Pandas
projection is material; maintaining two representations without evidence is
unnecessary. A short loop over only the corrected rows is acceptable when
updating `VaultRow` dictionaries.

Log catalogue size, candidate count, corrected count and elapsed time so the
production cost is visible.

## Tests

Add focused tests in
`tests/erc_4626/vault_protocol/test_yearn_export_cleanup.py`:

- a positive official match remains Yearn;
- an absent catalogue match becomes generic ERC-4626;
- an already-excluded generic row cannot retain stale Yearn curator or link
  data;
- technical Yearn features and unrelated flags are preserved;
- another protocol with a Yearn-compatible feature is untouched;
- an unavailable catalogue is logged and leaves the export unchanged; and
- the hook runner contains an unexpected hook failure and still runs the next
  hook.

Extend the nearest top-vault export test to prove hooks run before metrics;
the runner failure test proves a broken hook is contained while the export
continues. Run only these focused tests with the required
`.local-test.env` prefix and three-minute timeout.

## Manual verification script

Add `scripts/erc-4626/yearn/verify-yearn-export-cleanup.py` as a read-only live
check.

The script uses environment variables:

- `VAULT_JSON`, defaulting to
  `~/.tradingstrategy/vaults/top_vaults_by_chain.json`; and
- `LOG_LEVEL`, defaulting to `info`.

It fetches the official Yearn catalogue once, loads the generated JSON, and:

- fails if a JSON row attributed to Yearn lacks a positive official match;
- fails if a row carrying `yearn_registry_excluded` still has Yearn protocol,
  curator or homepage attribution;
- ignores rows already attributed to another protocol, even when they expose a
  Yearn-compatible feature; and
- prints a `tabulate` table of Monad Yearn-compatible rows with their final
  protocol and link;
- reports catalogue size, checked-row count and elapsed time.

It must expand `~`, exit non-zero on mismatches, and never modify the metadata
pickle, upload files or require an RPC endpoint.

## Documentation

Update:

- `scripts/erc-4626/README-vault-scripts.md` with the hook's place in the
  all-chain pipeline, failure behaviour and manual verification command;
- `docs/source/vaults/yearn/index.rst` with the positive official-match rule;
- module and function docstrings in the hook runner, Yearn cleanup,
  `top_vaults_json.main()` and `post_processing.export_top_vaults_json()`; and
- the relevant `docs/source/api` index and stubs for the new modules, as
  required by the repository documentation rules.

Do not build Sphinx locally or edit generated autosummary files.

## Rollout

1. Run the focused tests.
2. Build vault JSON locally with uploads disabled or a test upload prefix.
3. Run the manual Yearn verifier against that JSON.
4. Confirm the Monad false positives are generic ERC-4626 and sample genuine
   Yearn vaults still have Yearn attribution and working links.

## Non-goals

- No chain rescan or historical price changes.
- No mutation or migration of the production metadata pickle.
- No changes to Yearn adapter selection or technical feature detection.
- No general plugin framework, asynchronous HTTP or joblib.
- No unrelated vault metadata cleanup.
