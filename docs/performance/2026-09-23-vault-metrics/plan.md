# Plan to shorten stablecoin cleaning and top-vault publication

This is the investigation plan recorded before the local changes. The implementation and measured results are in [implementation-results.md](implementation-results.md). Compression reuse, narrower top-vault reads, cleaner write-back and phase logging are implemented locally. The daily sidecar failed parity and the per-vault copy pilot had negligible impact. Production warm-cycle validation remains pending deployment.

## What the evidence says

The reported seven-day logs show an improvement after #1586/#1588, followed by one slower cleaning round and one cold #1590 top-vault round. That is a regression against the first post-merge runs, not a demonstrated regression against the old production baseline. This server's [isolated baseline](baseline.md) shows that a persisted #1590 freshness state cut the local top-vault export from 8m25s to 4m16s; the production warm result is unknown. At baseline, the local cleaner took 95s, including 30s of EVM outlier repair, and Brotli compression of a 75.7 MB JSON took 51s per invocation.

## Original actions and outcome

1. **Make the next warm production result visible.** Implemented locally: due/skipped counts, filtered row count, phase wall times, boundary RSS and process major-fault deltas now reach the logger. The initial proposal also mentioned host swap-page counters, but those include unrelated processes and were dropped. After deployment, compare like-for-like phase times and due counts with the five post-#1586 rounds. If nearly all vaults remain due, inspect state paths and due rules. Record counts through the 3–6-day low-TVL expiry window.

2. **Compress the publication artefact once.** Implemented locally. When an alternative bucket is configured, the publication now prepares the same Brotli payload and source digest once, while preserving each bucket's failure handling and skip-if-current behaviour. The measured local compression cost is about **51s per invocation**, so avoiding the second invocation suggests a similar saving per two-bucket publication. The user's production report mentions two bucket uploads; this server's production bucket configuration and network time were not verified.

3. **Remove avoidable top-vault daily preparation work.** Direct daily-sidecar reuse failed a 200-vault parity sample across ten daily columns, including flow amounts and counts, and was rejected. Narrowing the hourly Parquet read to columns used by the current calculation passed the full warm export comparison after sorting flag and curator-post lists. It cut local warm preparation from 96.2s to 36.2s and peak RSS from 16.6 to 8.3 GiB. Raw JSON and state bytes differed, so exact serialisation parity is unproven.

4. **Isolate the EVM outlier repair overhead.** Profiling identified redundant positional writes of unchanged columns as the largest cost. Skipping those writes cut the local EVM stage from 38.2s to 6.3s and the whole cleaner from 100.3s to 63.5s; hourly and daily Parquet outputs were byte-identical. Temporary per-column timers were removed after the investigation.

5. **Make one bounded per-vault metric pilot.** The local 500-vault pilot found only 0.28s from avoiding a redundant sort and 0.08s from a narrower valid-row selection. No metric-loop change was made.

## Remaining validation

After deployment, compare at least two warm scanner cycles, then check the 3–6-day low-TVL expiry window. Use phase times and due counts to separate cadence, compression and data-path gains. Check output freshness and both buckets. The local changes have not been deployed or measured in production.

Claude's earlier independent code inspection and plan review are saved in [claude-independent.md](claude-independent.md) and [claude-plan-review.md](claude-plan-review.md). The [cold](metrics-cprofile.txt) and [warm-cache](metrics-cprofile-warm.txt) profiles show that YAML parsing is primarily one-time process work, so it was not prioritised.
