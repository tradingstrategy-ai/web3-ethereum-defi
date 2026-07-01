# Stablecoin source currency rate plan

Date: 2026-06-30

## Goal

Make stablecoin depeg monitoring currency-aware as part of the scanner
pipeline.

Stablecoin metadata should explicitly say which fiat/source currency the token
is meant to track, for example `usd`, `eur`, `jpy` or `chf`. For non-USD
stablecoins the daily rate updater should also persist the USD/source-currency
FX rate used for the check, so operators can audit why a token was or was not
marked as depegged.

The implementation should use the new `eth_defi.currency_api` DuckDB database
as the USD/native exchange-rate source, instead of treating every stablecoin as
if its nominal unit was one US dollar.

## Current state

- Stablecoin rate metadata lives in `eth_defi/data/stablecoins/*.yaml`.
- `eth_defi/feed/stablecoin_rate.py` writes mutable rate fields such as
  `usd_rate`, `peg_rate`, `peg_rate_currency`, `rate_fetch_failed_at` and
  `depegged_at`.
- `StablecoinRateTarget.peg_currency` is currently inferred from the token
  symbol/name by `_guess_peg_currency()`.
- `refresh_stablecoin_rates()` asks CoinGecko for `usd` and the inferred peg
  currency, then compares `peg_rate < DEPEG_THRESHOLD`.
- The YAML schema does not store the token's intended source currency as a
  first-class metadata field.
- The YAML schema does not store the external USD/source-currency exchange rate
  used to audit non-USD stablecoin checks.
- The new `eth_defi.currency_api` package stores raw exchange rates in DuckDB as
  `quote_currency` units per one `base_currency`. With the default
  `base_currency=usd`, the EUR row is `eur per 1 USD`; USD per EUR is the
  inverse.

The code is already partly protected against obvious non-USD false positives by
requesting CoinGecko's native `vs_currencies` value. The remaining gap is that
the native currency is inferred, not metadata, and the scanner does not record
the independent FX rate that should explain the native comparison.

The implementation must never silently default an unknown source currency to
`usd`. If the metadata does not have a trusted source currency, the scanner may
refresh `usd_rate`, but it must skip the depeg decision.

## Manual checks

I checked the EUR stablecoin YAML rows after pulling `origin/master`.

For `2026-06-26`, the currency API source reports:

```text
1 USD = 0.87934359 EUR
1 EUR = 1.137211905985 USD
```

Sample current YAML values:

```text
ageur  agEUR   usd=1.140000  stored_eur=0.996731  implied_eur=1.002452  depeg_native=False  dollar_threshold=False
ceur   CEUR    usd=1.140000  stored_eur=0.998119  implied_eur=1.002452  depeg_native=False  dollar_threshold=False
eura   EURA    usd=1.140000  stored_eur=0.996731  implied_eur=1.002452  depeg_native=False  dollar_threshold=False
eure   EURe    usd=1.140000  stored_eur=0.999365  implied_eur=1.002452  depeg_native=False  dollar_threshold=False
eurs   EURS    usd=1.210000  stored_eur=1.063000  implied_eur=1.064006  depeg_native=False  dollar_threshold=False
par    PAR     usd=1.180000  stored_eur=1.037000  implied_eur=1.037625  depeg_native=False  dollar_threshold=False
eurt   EURT    usd=0.054311  stored_eur=0.047636  implied_eur=0.047758  depeg_native=True   dollar_threshold=True
jeur   jEUR    usd=0.533557  stored_eur=0.467979  implied_eur=0.469180  depeg_native=True   dollar_threshold=True
seur   SEUR    usd=0.018311  stored_eur=0.016061  implied_eur=0.016102  depeg_native=True   dollar_threshold=True
```

The healthy EUR rows are consistent: `usd_rate / EURUSD` is close to the stored
native EUR rate. The small differences are expected because CoinGecko and the
currency API update on different schedules and CoinGecko rounds some returned
USD prices.

