# Xerberus vault risk classification plan

## Goal

Add Xerberus risk classification support for vaults, parallel to the existing
Core3 integration: a local `xerberus.duckdb` cache, a batch scanner, pipeline
hooks in the all-chain vault scanner, export of risk scores into the vault
metrics JSON, manual operator scripts, and a Core3-style README.

**Product export requirements (must ship):**

1. **Per-vault risk summary** on every vault row via
   `calculate_lifetime_metrics()` / `calculate_vault_record()` — field
   `xerberus`. Unlike Core3 (protocol-level only, same summary shared by all
   vaults of a protocol), Xerberus provides **per-vault (pool) ratings** keyed
   by `(chain_id, address)`. Protocol score is only a fallback when the vault
   itself is unscored.
2. **Top-level protocol metadata** in the top-vaults JSON as a separate key
   `xerberus_protocols`, analogous to `core3_protocols` — keyed by our protocol
   slug, filtered to protocols present in the exported vaults.

This plan is for design and implementation sequencing. It does not write
production state.

## Why

Core3 enriches export with protocol-level Probability of Loss (PoL) via
`core3_protocols` and a compact per-vault `core3` section (protocol-derived).
Xerberus is an independent risk-rating source focused on DeFi vaults and
structural risk. Combining both gives operators two independent views:

- Core3: project / protocol PoL
- Xerberus: **vault-level** composite score when available, plus protocol
  metadata for deeper protocol pages

## Review feedback addressed

### Claude Opus 4.5 plan reviews

| # | Finding | Resolution |
|---|---------|------------|
| 1 | `report_urls` address-only collision | Key by `(chain_id, address)`; prefer registry `entity_id` app URLs |
| 2 | Reverse protocol mapping | Forward + reverse maps; `protocol_slug` on protocol export records |
| 3 | Registry vs vault-list precedence | registry pool → vault_list → protocol fallback |
| 4 | `score_daily` upsert | DELETE + INSERT; same-day idempotence test |
| 5 | Rate limiter burst | Paced ≥7.5 s **and** ≤8/min bucket |
| 6 | Stale pools | Export max-age (default 30 days) |
| 7 | Cross-source score arithmetic | Ban + sibling keys only |
| 8 | Address case test | Required offline test |
| 9 | TypedDicts | Full export types |
| 10 | ZSTD syntax | `VARCHAR USING COMPRESSION 'zstd'` |
| 11 | Email env name | `XERBERUS_API_EMAIL` (+ `XERBERUS_USER_EMAIL` alias) |
| 12 | Healthcheck | Pre-flight raw-text; not rate-limited path |
| 13 | `score_scale` | Constant + unit test |
| 14 | Credential warning test | Required |
| 15 | API envelope errors | `XerberusAPIError` + multi-shape tests |
| 16 | Coverage stats | Optional top-level `xerberus_stats` |

### Clean subagent review (grounded)

| # | Finding | Resolution |
|---|---------|------------|
| S1 | Per-vault export attachment underspecified | Full preload-map design below; wire through `calculate_lifetime_metrics` |
| S2 | Concurrent DuckDB lifecycle | Core3-style close-before-export; read-only export open; operator note |
| S3 | Error / healthcheck shapes incomplete | Three error shapes + plain-text healthcheck |
| S4 | `score_daily` registry-only vs vault_list export | Document residual risk; history is registry-entity only |
| S5 | Rate limiter recipe concrete | Serial + min interval + minute bucket; no limit on healthcheck |
| S6 | Production call-sites | SCAN_CYCLES, docker-compose both services, `get_data_file_paths()` |
| S7 | Greenfield storage flags | `storage_compatibility_version="latest"` |

Product additions:

- `XERBERUS_API_KEY` **available** in operator environment
- Manual backfill scripts + `README-vault-scripts.md` cross-link
- Full `README-xerberus.md` (Core3-style)

### Claude Opus 4.5 re-review (export-ready pass)

| # | Finding | Resolution |
|---|---------|------------|
| R1 | TypedDicts only illustrative | Formal `XerberusPoolLookupRow`, `XerberusProtocolExportRecord`, `XerberusExportStats`, `XerberusVaultSection` in `vault_export.py` |
| R2 | `vault_list_snapshots` schema thin | Full column list (mirrors registry + `platform` / `vault_id`) |
| R3 | Healthcheck path variable | Concrete `GET /registry/healthcheck` (+ vault group optional); Phase 0 verifies |
| R4 | Pool-map merge logic vague | Explicit pseudocode: registry non-null wins; vault_list fills gaps only |
| R5 | `XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS` missing from constants list | Listed in `constants.py` module contents |

## Starting point: API reality check

Public path `/api-reference/risk` is gone. Use public REST only for v1:

| Surface | Fit |
|---------|-----|
| **REST** `https://api.xerberus.io/public/v1` | **Primary** batch path |
| Framework / Enterprise MCP | Later optional depth |

Base URL: `https://api.xerberus.io/public/v1`

Auth (every authenticated call):

