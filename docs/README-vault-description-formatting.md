# Vault description formatting

Individual vault records have two text fields with different formats.

#### Full description (`description`, "About the vault")

The website renders the full description as Markdown in the "About the
vault" box. Write it for reading on a vault page:

- **Paragraphs of two or three sentences**, separated by a blank line. Do not
  write one long block of text.
- **Bold** only for key words, amounts or short phrases a reader must not miss,
  such as material restrictions or shared capital. Emphasise a whole sentence only
  when its complete wording is essential to a material warning.
- **Inline links** to the sources of material claims, each linked once,
  preferably on first mention.
- **`####` subheadings** when a description covers several topics, such as how
  the strategy works, custody, material restrictions, or the company. Do not use
  larger heading levels; the page already has its own section headings.

`normalise_vault_description()` keeps this structure. It converts carriage
returns and literal `\n` / `\r` escapes from provider APIs to real line
breaks, collapses spaces within each line and reduces runs of blank lines to
a single paragraph break. Leading indentation is not preserved, so avoid
nested lists. See the [Neutral Trade Autopilot descriptions](../eth_defi/vault/handwritten_metadata.py)
for an example with subheadings, bold text and inline links.

#### Short description (`short_description`)

Short descriptions are shown verbatim in listings and tables. They must be
one plain sentence of single-line text: no Markdown, newline characters,
carriage returns or literal `\n` / `\r` escapes. When a description or a
provider-supplied summary is automatically copied into `short_description`,
retain only its first sentence and ending punctuation.
`normalise_vault_short_description()` flattens the text and extracts the first
sentence. A single line here means no embedded line breaks; the frontend may
still wrap text to fit a screen.

#### History

Nest's [first-party CMS](https://cms.nest.credit/api/vaults?limit=100&depth=2)
has supplied the BlackOpal LiquidStone II summary with a literal `\n` between
sentences, and its full description with actual paragraph breaks. Until
October 2026 both fields were flattened to single lines. Full descriptions
now keep their paragraphs, while summaries are still flattened.

The corrected summary is:

> Investment grade short-duration payment receivables.

Lagoon's [Coinshift USPC High Yield page](https://app.lagoon.finance/vault/1/0x09252D2C4afca9B1479efdD39fAa53DE9ff23114)
also supplies two sentences in its summary field. Its adapter now retains only
the first sentence, including for previously cached responses.

Nest, Lagoon and scanner output use the appropriate helper; automatically
generated Accountable summaries share the same first-sentence logic.
The public JSON exporter also applies both rules to every published vault,
including reused sticky records and native vault records that bypass the
ERC-4626 scanner.
Migration `009-normalise-vault-descriptions.py` repaired existing rows across all
protocols without fetching new data, flattening both fields under the earlier
single-line rule. It changes only `_description` and `_short_description`;
full descriptions retain every sentence. Paragraphs it removed return when a
vault is next scanned or when a later migration supplies new copy, such as
`015-neutral-trade-autopilot-markdown.py`. It preserves
notes, all other metadata and scanning state.
Rerunning an applied repair makes no further changes.

## Nest AXI manual override

Nest AXI's Plume vault (`98866:0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0`)
uses reviewed copy from `eth_defi/vault/handwritten_metadata.py` for both text
fields. The Nest adapter resolves that exact deployment before consulting
provider metadata, so subsequent scans retain the override.

**Run after merge:** migration `023-nest-axi-description.py` refreshes only
`_description` and `_short_description` in `vault-metadata-db.pickle`: at most
one existing row and two changed fields. A missing row is reported without
creation. Prices, reader state, timestamp caches and all other metadata remain
unchanged; no historical price backfill is needed.

