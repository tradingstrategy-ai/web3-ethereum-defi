# CuratorWatch

CuratorWatch is a public DeFi vault curator intelligence website:

https://curatorwatch.com/

Use this note when comparing CuratorWatch curator coverage against the local
feed files in `eth_defi/data/feeds/`, especially
`eth_defi/data/feeds/curators/`.

The site is a Vercel-hosted Next.js application. The homepage is statically
pre-rendered and embeds a React Server Components payload, but there are public
JSON endpoints that are easier and more stable to consume than scraping the
HTML.

Observed response headers include:

- `server: Vercel`
- `x-nextjs-prerender: 1` on statically rendered pages
- `x-matched-path` for route resolution
- `content-type: application/json` on API routes

## What the website tracks

The homepage presents curator-level and vault-level intelligence for curated
DeFi products.

Observed top-level website data:

- total curated TVL
- number of tracked curators
- number of tracked vault products
- asset distribution across all tracked products
- single-manager concentration by stablecoin
- ranked curator table by TVL
- curator grade summaries
- vault grade distribution
- latest newswire items mentioning tracked curators
- 30-day TVL chart data for top curators

The homepage copy says the data is "updated every 6h". Treat all counts as
time-sensitive and re-fetch them before using them in a report.

## Api surface

### Curator list

Endpoint:

```text
GET https://curatorwatch.com/api/curators?page=1
```

Pagination:

- `page` is supported.
- The observed page size is fixed at 20.
- Query parameters like `limit=100`, `pageSize=100`, `take=100`, and
  `all=true` were ignored during testing.
- Fetch pages until `data.pagination.page == data.pagination.totalPages`.

Observed response shape:

```json
{
  "success": true,
  "data": {
    "curators": [],
    "stats": {},
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "total": 77,
      "totalPages": 4
    }
  }
}
```

Useful curator fields:

- `curatorId`
- `curatorAddress`
- `name`
- `logoUrl`
- `website`
- `twitter`
- `jurisdiction`
- `entityType`
- `isRegulated`
- `totalAUM`
- `vaultCount`
- `avgApy`
- `avgNetApy`
- `assetDistribution`
- `stablePct`
- `protocols`
- `networks`
- `gradeDistribution`
- `engineRating`
- `dataSources`
- `riskScore`
- `strategyType`
- `tvlChange30d`
- `tvlChangePct30d`

Example fetch:

```shell
poetry run python - <<'PY'
import json
import urllib.request

curators = []
page = 1

while True:
    url = f"https://curatorwatch.com/api/curators?page={page}"
    payload = json.load(urllib.request.urlopen(url, timeout=30))
    data = payload["data"]
    curators.extend(data["curators"])

    if page >= data["pagination"]["totalPages"]:
        break
    page += 1

print(len(curators))
print([curator["name"] for curator in curators[:10]])
PY
```

### Curator detail

Endpoint:

```text
GET https://curatorwatch.com/api/curators/{slug}
```

Example:

```text
GET https://curatorwatch.com/api/curators/gauntlet
```

Observed response shape:

```json
{
  "success": true,
  "data": {
    "curator": {},
    "vaults": []
  }
}
```

Useful detail fields on `data.curator` include:

- `id`
- `address`
- `name`
- `website`
- `twitter`
- `discord`
- `email`
- `legalName`
- `entityType`
- `jurisdiction`
- `registeredState`
- `headquarters`
- `description`

The slug is usually the lower-case, hyphenated display name shown in the site
URL, e.g. `/curator/gauntlet` maps to `/api/curators/gauntlet`.

Some detail requests are slower than the summary endpoint because uncached
paths may trigger Vercel server-side work.

### Vault list

Endpoint:

```text
GET https://curatorwatch.com/api/vaults
```

This endpoint returned the full vault list in one response when tested. Do not
assume that remains true if the website grows; check for pagination fields
before relying on it.

Observed response shape:

```json
{
  "success": true,
  "data": [],
  "count": 401
}
```

Useful vault fields:

- `id`
- `address`
- `name`
- `symbol`
- `chainId`
- `chainName`
- `asset`
- `curatorAddress`
- `curatorName`
- `fees`
- `latestSnapshot`
- `adapters`
- `riskAssessment`
- `turtleId`
- `protocol`
- `dataSource`
- `estTotalAPR`
- `netAPR`
- `aprBreakdown`
- `riskScore`
- `grade`
- `gradeFailures`
- `warnings`
- `listed`
- `creationTimestamp`
- `updatedAt`

This is the best endpoint for gathering evidence vaults when deciding whether a
CuratorWatch name should become a local curator YAML file, an alias, or a
rejected product label.

### Changes

Endpoint:

```text
GET https://curatorwatch.com/api/changes?hours=24&limit=5
```

Observed response shape:

```json
{
  "success": true,
  "data": {
    "changes": [],
    "summary": {},
    "curatorAlertCounts": {},
    "pagination": {}
  }
}
```

Useful parameters:

- `hours`: lookback window
- `limit`: number of changes to return

The sidebar appears to call this endpoint with `hours=24&limit=0` to fetch only
the current alert count.

## Local comparison workflow

Use `eth_defi/data/feeds/README.md` and `eth_defi/feed/README-feed.md` for the
local feeder schema.

For curator coverage, compare CuratorWatch names against:

- `feeder-id`
- `name`
- `twitter`
- `website`
- `canonical-feeder-id`
- `ipor-atomist`
- `euler-entity`
- `morpho-curator`
- `lagoon-curator`

First compare against `eth_defi/data/feeds/curators/*.yaml`. Then compare
against all feed roles under `eth_defi/data/feeds/**/*.yaml`, because some
CuratorWatch curators are already represented as protocol or stablecoin feeds.

Important local files:

- `eth_defi/data/feeds/curators/`
- `eth_defi/data/feeds/protocols/`
- `eth_defi/data/feeds/stablecoins/`
- `eth_defi/data/curators.md`
- `eth_defi/vault/curator.py`

The local `find-new-curators` skill is useful for this work:

```shell
poetry run python .claude/skills/find-new-curators/scripts/print-existing-curators.py
```

## Caveats

CuratorWatch labels are not always one-to-one with legal organisations.

Examples seen during the first comparison:

- `Spark Chip` likely maps to the existing Spark organisation.
- `3Jane Ecosystem` likely maps to existing `3jane`.
- `Trezor Steakhouse` is probably a co-branded Steakhouse curator label.
- `Janus Henderson` overlaps with local `janus-henderson-anemoy`.
- `Mainstreet`, `Origin Protocol`, and `Flying Tulip` already existed locally
  as protocol or stablecoin feeds, but not necessarily as curator aliases.

Do not blindly add every missing CuratorWatch row as a new curator YAML file.
Use `/api/vaults` and `/api/curators/{slug}` to gather evidence first, then
classify each row as one of:

- new third-party curator
- protocol-managed curator
- alias curator
- name-pattern update
- rejected product or strategy label

For source-bearing curator YAML files, prefer official websites, official
Twitter/X handles, LinkedIn company pages, and RSS feeds when available. If the
only evidence is a CuratorWatch label with no external identity and low TVL,
report it as a low-confidence candidate instead of adding it.
