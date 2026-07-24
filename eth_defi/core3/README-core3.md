# Core3 projects data API

Core3 (formerly CER.live) is a self-regulatory risk intelligence platform for Web3.
It provides a standardised **Probability of Loss (PoL)** risk metric for crypto projects
and centralised exchanges, scoring risk on a 0-100 scale where 0 = Exceptional and
100 = Critical risk.

- Website: https://core3.io
- Documentation: https://docs.core3.io
- OpenAPI spec: https://docs.core3.io/api-reference/projects-data-openapi.json
- Contact: info@core3.io

## Modules

| Module | Description |
|--------|-------------|
| `eth_defi.core3.constants` | Shared constants: API URL, database paths, rate limit config, section names |
| `eth_defi.core3.session` | `Core3Session` (requests.Session subclass) with rate limiting and retry logic |
| `eth_defi.core3.api` | Fetch helpers for all Core3 API endpoints (project list, detail, PoL history, sections) |
| `eth_defi.core3.database` | `Core3Database` — DuckDB persistence with thread-safe inserts, deduplication, sync state watermarks, and query methods |
| `eth_defi.core3.scanner` | `scan_projects()` orchestrator — parallel fetching with `joblib.Parallel`, incremental sync, error handling |
| `eth_defi.core3.mappings` | `CORE3_MAPPINGS` — canonical mapping from our vault protocol slugs to Core3 project slugs |
| `eth_defi.core3.vault_protocol` | `build_core3_protocols_for_export()` / `get_core3_protocol_record()` — resolve vault protocol slugs to Core3 records for the vault metrics JSON export (`core3_protocols` key), including the latest per-category PoL sub-scores (`pol_categories`) |

## Database files

Default location: `~/.tradingstrategy/vaults/core3/`

| File | Description |
|------|-------------|
| `core3.duckdb` | Main DuckDB database with all project snapshots and time-series data |
| `core3.duckdb.wal` | DuckDB write-ahead log (automatically managed) |
| `rate-limit.sqlite` | SQLite database for thread-safe rate limiting state across `joblib` workers |

``Core3Database`` automatically migrates legacy database files to the latest
DuckDB storage format when opening them. The migration rewrites both raw JSON
payload columns with native Zstandard compression, verifies row counts and
atomically replaces the original file. Because it selects DuckDB's latest
format, the migrated file requires the writer's DuckDB version or newer.

### Database tables

| Table | Description |
|-------|-------------|
| `project_snapshots` | One row per poll cycle per project — raw JSON payload plus extracted key columns (slug, name, rank, PoL score/rating, market cap) |
| `section_snapshots` | Optional section detail storage (security, financial, operational, reputational, regulatory) |
| `pol_daily` | API-native PoL score time-series (sparse timestamps); `__index__` slug for aggregate |
| `pol_category_daily` | API-native category PoL breakdown time-series (security, financial, operational, reputational, regulatory scores) |
| `sync_state` | Per-slug watermarks for incremental sync — tracks `last_ts`, `backfill_done`, and `last_synced` |