- `x-api-key` ← `XERBERUS_API_KEY` (**available**)
- `x-user-email` ← `XERBERUS_API_EMAIL` (alias `XERBERUS_USER_EMAIL`)

| Method | Path | Data |
|--------|------|------|
| `GET` | `/registry/scores` | Entities + composite score 0–100; `type=pool,protocol,...` |
| `GET` | `/vault/list?platform=` | Platforms: `ipor`, `morpho`, `spark`, `t3tris` |
| `GET` | `/vault/reports/download/:address` | Dendrogram deep link |
| `GET` | `/registry/healthcheck` | Plain-text liveness (unauthenticated, outside limiter). Primary pre-flight. |
| `GET` | `/vault/healthcheck` | Plain-text liveness for vault group (optional; same pattern). |

Pool entity:

```json
{
  "type": "pool",
  "id": "pool_xyz",
  "name": "Gami USDC",
  "chain": "arbitrum",
  "address": "0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f",
  "chainId": 42161,
  "score": 44
}
```

- Pools: key `(chainId, address.lower())` — never key by `name` (user-editable, collides).
- Protocols: no address; map via `XERBERUS_PROTOCOL_MAPPINGS`.
- Score polarity ≠ Core3 PoL: higher composite ≈ better; Core3 lower PoL ≈ safer.
  Document with `score_scale`; never blend.

Constraints:

1. **10 requests / 60 s / IP** — serial + paced
2. **No history API** — we store snapshots + `score_daily`
3. Vault list platform allowlist is narrow; registry pools are broader
4. Report download O(n) — default off

Docs: https://xerberus.gitbook.io/documentation/

## How Core3 works today (mirror + diverge where needed)

| Layer | Core3 | Xerberus |
|-------|-------|----------|
| Package | `eth_defi/core3/` | `eth_defi/xerberus/` |
| DuckDB | `…/core3/core3.duckdb` | `…/xerberus/xerberus.duckdb` |
| Standalone scan | `scripts/core3/scan-core3.py` | `scripts/xerberus/scan-xerberus.py` + backfill scripts |
| Pipeline | `scan_core3_fn` when `CORE3_API_KEY` | `scan_xerberus_fn` when key+email |
| **Per-vault JSON field** | `core3` — **protocol-derived** via slug map | `xerberus` — **pool-first** via `(chain_id, address)` |
| **Top-level JSON key** | `core3_protocols` | `xerberus_protocols` |
| Preload for metrics | `dict[protocol_slug → Core3ExportRecord]` | **two** structures: pool map + protocol map |
| Matching | `CORE3_MAPPINGS` | Address join (pools) + protocol mappings |
| README | `README-core3.md` | `README-xerberus.md` |

**Critical product difference:** Core3 attaches the same protocol summary to every
vault of that protocol. Xerberus must attach **vault-specific** scores when the
pool is scored; two Morpho vaults can have different Xerberus scores.

## Recommended architecture

### REST-first, MCP later

v1: public REST only. MCP scorecards later if product needs dimension depth.

### Matching model

| Entity | Match | Role |
|--------|-------|------|
| `pool` | `(chain_id, address.lower())` | **Primary** per-vault rating |
| `protocol` | our `protocol_slug` ↔ Xerberus `entity_id` | Fallback for unscored vaults + top-level `xerberus_protocols` |
| `organisation` / `asset` | skip v1 | |

### Canonical per-vault score lookup order

When building vault row `xerberus` (after max-age filter):

1. **Registry pool** — non-null score for `(chain_id, address)`
2. **Vault list** — same key if registry missing or null score
3. **Protocol fallback** — mapped protocol registry score

Never invent scores. Older than `XERBERUS_MAX_SCORE_AGE_DAYS` (default 30) → treat as miss.

### Score interpretation

Per-vault compact section (pool hit):

```json
{
  "xerberus": {
    "score": 44,
    "score_scale": "0_100_higher_is_better",
    "entity_type": "pool",
    "entity_id": "pool_xyz",
    "name": "Gami USDC",
    "report_url": "https://app.xerberus.io/pool/dendrogram/pool_xyz",
    "fetched_at": "2026-07-25T12:00:00"
  }
}
```

Protocol fallback only:

```json
{
  "xerberus": {
    "score": 84,
    "score_scale": "0_100_higher_is_better",
    "entity_type": "protocol",
    "entity_id": "proto_abc",
    "name": "Morpho",
    "protocol_slug": "morpho",
    "report_url": null,
    "fetched_at": "..."
  }
}
```

Hard rule: no arithmetic mix of Core3 PoL and Xerberus composite.

---

## Export design (required product surface)

This section is the authoritative wiring for JSON export. Implement exactly
this shape.

### Dual surface (like Core3, but pool-aware)

| Surface | Core3 | Xerberus |
|---------|-------|----------|
| Per vault row | `vault["core3"]` | `vault["xerberus"]` |
| Top-level metadata | `core3_protocols` | `xerberus_protocols` |
| Optional stats | — | `xerberus_stats` |

### A. Preload once when opening DuckDB (export path)

In `top_vaults_json.main()` (and any other caller of
`calculate_lifetime_metrics` that should enrich):

