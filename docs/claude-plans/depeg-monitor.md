# Stablecoin depeg monitor implementation plan

Date: 2026-06-26

## Goal

Filter out stablecoins, and vaults denominated in those stablecoins, when the
denomination token has materially depegged.

The system will refresh stablecoin exchange rates from the CoinGecko free API,
persist the latest USD rate in `eth_defi/data/stablecoins/*.yaml`, stamp a
manual-clearable `depegged_at` flag when the token trades below 90% of its
nominal peg, and mark all affected vaults as
`VaultTechnicalRisk.blacklisted`.

The daily refresh runs as part of the existing vault post scan process in
`scripts/erc-4626/scan-vault-posts.py`.

## Starting context

- Feed collection is documented in `eth_defi/feed/README-feed.md`.
- Stablecoin metadata is stored in `eth_defi/data/stablecoins/*.yaml` and
  loaded/exported by `eth_defi/stablecoin_metadata.py`.
- Stablecoin YAML files already store `links.coingecko`, but this URL is not
  authoritative enough for rate fetching. The Kava USDX metadata currently
  points to CoinGecko's `kava-lend` page, while the actual Kava USDX price is
  served from CoinGecko id `usdx`.
- Store both `coingecko_id` and `coingecko_link` directly on the stablecoin
  metadata entry so the fetch key and the human-auditable CoinGecko page are
  explicit and do not depend on the generic `links` block.
- Vault blacklisting already uses `VaultTechnicalRisk.blacklisted` in
  `eth_defi/vault/risk.py` and dynamic export checks in
  `eth_defi/research/vault_metrics.py`.
- The post scan process already mutates YAML source files for operational state
  such as dead Twitter and RSS source markers.
- CoinGecko documents the keyless public base URL as
  `https://api.coingecko.com/api/v3/`, and the `/simple/price` endpoint can
  query multiple coin ids with `vs_currencies` and
  `include_last_updated_at=true`.

## YAML schema

Add these fields to every stablecoin metadata entry:

```yaml
coingecko_id: usd-coin
coingecko_link: https://www.coingecko.com/en/coins/usd-coin
coingecko_id_source: url
coingecko_id_verified_at: '2026-06-26T00:00:00'
usd_rate: 0.9998
usd_rate_fetched_at: '2026-06-26T00:00:00'
usd_rate_updated_at: '2026-06-26T00:00:00'
peg_rate: 0.9998
peg_rate_currency: usd
rate_fetch_failed_at: ''
rate_fetch_failed_reason: ''
depegged_at: ''
```

Rules:

- For standard files, fields live at the top level, near `checks`.
- For `entries:` files, fields live inside each entry.
- Stablecoin YAML is currently loaded schemaless with `strictyaml.load()`.
  Treat all YAML scalar values as strings on read. The new module must coerce
  floats and datetimes explicitly when building `StablecoinRateTarget`, and the
  JSON exporter must coerce numeric rate fields to JSON numbers rather than
  passing through YAML strings.
- `coingecko_id` is the canonical CoinGecko API coin id used for
  `/simple/price` calls. Prefer this explicit field over parsing
  `coingecko_link` or `links.coingecko`.
- `coingecko_link` is the canonical human-readable CoinGecko page URL for the
  same asset. It should normally be
  `https://www.coingecko.com/en/coins/{coingecko_id}`. Persist it even when the
  legacy `links.coingecko` field is also present.
- `coingecko_id_source` records how the id was selected. Suggested values:
  `manual`, `url`, and `search`. Use `manual` for ambiguous symbols such as
  USDX, where several CoinGecko assets share the same ticker.
- `coingecko_id_verified_at` is a naive UTC ISO timestamp with seconds
  precision recording when the id last returned a valid CoinGecko price, or an
  empty string when never verified.
- `usd_rate` is a float, or empty string when never fetched.
- `usd_rate_fetched_at` is a naive UTC ISO timestamp with seconds precision
  recording when our updater fetched and wrote the rate, or empty string when
  never fetched. This is the daily gating timestamp and must always be based on
  our `now_`, not CoinGecko's upstream update timestamp.
- `usd_rate_updated_at` is a naive UTC ISO timestamp with seconds precision
  recording CoinGecko's upstream `last_updated_at` for the USD rate when
  present, or empty string when CoinGecko did not provide it.
- `peg_rate` is the price in the denomination's peg currency that was used for
  the depeg decision, or empty string when the peg currency is unknown.
- `peg_rate_currency` is the CoinGecko `vs_currencies` key used for `peg_rate`
  (`usd`, `eur`, `chf`, `xau`, etc.), or empty string when unknown.
- `rate_fetch_failed_at` is a naive UTC ISO timestamp with seconds precision
  recording when the latest rate refresh failed for this entry, or empty string
  when the latest refresh succeeded or has never been attempted.
- `rate_fetch_failed_reason` is a short machine-readable reason for the latest
  failed refresh, or empty string when there is no active failure. Suggested
  values: `missing_coingecko_id`, `invalid_coingecko_id`,
  `coingecko_price_missing`, `coingecko_http_error`, `coingecko_response_error`.
- `depegged_at` is a naive UTC ISO timestamp with seconds precision, or empty
  string when not flagged.
- The updater sets `depegged_at` only when the current rate is below the
  threshold.
- The updater never clears `depegged_at`; operators clear the flag manually by
  editing the YAML file.
- If an operator clears `depegged_at` while the token is still below threshold,
  the next daily refresh stamps it again.
- The updater clears `rate_fetch_failed_at` and `rate_fetch_failed_reason` on a
  successful rate refresh for that entry.
- If no CoinGecko price is available for an entry, the updater stamps
  `rate_fetch_failed_at` and `rate_fetch_failed_reason` but does not stamp
  `depegged_at` and does not blacklist vaults.
- If `coingecko_id`, `coingecko_link`, and `links.coingecko` disagree, use
  `coingecko_id` for the fetch and log a warning. The URLs can be corrected by
  a later metadata cleanup, but an incorrect URL must not cause the rate updater
  to fetch the wrong asset.
- When a parsed URL id or manually selected id successfully returns a price,
  update `coingecko_id_verified_at` to the fetch timestamp. For newly parsed URL
  ids, also persist `coingecko_id`, `coingecko_link`, and
  `coingecko_id_source: url`.
- Do not persist volatile CoinGecko market-cap or 24-hour-volume fields from
  the probe response. They are useful for manual validation but are not needed
  for the daily fetch, depeg decision, or vault blacklisting.
- Daily gating applies to both successful and failed attempts. Skip an entry
  when either `usd_rate_fetched_at` or `rate_fetch_failed_at` is already the
  current UTC date, unless `force=True`. This prevents missing CoinGecko ids,
  delisted assets, and transient HTTP failures from being retried on every
  8-hour post scan loop.
- Daily gating reads raw YAML strings and parses the stored ISO date prefix.
  It must not depend on metadata JSON export normalisation, where empty strings
  become `None`.
