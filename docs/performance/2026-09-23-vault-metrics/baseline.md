# Vault metrics baseline on vitalik8

Measured on 23 September 2026 at checkout `08d2a5ab87ea29d84d58474d9bd500eff78621cb`. The input was the local production-format snapshot in `/home/mikko/.tradingstrategy`: 22,552,978 raw price rows and 10,116,623 cleaned hourly rows. The benchmark read these files without modifying them. Outputs and freshness state went to a scratch directory; no scanner, R2 upload, or remote call was run. See [environment.json](environment.json) for versions and host capacity. The host had substantial allocated swap at capture time; this alone does not prove active swapping during a run.

| Isolated stage | Cold/full due | Warm state | Saved evidence |
|---|---:|---:|---|
| Stablecoin cleaning, whole function | 95.2s; peak 20.5 GiB | — | [JSON](cleaning-baseline.json), [log](cleaning-baseline.log) |
| EVM outlier repair inside cleaning | 29.8s | — | [log](cleaning-baseline.log) |
| Outlier forward-fill + array kernel | 2.3s | — | [log](cleaning-baseline.log) |
| Top-vault export, excluding compression/uploads | 504.6s; peak 24.5 GiB | 255.8s; peak 16.6 GiB | [cold](top-vaults-cold.json), [warm](top-vaults-warm.json) |
| Top-vault daily preparation | 201.4s | 94.4s | Same export artefacts |
| Top-vault metric loop | 275.3s | 137.9s | Same export artefacts |
| Top-vault due / skipped vaults | 12,795 / 0 | 5,765 / 7,030 | Same export artefacts |
| Top-vault filtered hourly rows | 10,116,623 | 8,490,655 | Same export artefacts |
| Brotli quality 11 for the 75.7 MB output | 51.0s CPU and wall | Same output class | [JSON](brotli-baseline.json) |

The warm gate reduced due vaults by 55%, daily preparation by 53%, and the metric loop by 50%. It still read 84% of the hourly rows: due vaults hold most of the history. A fresh process with persisted freshness files retains the gate, while the in-process YAML cache starts cold.

The cleaning log leaves about 27.5s inside EVM outlier repair after its logged forward-fill and array kernel. At baseline capture, this pointed to surrounding frame allocation and write-back; the later profiling identified redundant column writes. The whole local cleaner is much faster than the production rounds supplied by the user, so a production slowdown cannot be assigned to one line from this measurement. The local top-vault export excludes R2 publication; the then-current two-bucket path compressed the JSON twice, at roughly 51s per invocation locally.

Other measured cleaning work includes metadata/filtering (15.9s), perpetual metric finalisation (6.7s), sorting (3.7s plus 2.8s output sort), Parquet write (3.5s), inactive-lead filtering (3.3s), and daily sidecar write (3.1s). The remaining smaller stages and orchestration account for the rest of the 95s. EVM repair was the largest unexplained substage at baseline capture; later profiling identified its redundant column write-back.

For function-level context, [cold cProfile](metrics-cprofile.txt) and [warm-cache cProfile](metrics-cprofile-warm.txt) cover the same 250 stablecoin vaults. The warm-cache profile spends 3.15s in period calculations and 2.44s in flow attachment within an instrumented 11.94s loop. The cold profile parses stablecoin and curator YAML once; those caches are reused afterwards. cProfile overhead, sample selection and one-time cache work make these figures unsuitable for linear extrapolation to the whole export. The later measured changes are in [implementation-results.md](implementation-results.md).

The user's rotated-log reconstruction reports five post-#1586/#1588 top-vault publications at a 15m07s median, versus 16m20s–16m44s before the performance PRs. Its one cold #1590 run was 15m51s; the five post-#1586 cleaning runs had a 3m20s median versus 5m31s in the latest round. Those production figures were supplied in the request and have not been re-derived on this host. The local cold/warm comparison supports the interpretation that #1590 needs persistent warm state before it can save work; it does not explain the production cleaning variability.

Reproduction commands, with the same snapshot and a new, empty scratch directory for each cold/warm pair. Set `BENCHMARK_SCRATCH_DIR` to a path that has never held benchmark state. The script now rejects a mislabelled cold or warm run; that guard was added **after** the baseline captures, which were checked by inspecting their saved due/skipped counts:

```shell
PIPELINE_DATA_DIR=/home/mikko/.tradingstrategy BENCHMARK_SCRATCH_DIR=/tmp/white-snake-vault-top-new BENCHMARK_PHASE=cold BENCHMARK_RESULT_PATH=/tmp/white-snake-vault-top-cold.json poetry run python -u docs/performance/2026-09-23-vault-metrics/benchmark-top-vaults.py
PIPELINE_DATA_DIR=/home/mikko/.tradingstrategy BENCHMARK_SCRATCH_DIR=/tmp/white-snake-vault-top-new BENCHMARK_PHASE=warm BENCHMARK_RESULT_PATH=/tmp/white-snake-vault-top-warm.json poetry run python -u docs/performance/2026-09-23-vault-metrics/benchmark-top-vaults.py
PIPELINE_DATA_DIR=/home/mikko/.tradingstrategy BENCHMARK_RESULT_DIR=docs/performance/2026-09-23-vault-metrics BENCHMARK_CACHE_PHASE=warm poetry run python -u docs/performance/2026-09-23-vault-metrics/profile-vault-metrics.py
```

The cleaning run used the existing `scripts/erc-4626/benchmark-vault-price-cleaning.py` with stablecoin family, the local raw Parquet, metadata and settlement database, and temporary output. Its precise input schema and paths are in [cleaning-baseline.json](cleaning-baseline.json).