```text
1. Resolve path: resolve_xerberus_database_path()
2. If path.exists():
     db = XerberusDatabase(path, read_only=True)   # export is read-only
     try:
       xerberus_pools = build_xerberus_pool_lookup(db, max_age_days=...)
         # dict[(chain_id, address_lower)] -> intermediate score row
       xerberus_protocols = build_xerberus_protocols_for_export(
           db, all_protocol_slugs_from_vault_db)
         # dict[our_protocol_slug] -> XerberusProtocolExportRecord
     finally:
       db.close()
   else:
     xerberus_pools = {}
     xerberus_protocols = {}

3. lifetime_data_df = calculate_lifetime_metrics(
       returns_df,
       vault_db,
       core3_protocols=core3_protocols,
       xerberus_pools=xerberus_pools,           # NEW — per-vault lookup
       xerberus_protocols=xerberus_protocols,   # NEW — protocol fallback + context
   )

4. After sticky filter / exported vault list:
   - Restrict xerberus_protocols to protocol_slugs present in exported vaults
     (same pattern as core3_protocols)
   - Compute xerberus_stats from exported vaults (matched pool / protocol /
     null counts)

5. output_data: VaultMetricsExport = {
       "generated_at": ...,
       "metadata": ...,
       "core3_protocols": core3_protocols,
       "xerberus_protocols": xerberus_protocols,   # NEW top-level key
       "xerberus_stats": xerberus_stats,           # optional but recommended
       "curators": ...,
       "vaults": vaults,  # each vault has "xerberus" sibling of "core3"
   }
```

**Performance:** Preload full latest pool table into a Python dict once. Do
**not** query DuckDB per vault inside `calculate_vault_record`. Protocol map is
small (same order as Core3).

### B. `calculate_lifetime_metrics()` / `calculate_vault_record()`

Files: `eth_defi/research/vault_metrics.py`

#### Signature changes

```python
def calculate_lifetime_metrics(
    df: pd.DataFrame,
    vault_db: VaultDatabase | dict[VaultSpec, VaultRow],
    returns_column: str = "returns_1h",
    core3_protocols: dict[str, Core3ExportRecord] | None = None,
    xerberus_pools: dict[tuple[int, str], XerberusPoolLookupRow] | None = None,
    xerberus_protocols: dict[str, XerberusProtocolExportRecord] | None = None,
    stablecoin_rate_feeder: StablecoinRateFeeder | None = None,
) -> pd.DataFrame:
```

Thread both Xerberus structures into `calculate_vault_record(...)` the same way
`core3_protocols` is threaded today.

#### Per-vault resolution inside `calculate_vault_record`

```python
# Core3: protocol-level only
core3_section = build_core3_vault_section((core3_protocols or {}).get(protocol_slug))

# Xerberus: vault-first, then protocol fallback
xerberus_section = resolve_xerberus_vault_section(
    chain_id=chain_id,
    address=vault_address,  # normalised lower inside helper
    protocol_slug=protocol_slug,
    pools=xerberus_pools or {},
    protocols=xerberus_protocols or {},
)
```

`resolve_xerberus_vault_section` implements the canonical lookup order and
returns `XerberusVaultSection | None`.

#### `VaultMetricsRecord` TypedDict

Add sibling of `core3`:

```python
#: Compact Xerberus risk summary for this vault (pool score preferred),
#: or ``None`` when no Xerberus data is available. Unlike ``core3``, this
#: is primarily a per-vault rating when the pool is scored.
xerberus: "XerberusVaultSection | None"
```

In the record dict:

```python
"core3": core3_section,
"xerberus": xerberus_section,
```

#### CSV / strip paths

Where `_del("core3")` strips nested objects for tabular export, also
`_del("xerberus")` so both risk blobs stay JSON-only.

### C. Top-level `xerberus_protocols` (protocol metadata)

Parallel to `core3_protocols` — **not** a dump of all pools.

#### `VaultMetricsExport` TypedDict

```python
class VaultMetricsExport(TypedDict):
    generated_at: str
    metadata: ExportMetadata
    core3_protocols: dict[str, Core3ExportRecord]
    xerberus_protocols: dict[str, XerberusProtocolExportRecord]  # NEW
    xerberus_stats: NotRequired[XerberusExportStats]             # NEW optional
    curators: dict[str, CuratorExportRecord]
    vaults: list[VaultMetricsRecord]
```

#### Build helper

`build_xerberus_protocols_for_export(db, protocol_slugs) -> dict[str, XerberusProtocolExportRecord]`:

- For each our `protocol_slug` in `protocol_slugs`:
  - Resolve Xerberus protocol `entity_id` via `XERBERUS_PROTOCOL_MAPPINGS`
  - Load latest registry protocol snapshot (max-age aware)
  - Emit export record keyed by **our** slug (not Xerberus id)
- Skip unmapped / missing / stale

#### Formal TypedDicts (`vault_export.py`)

Implement these as real `TypedDict` classes (Sphinx `#:` member comments per
repo style). Not illustrative — copy into code.