The operational problem is visible at the threshold boundary:

- At `1 EUR = 1.137211905985 USD`, a EUR stablecoin at `1.00 USD` is only
  `0.87934359 EUR`.
- A dollar-only `usd_rate < 0.90` check would not mark it depegged.
- A native EUR check would mark it depegged because `0.87934359 < 0.90`.

JPY is the opposite kind of risk: a healthy 1 JPY stablecoin is roughly
`0.006` to `0.007 USD`, so a dollar-only check marks it depegged even when it is
near `1.0 JPY`.

## Proposed yaml schema

Add source-currency and FX audit fields to every stablecoin metadata entry:

```yaml
source_currency: eur
source_currency_source: manual
source_currency_usd_rate: 1.137211905985
source_currency_usd_rate_date: '2026-06-26'
source_currency_usd_rate_fetched_at: '2026-06-30T12:00:00'
source_currency_usd_rate_source: fawazahmed0
```

Field semantics:

- `source_currency`: lower-case ISO-like ticker for the intended nominal unit,
  e.g. `usd`, `eur`, `jpy`, `gbp`, `chf`, `cad`, `aud`, `sgd`, `try`, `hkd`,
  or `nzd`. Exclude `xau` from the v1 automated depeg path until we have an
  explicit metal unit policy, because gold-backed tokens may represent one
  troy ounce, grams, or another unit.
- `source_currency_source`: how the source currency was selected. Use `manual`
  for curated YAML, `inferred` only for review output during migration/backfill,
  and leave empty only when unknown. `inferred` values must not stamp
  `depegged_at` until an operator promotes them to `manual`. Production depeg
  stamping must require `source_currency_source: manual`; any other value,
  including an empty value, skips the native depeg decision.
- `source_currency_usd_rate`: USD per one source-currency unit. For EUR this is
  EURUSD, for JPY this is USD per JPY. For `source_currency: usd`, this may be
  `1.0` or omitted; prefer writing `1.0` for a uniform schema.
- `source_currency_usd_rate_date`: the currency API calendar date used for the
  FX value.
- `source_currency_usd_rate_fetched_at`: naive UTC timestamp when our scanner
  read the value from the currency DB.
- `source_currency_usd_rate_source`: provider/source string from the currency
  API row, initially `fawazahmed0`.

Keep existing fields:

- `usd_rate`: token price in USD from CoinGecko.
- `peg_rate`: token price in the native source currency used for the depeg
  decision. After this change it should be derived from
  `usd_rate / source_currency_usd_rate` when `source_currency != usd`, unless a
  future source explicitly overrides it.
- `peg_rate_currency`: keep as an audit alias for the actual depeg comparison
  currency. It should equal `source_currency` when the source currency is known.
- `depegged_at`: sticky operator-cleared marker.

Example for PAR:

```yaml
source_currency: eur
source_currency_source: manual
usd_rate: 1.18
source_currency_usd_rate: 1.137211905985
source_currency_usd_rate_date: '2026-06-26'
source_currency_usd_rate_fetched_at: '2026-06-30T12:00:00'
source_currency_usd_rate_source: fawazahmed0
peg_rate: 1.037625
peg_rate_currency: eur
depegged_at: ''
```

Example for a healthy JPY stablecoin:

```yaml
source_currency: jpy
source_currency_source: manual
usd_rate: 0.0064
source_currency_usd_rate: 0.006181
source_currency_usd_rate_date: '2026-06-26'
source_currency_usd_rate_fetched_at: '2026-06-30T12:00:00'
source_currency_usd_rate_source: fawazahmed0
peg_rate: 1.03543
peg_rate_currency: jpy
depegged_at: ''
```

## Scanner design

Add currency-aware rate resolution to `eth_defi/feed/stablecoin_rate.py`.

