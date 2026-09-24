# Sparkline export optimisations plan

## Goal

Make the production vault sparkline export bounded, deterministic and cheap
enough to run after normal scanner post-processing without Matplotlib stalls or
large memory spikes. Preserve the existing public object names, image sizes,
colours, gzip transport and 90-day chart semantics.

In addition, publish supported low-TVL vault sparklines no more than once every
three days while continuing to process higher-TVL vaults on every normal
sparkline-export invocation. Maintain this cadence in persistent, atomic local
state so container restarts do not reset every low-TVL vault to due.

This plan covers the six optimisations identified by the 2026-09-22 production-
shaped smoke test:

1. handle constant-price series safely;
2. replace Matplotlib with direct SVG and Pillow PNG rendering;
3. stop expanding daily observations to hourly points;
4. detect unchanged inputs before rendering or touching R2;
5. stream bounded render/upload batches rather than retaining every image; and
6. use a sparkline-specific, conservative worker count.

## References reviewed

- `eth_defi/research/sparkline.py`: current Matplotlib preparation, rendering
  and export functions.
- `scripts/erc-4626/export-sparklines.py`: eligibility, process-pool rendering,
  R2 change detection and publication orchestration.
- `eth_defi/cloudflare_r2.py`: deterministic source digests and
  `HeadObject`-based unchanged-object checks.
- `eth_defi/vault/post_processing.py`: public and crypto cleaning order and the
  current sparkline post-processing phase.
- `eth_defi/vault/crypto_vaults.py`: private stablecoin/ETH/BTC daily price
  materialisation and atomic sticky-state conventions.
- `eth_defi/vault/denomination.py`: reviewed denomination-family classifier and
  fixed-rate threshold conversion.
- `eth_defi/vault/scan_all_chains.py`: persistent pipeline paths, loop cadence
  and daily local backup list.
- `tests/research/test_sparkline.py` and
  `tests/erc_4626/test_export_sparklines.py`: current rendering and
  orchestration regression coverage.
- `tests/vault/test_crypto_vaults.py`: denomination and threshold conversion
  tests.
- `scripts/erc-4626/README-vault-scripts.md`: operator documentation for the
  standalone sparkline export.

## Current findings

The 2026-09-22 smoke test used the local production-shaped Parquet containing
10,116,623 rows and 12,795 vault IDs. The current inclusion policy selected
5,500 vaults, of which 5,426 had enough finite price history. Preparing the
full set took 10.69 seconds, so input loading and Pandas preparation are not the
primary problem.

Of the 5,426 prepared vaults, 1,905 had an exactly constant visible share-price
series. The current renderer passes `[y_min, y_max]` with identical values to
`Axes.imshow()`. Although Matplotlib expands identical axis limits, it does not
repair the gradient image extent. SVG export can then stall in
`matplotlib.image._resample`; the 100-vault smoke test reproduced this on its
seventh vault.

For 60 non-constant vaults, serial Matplotlib SVG rendering took 0.67 seconds,
PNG rendering 0.96 seconds, and compression plus hashing 0.70 seconds. A
20-process render took 1.56 seconds, including worker overhead. The average
uncompressed Matplotlib SVG was approximately 177 KiB because the daily series
is expanded to hourly points and the gradient is rasterised inside the SVG.

A direct-rendering prototype completed a mixed 98-vault sample, including
constant-price vaults, in 0.007 seconds for native SVG and 0.651 seconds for
two-times-supersampled Pillow PNG. Its average SVG was 1.6 KiB and average PNG
4.2 KiB. Treat these figures as implementation guidance, not permanent timing
assertions in unit tests.

The looped production configuration checks scheduling every hour. At least one
native protocol is normally due every four hours, and post-processing currently
runs sparkline export after every non-empty scan tick. The new three-day rule
is therefore a per-vault throttle inside the sparkline exporter, not a change
to chain or protocol scan scheduling.

## Output and input contract

Keep the existing public object contract unchanged:

```text
sparkline-90d-{vault_id}.svg
sparkline-90d-{vault_id}.png
Content-Encoding: gzip
SVG: 100 x 25 pixels
PNG: 300 x 300 pixels
Window: 90 calendar days
Minimum finite history: 14 elapsed days
```

Use `$PIPELINE_DATA_DIR/crypto-vaults/crypto-cleaned-vault-prices-1d.parquet`
as the canonical sparkline input instead of the public stablecoin-only
`cleaned-vault-prices-1h.parquet`. The crypto file already contains the
supported stablecoin, ETH and BTC denomination families, preserves the final
real observation for each occupied UTC date, and is built before sparkline
export in post-processing. Sparkline preparation already reduces input to
daily observations, so consuming this daily file does not reduce chart
resolution.

Change `run_post_processing()` so sparkline export requires both public
cleaning and crypto cleaning to have succeeded in the current run. Pass the
resolved crypto price path, vault database path and new state path explicitly
to the exporter rather than relying on independent default-path discovery. A
standalone invocation resolves the same paths through `get_pipeline_data_dir()`.
Never fall back to a stale public-only file when current crypto cleaning fails.
Assert at load time that the crypto Parquet has a naive UTC timestamp index and
at least `id`, `share_price` and `total_assets` columns with numeric price/asset
values. A missing or incompatible `total_assets` column is a hard input-schema
failure, not a reason to classify every vault as low TVL.

Remove the existing stablecoin-only and minimum-peak-TVL exclusion from
`get_included_vault_ids()`. Eligibility becomes:

1. the metadata denomination classifies as stablecoin, ETH or BTC through
   `classify_denomination()`;
2. the price file contains finite share-price observations spanning at least
   `MIN_SPARKLINE_HISTORY`; and
3. the vault has at least one finite `total_assets` observation from which the
   current TVL class can be determined.

Low TVL affects cadence, not permanent eligibility. This is what allows small
vaults to receive sparklines every three days instead of receiving no
sparkline at all. Retire the ApeX-only USD 500 peak-TVL exception because it is
superseded by the common low-TVL cadence.

## Low-TVL policy

Classify a vault from its latest finite `total_assets` observation at or before
the sparkline's `end_at`, using the persisted metadata denomination symbol.
`total_assets` is already expressed in the denomination token's human-readable
unit; do not multiply by token decimals and do not use historical peak TVL for
cadence.

The strict low-TVL thresholds are:

| Denomination family | Low when latest TVL is below | Fixed conversion policy |
|---|---:|---|
| stablecoin/USD | 5,000 | USD 5,000 / USD 1 per stable unit |
| ETH | 2.5 ETH | USD 5,000 / USD 2,000 per ETH |
| BTC | 0.1 BTC | Policy-equivalent USD 6,000 / USD 60,000 per BTC; not a live valuation |

Equality is not low TVL: 5,000 USD units, 2.5 ETH and 0.1 BTC are high-TVL
for cadence purposes. Missing, non-finite or negative latest TVL is a data
quality error and skips that vault with a warning; it must not silently choose
a cadence or advance completion state. Zero is valid TVL and classifies as low.

Parquet values may arrive as `float64`, while policy thresholds are `Decimal`.
Use one comparison helper for every family: validate finiteness in the source
numeric type, construct `Decimal(str(latest_total_assets))`, and compare it to
the `Decimal` threshold with `<`. Never compare a binary float directly with a
`Decimal`, and never make scheduling comparisons against the float copy stored
for state observability.

Add named `Decimal` policy constants and a typed
`resolve_sparkline_tvl_threshold(denomination_symbol)` helper to
`eth_defi/vault/denomination.py`. Reuse `classify_denomination()` and
`convert_usd_threshold_to_denomination()` so wrappers follow the same reviewed
canonical-family mapping as the crypto export. Pass family-specific USD
guidelines to the existing converter: USD 5,000 for stablecoin and ETH, and
USD 6,000 for BTC. Do not call the converter with USD 5,000 for BTC, because
that produces 0.083333 BTC and violates the explicit 0.1 BTC policy. Do not
use the daily exchange-rate database for this scheduling decision: these are
stable publication-policy thresholds, not live USD valuations.