- Preserve existing YAML ordering and formatting as much as practical. Use a
  concrete round-trip mutation mechanism, not trailing append helpers. Prefer
  `ruamel.yaml` round-trip editing with preserved order/comments, or implement
  an entry-index-keyed block editor that can update and clear fields inside the
  correct `entries:` item. If `ruamel.yaml` is added, add it as an explicit
  `pyproject.toml` dependency with a comment explaining that stablecoin rate
  YAML mutation needs round-trip formatting preservation.

## Depeg threshold

Use `DEPEG_THRESHOLD = 0.90`.

Use `MIN_PLAUSIBLE_STABLECOIN_RATE = 0.01` as a wrong-asset guard before the
depeg comparison for URL-derived or unverified CoinGecko ids. For those ids, a
positive `peg_rate` below this floor is treated as a data-quality failure with
`rate_fetch_failed_reason: coingecko_price_missing`, not as a depeg. Explicitly
manual and verified `coingecko_id` values may still stamp a sub-cent
catastrophic depeg. This keeps stale metadata such as the Kava USDX
`kava-lend` link (`0.00049975`) from silently blacklisting vaults, while still
allowing a real manually verified depeg to stamp `depegged_at`.

The persisted `usd_rate` is always the token price in USD. The depeg comparison
should use the nominal peg currency when it is known:

- USD-pegged tokens: compare CoinGecko `usd` price against `0.90`.
- EUR/CHF/SGD/TRY and other recognised fiat-pegged tokens: request both `usd`
  and the peg currency from CoinGecko, persist `usd_rate`, and compare the peg
  currency price against `0.90`.
- XAU/gold-pegged tokens: compare against `xau` when a conservative mapping is
  available. This assumes troy-ounce pegs such as PAXG; do not map gram-pegged
  gold tokens without a separate peg-unit rule.
- Unknown or ambiguous peg targets: refresh and persist `usd_rate`, but do not
  stamp `depegged_at`. Log a warning so the stablecoin can be annotated later.

Add a conservative symbol/name based peg-currency map in the new module. Avoid
guessing when a wrong guess would blacklist vaults incorrectly.

Persist `peg_rate` and `peg_rate_currency` alongside `usd_rate` so an operator
can audit why a non-USD stablecoin was or was not flagged. Do not use
`usd_rate_updated_at` or any other upstream timestamp for daily refresh gating;
CoinGecko timestamps can lag and would otherwise cause repeated fetches in every
post scan cycle.

## Kava USDX CoinGecko probe

The Trading Strategy Kava USDX page
(`https://tradingstrategy.ai/trading-view/vaults/stablecoins/usdx`) resolves to
the second entry in `eth_defi/data/stablecoins/usdx.yaml`.

Probe findings on 2026-06-26:

- The current local Kava USDX entry has
  `links.coingecko: https://www.coingecko.com/en/coins/kava-lend`.
- CoinGecko `/simple/price` for `kava-lend` returned a HARD/Kava Lend price:
  `usd: 0.00049975`, `last_updated_at: 1782199898`
  (`2026-06-23T07:31:38` after UTC conversion).
- CoinGecko `/simple/price` for `usdx` returned the Kava USDX price:
  `usd: 0.646809`, `usd_market_cap: 72165490.51902083`,
  `usd_24h_vol: 11420.442904345602`,
  `last_updated_at: 1782464168`
  (`2026-06-26T08:56:08` after UTC conversion).
- CoinGecko search for `USDX` returned both `usdx` for Kava USDX and
  `usdx-money-usdx` for Stables Labs USDX, proving symbol-based automatic
  resolution is ambiguous.

Plan impact:

- Add `coingecko_id: usdx`,
  `coingecko_link: https://www.coingecko.com/en/coins/usdx`,
  `coingecko_id_source: manual`, and a verified timestamp to the Kava USDX
  entry when implementing the YAML migration.
- Add `contract_addresses` to the Kava USDX entry during the migration before
  relying on vault blacklisting. The current `usdx.yaml` has no
  `contract_addresses`, and symbol fallback is intentionally disabled for
  multi-entry files, so a depegged USDX entry without addresses can be stamped
  but cannot blacklist any vault.
- Correct the legacy Kava USDX `links.coingecko` URL to
  `https://www.coingecko.com/en/coins/usdx` during metadata cleanup, or stop
  exporting the legacy field if `coingecko_link` supersedes it.
- Use `coingecko_id: usdx-money-usdx` for the Stables Labs USDX entry if it is
  kept fetchable, with
  `coingecko_link: https://www.coingecko.com/en/coins/usdx-money-usdx`. The
  current URL slug `stables-labs-usdx` did not return a `/simple/price` result
  in the probe and would otherwise produce
  `rate_fetch_failed_reason: coingecko_price_missing`.
- Never auto-select a CoinGecko id from search results when multiple plausible
  matches share the same symbol; require a stored `coingecko_id` instead.

## New module

Create `eth_defi/feed/stablecoin_rate.py`.

Proposed API:

```python
@dataclass(slots=True)
class StablecoinRateTarget:
    yaml_path: Path
    entry_index: int | None
    slug: str
    symbol: str
    name: str
    coingecko_id: str | None
    coingecko_link: str | None
    coingecko_id_source: str | None
    coingecko_id_verified_at: datetime.datetime | None
    peg_currency: str | None
    usd_rate: float | None
    usd_rate_fetched_at: datetime.datetime | None
    usd_rate_updated_at: datetime.datetime | None
    peg_rate: float | None
    peg_rate_currency: str | None
    rate_fetch_failed_at: datetime.datetime | None
    rate_fetch_failed_reason: str | None
    depegged_at: datetime.datetime | None


@dataclass(slots=True)
class StablecoinRateRefreshSummary:
    files_scanned: int
    entries_seen: int
    rates_fetched: int
    files_updated: int
    depegged_count: int
    unactionable_depegged_count: int
    skipped_missing_coingecko: int
    skipped_unknown_peg: int
    failed_count: int
```

Add a small section dataclass for vault metric rows:

```python
@dataclass(slots=True)
class DenominationTokenRate:
    coingecko_id: str | None
    usd_rate: float | None
    usd_rate_fetched_at: datetime.datetime | None
    usd_rate_source: str | None


@dataclass(slots=True)
class StablecoinRateFeeder:
    data_dir: Path = STABLECOINS_DATA_DIR
    _depegged_contracts: set[tuple[int, str]] | None = field(default=None, init=False, repr=False)
    _depegged_symbols: set[str] | None = field(default=None, init=False, repr=False)
    _rate_contracts: dict[tuple[int, str], DenominationTokenRate] | None = field(default=None, init=False, repr=False)
    _rate_symbols: dict[str, DenominationTokenRate] | None = field(default=None, init=False, repr=False)

    def get_denomination_token_rate_section(
        self,
        chain_id: int | None,
        address: HexAddress | None,
        symbol: str | None,
    ) -> DenominationTokenRate:
        ...

    def is_depegged_stablecoin_token(
        self,
        chain_id: int | None,
        address: HexAddress | None,
        symbol: str | None,
    ) -> bool:
        ...
```

Functions:

- `extract_coingecko_id(url: str) -> str | None`
- `resolve_coingecko_metadata(entry: StablecoinMetadataEntry) -> tuple[str | None, str | None, str | None]`
- `iter_stablecoin_rate_targets(data_dir: Path) -> Iterator[StablecoinRateTarget]`
- `fetch_stablecoin_rates(targets: Sequence[StablecoinRateTarget], timeout: float) -> dict[str, dict[str, float | int]]`
- `refresh_stablecoin_rates(data_dir: Path = STABLECOINS_DATA_DIR, now_: datetime.datetime | None = None, force: bool = False, timeout: float = 20.0) -> StablecoinRateRefreshSummary`
- `apply_coingecko_mapping_file(data_dir: Path, mapping_path: Path) -> int`

`vault_metrics.py` should use `StablecoinRateFeeder` as its dependency
boundary. Production and hot-path code must share one feeder instance across a
batch so YAML paths, cache invalidation, chain alias handling, and future
data-source changes stay out of the vault metrics calculation code.

`DenominationTokenRate` and `StablecoinRateFeeder` live in
`eth_defi/feed/stablecoin_rate.py`. `eth_defi/research/vault_metrics.py`
imports the dataclass and feeder from that module, and separately extends its
own `VaultMetricsRecord` `TypedDict` with the `denomination_token_rate` field.
Keep the dataclass fields and the `TypedDict` field type in sync.

Implementation notes:

- Use a normal browser-like `User-Agent`. Keep the HTTP client surface
  monkeypatchable as `stablecoin_rate.requests.get`, but avoid adding a new
  dependency if the standard-library fallback is enough for keyless GETs.
- Use the keyless endpoint by default:
  `https://api.coingecko.com/api/v3/simple/price`.
- Support optional `COINGECKO_DEMO_API_KEY`; when present, send
  `x-cg-demo-api-key`. Because neither `refresh_stablecoin_rates()` nor
  `PostScanConfig` carries this key, `eth_defi.feed.stablecoin_rate` should
  read `os.environ["COINGECKO_DEMO_API_KEY"]` directly.
- Request prices in batches. The documented `/simple/price` ids limit is high
  enough for the current stablecoin set, but keep batching to avoid future
  surprises.
- Request `include_last_updated_at=true`. CoinGecko returns
  `last_updated_at` as a Unix epoch integer; convert it with
  `native_datetime_utc_fromtimestamp()` and persist the resulting naive UTC ISO
  timestamp as `usd_rate_updated_at`. Fall back to an empty string for this
  upstream timestamp; `usd_rate_fetched_at` remains our fetch time.
- Always request `usd` plus the peg currency for non-USD pegs. If CoinGecko
  does not support the peg currency key, persist `usd_rate`, leave
  `depegged_at` empty, increment `skipped_unknown_peg`, and log a warning
  instead of crashing.
- Prefer the stored `coingecko_id` field. Fall back to parsing
  `coingecko_link`, then legacy `links.coingecko`, only when the explicit id is
  empty. Persist successful URL fallback resolution as `coingecko_id`,
  `coingecko_link`, and `coingecko_id_source: url`.
- Do not use CoinGecko search for automatic production resolution. Search is
  acceptable for manual probes and metadata cleanup, but ambiguous symbols such
  as USDX must be resolved by explicitly storing `coingecko_id`.
- Treat HTTP errors, missing ids, missing CoinGecko prices, and malformed
  responses as refresh failures for those entries only. Stamp
  `rate_fetch_failed_at` and `rate_fetch_failed_reason`; do not abort the entire
  post scan cycle.
- `StablecoinRateTarget.coingecko_id` is optional so entries with missing
  CoinGecko metadata can still be counted, stamped with
  `rate_fetch_failed_reason: missing_coingecko_id`, and included in summaries.
- Treat missing, `null`, zero, negative, NaN, or non-finite prices as
  `coingecko_price_missing` failures. Do not stamp `depegged_at` from invalid
  or non-positive price values.
- Apply the `MIN_PLAUSIBLE_STABLECOIN_RATE` wrong-asset guard before the
  depeg threshold comparison only for URL-derived or unverified CoinGecko ids.
  Implausibly tiny positive rates from those ids are `coingecko_price_missing`
  data-quality failures, not depegs. Manual and verified ids may still stamp a
  sub-cent catastrophic depeg.
- Daily gating is file-content based: skip an entry when either
  `usd_rate_fetched_at` or `rate_fetch_failed_at` is already the current UTC
  date, unless `force=True`.
- `refresh_stablecoin_rates()` first builds all targets, then filters out
  targets gated by today's `usd_rate_fetched_at` or `rate_fetch_failed_at`.
  Only the remaining due targets are passed to the batched CoinGecko request.
  Batch network fetches are keyed by unique `coingecko_id`, while the gate,
  result stamping, and YAML writes remain per target entry.
- If CoinGecko returns no price for the selected id, stamp
  `rate_fetch_failed_at` with
  `rate_fetch_failed_reason: coingecko_price_missing` instead of silently
  treating the asset as healthy.
- Use naive UTC datetimes and `native_datetime_utc_now()`.
- Treat `StablecoinRateFeeder` instances as batch-scoped caches. Code that
  refreshes YAML and then needs fresh lookups should construct a new feeder.
- Use module-level `logger = logging.getLogger(__name__)`.

## Stablecoin metadata export

Update `eth_defi/stablecoin_metadata.py`:

- Add `coingecko_id`, `coingecko_link`, `coingecko_id_source`,
  `coingecko_id_verified_at`, `usd_rate`, `usd_rate_fetched_at`,
  `usd_rate_updated_at`, `peg_rate`, `peg_rate_currency`,
  `rate_fetch_failed_at`, `rate_fetch_failed_reason`, and `depegged_at` to the
  documented YAML format.
- Extend the relevant `TypedDict` exports so R2 metadata JSON includes the new
  fields.
- Parse empty strings as `None` in JSON export, following the existing optional
  field convention.
- Coerce `usd_rate` and `peg_rate` from YAML strings to JSON numbers during
  export. Do not let schemaless `strictyaml` parsing leak numeric fields as
  JSON strings.
- Avoid data-directory split-brain. Any code path that exports all stablecoin
  metadata or calls `load_all_stablecoin_metadata()` must be able to read from
  the same directory used by `refresh_stablecoin_rates()` and
  `StablecoinRateFeeder`. Add a shared optional `data_dir:
  Path = STABLECOINS_DATA_DIR` parameter or equivalent helper for the
  all-files loader/export paths, and use it in tests that override
  `stablecoin_data_dir`.
- Put the new fields on `StablecoinMetadata` as top-level fields, not under
  `StablecoinChecks`. They live near `checks` in YAML for readability, but they
  must use the existing `normalise()` empty-string-to-`None` export behaviour;
  `checks` intentionally preserves empty strings.
- Keep backwards compatibility with files that do not yet have the fields.

## Vault blacklisting

Add a dynamic depeg-denomination check in vault metric calculation.

Recommended hook:

- Extend `eth_defi/research/vault_metrics.py::VaultMetricsRecord` with a new
  `denomination_token_rate` section field modelled by the
  `DenominationTokenRate` dataclass.
