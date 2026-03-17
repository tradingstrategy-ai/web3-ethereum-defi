---
name: check-stablecoins
description: Check all stablecoin YAML files for Twitter activity and domain availability, adding a checks section to each file
---

# Check stablecoins

This skill audits all stablecoin YAML files in `eth_defi/data/stablecoins/` for liveness — checking whether their Twitter/X accounts are active and homepage domains are reachable. It adds a `checks` section to each file and attempts to fill in missing information. If wind-down news is found, it updates `long_description`.

## Required inputs

None. The skill operates on the full set of stablecoin YAML files automatically. Optionally the user may specify:

1. **Recheck mode** - Whether to overwrite existing `checks` sections (default: skip files that already have checks)
2. **Subset** - A glob or list of specific YAML files to check (default: all `*.yaml` in the stablecoins directory)

## Output format

Each YAML file receives a `checks` mapping with these keys:

```yaml
checks:
  twitter_last_post_at: '2026-03-10'
  domain_up_at: '2026-03-17'
  marked_dead_at: ''
  information_found_missing_at: ''
```

### Field definitions

- `twitter_last_post_at` - Date (YYYY-MM-DD) of the most recent Twitter/X post. Empty string if the account is deleted, suspended, missing, or if no twitter link exists and none could be found.
- `domain_up_at` - Date (YYYY-MM-DD) when the homepage was confirmed reachable. Empty string if the homepage is down, unreachable, or if no homepage link exists and none could be found.
- `marked_dead_at` - Date (YYYY-MM-DD, set to today) **only with strong evidence**: a known-good domain is down, OR a known-good Twitter account's last post is more than 6 months ago. **Never** set just because information is missing. Empty string if the stablecoin appears alive or if evidence is insufficient.
- `information_found_missing_at` - Date (YYYY-MM-DD, set to today) if we could not determine liveness: no twitter AND no homepage links exist (even after searching), or all checks returned inconclusive results. This flags files needing manual review. Empty string if at least one check succeeded.

### Placement rules

- **Single-entry files** (files with top-level `name` and `links`): Add `checks` at the top level, as the last key.
- **Multi-entry files** (files with `entries:` list): Add `checks` inside **each entry**, after that entry's other keys. Each entry has its own twitter/homepage and must be checked independently.

## Step 1: Inventory and batch assignment

1. Use Glob to list all `*.yaml` files in `eth_defi/data/stablecoins/`.
2. Read each YAML file to determine:
   - Whether it already has a `checks` section (skip unless recheck mode)
   - Whether it is single-entry or multi-entry format
   - Extract all `links.twitter` and `links.homepage` URLs
3. Divide the files needing checks into **8 batches** of roughly equal size (~24 files each).
4. Record the batch assignments so each subagent knows exactly which files to process.

## Step 2: Spawn subagents for parallel checking

Spawn **8 subagents** using the Agent tool in a single message (all 8 in parallel). Each subagent receives:
- Its batch of YAML file paths (as a list)
- The current date string (YYYY-MM-DD format, for date fields)
- The complete subagent instructions below

### Subagent instructions

For each YAML file in your batch, perform these steps:

#### 2a: Read the file

Read the YAML file with the Read tool. Identify whether it is single-entry (top-level `links`) or multi-entry (`entries:` list). For multi-entry files, process each entry separately. Extract the `name`, `links.twitter`, and `links.homepage` values.

#### 2b: Fill missing information

If `links.twitter` is empty (`''`):
1. Use WebSearch with query `"{stablecoin name}" stablecoin twitter site:x.com` to try to find the project's Twitter/X account.
2. If a clear match is found, update the `twitter` field in the YAML file's `links` section using the Edit tool.
3. If no match is found, leave it empty and continue.

If `links.homepage` is empty (`''`):
1. Use WebSearch with query `"{stablecoin name}" stablecoin official website` to try to find the project's homepage.
2. If a clear match is found, update the `homepage` field in the YAML file's `links` section using the Edit tool.
3. If no match is found, leave it empty and continue.

#### 2c: Check Twitter/X

For each `links.twitter` URL (including any newly found ones):

1. If the URL is empty after step 2b, set `twitter_last_post_at: ''`.
2. If the URL is present, use WebFetch on the Twitter/X profile URL to check the account. Look for:
   - The date of the most recent tweet/post
   - Signs the account is suspended, deleted, or does not exist
3. If WebFetch fails (403, timeout, blocked), try these fallbacks in order:
   - WebSearch with query `site:x.com "{handle}" latest` to find recent activity
   - WebFetch on `https://nitter.net/{handle}` as an alternative Twitter frontend
4. If the account is suspended or does not exist, set `twitter_last_post_at: ''`.
5. If a date is found, convert to YYYY-MM-DD format. If the date is relative (e.g., "2 days ago", "Mar 10"), compute the absolute date relative to today.

#### 2d: Check homepage domain

For each `links.homepage` URL (including any newly found ones):