```python
from typing import Literal, NotRequired, TypedDict


class XerberusPoolLookupRow(TypedDict):
    """Preloaded per-vault score row for O(1) resolve during metrics export."""

    #: EIP-155 chain id.
    chain_id: int
    #: Lowercased EVM vault address.
    address: str
    #: Composite score 0–100, or None if unscored.
    score: float | None
    #: Xerberus entity id (registry pool id or vault_list vault_id).
    entity_id: str
    #: Display name; never used as join key.
    name: str | None
    #: ISO 8601 or naive UTC datetime serialised at export.
    fetched_at: str
    #: Which table supplied this row.
    source: Literal["registry", "vault_list"]
    #: Optional dendrogram deep link when known.
    report_url: str | None


class XerberusVaultSection(TypedDict):
    """Compact per-vault Xerberus risk summary on each vault metrics row."""

    score: float | None
    score_scale: str  # always XERBERUS_SCORE_SCALE
    entity_type: Literal["pool", "protocol"]
    entity_id: str
    name: str | None
    protocol_slug: str | None  # set when entity_type == "protocol"
    report_url: str | None
    fetched_at: str


class XerberusProtocolExportRecord(TypedDict):
    """Top-level protocol metadata in ``xerberus_protocols`` (like Core3)."""

    #: Our vault protocol slug (same as map key).
    protocol_slug: str
    #: Xerberus registry protocol id.
    entity_id: str
    name: str | None
    score: float | None
    score_scale: str
    fetched_at: str


class XerberusExportStats(TypedDict):
    """Optional coverage summary for the exported vault list."""

    total_vaults: int
    pool_matches: int
    protocol_fallbacks: int
    unmatched: int
    coverage_pct: float
```

Richer than the compact vault section at protocol top-level; free to extend
`XerberusProtocolExportRecord` with extra snapshot fields later. Keep
Core3-style: fuller metadata at top level, compact summary on each vault.

#### Filtering (sticky / sample export)

- After vault list is finalised, keep only slugs present on exported vaults
  (mirror `core3_protocols` filter in `top_vaults_json.py`).
- `sample_export.py`: filter `xerberus_protocols` like `core3_protocols`.

### D. Per-vault compact section

Built by `resolve_xerberus_vault_section(...)` → `XerberusVaultSection | None`
(see formal TypedDict above).

### E. `build_xerberus_pool_lookup`

```python
def build_xerberus_pool_lookup(
    db: XerberusDatabase,
    max_age_days: int | None = XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS,
) -> dict[tuple[int, str], XerberusPoolLookupRow]:
```

**Merge precedence (required pseudocode):**

```text
lookup: dict[(chain_id, address_lower)] = {}

# 1. Registry pools first (canonical when score is non-null)
for row in latest_registry_pools(max_age_days):
    if row.chain_id is None or row.address is None:
        continue  # already skipped at insert, defensive
    key = (row.chain_id, row.address.lower())
    lookup[key] = XerberusPoolLookupRow(..., source="registry", score=row.score, ...)

# 2. Vault-list fills gaps only
for row in latest_vault_list(max_age_days):
    key = (row.chain_id, row.address.lower())
    existing = lookup.get(key)
    if existing is None:
        lookup[key] = XerberusPoolLookupRow(..., source="vault_list", ...)
    elif existing["score"] is None and row.score is not None:
        # Registry had a row but null score — promote vault_list score
        lookup[key] = XerberusPoolLookupRow(..., source="vault_list", ...)
    # else: registry non-null wins; leave existing

return lookup
```

Rules in one line:

1. Registry row with **non-null** score always wins.
2. Vault list is used only when the key is **missing** or registry score is
   **null**.
3. Apply max-age when selecting "latest" rows so resolvers stay pure.
4. Optionally join `report_urls` by `(chain_id, address)` into the row.

### F. Other export entrypoints

| Call site | Change |
|-----------|--------|
| `eth_defi/vault/top_vaults_json.py` | Open Xerberus DB read-only; pass both maps; top-level keys |
| `scripts/erc-4626/post-process-prices.py` | Pass `XERBERUS_DATABASE_PATH` into exporter if applicable |
| `eth_defi/vault/sample_export.py` | Filter `xerberus_protocols` |
| Notebooks / `vault_metrics` `__main__` | Optional Xerberus kwargs default `None` |
| Sticky export stats | Optional missing Xerberus protocol count (mirror Core3 sticky stats if useful) |

### G. Example final JSON shape