Add boundary tests for native and wrapped symbols, including `USDC`, `sUSDe`,
`WETH`, `wstETH`, `WBTC` and `cbBTC`, plus unsupported and blank symbols.
Test zero, the exact threshold, and one value on each side of the threshold for
all three families.

## Persistent sparkline state

Create `$PIPELINE_DATA_DIR/sparkline-export-state.json`. Keep it separate from
`vault-export-state.json`, `crypto-vault-export-state.json` and scanner cycle
state because it records image publication, not metadata qualification or data
fetch completion.

Use an explicit versioned JSON envelope:

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-22T00:00:00Z",
  "renderer_version": 1,
  "vaults": {
    "1-0x...": {
      "denomination_family": "stablecoin",
      "low_tvl": true,
      "latest_total_assets": 1234.5,
      "threshold": 5000.0,
      "input_sha256": "...",
      "last_seen_at": "2026-09-22T00:00:00Z",
      "last_attempted_at": "2026-09-22T00:00:00Z",
      "last_completed_at": "2026-09-22T00:00:00Z",
      "last_uploaded_at": "2026-09-22T00:00:00Z",
      "renderer_version": 1,
      "publication_target": "https://account.r2.cloudflarestorage.com/sparklines",
      "consecutive_failures": 0,
      "next_retry_at": null
    }
  }
}
```

`last_completed_at` means the exporter successfully established that both SVG
and PNG are current, either by uploading them or by matching an already
successful canonical input hash for the same renderer version and non-secret
R2 destination. `last_uploaded_at` changes only when at least one object was
actually uploaded. Use naive UTC internally and serialise with a `Z` suffix
through one focused parser/formatter pair. Timestamp and digest fields that
have no prior outcome are nullable; validate every populated entry strictly
rather than coercing malformed values. The standalone benchmark calls the
rendering helpers directly and never persists publication state.

Define `SPARKLINE_STATE_RETENTION_DAYS = 90`. `last_seen_at` records the most
recent run whose input contained usable price history for the vault, even when
its current TVL value was invalid. Prune at state-save time only
when `now - last_seen_at > 90 days`; exactly 90 days remains retained.

Evaluate scheduling in this order:

1. `FORCE_SPARKLINE_EXPORT=true` bypasses cadence, failure backoff and the local
   unchanged-input fast path, but not input validation or remote digest checks.
2. Invalid TVL skips the vault without creating new state. If an entry already
   exists, refresh only `last_seen_at` so repeated bad input remains observable
   without overwriting its last valid classification.
3. Unless forced, a future `next_retry_at` defers both high- and low-TVL vaults.
4. A current high-TVL classification is due on every invocation after any
   applicable failure backoff.
5. A current low-TVL classification is due if it has never completed or when
   `now >= last_completed_at + 72 hours`. A high-to-low transition retains the
   previous completion time and does not itself make the vault due.

State rules:

1. A new low-TVL vault is due immediately.
2. A low-TVL vault is next due when `now >= last_completed_at + 72 hours`.
3. A high-TVL vault is considered on every sparkline-export invocation, but
   unchanged-input detection may turn that consideration into a local no-op.
4. A low-to-high transition bypasses the 72-hour throttle and is considered
   immediately after any applicable failure backoff.
5. A high-to-low transition uses the previous successful
   `last_completed_at`; it does not force an extra export merely because the
   classification changed.
6. A failed render, failed upload, partial two-format publication or invalid
   TVL never advances `last_completed_at`, `last_uploaded_at` or
   `input_sha256`.
7. If one format succeeds and the other fails, preserve the prior digest and
   completion/upload timestamps while advancing only the failure/backoff
   fields. The next eligible retry repeats the vault; deterministic bytes and
   R2 digest checks make the repeated successful format harmless.
8. Missing state initialises an empty document. Invalid JSON, a wrong schema or
   invalid entry data raises a hard error instead of silently discarding the
   schedule.
9. Keep entries for temporarily absent vaults so a short-lived input omission
   does not reset their cadence. Apply the named 90-day retention rule above.

Use the following field-update matrix; classification is recomputed from
current input before cadence evaluation and never inferred from stale state:

| Outcome | Fields updated |
|---|---|
| Seen but low-TVL throttled | `denomination_family`, `low_tvl`, `latest_total_assets`, `threshold`, `last_seen_at` |
| Due and canonical input unchanged | Classification fields, `last_seen_at`, `last_attempted_at`, `input_sha256`, `last_completed_at`; clear failure fields; leave `last_uploaded_at` unchanged |
| Both formats successfully checked/uploaded | Classification fields, `last_seen_at`, `last_attempted_at`, `input_sha256`, `last_completed_at`; set `last_uploaded_at` only if a PUT occurred; clear failure fields |
| Render or publication failure | Classification fields, `last_seen_at`, `last_attempted_at`, incremented `consecutive_failures`, `next_retry_at`; preserve the prior digest and completion/upload timestamps |
| Invalid TVL with existing state | `last_seen_at` only; preserve cadence, digest, classification and completion fields; do not create an entry for a never-seen vault |

Low-TVL cadence limits successful completion to once per 72-hour window. A
failure is not a successful export, but it must not be retried on every scanner
tick. Persist an exponential retry delay of 6 hours, 12 hours and then 24 hours
maximum in `next_retry_at`; both high- and low-TVL failed vaults respect it.
There are no exporter-level in-run retries. Botocore and the existing R2
`HeadObject` helper may apply their own bounded transport retries, after which
the per-vault attempt is final for this run.

Write state with `atomicwrites.atomic_write()` after every completed bounded
batch, merging successful outcomes and failure-backoff fields before each
commit. A crash before one batch commit can repeat only that batch and cannot
defer an unpublished image. Persist successful vault advances even when
another vault fails and the overall sparkline step returns failure.
Add the state path to `scan_all_chains.py`'s `bkp_files` so it receives the
existing first-run-of-day local backup. Do not add this operational state to
public R2 data-file exports.

Serialise all sparkline runs through the existing pipeline writer lock. Replace
the dynamic script `main()` call in post-processing with a typed library entry
point that assumes the surrounding scan already holds `scan-pipeline`. The
standalone script wrapper acquires that same lock before reading inputs or
state. This prevents the standalone exporter and looped scanner from applying
last-writer-wins state updates or reading a Parquet file during replacement.

Add `FORCE_SPARKLINE_EXPORT=true` as an operator repair switch. It bypasses
the 72-hour cadence, persisted failure backoff and unchanged-input skips but
still requires valid inputs and advances state only after successful
publication. Pass
`SKIP_SPARKLINES` and the new force setting through both Compose scanner
services; `SKIP_SPARKLINES` is read by Python today but is not currently
forwarded by `docker-compose.yml`.

## 1. Handle constant-price series safely

Move pixel-coordinate calculation into a renderer-independent helper. For a
non-zero price range, retain the current equal vertical margins. For an exact
constant range, place every point on the vertical centre line and omit the
zero-height gradient area. A one-point or constant chart therefore becomes a
normal horizontal green line on the existing background and never constructs
a degenerate image or polygon extent.

Keep finite-price validation in `filter_finite_share_prices()`. Explicitly test
an exact constant series, a near-constant series, nullable values and a series
whose first observation begins inside the 90-day window. Both output formats
must complete and have the expected dimensions.

## 2. Replace Matplotlib with direct renderers

Add small deterministic render functions in `eth_defi/research/sparkline.py`:

- native SVG: emit the background rectangle, one SVG `linearGradient`, one
  clipped area path when the series is non-constant, and one line path;
- PNG: use Pillow, render at two-times resolution for antialiasing, composite
  the vertical gradient through a polygon mask, draw the line, then downsample
  with Lanczos to 300 x 300.

Do not add Cairo, pyvips, OpenCV, Plotly or Kaleido. Pillow is already present
in the installed project environment and the prototype demonstrates that it is
sufficient. If Pillow is not currently a direct project dependency, add it to
the appropriate existing image/data extra with the required explanatory
`pyproject.toml` comment and regenerate the lock file.

Keep SVG numeric formatting fixed and bounded, use a fixed element ID, omit
timestamps and metadata, and preserve stable attribute ordering so identical
inputs produce byte-identical output. Keep PNG save options explicit and
stable. Increment `SPARKLINE_RENDERER_VERSION` whenever visual semantics,
dimensions, compression-independent bytes or style constants change.

Retain the Matplotlib `render_sparkline_simple()` API only if another caller
still needs it. Remove Matplotlib from the production gradient export path and
avoid returning `Figure` objects from the new public functions. Update the
Sphinx-visible API documentation and type hints to reflect byte-returning
renderers.

Before replacement, generate representative old/new fixtures for rising,
falling, sparse, young and constant vaults and inspect them side by side. Exact
pixel equality is not required, but the 90-day x placement, colours, margins,
line direction and blank pre-history must remain recognisable.

## 3. Keep daily observations sparse

Remove `forward_fill_vault()` from production sparkline rendering. The prepared
input already contains no more than 91 daily points. Preserve the existing
visual step semantics without materialising hourly rows by encoding horizontal
then vertical path segments between observations. If product design instead
prefers straight interpolation, make that an explicit reviewed visual change;
do not let a renderer rewrite decide it accidentally.

Coordinate calculation must use the explicit `(start_at, end_at)` 90-day range,
not the first and last observation. A young vault therefore retains blank space
on the left, while an established sparse vault continues to receive the seeded
observation at `start_at` from `prepare_sparkline_data()`.

Both bounds are data-derived and stable across wall-clock runs: `end_at` is the
normalised UTC day of the latest finite share-price observation and `start_at`
is exactly `end_at - 90 days`. Never use the export time as a chart bound. A
dormant vault with unchanged observations must retain the same coordinates and
canonical input digest indefinitely.

## 4. Skip unchanged inputs before rendering

Calculate one canonical input SHA-256 per vault before rendering. Hash a
versioned serialisation containing:

- `SPARKLINE_RENDERER_VERSION`;
- output dimensions and all style constants;
- `start_at` and `end_at` as integer UTC nanoseconds;
- the ordered finite daily timestamps as integer UTC nanoseconds; and
- share prices encoded in a stable binary float representation.

Do not hash Pandas string output, locale-sensitive text or `total_assets`.
TVL affects scheduling but not pixels. Do not use the old rendered-byte digest
as the pre-render input digest; retain rendered-byte digests only for R2 object
metadata compatibility.

When a due vault's state has the same renderer version and input digest from a
previous successful two-format publication, skip rendering, compression and R2
`HeadObject` requests, then advance `last_completed_at`. Missing state, changed
input or a renderer-version change enters render/upload. The force flag always
enters render/upload.

This local fast path assumes R2 objects are not independently deleted. Document
`FORCE_SPARKLINE_EXPORT=true` as the repair procedure for external drift. Keep
the existing per-object `skip_if_current=True` check during actual publication
so a state loss or partial prior run does not upload identical bytes.

## 5. Stream bounded render and upload batches

Replace the all-render-then-all-upload lists with bounded batches, defaulting to
100 vaults. For each batch:

1. classify cadence and discard not-due low-TVL vaults before rendering;
2. calculate input digests and discard unchanged vaults;
3. render the two deterministic byte payloads for each remaining vault;
4. gzip with `mtime=0`, calculate rendered source digests and upload both
   formats;
5. discard payload bytes as soon as each vault finishes; and
6. merge successful per-vault outcomes into the in-memory state.

Checkpoint merged state atomically after each batch as described above. Emit
progress through `tqdm_loggable.auto.tqdm` and log counts for ineligible, insufficient
history, invalid TVL, low-TVL throttled, unchanged, rendered, uploaded,
unchanged on R2 and failed vaults. Never leave a long batch without output for
more than one minute.

Treat one vault as the publication unit: return one result containing both
format outcomes rather than flattening images before upload. Catch only the
specific validation, Pillow, botocore and R2 exceptions that can be handled.
Log a concise warning for the per-vault failure and its computed retry time.
Because the exporter performs no additional in-run attempt, that failure is
final for the current run: log its traceback once and mark the overall
sparkline post-processing step failed after persisting all batch outcomes.

## 6. Use a sparkline-specific worker limit

Add `SPARKLINE_MAX_WORKERS`, default 8, and stop inheriting the scanner-wide
`MAX_WORKERS=50`. Pass it through both Compose scanner services and document it
in `README-vault-scripts.md`.

Use Joblib's threading backend for bounded render/upload work unless profiling
shows the Pillow operations fail to release enough of the GIL. The direct SVG
work is tiny, Pillow performs the significant raster operations in native
code, and threads avoid importing Pandas and Matplotlib into dozens of worker
processes. Cap the R2 client's connection pool to the same worker count.

Benchmark worker counts 1, 4, 8 and 16 on the same deterministic 100-vault
sample. Choose a different default only if 8 is materially slower without a
memory or R2-pressure benefit. Do not encode wall-clock thresholds in normal CI
tests.

## Implementation sequence

1. Add threshold constants/resolution tests and versioned sparkline-state
   load, validation, cadence and atomic-save helpers with unit tests.
2. Add renderer-independent coordinate/path preparation and the constant-range
   regression tests.
3. Add deterministic direct SVG and Pillow PNG renderers, visual fixtures and
   deterministic-byte tests.
4. Change the exporter to consume the current crypto daily Parquet and select
   all three supported denomination families.
5. Add canonical input hashing and state-backed unchanged detection.
6. Refactor publication around per-vault results and bounded batches.
7. Add `SPARKLINE_MAX_WORKERS`, `FORCE_SPARKLINE_EXPORT`, Compose wiring,
   progress counters and operator documentation.
8. Add the new state to local daily backups and gate sparkline post-processing
   on current crypto-cleaning success.
9. Run the focused unit tests, the 100-vault no-upload benchmark, and one
   credential-guarded real R2 integration check.

## Testing and acceptance

Add focused tests covering:

- exact constant, near-constant, sparse, young, nullable and invalid price
  series;
- deterministic SVG and PNG bytes across repeated renders;
- exact 100 x 25 SVG and 300 x 300 PNG dimensions;
- daily step-path construction without hourly materialisation;
- stablecoin, ETH and BTC threshold values and equality boundaries;
- zero TVL as valid low TVL and missing/non-finite/negative TVL as invalid;
- invalid TVL creating no state for a new vault, and updating only
  `last_seen_at` for an existing vault without overwriting its last valid
  classification, digest, cadence or completion fields;
- latest finite TVL selection rather than historical peak TVL;
- a new low-TVL vault, the instant before 72 hours, exactly 72 hours and after
  72 hours;
- low-to-high and high-to-low transitions;
- unchanged input, changed input and renderer-version invalidation;
- missing state, corrupt state and atomic state replacement;
- one-format upload failure not advancing state;
- failure backoff at 6, 12 and capped 24 hours, including no render or R2
  attempt before a future `next_retry_at` for both a never-completed low-TVL
  vault and a high-TVL vault;
- force export bypassing cadence, failure backoff and unchanged skips;
- crypto-clean failure preventing stale sparkline publication;
- bounded batching and correct summary counters; and
- preserved R2 keys, content types, gzip encoding and cache control.

Also cover:

- a dormant vault producing the same bounds and digest at different simulated
  wall-clock times;
- an R2 spy seeing zero `HeadObject` and zero PUT calls across two unchanged
  runs, while a forced run reaches `HeadObject` but performs no PUT when remote
  bytes match;
- classification fields refreshing on every seen outcome without advancing
  completion fields on failure;
- high-to-low classification after a recent unchanged high-TVL completion;
- `last_seen_at` updates, temporary absence, exactly-90-day retention and
  pruning after 90 days;
- a missing `total_assets` crypto-Parquet schema failure;
- end-to-end inclusion of ETH and BTC vaults without the old peak-TVL gate;
- a partial final batch and conservation of counters across every terminal
  outcome;
- a successful vault remaining committed when a later vault in the same run
  fails, with the overall step reporting failure only after outcomes are
  checkpointed;
- an injected crash after a completed batch proving that the committed batch
  survives and only the uncommitted batch is repeated; and
- XML parsing of constant and non-constant SVG output.

Extend the existing standalone-script orchestration test rather than importing
the `tests` package from production code. Follow the repository test command
rule:

```shell
source .local-test.env && poetry run pytest \
  tests/research/test_sparkline.py \
  tests/erc_4626/test_export_sparklines.py \
  tests/vault/test_crypto_vaults.py \
  tests/erc_4626/test_post_processing.py