- `DenominationTokenRate` includes:
  - `coingecko_id`: CoinGecko coin id used for the rate fetch, or `None`
  - `usd_rate`: latest denomination stablecoin USD rate, or `None`
  - `usd_rate_fetched_at`: naive UTC timestamp for when our updater fetched
    the rate, or `None`
  - `usd_rate_source`: string source identifier, initially `coingecko`, or
    `None` when no rate is available
- `calculate_lifetime_metrics()` builds each vault row through
  `calculate_vault_record()`. Add the section in `calculate_vault_record()` so
  every vault row produced by `calculate_lifetime_metrics()` includes the rate
  section.
- Add `stablecoin_rate_feeder: StablecoinRateFeeder | None = None` to both
  `calculate_lifetime_metrics()` and `calculate_vault_record()`, and thread the
  same feeder through the internal `_apply_vault_record()` call.
  `calculate_lifetime_metrics()` should construct one default
  `StablecoinRateFeeder()` when the argument is omitted, then reuse that
  instance for all vault rows in the call.
- Do not pass `stablecoin_data_dir` directly through the vault metrics API.
  Full-stack tests that need temporary USDC/USDX YAML fixtures should construct
  `StablecoinRateFeeder(data_dir=tmp_path)` and pass the feeder to
  `calculate_lifetime_metrics()` or `calculate_vault_record()`.
- In `eth_defi/research/vault_metrics.py`, call the new stablecoin-rate helper
  through `stablecoin_rate_feeder` in `calculate_vault_record()` after `risk`
  and `risk_numeric` have been initialised. Do not insert at the line where
  `normalised_denomination` is first computed, because `risk` does not exist
  yet there.
- Place the final depeg override after the existing abnormal-value,
  Morpho-not-in-API, Morpho red-flag checks, and the later abnormal-volatility
  check so the blacklist state is not clobbered later in the function. Recompute
  `risk_numeric` from the final `risk` unconditionally immediately before the
  returned `pd.Series` is built. Do not only set `risk_numeric` in the depeg
  override; this also fixes the existing abnormal-volatility path where `risk`
  can change after `risk_numeric` was first calculated.
- If `normalised_denomination` resolves to a stablecoin with non-empty
  `depegged_at`, set:
  - `risk = VaultTechnicalRisk.blacklisted`
  - `risk_numeric = VaultTechnicalRisk.blacklisted.value`
  - a clear note such as
    `Denomination stablecoin {symbol} is marked as depegged`
  - a display flag if there is a suitable existing enum; otherwise add a
    focused `VaultFlag.depegged_denomination_token`.
- If `VaultFlag.depegged_denomination_token` is added, also add it to
  `eth_defi/vault/flag.py::BAD_FLAGS` so downstream "do not touch" checks see
  it as a blocking condition.
- Current `vault_display_flags` are built before the planned final depeg
  override. Rebuild or append the display flag after the depeg override so
  `other_data["vault_display_flags"]` includes the depegged denomination flag.
  Use the existing display flag shape:
  `{"severity": "red", "type": "depegged_denomination_token", "source": "stablecoin"}`.
  This can be produced by merging the current Morpho list with
  `make_vault_display_flags(red_flags=["depegged_denomination_token"], yellow_flags=[], source="stablecoin")`.
- Include `denomination_token_rate` in the returned `pd.Series` next to
  `core3` and `other_data`, because it is another compact per-vault enrichment
  section.

Design constraints:

- Do not blacklist a vault merely because CoinGecko is unavailable.
- Still include `denomination_token_rate` for vaults with a known stablecoin
  rate even when the rate is healthy, e.g. USDC. The section value is modelled
  as `DenominationTokenRate`.
- For vaults with no matched stablecoin rate, include
  `DenominationTokenRate(coingecko_id=None, usd_rate=None, usd_rate_fetched_at=None, usd_rate_source=None)`.
  Do not use `None` for the whole section; every row must expose the same
  `denomination_token_rate` dataclass shape.
- Do not blacklist a vault merely because the stablecoin lacks `coingecko_id`,
  `coingecko_link`, or `links.coingecko`.
- Do not blacklist a vault merely because the stablecoin has
  `rate_fetch_failed_at`; missing market data is an operational data-quality
  issue, not proof of a depeg.
- Prefer contract-aware matching: use the vault row's denomination chain and
  `_denomination_token.get("address")` against stablecoin YAML
  `contract_addresses`. In current `vault_metrics.py` the `_denomination_token`
  value is a plain dict, not an object with an `.address` attribute.
  `StablecoinRateFeeder` must read both top-level `contract_addresses` and
  per-entry `contract_addresses` inside `entries:` files.
  Fall back to normalised symbol matching only when the symbol maps to exactly
  one non-entry stablecoin record. Do not symbol-blacklist multi-entry files
  such as `ausd.yaml`, where one `symbol` can represent multiple unrelated
  assets.
- A depegged entry with no contract addresses and an ambiguous or multi-entry
  symbol is unactionable for vault blacklisting. Log a warning and surface a
  summary counter for these entries, because they are rate/depeg metadata but
  cannot safely affect vaults.
- Canonicalise both sides before contract-aware matching by converting YAML
  chain slugs to chain ids with an explicit alias table, then matching
  `(chain_id, lower-case address)`. Do not convert vault `chain_id` to a single
  YAML slug: the current data contains aliases such as `binance`, `bnb`, and
  `bsc` for BNB chain. Do not compare title-case display chain names from
  `get_chain_name()` to YAML chain slugs.
- When using symbol fallback, normalise YAML symbols with the same
  `eth_defi.token.normalise_token_symbol()` helper that `vault_metrics.py` uses
  for the vault denomination.
- Symbol fallback inside `StablecoinRateFeeder` must expose only the
  unambiguous fallback set. It must exclude all `entries:` files and any symbol
  that appears in more than one stablecoin YAML record, including case variants.
- Cache depegged stablecoin contract identifiers and unambiguous normalised
  symbols inside `StablecoinRateFeeder` so metrics calculation does not parse
  all YAML files for every vault.
- Build separate rate-section lookup caches for healthy and depegged rates,
  keyed by `(chain_id, address)` and by unambiguous normalised symbol, inside
  `StablecoinRateFeeder`. These use the same contract-aware and ambiguity rules
  as blacklisting, but they include healthy entries such as USDC so
  `denomination_token_rate` is populated even when no depeg exists.
- Treat `StablecoinRateFeeder` as a batch-scoped cache. Tests that mutate
  temporary YAML data should construct a fresh feeder for the temporary
  directory.
- Avoid introducing an import cycle between `eth_defi.research.vault_metrics`
  and `eth_defi.feed.stablecoin_rate`. If a top-level import creates a cycle,
  use a narrow lazy import to construct the default `StablecoinRateFeeder`.

## Post scan integration

Run the stablecoin population/refresh side job from the post scan process, but
gate it so the stablecoin YAML corpus is scanned and potentially rewritten at
most once every 24 hours unless explicitly forced. The post scanner may run
more often, e.g. every 8 hours, so this must be a durable scanner-level gate in
addition to the per-entry daily gates in stablecoin YAML.