```json
{
  "generated_at": "2026-07-25T12:00:00",
  "metadata": { "version": { "...": "..." } },
  "core3_protocols": {
    "morpho": { "slug": "morpho", "pol": { "score": 12.3, "rating": "AA" }, "...": "..." }
  },
  "xerberus_protocols": {
    "morpho": {
      "protocol_slug": "morpho",
      "entity_id": "proto_…",
      "name": "Morpho",
      "score": 84,
      "score_scale": "0_100_higher_is_better",
      "fetched_at": "2026-07-25T11:00:00"
    }
  },
  "xerberus_stats": {
    "total_vaults": 1000,
    "pool_matches": 120,
    "protocol_fallbacks": 400,
    "unmatched": 480,
    "coverage_pct": 52.0
  },
  "curators": { "...": "..." },
  "vaults": [
    {
      "chain": 1,
      "address": "0xabc…",
      "protocol_slug": "morpho",
      "core3": { "risk_score": 12.3, "risk_rating_label": "AA", "...": "..." },
      "xerberus": {
        "score": 61,
        "score_scale": "0_100_higher_is_better",
        "entity_type": "pool",
        "entity_id": "pool_…",
        "name": "Some Morpho Vault",
        "report_url": "https://app.xerberus.io/pool/dendrogram/pool_…",
        "fetched_at": "2026-07-25T11:00:00"
      }
    }
  ]
}
```

Two Morpho vaults in the same export may share the same `core3` summary and
the same `xerberus_protocols["morpho"]` entry, but have **different**
`vault.xerberus.score` values when both pools are scored.

---

## Package layout

```
eth_defi/xerberus/
  __init__.py
  README-xerberus.md
  constants.py
  errors.py
  session.py
  api.py
  database.py
  scanner.py
  mappings.py
  vault_export.py          # TypedDicts + preload + resolve + build_*_for_export

scripts/xerberus/
  scan-xerberus.py
  backfill-xerberus.py
  backfill-xerberus-reports.py
  xerberus-overview.py
  update-xerberus-mappings.py

tests/xerberus/
  test_xerberus_database.py
  test_xerberus_api_envelope.py
  test_xerberus_scanner.py
  test_vault_export.py

tests/vault/
  test_scan_all_chains_xerberus.py
  (extend test_sample_export.py for xerberus_protocols filter)

docs/source/api/xerberus/index.rst
```

Paths:

| File | Path |
|------|------|
| DuckDB | `~/.tradingstrategy/vaults/xerberus/xerberus.duckdb` |
| Rate-limit SQLite | `~/.tradingstrategy/vaults/xerberus/rate-limit.sqlite` |

### Environment variables

| Variable | Required | Notes |
|----------|----------|-------|
| `XERBERUS_API_KEY` | for scan | **Available** in operator env |
| `XERBERUS_API_EMAIL` | for scan | Alias `XERBERUS_USER_EMAIL` |
| `XERBERUS_DATABASE_PATH` | no | Default path above |
| `SKIP_XERBERUS` | no | `false`; default-on when both credentials set |
| `XERBERUS_FETCH_VAULT_LIST` | no | `true` |
| `XERBERUS_FETCH_REPORTS` | no | `false` |
| `XERBERUS_REPORT_LIMIT` | no | `50` |
| `XERBERUS_REQUESTS_PER_MINUTE` | no | `8` |
| `XERBERUS_MIN_REQUEST_INTERVAL_SECONDS` | no | `7.5` |
| `XERBERUS_MAX_SCORE_AGE_DAYS` | no | `30` |
| `LOG_LEVEL` | no | `warning` |

`resolve_xerberus_database_path()` mirrors `resolve_core3_database_path()`.

### `constants.py` module contents

```python
XERBERUS_API_URL: str = "https://api.xerberus.io/public/v1"
XERBERUS_DATABASE_PATH: Path = Path("~/.tradingstrategy/vaults/xerberus/xerberus.duckdb").expanduser()
XERBERUS_RATE_LIMIT_SQLITE_DATABASE: Path = Path(
    "~/.tradingstrategy/vaults/xerberus/rate-limit.sqlite"
).expanduser()
XERBERUS_USER_AGENT: str = "eth-defi/xerberus"
XERBERUS_SCORE_SCALE: str = "0_100_higher_is_better"
XERBERUS_VAULT_PLATFORMS: tuple[str, ...] = ("ipor", "morpho", "spark", "t3tris")
XERBERUS_DEFAULT_REQUESTS_PER_MINUTE: float = 8.0
XERBERUS_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS: float = 7.5
XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS: int = 30
XERBERUS_DEFAULT_TIMEOUT: float = 60.0
XERBERUS_DEFAULT_REPORT_LIMIT: int = 50
```

Also export `resolve_xerberus_database_path()` from this module.

## DuckDB schema

Core3 patterns:

- No PRIMARY KEY / UNIQUE (ART crash workaround)
- DELETE + INSERT dedupe
- `VARCHAR USING COMPRESSION 'zstd'` for payloads
- Connect with `storage_compatibility_version="latest"`
- `wal_autocheckpoint = '1TB'`, `save()`, `threading.Lock`
- Naive UTC datetimes
- Export opens with `read_only=True` when possible

### Tables

#### `registry_snapshots`

| Column | Type |
|--------|------|
| `entity_type` | VARCHAR |
| `entity_id` | VARCHAR |
| `name` | VARCHAR |
| `chain` | VARCHAR nullable |
| `address` | VARCHAR lowercased nullable |
| `chain_id` | INTEGER nullable |
| `score` | DOUBLE nullable |
| `fetched_at` | TIMESTAMP |
| `payload` | VARCHAR USING COMPRESSION 'zstd' |