```

Provide a no-upload benchmark mode or a focused benchmark script that reads a
deterministic 100-vault sample from the configured pipeline directory, reports
preparation/render/compression timings and payload sizes, and never creates an
R2 client. It must show progress during long preparation.

Because this materially changes the R2 integration, perform one minimal real
integration test using supplied environment credentials. Use a unique
`UPLOAD_PREFIX`, publish one non-constant and one constant fixture, fetch object
metadata, verify content type/encoding/digest, and clean up only those exact
test-prefixed keys. Never print credentials. Record the exact command, pass or
failure, date and redacted bucket/provider in the pull-request comment, as
required by the repository integration-test policy.

Acceptance criteria:

1. The production-shaped 100-vault sample completes without a stall and
   includes constant-price vaults.
2. Re-running an unchanged sample performs no rendering and no R2 requests
   unless forced.
3. A low-TVL vault successfully completes publication at most once per 72-hour
   window, while a newly high-TVL vault is processed immediately and failures
   follow the documented persisted retry backoff.
4. Peak memory remains bounded by the configured batch and worker counts rather
   than total eligible-vault count.
5. Native SVG averages remain below 5 KiB on the reference sample and retain
   deterministic bytes.
6. Existing object keys and frontend-visible dimensions remain unchanged.
7. The focused test set and the minimal real R2 integration check pass.

## Rollout and observability

Deploy first with `SPARKLINE_MAX_WORKERS=8` and retain the current invocation
frequency. On the first run, missing state intentionally makes every eligible
vault due; expect one full publication and then state-backed no-ops. Do not
preseed state from unverified R2 contents.

Log one end-of-run summary with elapsed preparation, rendering and R2 times,
the state path/schema, family counts, high/low TVL counts and every skip/failure
counter. Never log credentials or signed URLs. A run with any per-vault final
failure returns a failed post-processing status even if other vaults succeed.

After one week, compare run duration, peak resident memory, R2 request counts,
rendered vault count, unchanged count and low-TVL due count with the pre-change
logs. Only then consider lowering the high-TVL invocation frequency; the first
rollout should isolate renderer/state improvements from scheduler changes.

Rollback is code-only. Existing R2 keys remain compatible. Preserve
`sparkline-export-state.json` during rollback so a subsequent fixed deployment
does not republish all low-TVL vaults; older code ignores the file.

## Kimi review record

Kimi 2.0.2 reviewed the complete draft twice in no-tools mode on 2026-09-22.
Its design and test-coverage findings were incorporated before this plan was
finalised:

- derive chart bounds from the latest finite observation rather than wall-clock
  time, so dormant-vault hashes remain stable;
- normalise Parquet numerics through `Decimal(str(value))` and specify exact
  threshold-boundary tests;
- define a complete state field-update matrix, per-batch atomic checkpoints,
  retention and persisted failure backoff;
- lock standalone and scanner-driven runs against concurrent state writes;
- add zero-R2, forced-run, partial-batch, transition, XML and schema regression
  tests;
- verify invalid-TVL state preservation, retry deferral precedence, and
  successful checkpoint retention when a later vault fails; and
- phrase the low-TVL guarantee in terms of successful completion, while failed
  attempts follow the separate bounded retry schedule.

The remaining operational risks are explicitly handled by hard failure on
corrupt state, the shared writer lock, the force-repair switch for external R2
drift, and benchmark results being treated as machine-specific guidance rather
than CI timing assertions.