Update `eth_defi/feed/scanner.py`:

- Add `refresh_stablecoin_rates: bool = True` to `PostScanConfig`.
- Add `force_stablecoin_rate_refresh: bool = False` to `PostScanConfig`.
- Add `stablecoin_data_dir: Path = STABLECOINS_DATA_DIR`.
- Add `stablecoin_rate_timeout: float = 20.0`.
- Add `stablecoin_rate_gate_path: Path | None = None` to `PostScanConfig`.
  When omitted, derive a sidecar state path next to the post-scan database,
  e.g. `config.db_path.with_suffix(".stablecoin-rate-state.json")`.
- Persist the scanner-level gate as JSON containing
  `last_started_at`, `last_succeeded_at`, and `last_failed_at` naive UTC ISO
  timestamps. `last_succeeded_at` controls the 24-hour skip decision and
  survives process restarts.
- Update existing scanner integration tests that instantiate `PostScanConfig`
  so they either set `refresh_stablecoin_rates=False` or point
  `stablecoin_data_dir` at a temporary test directory. The default-on side job
  must not mutate real repository stablecoin YAML during feed scanner tests.
- At the start of `run_post_scan_cycle()`, call `refresh_stablecoin_rates()`
  only when enabled and either `force_stablecoin_rate_refresh=True` or the
  durable gate says the last successful run was at least 24 hours ago.
- Update `last_started_at` before the refresh. Update `last_succeeded_at` only
  after `refresh_stablecoin_rates()` returns successfully. Update
  `last_failed_at` when an unexpected refresh exception is caught.
- Add explicit stablecoin rate slots fields to
  `eth_defi/feed/collector.py::CollectorRunSummary`; do not attach arbitrary
  attributes at runtime because `CollectorRunSummary` is a `slots=True`
  dataclass. Use `TYPE_CHECKING`, a forward reference, or `Any` for the summary
  type annotation if importing `StablecoinRateRefreshSummary` would create an
  import cycle.
- Add explicit summary status fields so dashboards can distinguish disabled,
  skipped, successful, and failed side-job states:
  - `stablecoin_rate_status: str | None` with values `disabled`,
    `skipped_recent`, `succeeded`, or `failed`
  - `stablecoin_rate_summary: StablecoinRateRefreshSummary | None`, populated
    only when a refresh actually runs successfully
  - `stablecoin_rate_error: str | None`, populated only for unexpected refresh
    exceptions
- Fail soft: log stablecoin-rate refresh errors and continue post collection.

Update `scripts/erc-4626/scan-vault-posts.py`:

- Add environment variables:
  - `REFRESH_STABLECOIN_RATES`: default `true`
  - `FORCE_STABLECOIN_RATE_REFRESH`: default `false`, bypasses the scanner-level
    24-hour gate and passes `force=True` to `refresh_stablecoin_rates()`
  - `STABLECOIN_RATE_TIMEOUT`: default `20`
  - `STABLECOIN_DATA_DIR`: optional path override for tests, worktrees, and
    production deployments that pass the same directory to stablecoin metadata
    export/load paths. Do not point the refresh at a different directory than
    metadata export, because refreshed depeg flags would otherwise not appear in
    exported R2 metadata.
  - `STABLECOIN_RATE_GATE_PATH`: optional durable 24-hour gate JSON path
  - `COINGECKO_DEMO_API_KEY`: optional, passed through by environment
- Include a compact dashboard row for rates fetched, files updated and
  depegged stablecoins.

## Initial YAML population helper

Add a standalone helper script for the one-off or manually triggered
population of stablecoin YAML rate metadata before the post scanner owns the
full workflow.

Create `scripts/erc-4626/populate-stablecoin-rates.py`:

- Use environment variables only, following the style of the existing ERC-4626
  scripts.
- Read `STABLECOIN_DATA_DIR`, defaulting to `STABLECOINS_DATA_DIR`.
- Read `FORCE`, defaulting to `false`; when true, pass `force=True` to
  `refresh_stablecoin_rates()`.
- Read `STABLECOIN_RATE_TIMEOUT`, defaulting to `20`.
- Read optional `COINGECKO_ID_MAPPING_FILE`. When provided, apply explicit
  operator-curated mappings before refresh. The mapping file should key entries
  by stablecoin slug plus entry name or index, and provide `coingecko_id`,
  `coingecko_link`, and `coingecko_id_source: manual`.
- Read `COINGECKO_DEMO_API_KEY` from the environment indirectly through
  `eth_defi.feed.stablecoin_rate`.
- Set up logging like `scripts/erc-4626/scan-vaults.py` /
  `scripts/erc-4626/scan-vault-posts.py`.
- Call `refresh_stablecoin_rates(data_dir=..., force=..., timeout=...)`.
- Print one compact `tabulate` summary row with files scanned, entries seen,
  rates fetched, files updated, depegged count, failed count, skipped missing
  CoinGecko ids, skipped unknown pegs, and unactionable depegged entries.
- Exit non-zero only on unexpected implementation/configuration errors. Normal
  per-entry CoinGecko failures should be reflected in summary counters and YAML
  `rate_fetch_failed_*` fields.

This helper is the initial population mechanism for adding rate metadata to the
existing stablecoin YAML corpus. It may populate `coingecko_id` and
`coingecko_link` from an unambiguous existing URL or from the explicit
`COINGECKO_ID_MAPPING_FILE`, then populate `usd_rate`, `usd_rate_fetched_at`,
and related fields from CoinGecko. It must not auto-select from ambiguous
CoinGecko search results; entries without a reliable id should be stamped with
`rate_fetch_failed_reason: missing_coingecko_id` or left for manual mapping. It
should also be safe to re-run idempotently.

Future integration: after the helper has proven reliable, the same population
logic should become part of the post scanner loop and run at most once every 24
hours. The 24-hour gate should be explicit and separate from each individual
stablecoin's daily rate gate, so the post scanner can run more frequently
without repeatedly scanning and rewriting the stablecoin YAML corpus.

## Documentation

Update:

- `eth_defi/feed/README-feed.md` with a short section explaining the stablecoin
  rate refresh side job, the initial population helper, and the CoinGecko
  dependency.
- `eth_defi/stablecoin_metadata.py` module documentation with the new YAML
  fields.
- `docs/source/api/feed/index.rst` by adding `eth_defi.feed.stablecoin_rate`
  to the existing `autosummary` module list. Do not add a standalone
  `stablecoin_rate.rst`; this API section uses generated autosummary pages.

## Tests

Add focused tests, avoiding the full test suite.

Suggested files:

- `tests/feed/test_stablecoin_rate.py`
- `tests/research/test_vault_metrics_depeg.py` or the closest existing vault
  metrics test file
- `tests/stablecoin/test_stablecoin_metadata_export.py` if a stablecoin
  metadata test package already exists; otherwise add a focused test under
  `tests/feed`

Test cases:

- Happy-path full-stack integration uses USDC:
  - temporary `usdc.yaml` with `coingecko_id: usd-coin`,
    `coingecko_link: https://www.coingecko.com/en/coins/usd-coin`, and
    Ethereum/Base contract addresses
  - mocked CoinGecko `/simple/price` returns `usd: 1.0`
  - `refresh_stablecoin_rates()` writes `usd_rate`, timestamps, and clears
    failure/depeg fields
  - `calculate_lifetime_metrics(stablecoin_rate_feeder=StablecoinRateFeeder(data_dir=tmp_path))`
    uses the temporary YAML fixture, not repository stablecoin data
  - vault metrics for a USDC-denominated vault remain non-blacklisted
  - `calculate_lifetime_metrics()` output includes
    `denomination_token_rate.coingecko_id == "usd-coin"`,
    `denomination_token_rate.usd_rate == 1.0`,
    non-empty `denomination_token_rate.usd_rate_fetched_at`, and
    `denomination_token_rate.usd_rate_source == "coingecko"`
- Bad-path full-stack integration uses Kava USDX:
  - temporary multi-entry `usdx.yaml` fixture with Kava USDX entry,
    `coingecko_id: usdx`, `coingecko_link:
    https://www.coingecko.com/en/coins/usdx`, and per-entry
    `contract_addresses`
  - mocked CoinGecko `/simple/price` returns `usd: 0.646809`
  - `refresh_stablecoin_rates()` stamps `depegged_at`
  - `calculate_lifetime_metrics(stablecoin_rate_feeder=StablecoinRateFeeder(data_dir=tmp_path))`
    uses the temporary YAML fixture, not repository stablecoin data
  - depegged contract lookup sees the USDX entry by `(chain_id, address)`
  - vault metrics for a vault whose `_denomination_token` dict has an
    `"address"` matching that USDX contract set `risk` to
    `VaultTechnicalRisk.blacklisted`,
    `risk_numeric` to `VaultTechnicalRisk.blacklisted.value`, and add the
    depegged denomination note/flag
  - `VaultFlag.depegged_denomination_token` is present in `BAD_FLAGS` if the
    enum is added
  - `other_data["vault_display_flags"]` contains the depegged denomination
    display flag after the final risk override
  - `calculate_lifetime_metrics()` output includes
    `denomination_token_rate.coingecko_id == "usdx"`,
    `denomination_token_rate.usd_rate == 0.646809`,
    non-empty `denomination_token_rate.usd_rate_fetched_at`, and
    `denomination_token_rate.usd_rate_source == "coingecko"`
- Parse CoinGecko ids from normal URLs such as
  `https://www.coingecko.com/en/coins/usd-coin`.
- Prefer a stored `coingecko_id` over `coingecko_link` and `links.coingecko`
  when they disagree.
- Persist a successfully parsed URL id as `coingecko_id`,
  `coingecko_link`, `coingecko_id_source: url`, and
  `coingecko_id_verified_at`.
- YAML mutation writes never-verified optional timestamps such as
  `coingecko_id_verified_at` as empty strings, not the literal `None`.
- Do not auto-resolve ambiguous symbols from CoinGecko search results; require
  an explicit `coingecko_id`.
- Cover the USDX case: `coingecko_id: usdx` should be treated as depegged,
  while the stale `kava-lend` URL must not override the stored id.
- Ignore empty or malformed CoinGecko ids and URLs.
- Fetch multiple ids from a mocked `/simple/price` response and persist
  `usd_rate`, `usd_rate_fetched_at`.
- Clear `rate_fetch_failed_at` and `rate_fetch_failed_reason` after a later
  successful rate refresh.
- Stamp `rate_fetch_failed_at` and `rate_fetch_failed_reason` when the
  CoinGecko response does not contain a price for a parsed id.
- Stamp `rate_fetch_failed_at` and `rate_fetch_failed_reason` for missing or
  invalid CoinGecko ids.
- Entries with missing `coingecko_id` still produce a target, are counted, and
  are stamped with `rate_fetch_failed_reason: missing_coingecko_id`.
- Persist CoinGecko's `last_updated_at` as `usd_rate_updated_at`, but use our
  `now_` value for `usd_rate_fetched_at` and daily gating.
- Convert CoinGecko `last_updated_at` epoch integers to naive UTC ISO strings
  with `native_datetime_utc_fromtimestamp()`.
- Coerce YAML string values such as `usd_rate: 0.9998` and
  `peg_rate: 0.9998` to Python floats when reading targets and to JSON numbers
  when exporting metadata.
- Parse daily-gating timestamps from raw YAML strings and cover the
  `23:59` to `00:01` UTC date-boundary case.
- Treat `0`, negative, `null`, NaN, and non-finite prices as
  `coingecko_price_missing` failures, not as depegs.
- Cover the wrong-asset boundary: a URL-derived or unverified id with a price
  below `MIN_PLAUSIBLE_STABLECOIN_RATE`, such as `kava-lend` at `0.00049975`,
  is a fetch/data-quality failure, while Kava USDX at `0.646809` is a real
  depeg. Also cover that a manual and verified id can still stamp a sub-cent
  catastrophic depeg.
- Stamp `depegged_at` when USD-pegged token price is below `0.90`.
- Do not stamp `depegged_at` when price is `0.90` or above.
- Compare non-USD fiat-pegged tokens using their peg currency rate, persist the
  USD rate separately, and persist `peg_rate`/`peg_rate_currency`.
- Always request `usd` plus the peg currency for non-USD pegs.
- If CoinGecko does not return the requested peg currency, persist `usd_rate`,
  leave `depegged_at` empty, increment `skipped_unknown_peg`, and log a
  warning.
- Compare XAU/gold-pegged tokens using the `xau` rate where mapped.
- For unknown or ambiguous peg targets, persist `usd_rate`, leave
  `depegged_at` empty, increment `skipped_unknown_peg`, and log a warning.
- Preserve an existing `depegged_at` value when the price recovers.
- Re-stamp `depegged_at` if the operator cleared it and the token remains
  below threshold.
- Skip daily refresh when `usd_rate_fetched_at` is already today.
- Skip daily retry when `rate_fetch_failed_at` is already today.
- `force=True` refreshes even when the entry was already updated today.
- `force=True` refreshes even when the entry already failed today.
- Multi-entry YAML files update only the relevant entries.
- Multi-entry YAML mutation is idempotent when re-run on a file whose fields
  already exist, updating values instead of appending duplicates.
- Two entries or files sharing the same `coingecko_id` are fetched once but
  stamped independently.
- Two entries sharing the same `coingecko_id` but with different daily-gate
  states only fetch for due targets; gated entries are not stamped again, and
  due entries are still updated from the shared fetched price.
- Metadata JSON export includes the new rate/depeg fields and converts empty
  strings to `None`.
- Metadata JSON export emits `usd_rate` and `peg_rate` as numbers, not strings.
- Metadata export/load tests prove that a temporary stablecoin data directory
  used by `refresh_stablecoin_rates()` is also the directory used for all-files
  metadata export, preventing package-dir/override split-brain.
- Temporary-data tests construct fresh `StablecoinRateFeeder` instances for the
  fixture directory.
- Healthy stablecoin rate lookup caches populate `denomination_token_rate` for
  USDC by `(chain_id, address)` and by unambiguous normalised symbol.