1. If the URL is empty after step 2b, set `domain_up_at: ''`.
2. If the URL is present, use WebFetch on the homepage URL to check if it is reachable.
3. If WebFetch returns successfully and the site has real content, set `domain_up_at` to today's date.
4. If WebFetch fails with a connection error, DNS error, or timeout, set `domain_up_at: ''`.
5. If the site returns 403 but has content (e.g., Cloudflare challenge page with "checking your browser"), the domain IS up — set `domain_up_at` to today's date.
6. If the site redirects to unrelated domain parking, set `domain_up_at: ''`.

#### 2e: Search for wind-down news

Use WebSearch with query `"{stablecoin name}" stablecoin shutdown OR "wind down" OR abandoned OR depeg` to check if the project has been discontinued.

If clear wind-down news is found (project shutting down, permanently depegged, team abandoned):
1. Read the current `long_description` value.
2. If `long_description` is empty (`''`), create a new value. If it already has content, append to it.
3. Add a `## Status` section with a brief summary of the situation and source links. Example:

```yaml
long_description: |
  ...existing content if any...

  ## Status

  The project announced wind-down in January 2026. The team ceased operations and
  the token depegged permanently. [Source](https://example.com/article).
```

4. Use the Edit tool to update `long_description` in the YAML file.

**Important:** Only add status information that is clearly documented. Do not fabricate or speculate. If unsure, skip this step.

#### 2f: Compute marked_dead_at and information_found_missing_at

**marked_dead_at** — set to today's date **only** if:
- We had a known-good homepage URL (non-empty `links.homepage` before step 2b, or clearly found via search) AND `domain_up_at` is empty (domain is confirmed down), OR
- We had a known-good Twitter URL AND `twitter_last_post_at` is non-empty AND the date is more than 6 months before today

**Never** set `marked_dead_at` just because information is missing. A deleted/suspended Twitter alone (with homepage up) does NOT trigger dead. Missing links that couldn't be found do NOT trigger dead.

**information_found_missing_at** — set to today's date if:
- After step 2b, both `links.twitter` and `links.homepage` are still empty (no way to check liveness), OR
- All check attempts returned inconclusive results (everything was blocked/timed out)

If at least one check succeeded (domain confirmed up, or twitter date extracted), leave `information_found_missing_at` as empty string.

#### 2g: Write checks to the YAML file

Use the Edit tool to add the `checks` section to the YAML file.

**For single-entry files**, append as the last key at top level:

```yaml
checks:
  twitter_last_post_at: '2026-03-10'
  domain_up_at: '2026-03-17'
  marked_dead_at: ''
  information_found_missing_at: ''
```

**For multi-entry files**, add `checks:` as the last key within each entry block, indented at the entry level (2 spaces):

```yaml
entries:
- name: Example Token
  short_description: ...
  links:
    homepage: https://example.com
    twitter: https://x.com/example
  contract_addresses:
    - chain: ethereum
      address: '0x...'
  checks:
    twitter_last_post_at: '2026-03-10'
    domain_up_at: '2026-03-17'
    marked_dead_at: ''
    information_found_missing_at: ''
- name: Another Token
  ...
  checks:
    twitter_last_post_at: ''
    domain_up_at: ''
    marked_dead_at: ''
    information_found_missing_at: '2026-03-17'
```

**YAML formatting rules:**
- Always quote date values with single quotes: `'2026-03-17'`
- Always quote empty strings: `''`
- Maintain 2-space indentation matching the rest of the file
- Do not reformat or reorder any existing keys
- Place `checks` as the last key at its level

## Step 3: Verify results

After all 8 subagents complete:

1. Use Grep to search for `checks:` across all YAML files — confirm all files were updated.
2. Use Grep to count:
   - `marked_dead_at:` entries that are non-empty (confirmed dead stablecoins)
   - `information_found_missing_at:` entries that are non-empty (needing manual review)
   - `domain_up_at:` entries that are non-empty (confirmed live domains)
   - `twitter_last_post_at:` entries that are non-empty (confirmed twitter activity)
3. Spot-check 3-5 files from different batches by reading them to verify correct formatting.
4. Report a summary table to the user:

| Metric | Count |
|--------|-------|
| Total files checked | 188 |
| Active twitter found | N |
| Domain confirmed up | N |
| Marked dead (strong evidence) | N |
| Information missing (needs review) | N |
| Links filled in (twitter/homepage found) | N |
| Long descriptions updated (wind-down news) | N |

## Troubleshooting

### Twitter/X blocks all requests

If WebFetch consistently returns 403 or empty content for X.com URLs:
- Fall back to WebSearch with query `"{twitter_handle}" latest tweet` to find cached or third-party reports of activity
- Try Nitter mirrors: `https://nitter.net/{handle}` or `https://nitter.privacydev.net/{handle}`
- Check CoinGecko page for the stablecoin (often shows social links with activity indicators)

### Domain returns 403 with Cloudflare

This is common. If the WebFetch response contains "Cloudflare" or "checking your browser", the domain IS up. Set `domain_up_at` to today.

### Rate limiting

If WebFetch starts failing due to rate limits, the subagent should note which files were not fully checked. These files should get `information_found_missing_at` set so they can be retried later.

### Existing checks section

If a file already has a `checks:` section and recheck mode is off, skip it entirely. If recheck mode is on, replace the existing `checks` block with freshly computed values using the Edit tool.
