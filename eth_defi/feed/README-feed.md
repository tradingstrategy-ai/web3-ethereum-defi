# Feed post collection submodule

## Overview

This submodule collects vault-related posts from public RSS feeds, Twitter/X
usernames, and LinkedIn company feeds, normalises them into a shared post
format, and stores the results in DuckDB for later matching and analysis. Each
feeder file can also carry the feeder company website as metadata.

The current version is collection-only. It does not yet decide which collected
posts belong to which vaults, and it does not generate AI summaries.

## Design goals

The feed collector is built around a few explicit design choices:

- **Unified feeder schema**: protocols, curators, stablecoins, and vaults all
  use the same YAML format
- **Structured feeder folders**: source YAML files are grouped under
  `eth_defi/data/feeds/protocols/`, `eth_defi/data/feeds/curators/`, and
  `eth_defi/data/feeds/vaults/`
- **Slug-based identity**: each feeder is identified by one canonical
  `feeder-id`, which is the same as the curator slug, protocol slug, or vault
  slug
- **Website metadata**: feeder files may store one canonical company website
  alongside the feed sources
- **Idempotent collection**: repeated runs should only insert genuinely new
  posts
- **Public-web compatibility**: Twitter/X and LinkedIn collection use
  operator-supplied live feed bridges instead of API credentials
- **Proxy-aware fetching**: feed reads can use the repository Webshare proxy
  rotator when bridge operators start throttling
- **Conservative failure handling**: a single failing feed should not abort the
  whole run
- **Naive UTC timestamps**: all timestamps follow the repository-wide naive UTC
  convention

## Architecture

The submodule is split into three main Python modules:

- [`sources.py`](./sources.py) loads and validates feeder YAML files
- [`collector.py`](./collector.py) fetches feeds, parses entries, and
  normalises posts
- [`database.py`](./database.py) stores tracked sources and posts in DuckDB

The flow is:

```text
YAML feeder files
  eth_defi/data/feeds/**/*.yaml
        |
        v
sources.py
  load_post_sources()
        |
        v
collector.py
  collect_posts()
  - fetch RSS directly
  - build Twitter and LinkedIn bridge URLs
  - collect feeds with worker threads and tqdm progress reporting
  - parse RSS / Atom with feedparser
  - normalise text, timestamps, ids
        |
        v
database.py
  VaultPostDatabase
  - tracked_sources table
  - posts table
  - source sync state
  - deduplicating inserts
  - retention pruning
```

## Unified feeder schema

Each feeder file uses the same simplified schema:

```yaml
feeder-id: { curator slug, protocol slug, or vault slug }
name: { human-readable name }
role: { curator | protocol | stablecoin | vault }
website: { optional company website URL }
twitter: { optional Twitter/X username }
linkedin: { optional LinkedIn company id }
rss: { optional RSS or Atom feed URL }
```

Notes:

- `feeder-id` is the canonical slug and acts as the feeder identity
- `role` must be one of `curator`, `protocol`, `stablecoin`, or `vault`
- `website` is optional company metadata and is stored alongside tracked sources
- `twitter` is a username such as `gauntlet_xyz`, not a full profile URL
- `linkedin` is collected through operator-supplied LinkedIn bridge templates
- `linkedin` is a company id such as `gauntlet-xyz`, not a full LinkedIn URL
- at least one of `twitter`, `linkedin`, or `rss` must be present for collection
- one YAML file currently produces one to three tracked sources internally:
  - one Twitter source
  - one LinkedIn source
  - one RSS source

## Example feeder files

Morpho protocol feeder:

```yaml
feeder-id: morpho
name: Morpho
role: protocol
website: https://morpho.org/
twitter: morpholabs
linkedin: morpho-association
# Morpho does not currently expose an official public RSS feed
# rss:
```

Gauntlet curator feeder:

