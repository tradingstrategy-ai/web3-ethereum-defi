# Vault description formatting

Individual vault records have two text fields with different formats.

#### Full description (`description`, "About the vault")

The website renders the full description as Markdown in the "About the
vault" box. Write it for reading on a vault page:

- **Paragraphs of two or three sentences**, separated by a blank line. Do not
  write one long block of text.
- **Bold** for the few facts a reader must not miss, such as fees, lock-ups or
  the fact that capital is shared with another vault.
- **Inline links** to the sources of material claims, each linked once,
  preferably on first mention.
- **`####` subheadings** when a description covers several topics, such as how
  the strategy works, custody, fees and withdrawals, or the company. Do not use
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
