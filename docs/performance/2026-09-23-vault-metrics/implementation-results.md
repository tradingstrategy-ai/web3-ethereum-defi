# Implemented vault processing improvements

All measurements below used the same production-format local snapshot on vitalik8 on 23 September 2026. They are isolated host results, not production scanner-cycle timings. No R2 network upload or live scanner was run. The cleaner and warm-export before/after figures are separate local A/B process runs using the same input, and the same freshness state where applicable. They are later than the first captures in [baseline.md](baseline.md), so their absolute times differ. The initial experiments were based on checkout `08d2a5ab` with uncommitted local edits; the top-vault reference restored the old full-column read through `BENCHMARK_FORCE_ALL_COLUMNS=1`. The precise intermediate diffs were not archived, so those saved result files establish timings and input identity but are not complete code provenance records. The warm top-vault A/B was repeated on the current branch after moving to a newer `master` that includes Yearn export post-processing.

| Change | Before | After | Output check |
|---|---:|---:|---|
| Stablecoin cleaning: skip positional write-back for columns exactly unchanged by EVM outlier repair | 100.3s, peak 20.5 GiB | 63.5s, peak 17.5 GiB | Hourly and daily Parquet files byte-identical; [paired result](cleaning-optimisation-result.json) |
| Warm top-vault export: omit unused price columns from the due-vault read | 257.2s, peak 16.6 GiB | 181.8s, peak 8.3 GiB | JSON and both state files matched after sorting flag and curator-post lists; raw bytes differed; [paired result](top-projection-result.json), [parity](top-projection-parity.json) |
| Two-bucket Brotli publication, when an alternative bucket is configured | Estimated two × 51s compressions | One measured 50.7s local compression | Same payload and digest reused for both buckets in a mocked upload; [result](brotli-after.json) |

The repeated warm top-vault A/B used the current branch, the same 10,116,623-row hourly input, copied freshness state, and a fixed clock. Both runs executed the Yearn attribution hook, exported 5,431 vaults and produced 75,643,107-byte JSON files. The reference forced the previous full-column read; the projected run used the new column selection.

| Current-branch stage | Full-column reference | Projected-column run | Difference |
|---|---:|---:|---:|
| Whole warm export, excluding R2 | [262.9s](top-pr-reference.json) | [184.4s](top-pr-projection.json) | 78.5s faster (29.9%) |
| Daily preparation | 96.6s | 37.0s | 59.6s faster (61.7%) |
| Metric loop | 140.9s | 123.2s | 17.7s faster; likely influenced by lower memory use, not a changed metric algorithm |
| Process peak RSS | 16.55 GiB | 8.26 GiB | 8.29 GiB lower |

The current-branch [recursive parity check](top-pr-parity.json) found 174 reordered flag lists and 27 reordered curator-post lists with equal timestamps at the same positions; it found no other list reorders or value differences. Parsed metric-state objects matched. Raw JSON and state-file bytes still differed, so byte-for-byte parity is unproven.

The cleaner's EVM outlier stage fell from 38.2s to 6.3s. Profiling showed that unchanged `name`, `deposit_closed_reason`, `protocol` and `hypercore_repair_status` writes consumed most of the old 29.5s write-back. The changed-column comparison took 0.2s and wrote 10 columns instead of 43. This preserves forward-fill and dtype changes because the code compares complete source columns before deciding whether to write them back.

The top-vault daily sidecar was rejected for this path. On 200 sampled vaults it changed ten daily columns, including flow counts and amounts; see [sidecar-parity.json](sidecar-parity.json). The narrower hourly read was then tested on 500 vaults and on the full 5,765-vault warm export. JSON and state-file bytes differed. A full recursive comparison found 190 reordered flag lists and 14 reordered curator-post lists where every position retained the same `published_at`; it found no changed values or other reordered lists. The parsed metric-state objects matched exactly, although their bytes differed. See [ordering detail](top-projection-ordering.json). The source of the ordering differences was not established, so byte-for-byte output parity is unproven. The projection retains newly added price columns by default and excludes columns unused by the current calculation.

The optional per-vault metric copy pilot did not justify another code change. Across 500 vaults, guarding an already sorted index saved 0.28s and replacing the valid-row copy with positions saved 0.08s; see [metric-copy-pilot.json](metric-copy-pilot.json). The measured gains are small compared with the **after** warm-export metric loop of 121s in [top-projection-result.json](top-projection-result.json), which is a different run from the 137.9s initial baseline.

Phase diagnostics now log due counts, filtered rows, wall time, boundary RSS and process major-fault deltas. The standalone wrapper configures INFO logging, and daily preparation emits a `tqdm_loggable` progress bar. RSS is a boundary reading, not a phase peak; major faults do not by themselves identify the cause of a slow phase.

Focused tests passed: `tests/erc_4626/test_post_processing.py`, the mixed-protocol and duplicate-timestamp cleaner tests, and two daily-return tests (19 tests total). Local parity and Brotli benchmark artefacts are saved alongside this report. Production validation still requires deployment and observation of warm scanner cycles through the 3–6-day low-TVL expiry window.
