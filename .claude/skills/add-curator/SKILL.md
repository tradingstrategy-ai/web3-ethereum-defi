---
name: add-curator
description: Add a new vault curator to feed metadata and curator detection. Use when the user wants to add a verified curator, protocol-managed curator, or curator alias discovered from vault data.
---

# Add curator

This skill adds a vault curator to the repository and wires it into
feed tracking and Python curator detection.

## Inputs

Gather or infer these before editing:

- Curator name
- Curator slug, using the existing feeder slug style
- Evidence from vault data: vault names, protocols, chains, and why this is a curator
- Curator type: third-party curator, alias to an existing feeder, protocol-managed curator, or name-pattern update
- Short curator description and long curator description
- Optional website, Twitter/X, LinkedIn, blog/RSS, supporting links, and logo source

## Strategy tags

After identifying the vaults associated with the curator, run the
`categorise-vault-strategy` skill for **every newly added vault and every
existing vault newly covered by the curator metadata**. Curator attribution
does not replace strategy classification. Follow
`.claude/skills/categorise-vault-strategy/SKILL.md` to review each vault's
strategy context, update the protocol-local address-level `tags.py` mapping,
and verify `get_strategy_tags()` or the native vault export returns the
maintained tags.

Do not stop after tagging one representative vault: every vault identified as
newly associated with the curator must have an evidence-based mapping, or must
remain unmapped so its resolver returns the explicit missing-information
result (``None`` for ``VaultBase`` adapters).

When adding EVM address mappings, use plain lowercase string keys in
``tags.py`` (for example ``"0x1234..."``), not ``HexAddress(...)``
constructors. The strategy-tag lookup helper normalises adapter addresses
before reading these mappings.

If the candidate was produced by `find-new-curators`, open its result
file first and keep the evidence trail in mind.

## Naming policy

Use one clean, organisation-level public name for a curator. Prefer the main
brand name without legal, jurisdictional, regional or affiliate suffixes, for
example `China Asset Management`, not `China Asset Management (Hong Kong)`.

- Do not create separate curator records merely because an organisation has
  international entities, regional subsidiaries or locally incorporated legal
  vehicles.
- Keep the public `name`, short description and curator-facing long description
  on the main brand; use the same canonical curator slug for its international
  entities.
- Preserve an upstream's exact legal or regional spelling in the applicable
  protocol-specific mapping field (such as `asseto-role`) or a detection
  pattern, so attribution remains accurate without exposing a cluttered name.
- Mention a specific legal entity in the long description only when it adds
  material factual context; do not use it as the public curator identity.
- Create separate curator records only for genuinely separate, independently
  branded organisations, not geographic variants of one brand.

## Website descriptions

The YAML `short_description` and `long_description` fields are public website
copy for DeFi professionals, not repository notes.

- Use clear, neutral language to explain the curator's organisation, investment
  remit and the protocols, strategies or stablecoins it oversees when those are
  publicly verified. State whether the curator is independent or protocol
  operated when that affects how users assess responsibility.
- Do not imply custody, discretionary control, affiliation, endorsement or a
  performance record unless an authoritative source supports it. If no separate
  curator is disclosed, say so plainly rather than inferring one from contract
  data.
- Add inline Markdown links in the body: link the official website on first
  mention, and link the announcement, documentation or terms that support a
  material relationship or claim. A URL in a separate YAML field is not a
  replacement for the in-text source.
- Mention a public founder, executive or well-known backer only when a primary
  source confirms the connection and it materially helps readers understand the
  organisation. Link the supporting announcement or biography; never infer an
  investor from an aggregator database or use the name as an endorsement.
- Exclude project-specific logic and technical internals, including classifier
  decisions, scanner or adapter behaviour, ABI names, contract accessors and
  implementation details. Put those in code comments or technical
  documentation instead.

## Step 1: Check existing coverage

Run the existing curator inventory and search for nearby entries:

```shell
poetry run python .claude/skills/find-new-curators/scripts/print-existing-curators.py
rg -n "{curator name}|{curator slug}" eth_defi/data/feeds eth_defi/vault/curator.py
```

Check whether the same organisation already exists as a protocol or
stablecoin feeder:

```shell
rg --files eth_defi/data/feeds -g "*.yaml"
```

Prefer an alias YAML with `canonical-feeder-id` when the organisation
already has feed sources under `protocols/` or `stablecoins/`.
When an apparent candidate is a regional or international entity, search for
the main brand first and extend its protocol-specific aliases rather than
adding another curator record.

## Step 2: Verify identity and sources

Use official sources when possible:

- Official website homepage
- Twitter/X account linked from the website, written without `@`
- LinkedIn company slug from the official company page URL
- RSS or Atom feed for an official blog, only if it works
- Documentation, forum post, or vault UI proving the organisation acts as curator

Add descriptions from primary sources:

- `short_description`: one concise, audience-facing sentence. Prefer the
  Twitter/X or LinkedIn bio when available; otherwise use the official homepage
  tagline. Describe the organisation, not a single vault or token.
- `long_description`: two to four Markdown-safe sentences or short paragraphs
  following the website-description standard above. Avoid hype, rankings,
  unverifiable performance claims and investment advice.
- For alias curators, describe the curator organisation represented by
  the alias.  Do not blindly inherit a product-only description from
  the canonical feeder when it would misrepresent the alias.

