---
name: maintain-curators
description: Audit curator feeder YAML files for stale Twitter, LinkedIn, RSS and website error markers, decide whether each error is real, then clear false positives, repoint renamed handles/links, or tombstone dead entities — leaving a dated audit comment on every entry touched.
---

# Maintain curators

This skill performs a maintenance pass over the curator feeder YAML files in
`eth_defi/data/feeds/curators/`.  It looks at the **error / death markers**
that the feed scanner stamps onto these files, works out whether each marker
still reflects reality, and then repairs the entry: clearing false positives,
re-pointing renamed sources, or tombstoning entities that are genuinely gone.
Every entry that is touched gets a dated `# YYYY-MM-DD maintain-curators: …`
comment recording what was checked and decided.

This is the curator counterpart of `check-stablecoins`, but the curator files
use **inline trailing marker fields** (not a nested `checks:` block), so the
mechanics are different — follow this skill, not the stablecoin one.

## Reference material

Read these before editing — they define the schema and behaviour this skill
operates on. Consult only the sections you need.

**Documentation (READMEs)**

- `eth_defi/feed/README-feed.md` — the authoritative feed submodule doc.
  Especially: *Unified feeder schema* (field-by-field), *Canonical feeder
  aliases* (why alias files carry no sources), *Example feeder files* (a
  well-formed multi-source feeder), *Collection behaviour → RSS / Twitter/X /
  LinkedIn sources* (how each source is fetched and what "dead" means per
  transport), and *Failure handling*.
- `eth_defi/data/feeds/README.md` — the data folder layout (`curators/`,
  `protocols/`, `stablecoins/`, `vaults/`) and the alias contract.
- `.claude/skills/add-curator/SKILL.md` — how new curator entries and their
  source fields are created; reuse its identity/description conventions when
  repointing.
- `.claude/skills/check-stablecoins/SKILL.md` — the sibling liveness audit for
  stablecoins. Note the **different** marker shape (nested `checks:` block) so
  you don't copy it here.

**Schema and writer code (`eth_defi/feed/sources.py`)**

- `_MAPPING_SCHEMA` (strictyaml `Map`) — the **only** keys a curator YAML may
  contain. Adding any other top-level key makes the file fail to load. This is
  also the exhaustive list of marker fields.
- `_load_mapping_file()` — shows exactly how each marker disables its source
  (sets `twitter_username` / `linkedin_company_id` / `rss_url` to `None`) and
  the "all sources disabled → skip feeder" rule.
- `mark_twitter_source_dead()`, `mark_twitter_handle_unknown()`,
  `mark_linkedin_source_disabled()`, `mark_rss_source_dead()`,
  `mark_rss_source_failure()` — the append-only writers that *set* each marker.
  Mirror their field names exactly when you keep a marker; delete their output
  line when you clear one.
- `eth_defi/feed/scanner.py` → `_detect_dead_twitter_accounts()` and the RSS
  death sweep — the automation that **re-stamps** markers, and why clearing
  `twitter-dead-at` without proof of activity is futile.

**Example curator YAML files** (in `eth_defi/data/feeds/curators/`)

- `unified-labs.yaml` — healthy multi-source feeder (`website` + `twitter` +
  `rss`) with no markers; the clean baseline to compare against. Note that
  `linkedin-rss-hub-disabled-at` is present on ~47 files, so "no markers at all"
  is rare — most live feeders still carry the LinkedIn bridge marker.
- `august-digital.yaml` — `twitter-dead-at` on a still-active account (the
  classic anti-bot false positive to clear); also carries the routine
  `linkedin-rss-hub-disabled-at`.
- `smardex.yaml` — `twitter-handle-resolved-unknown-at` plus a hand-written
  `# SmarDex is active …` comment; the handle is in fact live.
- `gauntlet.yaml`, `block-analitica.yaml` — `rss-dead-at` on a Medium feed.
- `candle-effect.yaml` — website (`candleffect.com`) that no longer resolves;
  the website-tombstone case.
- `k3-capital.yaml` — an **alias** file (`canonical-feeder-id: sbold`) with a
  leading rationale comment; carries no sources, so never gets markers.

## Background: how the markers work

The loader `eth_defi/feed/sources.py` understands these optional fields. When a
marker is present, the **matching feed source is disabled** (set to `None`) and
silently skipped by the collector. If *all* of a feeder's sources are disabled,
the whole feeder produces no posts.