```yaml
feeder-id: gauntlet
name: Gauntlet
role: curator
website: https://www.gauntlet.xyz/
twitter: gauntlet_xyz
linkedin: gauntlet-xyz
rss: https://medium.com/feed/gauntlet-networks
```

See the folder summary in [`eth_defi/data/feeds/README.md`](../data/feeds/README.md).

## Stored data model

DuckDB stores two logical entities:

- `tracked_sources` stores feeder metadata plus sync state such as
  `last_checked_at`, `last_success_at`, `last_error`, and
  `last_post_published_at`
- `posts` stores normalised post content keyed by `(source_id, external_post_id)`

The tracked source row tells us which feeder and source produced the data and
also stores feeder metadata such as `website`. The post row stores the
collected content itself.

## Collection behaviour

### RSS sources

RSS sources are fetched directly from their configured feed URL.

### Twitter/X sources

Twitter/X usernames are normalised to a handle and then expanded to one or
more live feed URLs using `TWITTER_RSS_BASE_URLS` and
`TWITTER_FEED_URL_TEMPLATES`.

The collector supports two ways to build live Twitter/X feed URLs:

- `TWITTER_RSS_BASE_URLS` is for bridges that expose the conventional
  `/{handle}/rss` path
- `TWITTER_FEED_URL_TEMPLATES` is for explicit bridge URL templates, using the
  placeholder `{handle}`

In practice:

1. the feeder YAML stores a username such as `gauntlet_xyz`
2. `sources.py` normalises this to the canonical URL
   `https://x.com/gauntlet_xyz`
3. the live collection key becomes the handle `gauntlet_xyz`
4. `collector.py` expands that handle into one or more candidate live feed URLs
5. the collector tries each candidate URL in order until one returns a valid
   RSS or Atom document

There is intentionally **no hardcoded bridge default**. If neither
`TWITTER_RSS_BASE_URLS` nor `TWITTER_FEED_URL_TEMPLATES` is configured,
Twitter/X sources are skipped and marked as failed with a clear configuration
error.

For operators, `TWITTER_FEED_URL_TEMPLATES` is the more flexible option and is
usually the best fit for xcancel-style bridges.

Real live bridge examples that were verified during implementation on
2026-04-03:

- `https://xcancel.com/gauntlet_xyz/rss`
- `https://rss.xcancel.com/gauntlet_xyz/rss`

Example configuration:

```shell
export TWITTER_FEED_URL_TEMPLATES="https://xcancel.com/{handle}/rss,https://rss.xcancel.com/{handle}/rss"
```

### LinkedIn sources

LinkedIn company ids are normalised and then expanded to one or more live feed
URLs using `LINKEDIN_FEED_URL_TEMPLATES`.

The LinkedIn path is slightly stricter than Twitter/X:

1. the feeder YAML must contain a LinkedIn company id such as `gauntlet-xyz`
2. `sources.py` validates the company id and builds the canonical URL
   `https://www.linkedin.com/company/gauntlet-xyz`
3. `collector.py` expands that id into one or more candidate live feed URLs
   using the placeholder `{company_id}`
4. each candidate is tried in order until one returns a valid RSS or Atom
   document

The collector currently supports LinkedIn company feeds only. It does not yet
support LinkedIn personal profiles or organisations that require a different
route structure.

Real live bridge examples that were verified during implementation on
2026-04-03:

- `https://rsshub.pseudoyu.com/linkedin/company/gauntlet-xyz/posts`
- `https://rss.owo.nz/linkedin/company/gauntlet-xyz/posts`
- `https://rsshub.umzzz.com/linkedin/company/gauntlet-xyz/posts`

Example configuration:

```shell
export LINKEDIN_FEED_URL_TEMPLATES="https://rsshub.pseudoyu.com/linkedin/company/{company_id}/posts,https://rss.owo.nz/linkedin/company/{company_id}/posts"
```

### Feed parsing

The collector uses `feedparser` to support both RSS and Atom feeds. For each
entry it stores:

- source
- title
- timestamp
- short description
- full text
- `ai_summary`, currently always `NULL`

