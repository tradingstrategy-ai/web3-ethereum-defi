# Tokenised-fund price-feed cycle plan

## Goal

Make every reviewed, refreshable tokenised-fund price adapter a separate
scheduled item in `scan-vaults-all-chains.py`. Registered products with missing
history must be filled automatically with approximate daily samples, without
deleting another vault's rows or reader state.

This plan covers recurring prices for products already present in the vault
metadata database. The existing protocol backfills remain responsible for
registering new products and for controlled metadata repairs.

## Why BCAP became stale

BCAP had valid Securitize rows from a one-off backfill, but no recurring
Securitize scheduler item appended later samples. The generic chain task was
not a reliable substitute because its activity filter can reject permissioned
tokenised funds, its cycle state is chain-wide, and its historical rewrite was
not scoped to the remaining addresses.

## Reviewed protocol coverage

The scheduler manifest must classify every selector in
`PROTOCOL_BACKFILLS`. A registry test fails when a selector is missing from the
manifest.

| Protocol | Dashboard item | Recurring price capability |
| --- | --- | --- |
| Asseto | `Asseto` | Reviewed vault adapter, sampled daily; refetch the latest seven stored samples when state is missing |
| Centrifuge | — | Supply only; no reviewed NAV/share source |
| FDIT | — | Supply only; no reviewed NAV/share source |
| Franklin | `Franklin` | Onchain `lastKnownPrice` adapter |
| Kinexys | — | Static adapter estimate, not a refreshable source |
| KAIO | — | Supply only; no reviewed NAV/share source |
| Libeara | `Libeara` | CUMIU and BELIF NAV adapters; ULTRA remains supply only |
| Midas | `Midas` | Products explicitly marked as tokenised funds |
| Ondo | `Ondo` | Issuer oracle adapter |
| OpenEden | `OpenEden` | TBILL oracle adapter |
| Securitize | `Securitize` | Products accepted by `has_historical_price()`, including BCAP |
| Spiko | `Spiko` | USTBL oracle adapter |
| Superstate | `Superstate` | USTB oracle adapter |
| Sygnum | `Sygnum` | Existing FILQ historical adapter, sampled approximately daily |
| Theo | — | Supply only; no reviewed scalar NAV source |
| USYC | `USYC` | Official oracle adapter |
| WisdomTree | `WisdomTree` | DataSpan-backed adapter; requires `WISDOMTREE_DATASPAN_API_KEY` |

These tasks use the existing vault adapters and common archive-state price
scanner. They produce approximate daily snapshots; they do not preserve every
source update or claim that the sampled block timestamp is the source's
publication timestamp.

## Scanner boundary

Add `eth_defi/tokenised_fund/scan.py` with:

- an immutable `TokenisedFundPriceScanSpec` registry;
- explicit context and result dataclasses;
- exact feature and product predicates for registered metadata rows;
- a complete capability manifest for scheduled and deliberately unsupported
  protocols;
- one common runner that reuses connections by chain and calls
  `scan_historical_prices_to_parquet()` once per exact product with
  `frequency="1d"` and a one-address ownership set.

The runner is stateless: the latest raw sample is its continuation marker. This
avoids adaptive generic-vault throttling and keeps the dedicated output near a
one-day frequency. A failed protocol does not advance its cycle key; an
idempotent retry may reuse rows committed before the failure.

## Missing-history and incremental rules

Resolve the start independently for every exact target:

1. With existing raw rows, resume from the last block carrying a valid price,
   so newer null or `NaN` samples cannot conceal a gap. Asseto and WisdomTree
   instead replay the latest seven priced samples because recent issuer data
   can be revised.
2. With no raw rows, start at the persisted vault discovery/deployment block.
3. Scan and rewrite only that target address before moving to the next target.

The runner never combines a bootstrap target and an incremental target in one
delete window. Generic reader states do not affect the dedicated daily scans.

Starting at token deployment intentionally prioritises broad coverage over an
exact oracle inception boundary. An adapter may emit empty pre-source samples;
priced history begins only where its reviewed source is available.

## Ownership and storage safety

When dedicated scheduling is enabled:

- exact products accepted by ready scheduled scanners are excluded from the
  generic chain price reader, including items not due in the current tick;
- the generic price writer receives its remaining exact addresses so its
  delete-and-rewrite phase cannot remove dedicated rows;
- disabled, unselected and product-filtered vaults stay with the generic
  reader, so configuring a focused selection does not stop their history;
- `SKIP_TOKENISED_FUNDS=true` is the explicit compatibility switch that
  restores generic ownership.

Dedicated feeds run only when `SCAN_PRICES=true`. All writes occur sequentially
under the existing pipeline lock and use existing atomic Parquet and pickle
paths. No schema failure may reset existing Parquet data.

## Scheduling and dashboard

Give all 12 scheduled protocols a built-in 24-hour cycle. An explicit
`SCAN_CYCLES` entry still overrides an item's interval. Each selected and ready
protocol receives its own dashboard row, `ChainResult` and cycle-state key.

Run tokenised-fund tasks after EVM chains and CurrencyRates, and before the
single post-processing pass. Continue to later protocols when one fails.

The dashboard reads the shared raw Parquet once per refresh and maps exact
registered targets to each protocol. `Last data` is the latest valid sampled
row. It is not an oracle/API freshness guarantee.

Operator settings:

- `SKIP_TOKENISED_FUNDS=true` disables dedicated ownership;
- `TOKENISED_FUND_PROTOCOLS=securitize,asseto` narrows active tasks while
  keeping omitted rows visibly disabled;
- `TOKENISED_FUND_MAX_WORKERS` defaults to 8;
- `WISDOMTREE_DATASPAN_API_KEY` enables WisdomTree; without it the item is
  visibly disabled;
- `Securitize=24h` and similar `SCAN_CYCLES` entries override defaults.

Pass these settings through both production scanner services in
`docker-compose.yml`.

## Documentation and tests

Update the vault-scripts README and API documentation to distinguish:

- recurring approximate daily samples;
- initial or corrective protocol backfills;
- sample timestamps from underlying source publication timestamps;
- automatic history filling for registered products from source availability,
  rather than a promise of valid prices before an oracle or API existed.

Focused offline tests cover:

- manifest completeness and stable selection;
- daily scheduler defaults and per-protocol cycle callbacks;
- deployment bootstrap when raw rows are absent;
- bounded continuation and issuer-tail replay from raw rows;
- independent rewrite boundaries for incremental and new targets;
- address ownership exclusion and scheduler result translation.

## Acceptance

The implementation is complete when:

1. all 12 reviewed price-capable protocols appear as independent daily items;
2. BCAP and other registered targets automatically fill missing approximate
   daily history and continue incrementally on later cycles;
3. generic chain rewrites cannot delete or duplicate dedicated rows;
4. unsupported protocols have explicit reasons in the manifest;
5. WisdomTree is visibly disabled without its required credential;
6. documentation does not describe unimplemented source-specific watermarks,
   freshness validation or event-preserving ingestion.