1. Extend `StablecoinRateTarget` with:
   - `source_currency: str | None`
   - `source_currency_source: str | None`
   - `source_currency_usd_rate: float | None`
   - `source_currency_usd_rate_date: datetime.date | None`
   - `source_currency_usd_rate_fetched_at: datetime.datetime | None`
   - `source_currency_usd_rate_source: str | None`

2. Add the new fields to `_RATE_FIELDS` and to
   `eth_defi/stablecoin_metadata.py::StablecoinMetadata` export parsing.

3. Replace `_guess_peg_currency()` usage with:
   - read `source_currency` from YAML first;
   - if missing, infer with the existing conservative heuristic only for a
     migration/review report;
   - do not use `usd` as a default for unknown currencies;
   - do not write `source_currency_source: inferred` during normal scanner
     refreshes;
   - stamp `depegged_at` only when `source_currency_source == "manual"`;
   - skip depeg decisions for `source_currency_source: inferred`, empty, or any
     other non-manual value;
   - log and skip depeg decisions when source currency is still unknown.

4. Add a small resolver around `CurrencyRateDatabase`:
   - input: `source_currency`, `coingecko_fetch_date`, `currency_db_path`,
     `source`;
   - output: USD per one source-currency unit plus rate date/source;
   - for `usd`, return `1.0` without a DB lookup;
   - for non-USD, read the latest available `exchange_rates` row at or before
     the CoinGecko fetch date, where `base_currency='usd'` and
     `quote_currency=source_currency`;
   - always read source-currency FX rates from the local DuckDB database through
     `CurrencyRateDatabase`; do not call the currency API network source from
     the stablecoin rate refresher;
   - invert the raw stored rate, because the DB stores source units per USD;
   - define `MAX_SOURCE_CURRENCY_RATE_AGE_DAYS = 7` and treat older FX rows as
     stale when `(coingecko_fetch_date - source_currency_usd_rate_date).days`
     is greater than the max age;
   - use one reference date for both lookup and staleness. In the first
     implementation this is `now_.date()`, because CoinGecko `/simple/price`
     returns only one `last_updated_at` for the token quote and the refresh is a
     daily operational snapshot. If later code stores per-token upstream dates,
     pass that same date into both lookup and staleness checks.
   - fail closed for missing/stale FX data: persist `usd_rate`, stamp a
     currency-rate failure reason, but do not stamp `depegged_at`.

5. Extend `refresh_stablecoin_rates()` parameters:

```python
def refresh_stablecoin_rates(
    data_dir: Path = STABLECOINS_DATA_DIR,
    now_: datetime.datetime | None = None,
    force: bool = False,
    timeout: float = 20.0,
    progress_bar: bool = False,
    currency_db_path: Path | None = None,
    currency_source: str = SOURCE_NAME,
) -> StablecoinRateRefreshSummary:
    ...
```

6. Compute depeg rate as:

```python
if source_currency is None:
    increment_skipped_missing_source_currency(...)
    return skip_depeg_decision(...)
elif source_currency_source != "manual":
    return skip_depeg_decision(...)
elif source_currency == "usd":
    peg_rate = usd_rate
    source_currency_usd_rate = 1.0
else:
    source_currency_rate = read_latest_usd_per_source_currency(...)
    if source_currency_rate is None:
        increment_skipped_missing_source_currency_rate(...)
        return skip_depeg_decision(...)
    if source_currency_rate.is_stale:
        increment_source_currency_rates_stale(...)
        return skip_depeg_decision(...)
    source_currency_usd_rate = source_currency_rate.usd_per_source_currency
    peg_rate = usd_rate / source_currency_usd_rate

depegged = peg_rate < DEPEG_THRESHOLD
```

7. Preserve the existing sticky `depegged_at` behaviour. Never clear it
   automatically.