- `denomination_token_rate` is always a `DenominationTokenRate` object. For an
  unmatched denomination token, all fields are `None`; the whole section
  is never `None`.
- Vault metrics blacklists a vault whose normalised denomination symbol is
  marked depegged.
- Vault metrics first matches depegged denomination by `(chain_id, address)` and
  does not blacklist unrelated entries sharing the same `symbol` in an
  `entries:` YAML file.
- Vault metrics converts YAML chain slug aliases to numeric chain ids and
  normalises both vault and YAML addresses before matching.
- Vault metrics covers YAML chain alias collisions such as `binance`, `bnb`,
  and `bsc` mapping to the same BNB chain id.
- A depegged multi-entry stablecoin without `contract_addresses`, such as the
  current USDX file before migration, logs an unactionable warning and does not
  silently blacklist by ambiguous symbol.
- USDX blacklisting test data includes per-entry `contract_addresses`, proving
  the Kava USDX use case works through contract-aware matching.
- Full-stack integration tests must not stop at the feed module. They must
  exercise the stack from mocked CoinGecko response through YAML mutation,
  `StablecoinRateFeeder` lookup, and `calculate_vault_record()` risk output.
- Vault metrics tests inject `StablecoinRateFeeder(data_dir=tmp_path)` instead
  of passing a raw stablecoin data directory into metrics functions.
- `calculate_lifetime_metrics()` includes a `denomination_token_rate` section
  for each vault row with `coingecko_id`, `usd_rate`,
  `usd_rate_fetched_at`, and `usd_rate_source`.
- Vaults with no matched stablecoin rate still get a stable
  `denomination_token_rate` shape modelled by `DenominationTokenRate`, with
  all fields set to `None`. Do not use `None` for the whole section.
- YAML symbols and vault denominations are normalised with the same
  `eth_defi.token.normalise_token_symbol()` path before any symbol fallback
  comparison.
- Symbol fallback ignores ambiguous symbols, multi-entry symbols, and case
  variants that resolve to more than one stablecoin record.
- Vault metrics does not blacklist a vault when the stablecoin has no rate, no
  CoinGecko id, `rate_fetch_failed_at`, or no `depegged_at`.
- Post scan cycle continues if CoinGecko returns an HTTP error.
- Existing feed scanner tests disable the side job or use a temporary
  `stablecoin_data_dir`, proving the default-on integration does not mutate real
  stablecoin YAML in unrelated tests.
- Summary counters are asserted for missing CoinGecko ids, unknown peg targets,
  unactionable depegged entries, failed CoinGecko responses, rates fetched, and
  files updated.

Use the existing repository HTTP mocking style: define a small response object
with `raise_for_status()` and patch `eth_defi.feed.stablecoin_rate.requests.get`
using `monkeypatch.setattr()`. Do not introduce `responses` or
`requests_mock` just for these tests.

Run targeted tests with the repository pytest wrapper:

```shell
source .local-test.env && poetry run pytest tests/feed/test_stablecoin_rate.py --log-cli-level=info
```

Use the 3 minute timeout convention for pytest commands.

## Implementation chunks

### Chunk 1: Rate module foundation

- [ ] Create `eth_defi/feed/stablecoin_rate.py`.
- [ ] Add dataclasses and CoinGecko id extraction.
- [ ] Keep the CoinGecko HTTP client monkeypatchable without adding an
      unnecessary direct dependency.
- [ ] Add explicit `coingecko_id` resolution, source tracking, and verification
      timestamp handling.
- [ ] Add scalar coercion helpers for YAML string values:
      floats, optional datetimes, and daily-gating date strings.
- [ ] Convert CoinGecko epoch `last_updated_at` values to naive UTC datetimes
      with `native_datetime_utc_fromtimestamp()`.
- [ ] Validate prices and treat missing, non-positive, NaN, and non-finite
      values as fetch failures.
- [ ] Add `MIN_PLAUSIBLE_STABLECOIN_RATE = 0.01` and apply it before the depeg
      threshold comparison for URL-derived or unverified ids so implausibly
      tiny positive prices become data-quality failures, not depegs. Manual
      and verified ids may still stamp sub-cent catastrophic depegs.
- [ ] Filter daily-gated targets before constructing the unique
      `coingecko_id` batch, while keeping result stamping per target entry.
- [ ] Store `coingecko_link` alongside `coingecko_id` for both standard and
      `entries:` stablecoin YAML records.
- [ ] Add stablecoin YAML target iterator for standard and `entries:` files.
- [ ] Add `StablecoinRateFeeder` as the public vault metrics access layer for
      rate lookups and depeg checks.
- [ ] Ensure vault metrics uses one shared `StablecoinRateFeeder` instance and
      does not reload stablecoin YAML inside the per-vault loop.
- [ ] Add healthy rate-section lookup caches by `(chain_id, address)` and
      unambiguous normalised symbol, separate from depegged-only caches.
- [ ] Add mocked CoinGecko fetcher tests.
- [ ] Add daily success-skip, daily failure-skip, and `force=True` tests.

### Chunk 2: YAML mutation

- [ ] Implement focused YAML mutation with a concrete round-trip strategy
      (`ruamel.yaml` or an entry-index-keyed block editor). It must update
      existing values, clear failure fields, and write inside the correct
      `entries:` item without duplicate fields.
- [ ] Add `usd_rate`, `usd_rate_fetched_at`, and `depegged_at` to updated
      stablecoin entries.
- [ ] Add `usd_rate_updated_at`, `peg_rate`, and `peg_rate_currency` to updated
      stablecoin entries.
- [ ] Add `rate_fetch_failed_at` and `rate_fetch_failed_reason` to failed
      stablecoin refresh entries, and clear them on successful refresh.
- [ ] Persist `coingecko_id`, `coingecko_link`, `coingecko_id_source`, and
      `coingecko_id_verified_at` when needed for reliable CoinGecko fetches.
- [ ] Ensure YAML mutation writes optional empty datetime fields as `''`, not
      the literal `None`.
- [ ] During metadata migration, add per-entry `contract_addresses` for
      depegged multi-entry assets that must affect vault blacklisting, starting
      with Kava USDX.
- [ ] Preserve existing `depegged_at` unless manually cleared.
- [ ] Add idempotent update tests for standard and multi-entry files.

### Chunk 3: Metadata export

- [ ] Extend `eth_defi/stablecoin_metadata.py` documentation and TypedDicts.
- [ ] Export new fields in `build_stablecoin_metadata_json()`, coercing
      numeric YAML strings to JSON numbers.
- [ ] Add an optional `data_dir: Path = STABLECOINS_DATA_DIR` path through
      all-files metadata load/export helpers so tests and configured refresh
      overrides do not write one directory and export another.
- [ ] Add backwards-compatible tests for old YAML files without rate fields.

### Chunk 4: Vault blacklist integration

- [ ] Add depegged stablecoin lookup helper with in-process cache.
- [ ] Add `DenominationTokenRate` section typing to
      `eth_defi/research/vault_metrics.py` with `coingecko_id`, `usd_rate`,
      `usd_rate_fetched_at`, and `usd_rate_source`.
