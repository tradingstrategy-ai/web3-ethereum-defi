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
- `august-digital.yaml` — `twitter-dead-at` where the handle is correct and the
  profile is live, but no in-window post could be confirmed → marker **retained,
  not cleared**. The cautionary example: handle-valid ≠ recently-active.
- `b-cube-ai.yaml` — `twitter-dead-at` confirmed **genuine**: latest indexed
  post was an Oct-2025 AMA, older than the 180-day cutoff → marker retained.
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
| `twitter-dead-at: YYYY-MM-DD` | `mark_twitter_source_dead()` / scanner `_detect_dead_twitter_accounts()` | **Our collector** recorded no post (`last_post_published_at`) within `death_detection_days` (**default 180**, see `scanner.py`). A *recency* signal, computed from our DB — **not** a live scrape | Disables the `twitter:` source |
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

### The two Twitter markers ask different questions — do not conflate them

This is the single most important distinction in this skill. The two Twitter
markers fail for different reasons and need different evidence to clear:

- **`twitter-handle-resolved-unknown-at` = handle validity.** "Does this handle
  resolve to a real account?" Often an **X-API / anti-bot false positive**. It
  is **refuted by a live, correctly-named profile** — if `WebSearch` shows the
  handle exists with the right brand, clear it. Recency is irrelevant here.

- **`twitter-dead-at` = recency.** "Has our collector seen a post in the last
  `death_detection_days` (180)?" It is computed from our own database, **not** a
  live scrape, so a working account our bridge simply failed to read can be
  flagged — but so can a genuinely dormant one. **"The profile exists and looks
  active" does NOT refute it.** The *only* thing that refutes `twitter-dead-at`
  is a **concrete post dated within ~180 days of today** (i.e. after
  `today − 180d`). If you cannot find such a dated post, you **cannot** clear
  the marker — leave it and record that recency was unverifiable. Clearing on a
  vague "live profile" is the classic mistake: the scanner will just re-stamp it
  on the next run, and worse, the audit trail will claim a false positive that
  was never demonstrated.

When you *do* clear `twitter-dead-at`, the audit comment **must cite the
concrete latest-post date** you found (e.g. "latest post 2026-05-30, within
180d — cleared"). No date, no clear.

### The anti-bot wall (applies to both)

X (Twitter) and LinkedIn return **HTTP 402 / 403 to nearly all automated
fetchers**, including a direct `WebFetch` of `https://x.com/<handle>` and, in
2026, most Nitter mirrors. That failure tells you **nothing** — never treat it
as proof of death. Use **`WebSearch`** for liveness/handle checks; it surfaces
indexed profile pages, dated post snippets, and news. But note its limit:
search often confirms a handle *exists* without giving a *dated* recent post —
which is enough to clear `twitter-handle-resolved-unknown-at` but **not enough**
to clear `twitter-dead-at`.

Conversely, **website / RSS / DNS failures are usually real** and can be
confirmed cheaply and conclusively with `curl` and `nslookup`.

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
- **The audit comment is the durable artefact, not the cleared marker.** A
  cleared `twitter-dead-at` can be **re-stamped** on the next scan if the
  collector still sees no in-window post, so the lasting value is the comment.
  Use it to separate the two facts you actually established: *handle validity*
  ("@handle is correct, live profile") versus *recency* ("latest confirmed post
  2026-05-30" or "recency unverifiable"). Even when you leave a marker in place,
  recording "handle verified correct" stops the next maintainer from wrongly
  *deleting the handle*. Always leave the comment; cite a concrete date whenever
  you have one.

### `twitter-dead-at` (recency — needs a concrete in-window post date)

This marker means our collector saw no post within **180 days**
(`death_detection_days`). To clear it you must find a **concrete post dated
after `today − 180d`** — nothing weaker counts. Decide:

1. `WebSearch` for `"{name}" {handle} site:x.com` and for recent dated posts or
   news referencing a specific recent post.
2. **Found a post dated within 180 days?** The marker is a false positive →
   **clear** it (Step 3, action 1) and **cite that date** in the audit comment.
3. **Found only old posts** (newest predates `today − 180d`, e.g. an Oct-2025
   AMA checked in Jun 2026)? The marker is **accurate** — the account is dormant
   by the scanner's definition. **Leave it**, and note the latest-post date.
4. **Could confirm the handle exists but found no dated recent post** (the
   common case — X blocks bots, Nitter is dead)? Recency is **unverifiable** →
   **leave the marker in place**. Do *not* clear: "the profile looks active" is
   not evidence of an in-window post, and the scanner would re-stamp it anyway.
   Record that the handle is correct but recency could not be confirmed.
5. If the name now maps to a different/renamed handle, **repoint** (and the new
   handle starts fresh, so clear the marker).

Compute the cutoff explicitly from today's date; do not eyeball "about six
months". On 2026-06-10 the cutoff is 2025-12-12.

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

Cleared a `twitter-dead-at` — note the **concrete in-window post date** that
justified it (without one, you may not clear):

```yaml
# 2026-06-10 maintain-curators: latest post 2026-05-30 (within 180d cutoff
# 2025-12-12); twitter-dead-at was stale — cleared.
twitter: example_handle
```

Cleared a `twitter-handle-resolved-unknown-at` — handle validity is enough here,
no recent-date needed:

```yaml
# 2026-06-10 maintain-curators: @SmarDex resolves to a live, correctly-named
# profile; handle-resolution marker was a false positive — cleared.
twitter: SmarDex
```

**Retained** a `twitter-dead-at` because recency could not be confirmed — record
that the handle is correct but no in-window post was found, so the next run does
not re-clear it wrongly:

```yaml
# 2026-06-10 maintain-curators: handle @august_digital correct (live profile),
# but no post within the 180-day cutoff could be confirmed (X blocks bots).
# Recency unverifiable — twitter-dead-at left in place.
twitter: august_digital
linkedin: augustdigital
linkedin-rss-hub-disabled-at: 2026-04-04
twitter-dead-at: 2026-04-06
```

Repointed a migrated feed:

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
| smardex | twitter-handle-resolved-unknown-at | handle resolves (live profile) | cleared |
| b-cube-ai | twitter-dead-at | latest post Oct 2025, past 180d cutoff | retained (genuinely stale) |
| august-digital | twitter-dead-at | handle correct, recency unverifiable | retained |
| candle-effect | — (website) | NXDOMAIN, no successor | website-dead comment |
| … | … | … | … |

Then list, briefly:

- Markers **cleared**, with the evidence — for `twitter-dead-at` the concrete
  in-window post date, for `twitter-handle-resolved-unknown-at` the live handle.
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
