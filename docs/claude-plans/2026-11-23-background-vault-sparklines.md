# Move vault sparkline exports off the scanner cycle

## Why

On 23 November 2026, sparklines took 355 seconds of a 1,039-second publication retry. A run on 22 November spent 124 seconds in the exporter with zero renders. The scanner holds its pipeline lock throughout the export, and the exporter prepares chart data before checking cadence or whether inputs changed. See the [sparkline exporter](../../eth_defi/research/sparkline_export.py).

## Run the exporter in the background

- After both price cleaners succeed in the looped scanner, stage the cleaned crypto daily Parquet and the matching vault catalogue under the existing pipeline lock. Publish the staged directory with an atomic latest-snapshot pointer. Use copies initially. Opening both files and validating the Parquet footer is sufficient for this local queue. If staging fails, log it and preserve the previous snapshot. The worker skips it when a newer daily price file exists.
- Run one worker on the same volume. A short queue lock protects the pointer and snapshot cleanup. The worker takes the sparkline writer lock before pinning the latest snapshot under the queue lock, then calls the existing exporter outside the scanner lock. Keep the latest snapshot and the active one. New snapshots supersede waiting snapshots. On restart, take the latest snapshot and reuse the per-vault completion and retry state. If an upload fails, retry from the latest snapshot when due, using the existing backoff. Remember which snapshot ran and sleep until a new snapshot arrives or a retry is due.
- Start the worker with the looped scanner in Compose. Keep one-shot scanner exports inline and share the sparkline writer lock across background, inline and manual exports. Manual export uses the latest staged snapshot when available.

## Skip unchanged chart inputs earlier

- Before sending work to a render process, fingerprint the ordered daily timestamps, share prices, total assets and denomination used by the vault. The chart window ends at the vault's own latest valid observation. Compare against the last completed fingerprint for that vault across snapshots. Skip only if the renderer version, destination and relevant settings still match and there is no pending failure or force request. Preserve existing cadence and completion bookkeeping.
- For changed inputs and older state, keep the existing preparation, cadence and canonical chart digest checks. Save the fingerprint after all three variants succeed or after confirming an unchanged, previously completed chart. Leave failures eligible for repair under the existing backoff, including partial uploads.

## Verify and roll out

- Test snapshot replacement during an upload, restart and retry without new input, failed staging, and switching publication modes. Check that readers see a matched price and catalogue pair and that only one publisher writes.
- Compare the fast path with the existing exporter for unchanged inputs, historical corrections, TVL and denomination changes, legacy state and forced repair. Keep the same chart output and object keys.
- Replay two consecutive production snapshots on an isolated copy. Measure scanner lock time, staging time, exporter runtime and how many vaults skip preparation. Confirm one complete publication and one retry in a test environment.