Skip insert if pool row lacks `chain_id` or `address` (log warning).

#### `vault_list_snapshots`

Platform-scoped rows from `/vault/list`. Used only as **export fallback** when
registry pool is missing or has a null score (see pool-map merge pseudocode).

| Column | Type | Notes |
|--------|------|-------|
| `platform` | VARCHAR | `ipor`, `morpho`, `spark`, `t3tris` |
| `vault_id` | VARCHAR | Xerberus vault id (e.g. `t3tris-gami-usdc-…`) |
| `name` | VARCHAR | Display name; never a join key |
| `chain` | VARCHAR | Network slug |
| `address` | VARCHAR | **Lowercased** EVM address |
| `chain_id` | INTEGER | EIP-155 |
| `score` | DOUBLE nullable | 0–100 or null |
| `fetched_at` | TIMESTAMP | Naive UTC poll time |
| `payload` | VARCHAR USING COMPRESSION 'zstd' | Full list-item JSON |

Skip insert if `chain_id` or `address` missing (log warning). Dedupe latest by
`(platform, chain_id, address)` or `(chain_id, address)` with max `fetched_at`.

#### `report_urls`

| Column | Notes |
|--------|-------|
| `chain_id` | Required — CREATE3 safe |
| `address` | Lowercased |
| `entity_id` | When known |
| `report_url` | |
| `fetched_at` | |
| `error` | null on success |

Prefer verified app URL
`https://app.xerberus.io/pool/dendrogram/{entity_id}` (GitBook already returns
this form); Phase 0 smoke as regression check.

#### `score_daily`

Registry-entity history only (`entity_type`, `entity_id`, `day`, `score`, …).

**Residual risk (documented):** vault_list-only scores can appear on vault rows
without a `score_daily` series. Do not invent registry history for list-only
ids unless product later defines a stable key strategy. Test: vault_list-only
export works; no spurious registry daily row.

Mutation: DELETE + INSERT for `(entity_type, entity_id, day)`.

#### `sync_state`

`data_type`, `last_synced`, `meta`.

### Query helpers

- Latest entities / pool / protocol / vault_list with max-age
- Batch insert registry
- Upsert score_daily
- Report URL by `(chain_id, address)`
- Address lowercasing inside helpers

## Session and API layer

### Errors

```python
class XerberusAPIError(RuntimeError): ...
```

Handle **three** error body shapes from setup docs:

1. Auth middleware: `{ "error": "Missing Email or API key" }` (no `status`)
2. Validation: `{ "status": "error", "message": "..." }`
3. Application: `{ "status": "error", "error": { "message", "code" } }`

### Session

- Headers: key, email, `User-Agent: eth-defi/xerberus`
- `LoggingRetry` on 429/5xx, `respect_retry_after_header=True`
- Rate limit recipe:
  - Serial authenticated calls only
  - Min interval default 7.5 s (pace)
  - Minute bucket ≤8 (cap)
  - Healthchecks: unauthenticated, **not** counted against limiter
- Credentials from env; assert both present

### API helpers

```text
fetch_registry_scores(...)
fetch_vault_list(...)
fetch_vault_report_url(...)  # 404 → None
fetch_healthcheck(session, group: str = "registry") -> str
  # GET {api_url}/{group}/healthcheck — raw text, no JSON unwrap, no rate limit
```

Concrete pre-flight URL (primary):

```text
GET https://api.xerberus.io/public/v1/registry/healthcheck
→ plain text e.g. "Hello from Registry!"
```

Optional secondary: `.../vault/healthcheck`. Scanner uses `group="registry"`
by default. JSON unwrap only for authenticated JSON endpoints.

## Scanner flow

1. Soft pre-flight: `fetch_healthcheck(session, "registry")` — log warning on
   failure; do not abort the all-chains pipeline solely on healthcheck miss
2. Open DB (read-write)
3. Registry `type=pool,protocol` → snapshots + score_daily
4. Optional vault lists (paced)
5. Optional capped reports
6. `sync_state`, `save()`, return db — **caller closes**

### Lifecycle / concurrency (Core3 parity)

- `scan_xerberus_fn`: `try/finally: db.close()` (test like
  `test_scan_core3_fn_closes_database`)
- All-chains: Xerberus enrichment runs in the same enrichment phase as Core3,
  **before** post-process / top-vaults JSON / R2
- Export always opens DB **after** scan tick has closed it
- Operator note in README: do not run standalone `scan-xerberus.py` concurrent
  with export/R2 (DuckDB single-writer)
- Dual-writer residual risk documented

## Protocol mappings

```python
XERBERUS_PROTOCOL_MAPPINGS: dict[str, str | None]
XERBERUS_PROTOCOL_MAPPINGS_REVERSE: dict[str, str]
```

Pools need no mapping. Heuristic updater script after first scan; manual confirm.

## Pipeline integration

### `scan_all_chains.py`

