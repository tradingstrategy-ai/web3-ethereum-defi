# Xerberus vault risk classification

Xerberus is an independent risk rating protocol for DeFi vaults. This package
fetches composite scores from the public REST API, stores them in DuckDB, and
exports them into the top-vaults JSON.

- Website: https://xerberus.io
- Documentation: https://xerberus.gitbook.io/documentation/
- Contact for keys: ms@xerberus.io

Unlike [Core3](../core3/README-core3.md) (protocol-level Probability of Loss),
Xerberus rates **individual vault pools** by `(chainId, address)` as well as
protocols. Two vaults of the same protocol may have different scores.

**Score polarity:** Xerberus composite scores are 0–100 where **higher is
better**. Core3 PoL is lower = safer. Never mix the two scales in ranking or
arithmetic.

## Modules

| Module | Description |
|--------|-------------|
| `eth_defi.xerberus.constants` | API URL, database paths, rate limits, score scale |
| `eth_defi.xerberus.session` | `XerberusSession` with dual auth + paced rate limiting |
| `eth_defi.xerberus.api` | Fetch helpers (registry, vault list, reports, healthcheck) |
| `eth_defi.xerberus.database` | `XerberusDatabase` DuckDB persistence |
| `eth_defi.xerberus.scanner` | `scan_xerberus()` orchestrator |
| `eth_defi.xerberus.cli` | Shared scan/backfill CLI entry helper |
| `eth_defi.xerberus.mappings` | Our protocol slug ↔ Xerberus protocol id |
| `eth_defi.xerberus.vault_export` | Per-vault sections + top-level `xerberus_protocols` |
| `eth_defi.xerberus.errors` | `XerberusAPIError` |

## Database files

Default location: `~/.tradingstrategy/vaults/xerberus/`

| File | Description |
|------|-------------|
| `xerberus.duckdb` | Snapshots, vault lists, score_daily, report URLs |
| `rate-limit.sqlite` | Thread-safe rate limit state |

There is **no public history API**. If the DuckDB is deleted, history cannot be
reconstructed; re-run `backfill-xerberus.py` for current scores only.

Do not run a standalone scan concurrent with top-vaults JSON export / R2 upload
(DuckDB single-writer). Export opens the database read-only.

### Database tables

| Table | Description |
|-------|-------------|
| `registry_snapshots` | Point-in-time registry entities (pool/protocol/…) |
| `vault_list_snapshots` | Platform vault lists (export fallback only) |
| `report_urls` | Dendrogram links keyed by `(chain_id, address)` |
| `score_daily` | Derived daily series from registry polls only |
| `sync_state` | Poll watermarks |

No PRIMARY KEY (DuckDB ART crash workaround). Payloads use
`VARCHAR USING COMPRESSION 'zstd'`.

## Authentication (API key + email)

Xerberus public REST auth is a **dual credential pair**. Every authenticated
request must send:

| HTTP header | Source |
|-------------|--------|
| `x-api-key` | `XERBERUS_API_KEY` or `create_xerberus_session(api_key=...)` |
| `x-user-email` | `XERBERUS_API_EMAIL` (alias `XERBERUS_USER_EMAIL`) or `create_xerberus_session(api_email=...)` |

The email is the address registered with Xerberus when the key was issued. The
server matches key + email; a wrong or missing email returns HTTP 400/401 even
if the key string looks correct.

### `XERBERUS_API_EMAIL` (required for live scans)

| Property | Detail |
|----------|--------|
| Env var | **`XERBERUS_API_EMAIL`** (preferred) |
| Alias | `XERBERUS_USER_EMAIL` (legacy / docs alias; same meaning) |
| Python | `create_xerberus_session(api_email="…")` or `XerberusSession(api_email="…")` |
| Resolver | `resolve_xerberus_api_email()` reads env only; does not invent a value |
| Healthcheck | Not required (`GET /registry/healthcheck` is unauthenticated) |

**Agents and automation must not guess the email.** Do not invent
`dev@…`, personal git emails, or probe candidate addresses. Use only:

1. An explicit `api_email=` argument passed by the operator/caller, or
2. `XERBERUS_API_EMAIL` / `XERBERUS_USER_EMAIL` already set in the environment /
   secrets file (e.g. gitignored `.local-test.env`) by a human operator.

If the email is missing, fail clearly and ask the operator to set it. Never
brute-force or trial-and-error auth against the live API.

Local development loads both variables from `.local-test.env` (gitignored;
sourced before scripts/tests). Operators must keep
`XERBERUS_API_KEY` and `XERBERUS_API_EMAIL` there (or in production secrets).
Agents only read those values — they never invent them.