| Marker field | Set by | Meaning | Effect |
|---|---|---|---|
| `twitter-dead-at: YYYY-MM-DD` | `mark_twitter_source_dead()` / scanner `_detect_dead_twitter_accounts()` | Twitter account was reachable but had **no new post** within `death_detection_days` | Disables the `twitter:` source |
| `twitter-handle-resolved-unknown-at: YYYY-MM-DD` | `mark_twitter_handle_unknown()` | X API **could not resolve the handle** to a user id (suspended, deleted, or renamed) | Disables the `twitter:` source |
| `linkedin-rss-hub-disabled-at: YYYY-MM-DD` | `mark_linkedin_source_disabled()` / `auto_disable_failed_linkedin_sources()` | The RSS-Hub LinkedIn bridge stopped returning posts for this company | Disables the `linkedin:` source |
| `rss-dead-at: YYYY-MM-DD` | `mark_rss_source_dead()` | RSS feed is valid but has published **nothing for ~a year or more** | Disables the `rss:` source |
| `rss-failure-at` / `rss-failure-status-code` / `rss-failure-exception-message` | `mark_rss_source_failure()` | Most recent RSS fetch **failed** (HTTP error / exception). Diagnostic only — does *not* disable the source on its own | None (diagnostic) |

For how each transport is actually fetched and what "dead" means per source,
see *Collection behaviour* in `eth_defi/feed/README-feed.md`. The disabling
logic is in `_load_mapping_file()` in `eth_defi/feed/sources.py`.

There is **no website marker field** and no typed tombstone field in the schema
(`_MAPPING_SCHEMA` in `eth_defi/feed/sources.py` is strict — an unknown key
makes the file fail to load).
Website deadness and whole-entity tombstones are therefore recorded as **YAML
comments**, never as new top-level keys. Do not invent new marker keys unless
you also extend `_MAPPING_SCHEMA`.

### The critical false-positive trap

X (Twitter) and LinkedIn now return **HTTP 402 / 403 to nearly all automated
fetchers**. The scanner that writes these markers hits the same wall, so:

- `twitter-handle-resolved-unknown-at` and many `twitter-dead-at` markers are
  **anti-bot false positives**, not evidence that the handle is wrong or the
  account is gone.
- A direct `WebFetch` of `https://x.com/<handle>` will usually fail with 402 —
  that failure tells you **nothing**. Do not treat it as proof of death.
- Verify Twitter/LinkedIn liveness with **`WebSearch`** (which surfaces indexed
  profile pages, recent-post snippets, news), not with `WebFetch` of the
  profile URL.

Conversely, **website / RSS / DNS failures are usually real** and can be
confirmed cheaply with `curl` and `nslookup`.

## Required inputs

None. The skill operates on every `*.yaml` in
`eth_defi/data/feeds/curators/`. Optionally the user may pass a **subset**
(specific feeder slugs or a glob) to limit the pass.

## Step 1: Inventory the flagged entries

List every curator file carrying a marker, grouped by marker type:

```shell
cd eth_defi/data/feeds/curators
grep -rlE '^(twitter-dead-at|twitter-handle-resolved-unknown-at|linkedin-rss-hub-disabled-at|rss-dead-at|rss-failure-at):' *.yaml
```

For a per-marker breakdown:

```shell
grep -rhoE '^[a-z-]*(dead-at|unknown-at|disabled-at|failure-at):' *.yaml | sort | uniq -c
```

Read each flagged file in full before acting — existing comments often already
record prior findings (e.g. `# SmarDex is active - DEX with concentrated liquidity`).

Skip **alias files** (those with `canonical-feeder-id:`): they carry no feed
sources, so markers do not belong there — see *Canonical feeder aliases* in
`eth_defi/feed/README-feed.md` and `k3-capital.yaml` for an example. If you
find a marker on an alias, flag it in the report rather than editing.

## Step 2: Decide whether each marker is real

Handle each marker type with the cheapest reliable check.

**Two principles that govern every decision:**

- **Confirmed-dead ≠ unverifiable.** Only *clear* a marker on positive evidence
  of life, and only *tombstone* on positive evidence of death. When everything
  is blocked or ambiguous (e.g. `rogue-traders`: no website, handle won't
  resolve, search results unrelated), the answer is **leave it and record
  "inconclusive"** — never tombstone on mere absence of evidence, and never
  clear on a hunch. This mirrors `check-stablecoins`' "never mark dead just
  because information is missing".
- **The audit comment is the durable artefact, not the cleared marker.** For
  `twitter-dead-at`/`twitter-handle-resolved-unknown-at`, the underlying cause
  is usually that our collector's bridge cannot reach X. Clearing re-enables the
  source, but if the bridge is still blind the scanner may **re-stamp** the
  marker on its next run. That is fine: the dated comment you leave
  ("verified @handle active … false positive") survives the re-stamp and stops
  the next maintainer from wrongly *deleting the handle* or treating the account
  as gone. Always leave the comment even when you expect a re-stamp.

