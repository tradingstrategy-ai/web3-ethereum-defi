# Sparkline multiprocessing plan

## Goal

Use multiprocessing by default for per-vault preparation and image generation,
while keeping threads for R2 uploads. Measure the full no-upload export and
choose a worker count that improves throughput without excessive memory use.

Do not change chart appearance, output bytes, object names, cadence or
publication state semantics. Batches remain bounded at 100 source vaults, but
their boundaries and the batch counter can shift when ineligible vaults are
filtered in a child process rather than before batching.

## What improvement to expect

The initial smoke measurement on the production-shaped input was:

| Workload | Workers | Result |
|---|---:|---:|
| 100 vaults, render only | 8 threads | 0.187 seconds |
| 100 vaults, render only | 24 threads | 0.181 seconds |
| 15,340 vaults, full forced export without uploads | 8 threads | 84.96 seconds |
| 15,340 vaults, full forced export without uploads | 24 threads | 84.35 seconds |

The eight-to-24-thread plateau did not establish a GIL bottleneck. A later
one-thread measurement of the same 100 vaults took 0.690 seconds, versus
0.175 seconds with eight threads: rendering already gains about 3.9× from
threads. Eight processes took 0.143 seconds, about 18% faster than eight
threads for the isolated sample. The full export also spends substantial time
outside image generation.

The repeatable benchmark was run against the same local snapshot, all 15,340
eligible vaults, 100-vault batches, one warm-up and three measured sample
renders. The full coordinator ran once per setting with forced rendering,
uploads disabled and state in a temporary directory:

| Backend | Workers | 100-vault median render | Full dry-run time | Peak process-tree RSS |
|---|---:|---:|---:|---:|
| threads | 8 | 0.175 s | 81.47 s | 3.73 GiB |
| processes | 1 | 0.692 s | 165.44 s | 3.74 GiB |
| processes | 2 | 0.403 s | 114.57 s | 3.71 GiB |
| processes | 4 | 0.193 s | 87.16 s | 4.20 GiB |
| processes | 8 | 0.143 s | 79.21 s | 5.17 GiB |

A separate timed full dry run identified where the 15,340-vault export spends
its time. Each backend was measured once with the same input and no uploads:

| Stage | Eight threads | Eight processes |
|---|---:|---:|
| Prepare vault histories | 29.67 s | 29.81 s |
| Check eligible vaults | 8.17 s | 7.98 s |
| Render 154 batches | 25.61 s | 20.74 s |
| Save state after each batch | 10.03 s | 9.70 s |
| Full run | 81.12 s | 75.93 s |

Process rendering was about 19% faster in the full render phase. The full-run
gain was 2.8% in the first comparison and 6.4% in the instrumented comparison;
single runs vary, and both gains remain below the production acceptance bar.

Every original candidate produced the same ordered output digest. Eight
processes were faster for the full dry run, but added approximately 1.45 GiB
peak process-tree RSS. Four processes were slower than threads. This showed
that process-only rendering was insufficient: per-vault preparation and
eligibility checks still ran serially in the parent.

The follow-up moved history preparation, TVL classification, cadence and
digest checks, and image generation into one process task per vault. It also
grouped source rows once into contiguous slices and reduced state-write
overhead. On the same 15,340-vault snapshot, one forced full no-upload run per
setting measured:

| Implementation | Workers | Full dry-run time | Peak process-tree RSS |
|---|---:|---:|---:|
| Original threaded path | 8 threads | 81.47 s | 3.73 GiB |
| Process-only rendering | 8 processes | 79.21 s | 5.17 GiB |
| Final fused preparation and rendering | 6 processes | 37.02 s | 4.63 GiB |
| Final fused preparation and rendering | 8 processes | 33.23 s | 5.10 GiB |

Six processes meet the 5 GiB target and reduce full no-upload time by about
55% versus the original threaded path. Eight are 3.8 seconds faster but exceed
the target. The production default is therefore six render processes and eight
upload threads. These are single-run local timings, and R2 upload throughput
was not measured.

Joblib runs ``n_jobs=1`` inline in the parent, so that row measures serial
execution rather than a one-child process pool.

## 1. Separate render and upload workers

Add two settings:

- `SPARKLINE_RENDER_WORKERS`, 6 by default after the memory and speed check;
- `SPARKLINE_UPLOAD_WORKERS`, 8 by default to preserve current upload
  concurrency.

Pass both through the two scanner services in `docker-compose.yml`.

Use `SPARKLINE_RENDER_WORKERS` only for image generation. Use
`SPARKLINE_UPLOAD_WORKERS` for the R2 client connection pool and upload
threads. Remove the current assumption that one worker count is suitable for
both operations.

Keep `SPARKLINE_MAX_WORKERS` as a temporary fallback for upload threads only.
A legacy thread count must not unexpectedly create that many renderer
processes. Document the two new settings and the fallback.

## 2. Add a process option for rendering

Use Joblib's Loky process backend by default. Retain the thread backend as an
explicit comparison and fallback. Continue to process batches of 100 vaults
so image payloads remain bounded.