Example (env after secrets are configured):

```shell
# .local-test.env (or production vault-rpc.env) must define both:
#   export XERBERUS_API_KEY=...
#   export XERBERUS_API_EMAIL=...   # exact email registered with Xerberus — do not guess
source .local-test.env && poetry run python scripts/xerberus/backfill-xerberus.py
```

Example (explicit Python arguments — preferred when wiring callers):

```python
import os

from eth_defi.xerberus.session import create_xerberus_session

session = create_xerberus_session(
    api_key=os.environ["XERBERUS_API_KEY"],
    api_email=os.environ["XERBERUS_API_EMAIL"],  # pass through explicitly; do not hardcode
)
```

## Environment variables

| Variable | Required | Notes |
|----------|----------|-------|
| `XERBERUS_API_KEY` | for scan | API key from secrets / operator env |
| `XERBERUS_API_EMAIL` | for scan | **Registered email for the key.** Alias: `XERBERUS_USER_EMAIL`. **Do not guess.** |
| `XERBERUS_DATABASE_PATH` | no | Default under `~/.tradingstrategy/vaults/xerberus/` |
| `SKIP_XERBERUS` | no | `true` disables all-chains enrichment |
| `XERBERUS_FETCH_VAULT_LIST` | no | Default `true` |
| `XERBERUS_FETCH_REPORTS` | no | Default `false` |
| `XERBERUS_REPORT_LIMIT` | no | Default `50` |

## Scripts

```shell
# Both XERBERUS_API_KEY and XERBERUS_API_EMAIL must be set (do not invent email)
source .local-test.env && poetry run python scripts/xerberus/scan-xerberus.py
source .local-test.env && poetry run python scripts/xerberus/backfill-xerberus.py
source .local-test.env && DRY_RUN=true poetry run python scripts/xerberus/backfill-xerberus.py
poetry run python scripts/xerberus/xerberus-overview.py   # local DuckDB only; no API
source .local-test.env && XERBERUS_REPORT_LIMIT=50 poetry run python scripts/xerberus/backfill-xerberus-reports.py
poetry run python scripts/xerberus/update-xerberus-mappings.py
```

## Export contract

Top-vaults JSON (`VaultMetricsExport`):

| Location | Field | Meaning |
|----------|-------|---------|
| Each vault row | `xerberus` | Compact pool-first score (`XerberusVaultSection`) |
| Top level | `xerberus_protocols` | Protocol metadata by our protocol slug |
| Top level | `xerberus_stats` | Optional coverage counts |

Lookup order for `vault.xerberus`:

1. Registry pool `(chain_id, address)` with non-null score
2. Vault list same key if registry missing/null
3. Protocol fallback via `XERBERUS_PROTOCOL_MAPPINGS`

Preloaded once in `top_vaults_json.main()` and passed into
`calculate_lifetime_metrics(..., xerberus_pools=, xerberus_protocols=)`.

## API overview

- Base URL: `https://api.xerberus.io/public/v1`
- Auth: **`x-api-key` + `x-user-email`** (see [Authentication](#authentication-api-key--email));
  email comes from `XERBERUS_API_EMAIL` or an explicit `api_email=` argument
- Rate limit: **10 requests / 60 seconds / IP** — client uses ≤8/min + 7.5s pacing
- Healthcheck: `GET /registry/healthcheck` (plain text, unauthenticated; no email required)

Offline tests need no credentials. Live scanner tests require both
`XERBERUS_API_KEY` and `XERBERUS_API_EMAIL` (operator-supplied; never guessed).

### Endpoints used

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/registry/scores?type=pool,protocol` | Composite scores |
| GET | `/vault/list?platform=` | Platform vaults (`ipor`, `morpho`, `spark`, `t3tris`) |
| GET | `/vault/reports/download/:address` | Dendrogram URL |
| GET | `/registry/healthcheck` | Liveness |

## Tests

```shell
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" \
  poetry run pytest tests/xerberus/ -v --timeout=300
```

## Production

All-chains schedules Xerberus when **both** `XERBERUS_API_KEY` and
`XERBERUS_API_EMAIL` are present (`SKIP_XERBERUS` to disable). Missing either
credential disables the scan with a warning. Example cycle:
`Core3=24h,Xerberus=24h`. Docker Compose passes `XERBERUS_API_KEY` and
`XERBERUS_API_EMAIL` on looped and oneshot services. R2 data export includes
`xerberus.duckdb` when present.

Operators must set the registered email in secrets; agents must not invent it.
