# Dense block timestamp caches

HyperSync-backed timestamp reads retain every block between the earliest and
latest cached or requested block. Hourly or daily price sampling controls
contract reads, not timestamp collection. Event-only reads also bridge the
existing cache boundary. The historical `fetch_sparse_*` names remain compatible
entry points but now populate dense coverage too. RPC-only chains retain their
sampled fallback because no indexed header source is available.

The [Envio query API](https://docs.envio.dev/docs/HyperSync/hypersync-query)
supports `include_all_blocks`; the cached reader uses timestamp-only pages,
configured request throttling and durable chunks. An interrupted read retains
completed chunks. A successful read must cover its indexed interval completely;
unresolved provider omissions raise a retryable error rather than returning an
incomplete cache. Empty event requests do not fetch or extend history.

Infinit event reads use the prepared shared cache directly. The scanner prepares
coverage first; standalone callers must do the same. Missing coverage raises a
clear error without downloading a separate timestamp cache.

[PR #1321](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1321)
introduced sparse timestamp sampling on 18 July 2026. On 13 November,
the local Arbitrum file contained all blocks through 485,040,756, followed by a
sparse tail ending at 523,456,169. It lacked 38,412,000 timestamps. Production
had a slightly newer sparse tail.
[PR #1764](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1764)
compacted the files losslessly; its
retained backup has the same gaps. Infinit needs dense coverage to match
application price observations to exact block timestamps, so its history
refresh was deferred while ordinary sampled scans continued.

## Numbered repair

`070-dense-block-timestamp-caches.py` fills holes within each existing
HyperSync-supported file's own first/last block envelope. It does not fetch a
new head, invent a genesis boundary, create new chain files, reset reader state
or change metadata/prices. RPC-only files are reported unchanged.

Preview reports missing rows without API requests or persistent writes. Apply
retains an independent original backup, builds a compact candidate, commits
downloaded chunks there, and preserves that candidate across provider failures.
Before atomic publication it checks density and compares every original
block/timestamp pair exactly. A changed original timestamp aborts publication.
Keep backups; the repair does not retire them automatically.

Before changing any file, preview and apply report a free-space budget for the
whole selected inventory. For each sparse cache, reserve its original backup
plus four times the larger of its current size or 32 bytes per dense target row,
plus 64 MiB. This covers candidate storage, WAL and validation spill without
assuming the repaired database will compress like a tiny sparse source. Existing
recovery copies are deducted on resume. Apply refuses insufficient space before
creating new backups or publishing any chain. The 13 November production snapshot
requires approximately **200 GiB plus the source cache sizes** in free space
under this conservative allowance; the preview report is authoritative for the
current inventory. Actual compressed files can be much smaller.

Close all consumers of the selected timestamp directory before preview or
apply. The runner owns pipeline guards; the additional directory lock excludes
timestamp connections. A leftover canonical WAL must be checkpointed by its
owner before this migration runs.

For local Arbitrum maintenance, load the existing credentials and use the
numbered script's isolated per-chain journal:

```shell
source .local-test.env
TIMESTAMP_CHAIN_IDS=42161 PYTHONPATH="$PWD" poetry run python migration/scripts/070-dense-block-timestamp-caches.py
TIMESTAMP_CHAIN_IDS=42161 DRY_RUN=false PYTHONPATH="$PWD" poetry run python migration/scripts/070-dense-block-timestamp-caches.py
```

`TIMESTAMP_CACHE` optionally selects the cache directory. `HYPERSYNC_RPM`
must match the supplied key's quota. Local selected runs use
`.timestamp-maintenance/dense-42161/migration.json`, so they cannot mark an
all-chain production run complete. Interrupted attempts reuse the same journal
and staged candidate. Do not remove their recovery directories.

## Run after merge

Inspect production Compose mounts, deploy the stamped image, and stop all
timestamp consumers. Use the normal pending migration batch with
`TIMESTAMP_CHAIN_IDS` unset, from `~/vault-scanner/web3-ethereum-defi`:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true -e HYPERSYNC_RPM=10 --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false -e HYPERSYNC_RPM=10 --entrypoint vault-migrate vault-scanner-oneshot run
```

Follow the [migration runbook](../.claude/docs/migration-scripts.md#production-runbook)
for image provenance, failure recovery and restarting the same image. For each
supported non-empty cache, require zero gaps and `count = last - first + 1`.
Confirm the next Arbitrum scan no longer defers Infinit history. The normal
Infinit scanner replays its retained source history by address; no price or
reader-state reset is needed for this timestamp-only repair.

At 10 Envio requests/minute, allow **90–120 minutes** for the supported production
inventory measured on 13 November: 368,431,261 missing timestamps across 22
chains. This extrapolates one Arbitrum measurement, rather than a fleet benchmark.
Coalescing nearby gaps can re-read cached headers, and backup, candidate rebuild
and exact validation time also vary by chain and disk. Treat this as an initial
maintenance window, with progress retained if it needs to be resumed.

The published change is [PR #1768: fix: keep HyperSync timestamp caches dense and
repair historical gaps](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1768).
Migration 070's header was written before the PR was opened. The numbered script
is kept byte-for-byte unchanged because it has already succeeded in the local
manual journal; this runbook records the final PR identity instead.

## Local result on 13 November 2026

Arbitrum repair applied successfully through the isolated numbered journal:

| Check | Result |
| --- | --- |
| Cached interval | Blocks 1–523,456,169 |
| Original rows preserved exactly | 485,044,169 |
| Added timestamps | 38,412,000 |
| Published rows | 523,456,169 |
| Remaining gaps | 0 |
| Fresh-process count and Infinit coverage guard | Passed |
| Canonical or staged WAL left behind | None |
| Repeat numbered run | Skipped as already applied |

The independent original backup remains recorded in the journal. Apply used
the command above with `HYPERSYNC_RPM=10`. An earlier run was paused after a
concurrent live check received HTTP 429; retry reused durable candidate progress
at the lower rate and completed without further provider rate-limit errors.

Test and review evidence is recorded in the PR.

The production timestamp directory was not repaired by this local run. It
still requires the normal numbered migration after deployment.