### `twitter-dead-at` (inactivity)

The account exists but looked idle. Decide:

1. `WebSearch` for `"{name}" {handle} site:x.com` and for recent news/posts.
2. If you find posts dated **within the last ~6 months**, the account is
   active → the marker is a **false positive** (the scanner could not read X,
   or the account resumed). Plan to **clear** it (Step 3, action 1).
3. If the account genuinely shows no activity for 6+ months, or the name now
   maps to a different/renamed handle, treat it as real (repoint or tombstone).
4. If you cannot tell (everything blocked), **leave the marker untouched** and
   record that it was inconclusive. Never clear on a guess — the scanner will
   just re-stamp an inactive account on its next run.

### `twitter-handle-resolved-unknown-at` (handle did not resolve)

Stronger signal that the literal handle is broken, but also the marker most
often caused by X anti-bot:

1. `WebSearch` for the project's current official X handle.
2. If the **same handle** clearly still exists and posts → false positive →
   **clear** the marker.
3. If the project has **renamed/moved** to a new handle → **repoint**: update
   the `twitter:` value to the new handle and clear the stale marker.
4. If the account is genuinely suspended/deleted with no replacement → it is
   real; leave the marker (it already disables the source) and, if the *whole
   entity* is gone, add a tombstone comment.

### `rss-dead-at` and `rss-failure-*`

These are checkable directly (RSS is not anti-bot protected). Fetch the feed and
read the newest item date in one go — this recipe handles both RSS
(`<pubDate>`) and Atom (`<updated>`):

```shell
code=$(curl -s -o /tmp/feed.xml -w '%{http_code}' -L --max-time 25 -A 'Mozilla/5.0' "<rss url>")
newest=$(grep -oE '<pubDate>[^<]+</pubDate>|<updated>[^<]+</updated>' /tmp/feed.xml | head -1 | sed 's/<[^>]*>//g')
echo "HTTP $code newest=$newest"
```

1. Fresh items (newest within ~12 months) + HTTP 200 → false positive → **clear**
   the `rss-dead-at` marker (and any stale `rss-failure-*` fields).
2. HTTP 200 but newest item is over a year old → `rss-dead-at` is **real**;
   leave it. **Do this next:** a stale-but-200 feed almost always means the org
   *migrated platforms* rather than stopped writing — most of these are Medium
   feeds (`medium.com/feed/...`) abandoned when the team moved to its own blog,
   Substack, Mirror, or Paragraph. `WebSearch` for `"{name}" blog OR newsletter`
   and, if you find a live successor feed, **repoint** `rss:` to it and clear the
   marker. If no successor is found, leave the marker and say so.
3. Persistent 404 / DNS failure → the feed URL itself is gone; look for the
   current feed and repoint, or leave the marker if none exists.

**Caveats that bit during real runs:**
- Medium/Substack feeds return only the ~10 most recent items, so a low item
  count is normal — judge on the newest *date*, not the count.
- A `<pubDate>` is the *post* date; ignore the channel-level `<lastBuildDate>`
  (it updates even on dead blogs). Taking the first `<item>`/`<entry>` date as
  above avoids this.
- `rss-failure-*` (transient fetch error) is **not** `rss-dead-at` (stale
  content). A feed can be `failure` today (rate-limited 429/503) yet perfectly
  alive — re-fetch before acting; only clear `rss-failure-*` once it returns 200.

### `linkedin-rss-hub-disabled-at`

This reflects the **RSS-Hub bridge** failing for that company, not necessarily
the company's LinkedIn page. Bridge availability is outside our control and
these are usually left as-is. Only clear if the user has confirmed the bridge
works again for that company. Default action: **leave untouched**, note it.

### Website links

The `website:` field is **metadata only** — it is not a tracked feed source, so
a dead website does not disable collection or get auto-marked. It is the lowest
urgency check, but cheap, so do it while you are in the file:

```shell
nslookup <domain> 8.8.8.8          # NXDOMAIN from a public resolver == dead
curl -sL --max-time 12 -o /dev/null -w '%{http_code}' <website>
```

- `200` (or a `403` Cloudflare challenge page that still has content) → fine.
- `NXDOMAIN` **from a public resolver (8.8.8.8 and 1.1.1.1, not just your local
  one — a VPN/corporate resolver can lie both ways)**, or persistent connection
  failure → the domain is dead. Search for the project's current site;
  **repoint** the `website:` value if found.
- A dead website does **not** by itself mean the entity is dead — it may still
  run on-chain (e.g. `candle-effect`: `candleffect.com` is NXDOMAIN but its
  Lighter public pools persist). Tombstone only the *website* with a comment;
  reserve a whole-entity tombstone for when the sources are dead too.