Avoid adding aggregator pages, unofficial social accounts, or broken
RSS feeds.  If a social account is inferred from search results, note
that it was inferred in the final answer.

## Step 3: Update feed files

For a new third-party curator, create:

```yaml
feeder-id: {curator-slug}
name: {Curator name}
role: curator
website: https://example.org
short_description: {One-sentence curator description.}
long_description: |
  {Two to four sentence curator description based on official homepage,
  docs, Twitter/X bio, or LinkedIn bio.}
twitter: example
linkedin: example-company
rss: https://example.org/feed.xml

# Evidence and background references.
#
# Use this section to preserve why this organisation is treated as a
# vault curator.  Prefer primary sources: protocol forum announcements,
# protocol docs, curator launch posts, vault UI pages, or official blog
# posts.  These links are metadata only; the feed collector will not
# fetch them as post sources.
#
# Be verbose with titles so a future reviewer can understand the
# evidence without opening every link.
other-links:
  - title: Protocol forum - {Curator name} announced as curator for {vault or strategy name}
    url: https://example.org/curator-announcement
```

Save this as:

```text
eth_defi/data/feeds/curators/{curator-slug}.yaml
```

Omit unknown optional fields instead of leaving empty keys.
Always add `short_description` and `long_description` for new curator
YAML files.  If primary-source evidence is too weak to write them, stop
and ask instead of inventing descriptions.
Use `other-links` for evidence pages such as protocol forum
announcements, documentation pages, or vault launch posts that prove
the organisation acts as curator.

For an alias to an existing feeder, create identity and description
metadata:

```yaml
# {Curator name} curator - feeds provided by {role}/{canonical-slug}.yaml
feeder-id: {curator-slug}
name: {Curator name}
role: curator
canonical-feeder-id: {canonical-slug}
short_description: {One-sentence curator description.}
long_description: |
  {Two to four sentence curator description based on official sources.}
```

Do not duplicate feed sources on alias files.

## Step 4: Add or reuse logos

Curator logos should follow the same repository logo conventions as
vault protocol logos unless a curator-specific logo location has been
introduced later.

When the runtime supports subagents and the user has asked for or
allowed delegated work, use subagents for the logo workflow:

- Ask one subagent to use `extract-vault-protocol-logo` as a template
  for extracting the official curator logo from the curator website or
  brand kit into `eth_defi/data/vaults/original_logos/{curator-slug}/`.
- After original logos exist, ask another subagent to use
  `post-process-logo` to create standardised PNGs in
  `eth_defi/data/vaults/formatted_logos/{curator-slug}/`.

If subagents are not available, run the same two skills sequentially.
Prefer official brand kits, website header logos, GitHub assets, and
then Twitter/X avatars in that order.  Keep the original logo source
documented in the logo folder when the extraction skill creates a
report.

For alias curators, reuse the canonical feeder logo unless the product
has a distinct curator brand.

When the curator is also a vault protocol, add the logo using the
protocol slug so vault metadata and curator metadata share the same
asset path.

## Step 5: Update curator detection

Edit `eth_defi/vault/curator.py`.

For third-party curators:

- Usually the YAML `name` field is enough.
- Add entries to `CURATOR_NAME_PATTERNS` only for verified vault-name
  variants, short names, acronyms, or product names.
- Avoid generic single-word patterns unless the word is distinctive and
  has been checked against the vault dataset for false positives.
- Sort longer or more specific patterns before short aliases when adding
  multiple variants for the same curator.

For a name-pattern update to an existing curator, change only
`CURATOR_NAME_PATTERNS` and explain why no new feed file was needed.

For protocol-managed curators:

- Add blanket protocol-managed protocols to `PROTOCOL_CURATED_SLUGS`.
- Add all protocol curator slugs to `ALL_PROTOCOL_CURATOR_SLUGS` if the
  protocol is address-scoped rather than blanket-managed.
- Add the display name to `PROTOCOL_CURATOR_NAMES`.
- Add address-set detection in `identify_curator()` only when some, but
  not all, vaults under the protocol are protocol-managed.
- Make sure the returned slug matches the feeder slug used elsewhere.

## Step 6: Verify

Run the curator inventory:

```shell
poetry run python .claude/skills/find-new-curators/scripts/print-existing-curators.py
```

Find the relevant targeted tests:

```shell
rg -n "identify_curator|CURATOR_NAME_PATTERNS|PROTOCOL_CURATED|canonical_feeder" tests eth_defi
```

Run targeted tests only.  Use `.local-test.env` for pytest:

```shell
source .local-test.env && poetry run pytest {test file or test name} -v
```

If Python was edited, run:

```shell
poetry run ruff format eth_defi/vault/curator.py
poetry run ruff check eth_defi/vault/curator.py
```

If YAML feed files were edited and no specific feed tests exist, at
least run a script or small import path that loads feeder metadata.
For curator YAML edits, also verify descriptions are present and load
through the shared schema:

```shell
poetry run python - <<'PY'
from pathlib import Path

from eth_defi.feed.sources import load_feeder_metadata

for path in sorted(Path("eth_defi/data/feeds/curators").glob("*.yaml")):
    data = load_feeder_metadata(path)
    assert data.get("short_description"), path
    assert data.get("long_description"), path

print("ok")
PY
```

## Step 7: Report

Summarise:

- Feed files created or aliased
- Short and long descriptions added, with source pages used
- Detection logic changed
- Logo files added or reused
- Verification commands run
- Any inferred sources or unresolved identity questions