- [ ] Include the denomination token rate section in every row produced by
      `calculate_lifetime_metrics()` via `calculate_vault_record()`.
- [ ] Add `stablecoin_rate_feeder: StablecoinRateFeeder | None = None`
      parameters to `calculate_lifetime_metrics()` and
      `calculate_vault_record()`, and pass the same feeder through
      `_apply_vault_record()`.
- [ ] Construct one default `StablecoinRateFeeder()` per
      `calculate_lifetime_metrics()` call when no feeder is supplied, and reuse
      it for all vault rows.
- [ ] Keep `stablecoin_data_dir` out of the vault metrics function signatures;
      tests inject temporary YAML through `StablecoinRateFeeder(data_dir=tmp_path)`.
- [ ] Ensure unmatched denomination tokens still return
      `DenominationTokenRate(coingecko_id=None, usd_rate=None,
      usd_rate_fetched_at=None, usd_rate_source=None)`.
- [ ] Add vault metric risk override for depegged denomination tokens after
      all existing dynamic blacklist checks, including abnormal volatility.
- [ ] Recompute `risk_numeric` unconditionally from final `risk` immediately
      before building the returned `pd.Series`.
- [ ] Match denomination tokens by `(chain_id, address)` first, reading
      top-level and per-entry `contract_addresses`, with normalised symbol
      fallback only for unambiguous records.
- [ ] Add YAML chain slug alias to numeric chain id mapping, covering aliases
      such as `binance`, `bnb`, and `bsc`.
- [ ] Add chain-alias and address normalisation tests.
- [ ] Warn and count depegged entries that are unactionable for vault
      blacklisting because they lack addresses and have ambiguous symbols.
- [ ] Add a user-facing note and display flag; rebuild or append
      `other_data["vault_display_flags"]` after the final depeg override using
      severity `red`, type `depegged_denomination_token`, and source
      `stablecoin`.
- [ ] If adding `VaultFlag.depegged_denomination_token`, include it in
      `BAD_FLAGS`.
- [ ] Add focused vault metrics tests.
- [ ] Add full-stack integration tests:
      USDC happy path remains non-blacklisted, and USDX bad path is refreshed,
      marked depegged, resolved by denomination token contract, and blacklisted
      in `calculate_lifetime_metrics()`/`calculate_vault_record()`, with
      `denomination_token_rate.coingecko_id`,
      `denomination_token_rate.usd_rate`,
      `denomination_token_rate.usd_rate_fetched_at`, and
      `denomination_token_rate.usd_rate_source` asserted.

### Chunk 5: Post scan side job

- [ ] Extend `PostScanConfig`.
- [ ] Call the refresh at the start of `run_post_scan_cycle()` only when the
      side job is enabled and the durable 24-hour gate allows it.
- [ ] Add an explicit post-scan-level 24-hour gate for the stablecoin
      population/refresh job, separate from per-entry daily rate gates. The
      scanner can run more often, but the stablecoin YAML corpus should be
      scanned and rewritten at most once per 24 hours unless forced.
- [ ] Persist the 24-hour gate in a JSON sidecar file with `last_started_at`,
      `last_succeeded_at`, and `last_failed_at` so the gate survives process
      restarts.
- [ ] Add `force_stablecoin_rate_refresh` to `PostScanConfig` and
      `FORCE_STABLECOIN_RATE_REFRESH` to `scan-vault-posts.py`.
- [ ] Add environment variable wiring in `scan-vault-posts.py`, including
      `STABLECOIN_DATA_DIR` and `STABLECOIN_RATE_GATE_PATH`.
- [ ] Add dashboard/log output.
- [ ] Extend `eth_defi/feed/collector.py::CollectorRunSummary` with an
      explicit stablecoin rate status, summary, and error fields for dashboard
      output, avoiding an import cycle with a forward reference,
      `TYPE_CHECKING`, or `Any`.
- [ ] Update existing scanner tests to disable the side job or use temporary
      stablecoin YAML data.
- [ ] Use fresh `StablecoinRateFeeder(data_dir=tmp_path)` instances in tests
      that use temporary data.
- [ ] Add tests proving post scan continues when the refresh fails.
- [ ] Add a scanner test proving `CollectorRunSummary.stablecoin_rate_summary`
      is populated on a successful refresh.
- [ ] Add scanner tests for disabled, skipped-recent, forced, failed, 8-hour
      loop, and process-restart 24-hour gate behaviour, asserting
      `stablecoin_rate_status`, `stablecoin_rate_summary`, and
      `stablecoin_rate_error` semantics in each case.
- [ ] Add a scanner/export integration test proving `STABLECOIN_DATA_DIR` and
      metadata export/load paths use the same stablecoin YAML directory when an
      override is configured.

### Chunk 6: Initial population helper script

- [ ] Add `scripts/erc-4626/populate-stablecoin-rates.py`.
- [ ] Wire `STABLECOIN_DATA_DIR`, `FORCE`, `STABLECOIN_RATE_TIMEOUT`,
      `COINGECKO_ID_MAPPING_FILE`, and `COINGECKO_DEMO_API_KEY` via
      environment variables.
- [ ] Support explicit operator-curated CoinGecko id mappings from
      `COINGECKO_ID_MAPPING_FILE` and do not auto-select ambiguous CoinGecko
      search results.
- [ ] Use the same logging conventions as the existing ERC-4626 scripts.
- [ ] Call `refresh_stablecoin_rates()` and render the
      `StablecoinRateRefreshSummary` as one compact `tabulate` row.
- [ ] Ensure normal per-entry CoinGecko failures do not make the helper exit
      non-zero; unexpected implementation/configuration errors should still
      fail hard.
- [ ] Add a smoke/unit test for the helper entry point that monkeypatches
      `refresh_stablecoin_rates()` and verifies env var parsing plus summary
      rendering.
- [ ] Add a helper test proving `COINGECKO_ID_MAPPING_FILE` mappings are
      applied before refresh and ambiguous search results are not auto-selected.
- [ ] Add helper tests proving `failed_count > 0` exits successfully, while an
      unexpected exception exits non-zero.

### Chunk 7: Documentation and verification

- [ ] Update `eth_defi/feed/README-feed.md`.
- [ ] Add `eth_defi.feed.stablecoin_rate` to
      `docs/source/api/feed/index.rst`.
- [ ] Run formatting with `poetry run ruff format`.
- [ ] Run targeted tests.
- [ ] Inspect `git diff` to confirm no unrelated stablecoin YAML churn.

## Operational notes

- This job mutates repository YAML files during post scanning. In production,
  make sure the scanner runs in a checkout where this state is expected to be
  persisted or committed by an operator.
- CoinGecko free/keyless access is rate-limited. The once-per-day gate and
  batched request design are important for keeping the process quiet.
- A depeg flag is intentionally sticky. Human operators decide when a stablecoin
  is safe again by clearing `depegged_at`.
- The implementation should report missing CoinGecko ids separately from true
  depegs; missing rate coverage is an operational data-quality issue, not a
  blacklist reason.