No PRIMARY KEY or UNIQUE constraints are used due to a DuckDB 1.5.0 ART index crash
on Python 3.14 + macOS ARM64 ([duckdb#17006](https://github.com/duckdb/duckdb/issues/17006)).
Deduplication is handled at the application level using DELETE + INSERT via temp tables.

## Scripts

### scan-core3.py — batch scanner

Fetches all Core3 projects and stores snapshots + PoL time-series in DuckDB.
Supports incremental sync (first run does full backfill, subsequent runs fetch only new data).

```shell
source .local-test.env && poetry run python scripts/core3/scan-core3.py
```

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `CORE3_API_KEY` | (required) | Core3 API key (prefixed `core3_`) |
| `LOG_LEVEL` | `warning` | Logging level: debug, info, warning, error |
| `CORE3_DATABASE_PATH` | `~/.tradingstrategy/vaults/core3/core3.duckdb` | Path to DuckDB database file |
| `LIMIT` | (none) | Limit number of projects to scan (for testing) |
| `MAX_WORKERS` | `8` | Maximum number of parallel workers for API fetching |
| `FETCH_SECTIONS` | `true` | Set to `false` to skip section detail endpoints (5 extra API calls per project) |

### core3-overview.py — database inspector

Displays a tabulated overview of the database contents: one row per project with
rank, PoL score, market cap, snapshot counts, and PoL date ranges.

```shell
poetry run python scripts/core3/core3-overview.py
```

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `CORE3_DATABASE_PATH` | `~/.tradingstrategy/vaults/core3/core3.duckdb` | Path to DuckDB database file |

### update-core3-mappings.py — vault protocol mapping updater

Compares our vault protocol metadata (`eth_defi/data/vaults/metadata/*.yaml`) against the
Core3 DuckDB database and generates a Markdown report at `/tmp/core3-mappings.md` with:

1. Our vault protocols table (name, slug, homepage)
2. Core3 DeFi-related projects table (name, slug, category, PoL)
3. Current confirmed mappings
4. Candidate new mappings discovered by heuristics
5. Unmapped protocols with no Core3 equivalent

The script applies four matching heuristics in priority order:

1. **Exact slug match** — our slug exists verbatim in Core3
2. **Website domain match** — our `links.homepage` domain matches Core3 `links.website`
3. **DeFi Llama slug match** — our `links.defillama` slug matches Core3 `coingecko_id`
4. **Normalised name match** — name similarity after stripping suffixes like "Finance", "Protocol"

Candidates must be manually verified before adding to `CORE3_MAPPINGS` in
`eth_defi/core3/mappings.py` — false positives occur (e.g. "GOAT Network" L2 vs "Goat Protocol" vaults).

```shell
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run python scripts/core3/update-core3-mappings.py
```

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `CORE3_DATABASE_PATH` | `~/.tradingstrategy/vaults/core3/core3.duckdb` | Path to DuckDB database file |
| `LOG_LEVEL` | `warning` | Logging level |

### update-core3-mappings.py — vault protocol mapping updater

Compares our vault protocol metadata (`eth_defi/data/vaults/metadata/*.yaml`) against the
Core3 DuckDB database and generates a Markdown report at `/tmp/core3-mappings.md` with:

1. Our vault protocols table (name, slug, homepage)
2. Core3 DeFi-related projects table (name, slug, category, PoL)
3. Current confirmed mappings
4. Candidate new mappings discovered by heuristics
5. Unmapped protocols with no Core3 equivalent

The script applies four matching heuristics in priority order:

1. **Exact slug match** — our slug exists verbatim in Core3
2. **Website domain match** — our `links.homepage` domain matches Core3 `links.website`
3. **DeFi Llama slug match** — our `links.defillama` slug matches Core3 `coingecko_id`
4. **Normalised name match** — name similarity after stripping suffixes like "Finance", "Protocol"

Candidates must be manually verified before adding to `CORE3_MAPPINGS` in
`eth_defi/core3/mappings.py` — false positives occur (e.g. "GOAT Network" L2 vs "Goat Protocol" vaults).

```shell
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run python scripts/core3/update-core3-mappings.py
```

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `DB_PATH` | `~/.tradingstrategy/core3/risk-data.duckdb` | Path to DuckDB database file |
| `LOG_LEVEL` | `warning` | Logging level |

### reproduce-duckdb-crash.py — crash reproducer

Standalone DuckDB crash reproducer (no API key needed). Simulates the scanner's data volume
and threading patterns across 24 scenarios to isolate the SIGSEGV trigger.

```shell
# Run all scenarios
poetry run python scripts/core3/reproduce-duckdb-crash.py all

# Run a specific scenario
poetry run python scripts/core3/reproduce-duckdb-crash.py 12
```

## Tests

| Test module | Description | Requires API key |
|-------------|-------------|-----------------|
| `tests/core3/test_core3_scanner.py` | Integration tests: scan projects, verify snapshots, PoL history, idempotency | Yes (`CORE3_API_KEY`) |
| `tests/core3/test_core3_database.py` | Offline tests: insert, deduplication, sync state, query methods with synthetic data | No |

```shell
# Run offline tests (no API key needed)
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/core3/test_core3_database.py -v --timeout=300

# Run integration tests (requires CORE3_API_KEY)
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/core3/test_core3_scanner.py -v --timeout=300
```

## API overview

- **Type**: REST / JSON, read-only (all endpoints are `GET`)
- **Base URL**: `https://api.core3.io/projects_data`
- **Version**: All routes under `/v1`
- **Authentication**: `x-api-key` header, keys prefixed `core3_`
- **Environment variable**: `CORE3_API_KEY`
- **Cloudflare**: Requires a `User-Agent` header — requests without one get blocked (HTTP 403, error 1010)
- **Coverage**: ~1,427 crypto projects as of June 2026

## Key concepts

### Probability of loss (PoL)

A data-driven, non-price risk metric. Does **not** evaluate expected returns or token
price performance. Scores map to credit-style tiers:

| Rating | PoL range | Meaning |
|--------|-----------|---------|
| AAA    | ~0        | Exceptional |
| AA/A   | Low       | Very low loss probability |
| BBB/BB/B | Medium  | Moderate, increasing probability |
| CCC/CC/C | High    | High probability |
| DDD/DD/D | Very high | Critical risk |

PoL is assessed across six risk categories (each with its own sub-score):

1. **Security** — audits, bug bounty, contract verification, third-party monitoring
2. **Financial** — revenue sources, treasury quality, token inflation, circulating supply
3. **Operational** — GitHub activity, team track record, liquidity risks, certifications
4. **Reputational** — auditor ratings, incident response, red flags, social metrics, insurance
5. **Regulatory** — KYC/KYT, jurisdiction, legal documentation, team transparency
6. **Dependency** — bridges, oracles, custody, infrastructure providers

The methodology uses 98 metrics and sub-metrics, combined via a baseline assessment
module and a contextual adjustment module.

### Proof of voice (PoV)

Expert-authored qualitative reviews: structured pros/cons lists and analyst reviews
providing human context alongside quantitative PoL data.

### Seals

Verifiable trust marks: `security_measures`, `independent_certificates`, `self_regulation`.

## Endpoints

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/health` | Liveness check; returns `200` (ok) or `503` |

Response: `{status, info: {database: {status}, cache: {status}}, error, details}`

### Project list and search

| Method | Path | Parameters | Description |
|--------|------|------------|-------------|
| GET | `/v1/list` | — | All ~1,427 projects with slug, name, coingecko_id, PoL score |
| GET | `/v1/search` | `search` (text, required) | Search by name/slug; returns slug, name, ticker, rank, logo, PoL |
| GET | `/v1/search/trending` | — | Currently trending projects |

### Ratings (paginated + filterable)

| Method | Path | Parameters | Description |
|--------|------|------------|-------------|
| GET | `/v1/ratings` | `page` (default 1), `page_size` (default 20, max 50), `sort_by`, `sort_direction`, `categories[]`, `market_cap[]`, `chains[]`, `compliance[]` | Paginated project rankings with PoL, market cap, seals |
| GET | `/v1/ratings/parameters` | — | Available filter values and sort fields |

Sort fields: `rank` (default), `name`, `certifications`, `market_cap`, `market_cap_change_24h`, `pol`, `data_coverage`, `category`.

Filter options include ~30 categories (Layer 1, DeFi, GameFi, AI, etc.), market cap ranges,
chain filters, and compliance filters (`audited`, `kyc`).

### Project detail

| Method | Path | Parameters | Description |
|--------|------|------------|-------------|
| GET | `/v1/{slug}` | — | Full project profile |

Returns: `slug`, `name`, `description`, `rank`, `pol` (score/rating/confidence), `ticker`,
`coingecko_id`, `logo`, `link`, `launched_at`, `category`, `data_coverage` (percentage),
`market_cap` (in_usd, change_24h_percentage, change_24h_in_usd), `chains[]`,
`links` (website, legal, whitepaper, socials[]), `tags[]`, `top_risks[]` (content, date),
`recent_changes[]`, `seals`.

### Project section endpoints

All section endpoints take `{slug}` as a path parameter and return
the section-level PoL sub-score alongside section-specific data.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/{slug}/security` | Audits (token/product), bug bounty, token contract verification, third-party monitoring/prevention |
| GET | `/v1/{slug}/financial` | Revenue sources, treasury quality (asset distribution, trends, spikes), circulating supply analysis, lockers |
| GET | `/v1/{slug}/financial/inflation/history/chart` | Inflation history; optional `days` (1-365) |
| GET | `/v1/{slug}/reputational` | Top auditor rating, past incidents, red flags, social metrics (Twitter, website, Google Trends), insurance |
| GET | `/v1/{slug}/regulatory` | KYC/KYT, jurisdiction quality, legal presence, team transparency, regulatory status |
| GET | `/v1/{slug}/operational` | GitHub activity heatmap, team track records, liquidity risks (CEX/DEX quality), documentation links, certifications (ISO 27001, CCSS) |
| GET | `/v1/{slug}/proof_of_voice` | Pros, cons, expert reviews (reviewer handler, date, text) |
| GET | `/v1/{slug}/proof_of_voice/history/chart` | PoV sentiment over time (positive/negative %); optional `days` |

### PoL score endpoints

#### Index-level (aggregate across all projects)

| Method | Path | Parameters | Description |
|--------|------|------------|-------------|
| GET | `/v1/pol` | — | Current aggregate PoL score |
| GET | `/v1/pol/history` | `from`, `to` (unix timestamps, required) | Historical PoL points |
| GET | `/v1/pol/history/chart` | `days` (1-365, optional; omit for all-time) | PoL chart data |

#### Per-project PoL

| Method | Path | Parameters | Description |
|--------|------|------------|-------------|
| GET | `/v1/{slug}/pol/current` | — | Current project PoL with rating and confidence |
| GET | `/v1/{slug}/pol/history` | `from`, `to` | Historical PoL |
| GET | `/v1/{slug}/pol/history/chart` | `days` | PoL chart |
| GET | `/v1/{slug}/pol/by_category` | — | PoL broken down by security, financial, operational, reputational, regulatory |
| GET | `/v1/{slug}/pol/by_category/history` | `from`, `to` | Category breakdown over time |
| GET | `/v1/{slug}/pol/by_category/history/chart` | `days` | Category chart |
| GET | `/v1/{slug}/pol/by_metric` | — | PoL broken down by individual metrics with weights |
| GET | `/v1/{slug}/pol/by_metric/history` | `from`, `to` | Metric-level history |
| GET | `/v1/{slug}/pol/by_metric/history/chart` | `days` | Metric-level chart |

## Common parameters

- **`{slug}`**: Project identifier string (e.g. `ethereum`, `aave`, `bitcoin`). Matches CoinGecko IDs in most cases.
- **`from`, `to`**: Unix timestamps in seconds (inclusive range). Required for `/history` endpoints.
- **`days`**: Integer 1-365. Optional for `/history/chart` endpoints; omit for all-time data.

## Response format

All responses are JSON. Key shared types:

```
PolDto {
  score: number       // 0-100, lower = less risk
  rating: string|null // "AAA", "AA", "A", "BBB", ..., "D", or null
  confidence: string|null // "Exceptional", "High", "Medium", "Low", "Critical", or null
}
```

History/chart endpoints return `{points: [{score, timestamp}, ...]}` where `timestamp`
is a unix timestamp in seconds.

## Example usage

```bash
# Health check
curl -sS "https://api.core3.io/projects_data/v1/health" \
  -H "x-api-key: $CORE3_API_KEY" \
  -H "User-Agent: eth-defi"

# Get Ethereum project detail
curl -sS "https://api.core3.io/projects_data/v1/ethereum" \
  -H "x-api-key: $CORE3_API_KEY" \
  -H "User-Agent: eth-defi"

# Get PoL breakdown by category
curl -sS "https://api.core3.io/projects_data/v1/ethereum/pol/by_category" \
  -H "x-api-key: $CORE3_API_KEY" \
  -H "User-Agent: eth-defi"

# Ratings with filtering
curl -sS "https://api.core3.io/projects_data/v1/ratings?page=1&page_size=10&sort_by=pol&sort_direction=ASC" \
  -H "x-api-key: $CORE3_API_KEY" \
  -H "User-Agent: eth-defi"
```

## Notes and quirks

- The API is behind Cloudflare. A `User-Agent` header is **required** or requests fail with HTTP 403 / error code 1010.
- The `/v1/list` endpoint returns all ~1,427 projects in a single response (not paginated). Only `/v1/ratings` is paginated.
- Many fields are nullable. For less-covered projects, section data may be mostly `null`.
- The `by_metric` endpoint currently returns duplicated metric entries (observed for Ethereum) — this appears to be an API bug.
- Market cap values are returned as strings (`"226130642842"`), not numbers.
- The `link` field in project detail has a malformed URL (e.g. `https://core3.ioethereum` missing a `/`).
- There is also a separate **Exchanges Data API** (`/exchanges_data`) for centralised exchange risk assessment, documented at https://docs.core3.io/exchanges-data-api.md with its own OpenAPI spec.