- `XERBERUS_PROTOCOL_NAME = "Xerberus"`
- `should_scan_xerberus(skip, api_key, api_email)`
- `scan_xerberus_fn` → `ChainResult`, always closes DB
- Document env in `scan-vaults-all-chains.py` header
- Example cycle string: include `Xerberus=24h` next to Core3 in docs /
  `SCAN_CYCLES` examples (operators set cycles explicitly like Core3)

### docker-compose

Pass `XERBERUS_API_KEY` and `XERBERUS_API_EMAIL` on **both** looped and oneshot
services (mirror `CORE3_API_KEY` placement).

### R2 / data files

Extend `eth_defi.vault.data_file_export.get_data_file_paths()` to include
`xerberus.duckdb` when present (not only the thin CLI wrapper).

### Production env

```bash
XERBERUS_API_KEY=...    # available
XERBERUS_API_EMAIL=...
# SKIP_XERBERUS=false
# SCAN_CYCLES=...Core3=24h,Xerberus=24h...
```

## Manual scripts and backfill

Documented in `README-xerberus.md` and cross-linked from
`scripts/erc-4626/README-vault-scripts.md`.

### scan-xerberus.py

Full poll (same as scheduled tick).

```shell
source .local-test.env && poetry run python scripts/xerberus/scan-xerberus.py
```

### backfill-xerberus.py

Operator bootstrap/repair. "Backfill" = full current snapshot refresh (no API
history). Supports `DRY_RUN`, alternate `XERBERUS_DATABASE_PATH`.

```shell
source .local-test.env && poetry run python scripts/xerberus/backfill-xerberus.py
source .local-test.env && DRY_RUN=true poetry run python scripts/xerberus/backfill-xerberus.py
```

### backfill-xerberus-reports.py

Capped report URL fill (`XERBERUS_REPORT_LIMIT`).

### xerberus-overview.py / update-xerberus-mappings.py

Inspector + mapping candidates report.

If `scan-xerberus` and `backfill-xerberus` stay thin wrappers over the same
`scan_xerberus()`, keep both only because backfill owns DRY_RUN + repair docs;
otherwise one entrypoint with flags is acceptable.

## README-xerberus.md (required Phase 1 deliverable)

Mirror `README-core3.md`:

1. Intro + vs Core3 (per-vault scores; polarity)
2. Modules table
3. Database files + no history rebuild warning
4. Tables + no-PK / Zstd / storage latest
5. All scripts with env tables
6. Tests
7. API overview, auth, rate limit
8. Key concepts + hard ban on score mixing
9. Endpoints with examples
10. Error shapes + healthcheck plain text
11. **Export contract**: per-vault `xerberus` via `calculate_lifetime_metrics`,
    top-level `xerberus_protocols`, lookup order, max-age, preload maps
12. Production: cycles, compose, R2, concurrency note
13. Residual risks (vault_list-only history, dual-writer)

## Tests

| Test | Coverage |
|------|----------|
| DB offline | insert, case fold, score_daily idempotence, max-age, report chain_id key, skip bad pool rows |
| API envelope | success; three error shapes; healthcheck text path |
| Scanner live | registry + one platform; close DB; skip without key |
| **vault_export** | preload map; **per-vault different scores for same protocol**; protocol fallback; max-age; `score_scale`; reverse map |
| **vault_metrics / lifetime** | `calculate_vault_record` / `calculate_lifetime_metrics` attach `xerberus` from pool map; protocol fallback; None when empty |
| sample export | filters `xerberus_protocols` |
| scan_all_chains | should_scan; missing key warning; wrapper closes DB |

```shell
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" \
  poetry run pytest tests/xerberus/ tests/vault/test_scan_all_chains_xerberus.py \
  tests/vault/test_sample_export.py -v --timeout=300
```

## Implementation phases

### Phase 0 — smoke (key available)

```bash
source .local-test.env

# Healthcheck (plain text, unauthenticated) — confirm concrete path works
curl -sS "https://api.xerberus.io/public/v1/registry/healthcheck"
# expect something like: Hello from Registry!

# Optional vault group healthcheck
curl -sS "https://api.xerberus.io/public/v1/vault/healthcheck"

# Authenticated registry pull
curl -sS -H "x-api-key: $XERBERUS_API_KEY" -H "x-user-email: $XERBERUS_API_EMAIL" \
  "https://api.xerberus.io/public/v1/registry/scores?type=pool,protocol" | head -c 2000
```

Confirm:

1. Healthcheck path returns 200 plain text (`/registry/healthcheck`).
2. Registry payload size / entity counts.
3. Score polarity on known names (Morpho, Aave).
4. Report URL form matches `https://app.xerberus.io/pool/dendrogram/{entity_id}`.

### Phase 1 — library + DuckDB + scripts + README

Package, scanner, backfill scripts, offline tests, full README-xerberus.md,
vault-scripts cross-link. No metrics export yet.

### Phase 2 — export (product surface)

1. `mappings.py` + mapping updater
2. `vault_export.py`: preload, resolve, TypedDicts, `build_xerberus_protocols_for_export`
3. `vault_metrics.py`: `VaultMetricsRecord.xerberus`,
   `calculate_lifetime_metrics(..., xerberus_pools=, xerberus_protocols=)`,
   `calculate_vault_record` attachment, CSV strip
