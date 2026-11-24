# Speed up sparkline uploads

The first production export after [PR #1822](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1822) took 257 seconds. It rendered 2,162 vaults, uploaded 6,115 of 6,486 images, and finished with zero failures. Later cycles can have a different mix of changed and unchanged images. At the time, the [sparkline exporter](../../eth_defi/research/sparkline_export.py) used eight upload threads and checked each rendered image with `HEAD` before deciding whether to `PUT`. The progress bar tracks work submitted to Joblib, so its last update can precede the last completed request.

## Measure eight and sixteen upload threads

- Time the blocking upload call in each batch with a monotonic clock, from before Joblib starts until it returns the results. Sum those durations in the export summary alongside total runtime, worker count and failed vaults. During the benchmark, use the sparkline client's SDK retry events to count `HEAD` and `PUT` attempts, retries, and `429` or `SlowDown` responses. Count the explicit `HEAD` conflict retries separately from the existing [R2 helper](../../eth_defi/cloudflare_r2.py) warnings.
- Alternate `SPARKLINE_UPLOAD_WORKERS=8` and `16` on the same host and network against a test bucket. Replay the same price and metadata snapshots with a representative mix of changed and unchanged assets. Restore matching local state and seeded objects before each run. Keep render workers and batch size fixed. The shared R2 client already sizes its connection pool from the upload thread count.
- Compare both thread counts with the current request path and with the digest shortcut. Check whether 16 threads consistently reduces wall-clock time and whether retries, throttling and failures stay at baseline. Keep the library and Compose defaults aligned and retain the existing environment override and legacy fallback.

## Skip the remote check for known changed images

- Add an optional map of published source SHA-256 digests to each vault's existing state entry, keyed by exact object key within the recorded R2 endpoint and bucket. Reuse the existing digest of the uncompressed image. Old state loads normally and gains digests on its next successful publication.
- For normal publication, use the existing upload helper to send a direct `PUT` when the new digest differs from a valid stored digest for that object. Keep the remote check for matching or missing digests, a new destination, and force or retry runs. This also verifies the object metadata and lets a recorded retry reuse variants that already uploaded.
- The main thread saves the new map with the vault completion record after all three images were uploaded or confirmed current in R2. A partial failure retains the prior completion record and existing retry backoff. A crash before the batch checkpoint can replay any of the batch's uploads. Repeated `PUT`s send the same bytes to the same key.

## Verify

- Exercise the normal export across consecutive batches and runs. Verify the actual `HEAD` and `PUT` calls for new and changed images, unchanged inputs and identical rendered bytes. Verify the upload worker defaults and the regular low-TVL publication cadence.
- In the replay, start the digest path with records from a prior successful publication. Compare both request paths at a fixed worker count to isolate the shortcut's effect. Check that all three variants have the same expected bytes and metadata in both runs, then confirm the timings and retry counts in the first production run after rollout.
- Keep publication inline after the price manifest, with the existing batch loop and atomic state file.

## Local replay

Two replays per configuration used 96 synthetic vaults, the S3 SDK and a local endpoint with 12 ms of simulated latency per request. The shortcut reduced `HEAD` calls from 288 to 72 per run. All configurations wrote the same 216 changed images and produced identical object bytes and metadata. With the shortcut, upload wall time was 1.14–1.15 s at eight threads and 0.94–0.96 s at sixteen. There were no retries or failures.

The default is now 16 threads. A replay against a test R2 bucket with representative vault data remains pending.