8. Keep CoinGecko as the token-price source. Stop depending on CoinGecko's
   `vs_currencies=eur,jpy,...` value for the authoritative depeg decision once
   the currency DB path is available. Keep CoinGecko native quotes as a rollout
   cross-check, but compare in USD space to reduce false alarms from heavily
   rounded low-priced quotes:
   `abs(usd_rate - coingecko_native_rate * source_currency_usd_rate) / usd_rate`.
   Warn when this relative difference is greater than `2%`; smaller source
   timing and quote rounding differences are expected.

9. Add summary counters:
   - `skipped_missing_source_currency`
   - `skipped_missing_source_currency_rate`
   - `source_currency_rates_fetched`
   - `source_currency_rates_stale`

## Pipeline integration

Use the all-chain scanner's existing currency API integration as the source of
truth for FX rates.

- `eth_defi/vault/scan_all_chains.py` already resolves
  `currency_api_db_path` from `PIPELINE_DATA_DIR` or `CURRENCY_API_DB_PATH`.
- It already runs `scan_currency_rates_fn()` as a best-effort auxiliary scan.
- Make the stablecoin rate side job accept the same `currency_api_db_path`.
- Ensure the scan order is:
  1. currency API scan updates `exchange-rates.duckdb`;
  2. stablecoin rate refresh reads that DB for non-USD FX rates;
  3. vault metrics/depeg blacklisting reads updated stablecoin YAML.

The post-feed scanner path in `eth_defi/feed/scanner.py` currently runs
`refresh_stablecoin_rates()` without a currency DB path. Add config fields:

```python
currency_api_db_path: Path | None = None
currency_api_source: str = SOURCE_NAME
```

If `currency_api_db_path` is not configured, the side job should still refresh
USD stablecoins and token USD prices, but it should skip non-USD depeg
decisions with a clear failure reason instead of falling back to dollar checks.
On any skipped non-USD depeg decision, leave `peg_rate` and
`peg_rate_currency` empty for that refresh rather than carrying forward stale
native comparison fields or defaulting them to USD.

The depeg threshold remains intentionally one-sided: it detects downside loss
of peg only. A token trading far above its source-currency unit is not marked by
this monitor unless a separate over-peg policy is added later.

## Migration

1. Add the schema fields to the code, parser and exporter.
2. Write a migration/helper script that annotates source currencies in
   `eth_defi/data/stablecoins/*.yaml`.
3. Seed obvious manual mappings first:
   - EUR: `ageur`, `ceur`, `eura`, `eurcv`, `eure`, `euroc`, `euroe`, `eurs`,
     `eurt`, `jeur`, `par`, `seur`, `veur`, `eurr`.
   - JPY: `cjpy`, `gyen`, `jpyc`.
   - GBP: `gbpt`, `tgbp`.
   - CHF: `jchf`, `zchf`.
   - CAD: `cadc`.
   - CNH: `cnht`, `tcnh`.
   - AUD: `audt`.
4. Before committing the non-USD seed list, check the local currency API
   database or upstream currency API response for each source ticker. Pay
   special attention to less-common tickers such as `cnh`, `try`, `sgd`, and
   `hkd`, because the provider may use a different quote key (for example
   `cny` instead of `cnh`) or omit the quote. Missing provider coverage is not a
   scanner bug, but it must be recorded as an intentional data gap before
   migration.
5. Mark migrated values as `source_currency_source: manual` when curated by
   file review.
6. For remaining tokens, use the existing heuristic only to produce a review
   report. Do not silently commit heuristic guesses as `manual`, and do not let
   `inferred` values participate in production depeg stamping.
7. Run the stablecoin rate refresh with a prepared currency DB and review the
   diff before committing mass YAML updates.

## Tests

Add focused tests in `tests/feed/test_stablecoin_rate.py`:

- EUR token at `usd_rate=1.00`, DB EURUSD=`1.1372`: stamps `depegged_at`
  because native rate is below `0.90 EUR`.
- JPY token at `usd_rate=0.0064`, DB USDJPY implying `USD per JPY=0.00618`:
  does not stamp `depegged_at` because native rate is near `1.0 JPY`.