Follow the [production runbook](../.claude/docs/migration-scripts.md#production-runbook)
to inspect Compose mounts, build the merged revision into the stamped image,
and stop persistent and competing writers before either command. From
`~/vault-scanner/web3-ethereum-defi`, preview all pending repairs, review their
reported changes, then apply:

```shell
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
```

Recreate the persistent services with the same image as described in the
runbook. After their normal export and upload cycle, check both published AXI
descriptions against the maintained copy. Migration success alone does not
publish the change.

## Veranta individual vault description

The unified avUSDC vault on Base
(`8453:0x944766f715b51967e56afde5f0aa76ceacc9e7f9`) uses address-scoped
handwritten copy through `VerantaVault.description` and `short_description`.
Its protocol YAML is separate: publishing that file alone shows the text on
the protocol page, not the individual vault's "About the vault" section.
Scanner and individual vault JSON export tests cover that distinction.

**Run after merge:** migration `031-veranta-vault-description.py` refreshes only
`_description` and `_short_description` for at most one existing Veranta row.
It reports a missing target without creation and rejects conflicting protocol
attribution. Successful migration 030 is unchanged and must not be rerun.
There is no price backfill, discovery or blockchain read. Notes, fees, names,
flags, unrelated metadata, reader state, prices and timestamp caches survive.

Record the eventual PR URL in migration 031 on the feature branch before
merging the PR. Follow the [production runbook](../.claude/docs/migration-scripts.md#production-runbook):
inspect `docker-compose.yml` and persistent mounts, update to the merged
revision, build the stamped image, and quiesce all competing writers. From
`~/vault-scanner/web3-ethereum-defi`, use the same image for the entire operation:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps --entrypoint vault-migrate vault-scanner-oneshot list
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
```

Both preview and apply require stopped writers. Review the entire pending
numbered batch before apply; expected for 031 is at most one row and two fields.
The runner acquires its lifetime guard before the writer lock. After normal
scanner export and publication, inspect the individual vault JSON's
`description` and `short_description`, then the
[vault page](https://tradingstrategy.ai/vaults/veranta-usdc-vault).
Verify paragraphs, fee bullets and source links render, and that existing
price history remains intact. A successful migration does not publish data
by itself.

## Birch Hill individual vault descriptions

Both publicly documented Base deployments have short and full descriptions:
Groma Ecosystem USDC (`8453:0xb038cf3e233b78225f6d350ac8319bec8457db4a`)
and RWA USDC Flagship (`8453:0xa5afd3a598ef000a5edd96ed65929ac7a6feea39`).
Their Morpho V2 and Yearn V3 accessors retain the reviewed copy on future scans.
The curator YAML supplies a separate organisation profile.

**Run after merge:** migration `038-birch-hill-descriptions.py` updates only
`_description` and `_short_description` for at most two existing rows and four
fields. Missing targets are reported without creation. There is no discovery,
blockchain read or historical price backfill; all other metadata and scanning
state are preserved. Migration 037 handles admission mapping separately and
must not be rerun if already successful.

Migration `039-birch-hill-strategy-tags.py` separately refreshes the Flagship's
`_strategy_tags` only: at most one existing row and one field. The shared resolver
uses its Yearn V3 features even when the public protocol label is `ERC-4626`.
The exact curator address override also corrects attribution during export;
it does not need a stored curator-field migration. Existing Birch Hill logo
variants are reused.

Record the eventual PR URL in migration 038 before merging. Use the stamped
merged image and the stop, list, preview, apply and restart commands above,
following the production runbook. Review the entire pending batch before apply;
both preview and apply require stopped writers, with the lifetime guard acquired
before the writer lock. After normal export and upload, verify both vaults'
individual JSON descriptions and rendered paragraphs, headings and source links.

## Systemic Strategies L/S Grids descriptions

The [L/S Grids vault](https://tradingstrategy.ai/vaults/systemic-strategies-l-s-grids)
on Hypercore (`9999:0x07fd993f0fa3a185f7207adccd29f7a87404689d`) uses
address-scoped handwritten short and full descriptions through the existing
native exporter. The copy covers the supply-based long/short grid mandate
and the risks of directional exposure. Routine fees and
lock-up timing remain in the page's dedicated metrics.
Research sources and limitations are recorded in
[the research note](../.claude/docs/systemic-strategies-vault-research.md).
The curator profile and HyperGrowth vault are separate from this override.

**Run after merge:** migration `041-systemic-strategies-grid-tags.py` refreshes
only `_strategy_tags` for at most one row and one field. The later migration
`042-systemic-strategies-description.py` refreshes only `_description` and
`_short_description` for at most one row and two fields. Missing targets are
reported without creation. Neither operation changes prices, reader state,
notes, fees or unrelated rows; no historical price backfill is needed.

Record the eventual PR URL in both new migrations before merging. Follow the
production runbook and the stop, list, preview, apply and
restart commands above. Inspect Compose mounts and build the merged revision
into the stamped image first. Stop the daemon and competing writers before
both preview and apply, preserving the lifetime-guard-before-writer-lock order.
Review the entire pending batch and use the same image throughout maintenance.
After the restarted scanner's normal export and upload cycle, verify the
individual vault JSON's descriptions and tags, then check that the page renders
its paragraphs, subheadings and source links. Migration success alone does not
publish the changes.

## Apply the repair

Use the [numbered migration guide](../.claude/docs/migration-scripts.md) for local previews and production
rollout. Set `PIPELINE_DATA_DIR` to an isolated copy directory for local checks.
The former `fix-vault-descriptions.py` command is retired. Its standalone
format is legacy; use a new numbered migration for later repairs. The unified command
runs all pending repairs in numeric order. Later copy repairs must supply
normalised Markdown descriptions and single-sentence summaries. Stop the daemon
and competing writers before either preview or apply.

After applying, restart the same inspected scanner image. Its normal cleaning,
exports and uploads publish the repaired descriptions. A successful journal
entry is skipped on subsequent runs; future normalisation changes need a new
numbered migration. Do not reset reader state or rescan historical prices to
repair display text.
