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
- Optional website, Twitter/X, LinkedIn, blog/RSS, supporting links, and logo source

If the candidate was produced by `find-new-curators`, open its result
file first and keep the evidence trail in mind.

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

## Step 2: Verify identity and sources

Use official sources when possible:

- Official website homepage
- Twitter/X account linked from the website, written without `@`
- LinkedIn company slug from the official company page URL
- RSS or Atom feed for an official blog, only if it works
- Documentation, forum post, or vault UI proving the organisation acts as curator

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
Use `other-links` for evidence pages such as protocol forum
announcements, documentation pages, or vault launch posts that prove
the organisation acts as curator.

For an alias to an existing feeder, create only identity metadata:

```yaml
# {Curator name} curator - feeds provided by {role}/{canonical-slug}.yaml
feeder-id: {curator-slug}
name: {Curator name}
role: curator
canonical-feeder-id: {canonical-slug}
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

## Step 7: Report

Summarise:

- Feed files created or aliased
- Detection logic changed
- Logo files added or reused
- Verification commands run
- Any inferred sources or unresolved identity questions