- Missing `source_currency` persists `usd_rate` but skips depeg and increments
  `skipped_missing_source_currency`.
- Missing FX row for non-USD source currency persists `usd_rate`, stamps a
  machine-readable rate failure, and does not stamp `depegged_at`.
- Stale FX row older than `MAX_SOURCE_CURRENCY_RATE_AGE_DAYS` persists
  `usd_rate`, increments `source_currency_rates_stale`, and does not stamp
  `depegged_at`.
- `source_currency_source: inferred` does not stamp `depegged_at`.
- Empty or otherwise non-manual `source_currency_source` does not stamp
  `depegged_at`.
- Unknown, inferred, missing-FX and stale-FX skip paths clear `peg_rate` and
  `peg_rate_currency` for the current refresh.
- `source_currency: usd` does not need a DB lookup and behaves like the current
  USD path.
- YAML writer updates top-level and `entries:` files without duplicating fields.
- Stablecoin metadata JSON export converts the new numeric fields to JSON
  numbers and empty strings to `None`.

Test setup for non-USD source-currency cases should create a temporary DuckDB
database using `CurrencyRateDatabase(tmp_path / "exchange-rates.duckdb")` and
insert fixture rows through the database API. Tests should pass that temporary
`currency_db_path` into `refresh_stablecoin_rates()` instead of relying on the
operator's real currency database.

Add scanner tests:

- `eth_defi/feed/scanner.py` passes `currency_api_db_path` through to
  `refresh_stablecoin_rates()`.
- all-chain scanner order keeps `CurrencyRates` before stablecoin depeg
  refresh when both are enabled.

Use specific tests only, for example:

```shell
source .local-test.env && poetry run pytest tests/feed/test_stablecoin_rate.py --log-cli-level=info
source .local-test.env && poetry run pytest tests/feed/test_stablecoin_rate_scanner.py --log-cli-level=info
source .local-test.env && poetry run pytest tests/vault/test_scan_all_chains_core3.py --log-cli-level=info
```

## Rollout notes

- Do not compare non-USD stablecoins directly against `0.90 USD` at any point.
- Do not mark non-USD stablecoins healthy merely because `usd_rate > 0.90`.
- Missing FX data is an operational data gap, not proof of health or depeg.
- Keep `depegged_at` sticky and operator-cleared.
- Keep the existing contract-aware blacklisting logic; this change only changes
  how the stablecoin YAML gets marked as depegged.
- Consider logging both native and USD threshold equivalents:
  `source_currency=eur peg_rate=0.8793 source_currency_usd_rate=1.1372 usd_rate=1.0 usd_threshold_equivalent=1.02349`.

## Implementation checklist

- [ ] Add source-currency fields to `StablecoinRateTarget`, `_RATE_FIELDS` and
      YAML parsing.
- [ ] Add source-currency fields to stablecoin JSON export and docs.
- [ ] Add currency DB lookup helper for latest USD/source-currency rate.
- [ ] Change depeg comparison to derive native `peg_rate` from `usd_rate` and
      `source_currency_usd_rate`.
- [ ] Add staleness handling with `MAX_SOURCE_CURRENCY_RATE_AGE_DAYS = 7`.
- [ ] Add temporary DuckDB fixture setup for source-currency rate tests.
- [ ] Add CoinGecko native quote rollout warnings for USD-space divergence
      greater than `2%`.
- [ ] Thread `currency_api_db_path` through post/feed scanner configuration.
- [ ] Thread `currency_api_db_path` through all-chain scanner stablecoin refresh
      flow, after currency API scanning.
- [ ] Add migration/backfill helper and review report for source currencies.
- [ ] Seed curated non-USD stablecoin `source_currency` values.
- [ ] Add tests for EUR, JPY, missing source currency and missing FX rows.
- [ ] Run focused tests with `.local-test.env`.