- Remember the domain may be referenced more than once (also under
  `other-links:` and inside `long_description`) — note all occurrences, even if
  you only repoint the canonical `website:` field.

## Step 3: Apply the fix

Use the **Edit** tool for surgical changes. Never reformat, reorder, or
re-indent existing keys. Preserve all comments.

**Action 1 — clear a false-positive marker.** Delete the entire marker line
(and any companion `rss-failure-*` lines when the RSS feed is confirmed
healthy). This re-enables the source on the next load.

**Action 2 — repoint a renamed handle / moved link.** Edit the source value in
place — `twitter:`, `linkedin:`, `rss:`, or `website:` — to the new value, and
delete the now-stale marker for that source.

**Action 3 — tombstone a dead entity.** When the *organisation itself* is gone
(site dead, socials dead, no replacement found), do **not** delete its sources.
Leave the existing markers in place (they already disable the dead sources) and
add a tombstone comment at the top of the file:

```yaml
# TOMBSTONE 2026-06-10 maintain-curators: entity appears defunct — website
# <url> is NXDOMAIN and X @<handle> is suspended with no successor found.
# Sources left disabled; kept for historical curator-name matching.
```

For a dead website where the rest of the entity still lives, leave `website:`
as-is (or repoint if a successor exists) and add a comment line next to it
rather than a new field.

## Step 4: Always leave a dated audit comment

Every entry you inspect and change gets one comment line recording the date and
what was done, placed directly **above the field it concerns** (or at the top of
the file for whole-entity decisions). Use this exact prefix so the trail is
greppable:

```yaml
# 2026-06-10 maintain-curators: verified @august_digital active via web search
# (recent posts); twitter-dead-at was an X anti-bot false positive — cleared.
twitter: august_digital
```

```yaml
# 2026-06-10 maintain-curators: blog migrated to Substack; repointed RSS and
# cleared stale rss-dead-at.
rss: https://example.substack.com/feed
```

Keep comments factual: what you checked, the evidence, the decision. UK/British
English. No hype. If a check was inconclusive and you left the marker, say so —
an explicit "left untouched, inconclusive" comment stops the next run repeating
the same dead-end.

## Step 5: Verify the files still load

After edits, confirm every curator file still parses through the strict schema
and that you have not accidentally disabled a feeder you meant to revive:

```shell
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run python - <<'PY'
from pathlib import Path
from eth_defi.feed.sources import load_feeder_metadata, load_post_sources

for path in sorted(Path("eth_defi/data/feeds/curators").glob("*.yaml")):
    load_feeder_metadata(path)  # raises if the file no longer validates

sources, skipped, aliases = load_post_sources()
curator_sources = [s for s in sources if s.role == "curator"]
print(f"ok — {len(curator_sources)} live curator sources, {skipped} skipped")
PY
```

A file that fails here usually means a stray key was added (schema is strict) or
indentation was disturbed — fix before reporting.

## Step 6: Report

Summarise as a table plus notes:

| Feeder | Marker found | Verdict | Action |
|---|---|---|---|
| august-digital | twitter-dead-at | false positive (active) | cleared |
| candle-effect | — (website) | NXDOMAIN, no successor | website tombstone comment |
| … | … | … | … |

Then list, briefly:

- Markers **cleared** (false positives) and the evidence used.
- Sources **repointed** (old → new handle/URL).
- Entities **tombstoned**, with why.
- Markers **left untouched** and why (inconclusive, bridge-level, or genuinely
  dead-but-correct).
- The result of the Step 5 load check.

## Constraints and gotchas

- **Schema is strict.** Only the keys in `_MAPPING_SCHEMA`
  (`eth_defi/feed/sources.py`) are allowed. Record anything else (website
  deadness, tombstones, rationale) as YAML comments.
- **`WebFetch` of x.com/linkedin.com is unreliable** (402/403). Use `WebSearch`
  for social liveness; reserve `WebFetch`/`curl` for websites and RSS.
- **Do not clear `twitter-dead-at` on a guess** — the scanner re-stamps idle
  accounts. Only clear with positive evidence of recent activity.
- **Never edit alias files** (`canonical-feeder-id:`) — they hold no sources.
- **Append-only spirit:** the Python `mark_*` helpers append marker lines; when
  *clearing* you edit by hand, but keep every other line byte-identical.
- Convert any relative dates ("2 days ago") to absolute `YYYY-MM-DD` using
  today's date.
- Keep the working set small if the user passed a subset; otherwise process all
  flagged files, but you do not need to comment on unflagged, healthy entries.
