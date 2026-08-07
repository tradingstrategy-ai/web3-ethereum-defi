---
name: document-curator-incident
description: Document curator incidents from one or more post links in curator YAML metadata, including affected vault addresses and protocols. Use when the user supplies social posts, announcements, post-mortems, or reports about a vault curator and wants to add a new incident or merge evidence into an existing incident.
---

# Document curator incident

Turn post links into a sourced curator incident under
`eth_defi/data/feeds/curators/{curator-slug}.yaml`.

## Input

Require one or more post links. Ask for replacement links only when none of the
supplied pages or indexed copies provide enough evidence to identify the curator
and describe the incident without guessing.

## Step 1: Read and verify the posts

Open every supplied link and record:

- The author and publication date
- The curator named or clearly implicated
- The underlying event, impact, and current resolution status
- The specifically affected vault addresses and protocol slugs
- Whether statements are first-party facts, a post-mortem, or third-party claims

When a social site blocks direct access, use indexed search results or another
public copy of the same post. Do not treat an inaccessible page as evidence and
do not turn an unverified allegation into a statement of fact. Attribute disputed
or third-party claims in the incident description.

Use the publication date of the primary, most authoritative post as the incident
`date` in `YYYY-MM-DD` format. If an authoritative source states a distinct event
date, prefer the event date. When merging, retain the existing date unless the new
evidence establishes a more accurate one.

## Step 2: Identify the curator YAML

Search existing curator metadata before editing:

```shell
rg -n -i "{curator name}|{curator handle}" eth_defi/data/feeds/curators eth_defi/vault/curator.py
```

Match the organisation that managed or curated the affected vault. Do not assign
the incident to a protocol, asset issuer, quoted commentator, or reposting account
merely because it appears in a post. If more than one existing curator is plausible,
stop and ask the user which curator is intended.

If no curator YAML exists, use the `add-curator` skill first. Do not create an
incomplete curator record as part of this workflow.

## Step 3: Decide whether to add or merge

Read the curator's existing `incidents` list in full.

- Merge when a record describes the same underlying event, even when the title,
  post date, or source URL differs.
- Treat an overlapping link as definitive evidence that the record already exists.
- Add a new record when the posts concern a separate event.
- If the supplied links cover multiple unrelated events, create or merge one record
  per event rather than combining them.

When merging, append only new links, preserve useful existing evidence, and revise
the title, description, date, label, or severity only when the new sources justify
the change. Merge newly confirmed vault addresses and protocols into their existing
lists without removing previously sourced context.

## Step 4: Classify and write the incident

Use this YAML shape:

```yaml
incidents:
  - date: YYYY-MM-DD
    links:
      - https://example.com/primary-source
      - https://example.com/additional-source
    vault_addresses:
      - 0x1234567890abcdef1234567890abcdef12345678
    protocols:
      - lagoon-finance
    title: Concise incident title
    description: |
      Write three to five factual Markdown sentences with [inline source links](https://example.com/primary-source).
      Attribute claims and use [additional sources](https://example.com/additional-source) where they support the body text.
    incident_kind: misleading
    severity: other
```

Write a neutral title and a three-to-five-sentence Markdown description. Every
description paragraph must contain at least one inline Markdown link. Prefer links
embedded on the claim they support; do not use bare URLs or a separate sources list
inside the description. Avoid promotional language, investment advice, and facts
not supported by the supplied posts.

Choose exactly one `incident_kind`:

- `collapse`: the curator or its managed operation failed broadly, became insolvent,
  or ceased functioning
- `significant_loss`: investors suffered a material loss without a full collapse
- `minor_loss`: losses were limited in scope or impact
- `misleading`: accounting, valuation, performance, or public disclosures were
  materially misleading
- `questionable_behaviour`: substantiated governance, conflict, or conduct concerns
  that fit none of the labels above

Use the incident's main nature as the label. Do not invent numerical loss thresholds.
Also set the schema-required `severity` to `collapse`, `significant_loss`,
`minor_loss`, or `other` based on demonstrated impact. A `misleading` or
`questionable_behaviour` incident normally has severity `other` unless sources
demonstrate a loss or collapse.

Do not change `risk.status` merely because an incident is added. Risk review is a
separate decision unless the user explicitly requests it.

## Step 5: Update context lists and validate

Store all supplied links that support the incident in `links`, preserving the
primary source first and removing exact duplicates. Links must use HTTP(S).

Fill `vault_addresses` with only the vaults demonstrated to be affected by the
sources. Resolve named vaults through repository metadata or the public vault page,
and lowercase EVM addresses. Include downstream vaults when a source explicitly
documents contagion through a position in the directly affected vault, but do not
add every vault managed by the curator. Fill
`protocols` with the canonical protocol slugs used by repository vault metadata.
Both lists must be non-empty. If an affected address or protocol cannot be verified,
stop and ask the user for the missing context rather than guessing.

Validate the edited curator through the shared strict YAML schema:

```shell
CURATOR_YAML=eth_defi/data/feeds/curators/{curator-slug}.yaml poetry run python - <<'PY'
import os
from pathlib import Path

from eth_defi.feed.sources import load_feeder_metadata

path = Path(os.environ["CURATOR_YAML"])
metadata = load_feeder_metadata(path)
assert metadata.get("incidents"), path
print(path)
PY
```

Then run:

```shell
git diff --check
```

Report the curator, whether the incident was added or merged, the selected date,
`incident_kind`, severity, and the number of links, vault addresses, and protocols
now attached to the record.
