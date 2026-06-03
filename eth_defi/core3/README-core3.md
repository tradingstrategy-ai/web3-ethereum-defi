# Core3 projects data API

Core3 (formerly CER.live) is a self-regulatory risk intelligence platform for Web3.
It provides a standardised **Probability of Loss (PoL)** risk metric for crypto projects
and centralised exchanges, scoring risk on a 0-100 scale where 0 = Exceptional and
100 = Critical risk.

- Website: https://core3.io
- Documentation: https://docs.core3.io
- OpenAPI spec: https://docs.core3.io/api-reference/projects-data-openapi.json
- Contact: info@core3.io

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