4. `VaultMetricsExport.xerberus_protocols` (+ optional stats)
5. `top_vaults_json.py` read-only open + wire
6. `sample_export.py` filter
7. Export + lifetime metrics tests (two vaults same protocol, different pool scores)

### Phase 3 — pipeline packaging

1. `scan_all_chains` + compose env both services + `SCAN_CYCLES` docs
2. `get_data_file_paths()` for R2
3. Report backfill script
4. Sphinx + CHANGELOG + CLAUDE.md table

### Phase 4 — optional

MCP depth, broader report fill, organisation/asset.

## Open questions

1. Score polarity confirmation (Phase 0)
2. Registry payload size — start `pool,protocol`
3. Whether `xerberus_stats` is required in public JSON or operator-only (default: include)

## Non-goals (v1)

- MCP in production scanner
- Replacing Core3
- Reconstructing multi-year history from API
- Parallel high-RPS HTTP
- Blended Core3+Xerberus ranks
- Top-level dump of all pools (use per-vault sections instead)

## Risk and failure modes

| Risk | Mitigation |
|------|------------|
| 429 | Pace + cap + retry |
| Missing credentials | Off + warning + test |
| Low coverage | Protocol fallback + stats + null section |
| Score confusion | Sibling keys + score_scale + ban |
| CREATE3 | `(chain_id, address)` |
| Stale data | Max-age |
| Dual-writer DuckDB | Close-before-export; read-only export; README note |
| Lost DB history | Document; re-run backfill for current only |
| vault_list-only no score_daily | Residual risk documented |

## Success criteria

1. DuckDB populated; scan + backfill work with `XERBERUS_API_KEY`.
2. `README-xerberus.md` at Core3 quality including export contract.
3. **Each vault row** may carry distinct `xerberus` pool scores via
   `calculate_lifetime_metrics`.
4. **Top-level** `xerberus_protocols` present in top-vaults JSON, parallel to
   `core3_protocols`, filtered to exported protocols.
5. Core3 fields unchanged; no cross-score arithmetic.
6. All-chains refreshes when credentials set; closes DB; export is read-only.
7. Tests cover case fold, precedence, dual vault scores same protocol,
   sample filter, credential skip.

## Suggested PR split

1. **feat: Xerberus DuckDB client, scanner, backfill, README**
2. **feat: Xerberus vault metrics export** — lifetime metrics per-vault +
   top-level `xerberus_protocols`
3. **feat: Xerberus all-chain cycle and packaging**

Commentary format on each PR.

## Code review checklist

- [ ] Per-vault `xerberus` via `calculate_lifetime_metrics` / `calculate_vault_record`
- [ ] Preloaded pool map — no per-vault DuckDB query
- [ ] Pool-map merge: registry non-null wins; vault_list fills gaps only
- [ ] Formal TypedDicts: PoolLookupRow, VaultSection, ProtocolExportRecord, ExportStats
- [ ] Two vaults same protocol can differ on `xerberus.score`
- [ ] Top-level `xerberus_protocols` in `VaultMetricsExport` / `top_vaults_json`
- [ ] Filtered to exported protocol slugs
- [ ] `sample_export` filters `xerberus_protocols`
- [ ] Lookup: registry pool → vault_list → protocol
- [ ] Max-age at export (`XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS = 30`)
- [ ] Full `vault_list_snapshots` column schema
- [ ] Address lowercasing
- [ ] `report_urls` has `chain_id`
- [ ] score_daily DELETE+INSERT; registry-only history residual noted
- [ ] Forward + reverse protocol maps
- [ ] `score_scale` always set
- [ ] No Core3/Xerberus arithmetic
- [ ] Error shapes + `GET /registry/healthcheck` plain text
- [ ] Paced + minute rate limit
- [ ] scan closes DB; export read-only
- [ ] compose + `get_data_file_paths` + SCAN_CYCLES docs
- [ ] `storage_compatibility_version=latest`
- [ ] `XERBERUS_API_KEY` documented as available
- [ ] README-xerberus complete
- [ ] Manual backfill scripts documented

## Reference files

- `eth_defi/core3/*` — session, database, scanner, vault_protocol, README
- `eth_defi/research/vault_metrics.py` — `VaultMetricsRecord`,
  `VaultMetricsExport`, `calculate_vault_record`, `calculate_lifetime_metrics`,
  `_del("core3")`
- `eth_defi/vault/top_vaults_json.py` — Core3 open/build/filter/export
- `eth_defi/vault/sample_export.py` — core3_protocols filter
- `eth_defi/vault/scan_all_chains.py` — should_scan / scan_fn / close
- `eth_defi/vault/data_file_export.py` — `get_data_file_paths`
- `scripts/core3/scan-core3.py`
- `scripts/erc-4626/README-vault-scripts.md`
- `tests/vault/test_scan_all_chains_core3.py`
- `tests/vault/test_sample_export.py`
- `tests/core3/*`