Pass only the current vault's source rows, denomination symbol and prior state
entry to each worker. The worker prepares and checks the vault, then renders
only when due. Do not pass the full price DataFrame, vault database or R2
client. The parent alone owns publication and state writes.

Keep the existing `_render_one_safe()` result contract so one vault's render
exception is reported without failing other vaults in the batch. Unexpected
preparation errors and worker crashes still abort the current batch. Preserve
the input order when collecting results.

Joblib reuses Loky workers between batches. Do not add a custom queue, custom
executor abstraction, worker initialiser or render/upload overlap in the first
implementation. They can be considered later only if profiling shows a
specific remaining bottleneck.

## 3. Keep uploads threaded

Keep `_publish_rendered_vault()` and `upload_sparklines()` in the parent
process and continue using Joblib's threading backend.

One upload task continues to publish both SVG and PNG for one vault. Preserve
the current gzip, digest, `HeadObject`, cache-header and partial-failure
behaviour.

Rendering and upload remain sequential within each 100-vault batch:

1. render the batch with the configured backend;
2. upload the completed images with threads;
3. update and save state; and
4. discard the batch before starting the next one.

This is simpler and keeps memory predictable. Pipelining rendering and uploads
is not part of this change.

## 4. Measure memory

Measure the parent and child processes together. Record:

- parent RSS;
- the sum of child RSS;
- total process-tree RSS; and
- cgroup or container peak memory when available.

Run the full no-upload benchmark across candidate process counts. The fused
six-process path meets both the full-run improvement and memory targets;
eight processes exceed the latter.

The practical memory target is a process-tree peak below 5 GiB for the
sparkline-only run. Also compare the peak with the complete production scanner,
because its 48 GiB container limit is shared with other post-processing work.

## 5. Benchmark before and after

Extend `scripts/erc-4626/benchmark-sparklines.py`. It must never upload and
must report preparation and rendering separately.

Run these configurations against the same input and vault IDs:

| Backend | Render workers | Purpose |
|---|---:|---|
| threads | 8 | Current baseline |
| threads | 24 | Confirm the thread plateau |
| processes | 1 | Measure process overhead |
| processes | 2 | Conservative candidate |
| processes | 4 | Mid-range candidate |
| processes | 8 | Check whether further scaling is worthwhile |

Run both:

- the deterministic 100-vault smoke sample; and
- all eligible vaults in 100-vault batches.

For the smoke sample, perform one warm-up followed by three measured runs and
report median render time, vaults per second, image count and peak process-tree
memory. Run the full coordinator once per configuration, reporting elapsed time,
rendered and failed vaults, and peak process-tree memory. Hash the ordered
sample output payloads and require the thread and process backends to produce
identical hashes.

Also repeat the existing forced full export with uploads disabled. This is the
number that shows the actual end-to-end benefit after serial preparation and
state handling. Use a temporary state file so the benchmark cannot change
production cadence state.

Select multiprocessing for production only when it:

1. produces identical images;
2. makes isolated rendering clearly faster;
3. improves the full no-upload export by at least 10%;
4. stays below the memory target; and
5. completes with no missing or failed vaults.

The first process-only rendering implementation missed these conditions. The
fused six-process implementation meets them: output parity was checked,
15,340 vaults rendered without failures, full no-upload time fell from 81.47
to 37.02 seconds, and sampled process-tree RSS was 4.63 GiB. Summed RSS counts
shared pages and is not an exact physical-memory measurement.

## 6. Tests and documentation

Add focused tests for:

- rendering the same constant, rising, falling and sparse inputs through the
  thread and process backends with identical bytes;
- successful rendering across a real Joblib process boundary;
- one vault failure not failing the rest of its batch;
- separate render and upload worker settings;
- the R2 connection pool following upload workers; and
- two batches completing with correct counters and state.

Run:

```shell
source .local-test.env && poetry run pytest \
  tests/research/test_sparkline.py \
  tests/research/test_sparkline_export.py \
  tests/erc_4626/test_export_sparklines.py
```

Update `scripts/erc-4626/README-vault-scripts.md` with the two worker settings,
the chosen benchmark result and the approximate memory cost per render worker.

## Implementation order

1. Extend the no-upload benchmark and capture the current threaded result.
2. Split render and upload worker settings.
3. Add selectable Loky process rendering and move per-vault preparation and
   decisions into the worker.
4. Keep upload execution on threads.
5. Add the focused tests.
6. Benchmark candidate process counts and choose six under the memory target.
7. Update Compose and documentation.
8. Make processes the production default after the same-input benchmark meets
   the speed and memory conditions above.

## Kimi review

Kimi 2.0.2 reviewed the earlier detailed version using maximum thinking. Its
useful findings retained here are:

- compare the full export, not only isolated rendering;
- measure child-process memory as well as parent RSS;
- keep process inputs small and never pass R2 clients or the full state; and
- use a temporary state file for the forced no-upload benchmark.

The custom executor lifecycle, PSS/cgroup accounting framework, new render
data model, pipeline overlap, strict statistical gates and extensive failure
machinery from that draft were removed as unnecessary for the first change.