If a feed entry does not expose a stable GUID or entry id, the collector
synthesises a deterministic fallback id from the entry URL and timestamp, with
further fallbacks if those fields are missing.

### Failure handling

The collector is intentionally conservative:

- feed collection is parallelised with worker threads instead of processes
- `MAX_WORKERS` controls how many sources are fetched concurrently
- `REQUEST_DELAY_SECONDS` adds a small per-worker delay before each source read
- there is no heavyweight scheduler-level retry loop or backoff policy
- failures are recorded per source and the run continues

There is still some light resilience built into the HTTP path:

- when Webshare proxies are enabled, the collector can rotate through several
  proxies before giving up
- if proxy-backed reads still fail, the collector falls back to a direct
  request instead of failing immediately
- for Twitter/X and LinkedIn bridges, multiple candidate bridge URLs can be
  configured and are tried in order

## Configuration

The main runner is
[`scripts/erc-4626/scan-vault-posts.py`](../../scripts/erc-4626/scan-vault-posts.py).

It uses environment variables instead of a command line parser:

- `DB_PATH`: DuckDB path, default `~/.tradingstrategy/vaults/vault-post-database.duckdb`
- `MAPPINGS_DIR`: optional override for the feeder directory root, default
  `eth_defi/data/feeds`
- `LOG_LEVEL`: logging level, default `warning`
- `MAX_WORKERS`: worker threads for concurrent feed reads
- `MAX_POSTS_PER_SOURCE`: maximum number of latest entries to inspect per source
- `REQUEST_TIMEOUT`: HTTP timeout in seconds
- `REQUEST_DELAY_SECONDS`: delay between source fetches
- `TWITTER_RSS_BASE_URLS`: comma-separated Nitter or xcancel-style bridge base URLs
- `TWITTER_FEED_URL_TEMPLATES`: comma-separated URL templates with `{handle}`
- `LINKEDIN_FEED_URL_TEMPLATES`: comma-separated URL templates with `{company_id}`
- `MAX_PROXY_ROTATIONS`: maximum proxy rotations before falling back to direct reads
- `WEBSHARE_API_KEY`: optional Webshare API token for proxy-backed feed fetches
- `WEBSHARE_PROXY_MODE`: optional Webshare proxy mode if your account supports it
- `MAX_POST_AGE_DAYS`: retention window for pruning old posts

Example run:

```shell
export TWITTER_FEED_URL_TEMPLATES="https://xcancel.com/{handle}/rss,https://rss.xcancel.com/{handle}/rss"
export LINKEDIN_FEED_URL_TEMPLATES="https://rsshub.pseudoyu.com/linkedin/company/{company_id}/posts,https://rss.owo.nz/linkedin/company/{company_id}/posts"
poetry run python scripts/erc-4626/scan-vault-posts.py
```

## Main files

The main files for this submodule are:

- [`eth_defi/feed/sources.py`](./sources.py): unified feeder schema, URL
  normalisation, and duplicate detection
- [`eth_defi/feed/collector.py`](./collector.py): HTTP reads, RSS/Atom parsing,
  text cleaning, synthetic id generation, and collection orchestration
- [`eth_defi/feed/database.py`](./database.py): DuckDB schema, source sync
  state, idempotent inserts, and retention pruning
- [`eth_defi/feed/testing.py`](./testing.py): reusable helpers for feed tests
- [`scripts/erc-4626/scan-vault-posts.py`](../../scripts/erc-4626/scan-vault-posts.py):
  standalone operator script for scheduled collection, summary output, and
  per-source dashboard tables
- [`eth_defi/data/feeds/README.md`](../data/feeds/README.md): top-level feed
  folder summary and pointer back to this README

## Current limitations

This module intentionally leaves a few concerns for later iterations:

- no post-to-vault matching yet
- no AI summarisation yet
- no retry or backoff policy beyond the next scheduled run

This keeps the first version easy to understand and easy to extend later.
