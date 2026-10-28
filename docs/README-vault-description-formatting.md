# Repair vault description formatting

Nest's [first-party CMS](https://cms.nest.credit/api/vaults?limit=100&depth=2)
has supplied the BlackOpal LiquidStone II summary with a literal `\n` between
sentences, and its full description with actual paragraph breaks. Individual
vault `description` and `short_description` values must be single-line text,
without newline characters, carriage returns or literal `\n` / `\r` escapes.
Short descriptions must contain only one sentence. When a description or a
provider-supplied summary is automatically copied into `short_description`,
retain only its first sentence and ending punctuation. This requirement does
not change protocol-level Markdown descriptions or notes. A single line here
means no embedded line breaks; the frontend may still wrap text to fit a screen.

The corrected summary is:

> Investment grade short-duration payment receivables.

Lagoon's [Coinshift USPC High Yield page](https://app.lagoon.finance/vault/1/0x09252D2C4afca9B1479efdD39fAa53DE9ff23114)
also supplies two sentences in its summary field. Its adapter now retains only
the first sentence, including for previously cached responses.

`normalise_vault_description()` flattens full descriptions, while
`normalise_vault_short_description()` also extracts the first sentence. Nest,
Lagoon and scanner output use the appropriate helper; automatically generated
Accountable summaries share the same first-sentence logic.
The public JSON exporter also applies both rules to every published vault,
including reused sticky records and native vault records that bypass the
ERC-4626 scanner.
Migration `009-normalise-vault-descriptions.py` repairs existing rows across all
protocols without fetching new data. It changes only `_description` and
`_short_description`; full descriptions retain every sentence. It preserves
notes, all other metadata and scanning state.
Rerunning an applied repair makes no further changes.

## Apply the repair

Use the [numbered migration guide](../.claude/docs/migration-scripts.md) for local previews and production
rollout. Set `PIPELINE_DATA_DIR` to an isolated copy directory for local checks.
The former `fix-vault-descriptions.py` command is retired. The unified command
runs all pending repairs, with description normalisation last. Stop the daemon
and competing writers before either preview or apply.

After applying, restart the same inspected scanner image. Its normal cleaning,
exports and uploads publish the repaired descriptions. A successful journal
entry is skipped on subsequent runs; future normalisation changes need a new
numbered migration. Do not reset reader state or rescan historical prices to
repair display text.
