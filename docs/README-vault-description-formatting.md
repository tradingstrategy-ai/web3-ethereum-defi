# Vault description formatting

Individual vault records have two text fields. For sourcing and editorial guidance, see the [description writing guide](../.claude/docs/description-writing-style.md).

## Full description (`description`, "About the vault")

The website renders Markdown in the "About the vault" section. For manually written descriptions, the writing guide specifies the opening heading, `#### Vault's trading strategy`.

`normalise_vault_description()` preserves Markdown paragraph breaks. It converts carriage
returns and literal `\n` / `\r` escapes from provider APIs to real line
breaks, collapses spaces within each line and reduces runs of blank lines to
a single paragraph break. Leading indentation is not preserved, so avoid
nested lists. See the [Neutral Trade Autopilot descriptions](../eth_defi/vault/handwritten_metadata.py)
for an example with subheadings, bold text and inline links.

## Short description (`short_description`)

Short descriptions are shown verbatim in listings and tables. Leave them empty
when no useful, supported summary is available; a closure label or an explanation
that information is missing is not a summary. Non-empty summaries must be
one plain sentence of single-line text: no Markdown, newline characters,
carriage returns or literal `\n` / `\r` escapes. When a description or a
provider-supplied summary is automatically copied into `short_description`,
retain only its first sentence and ending punctuation.
`normalise_vault_short_description()` flattens the text and extracts the first
sentence. A single line here means no embedded line breaks; the frontend may
still wrap text to fit a screen.

## Existing descriptions

Scanner adapters and the public JSON exporter normalise the fields, including cached records. [Migration 009](../migration/scripts/009-normalise-vault-descriptions.py) flattened paragraphs under the earlier rules. A later scan or a reviewed override can restore paragraph breaks from source text.

| Deployment | Source or repair |
| --- | --- |
| Neutral Trade Autopilots | [Migration 015](../migration/scripts/015-neutral-trade-autopilot-markdown.py) introduced Markdown descriptions. |
| Nest AXI on Plume | [Source evidence](protocol-research/nest-axi-research.md) and [migration 023](../migration/scripts/023-nest-axi-description.py). |
| Birch Hill on Base | [Research for the two vaults](protocol-research/birch-hill-research.md) and [migration 038](../migration/scripts/038-birch-hill-descriptions.py). |
| Systemic Strategies L/S Grids | [Mandate and sources](protocol-research/systemic-strategies-vault-research.md) and [migration 042](../migration/scripts/042-systemic-strategies-description.py). |
| RockawayX Liminal USDC | [Migration 058](../migration/scripts/058-rockawayx-liminal-metadata.py) covers the description and lending tags for the HyperEVM deployment. |
| Lagoon curator vaults | The [November deployment review](protocol-research/lagoon-curator-onboarding-2026-11-10.md) identified evidence for 25 of 34 vaults. [Migration 061](../migration/scripts/061-lagoon-curator-descriptions.py) targets those 25. The other nine use the normal provider fallback. |

## Veranta individual vault description

The unified avUSDC vault on Base has its own [handwritten description](../eth_defi/vault/handwritten_metadata.py). The protocol YAML supplies the protocol page. [Migration 031](../migration/scripts/031-veranta-vault-description.py) refreshes the two description fields for the individual vault.

## Apply the repair

For later corrections to persisted descriptions, add a new numbered migration. Follow the [production runbook](../.claude/docs/migration-scripts.md#production-runbook) to stop writers, preview the pending batch, apply it and restart the scanner. After export and upload, check the individual vault JSON and the rendered page. A description-only repair needs no historical price scan.
