# Infinit migration

One script migrates Infinit discovery, metadata and history. It automatically
updates cleaned history if that dataset exists. `DRY_RUN` is the only operation
switch and defaults to `true`.

```shell
source .local-test.env
DRY_RUN=true poetry run python scripts/infinit/migrate-infinit.py
DRY_RUN=false poetry run python scripts/infinit/migrate-infinit.py
```

Dry runs perform real reads on temporary copies. Apply uses the scanner lock,
backs up existing files and publishes validated changes. Unrelated vaults,
reader state and discovery cursors are preserved. History stops at the latest
contiguous cached block after deployment, bounded by the safe RPC head. The
timestamp cache is read-only during migration; progress and the summary remain
visible even with the container's default warning log level.

To rehearse locally, set `PIPELINE_DATA_DIR` to copied pipeline datasets and
`TIMESTAMP_CACHE_PATH` to a directory containing a copy of
`42161-timestamps.duckdb`, then run the same two commands. Normal credentials and dataset path overrides still work;
no stage, cleaning or block-range configuration is needed.

Production, after deploying the intended image and stopping conflicting scanner
writes, from `~/vault-scanner/web3-ethereum-defi`:

```shell
source ~/vault-scanner/vault-rpc.env
docker compose stop vault-scanner-looped
docker compose run --rm -e DRY_RUN=true --entrypoint poetry vault-scanner-oneshot run python scripts/infinit/migrate-infinit.py
docker compose run --rm -e DRY_RUN=false --entrypoint poetry vault-scanner-oneshot run python scripts/infinit/migrate-infinit.py
```

Review the summary before apply, then run `docker compose up -d vault-scanner-looped`.
The one-shot service
uses its existing persistent mounts; keep `HOME` and reader state intact.
See the [integration guide](../../docs/source/vaults/infinit/index.rst#migration)
for source-gap handling and publication recovery.

## Validation

On 2026-10-26, the consolidated script passed dry-run, apply and repeated-apply
rehearsals on isolated production copies. Its automatic cached boundary was
505781464 (exclusive): 5,969 source observations, 1,493 raw rows and 874 cleaned
rows. Price values were unchanged on repeated apply; unrelated rows, context,
discovery cursors, reader state and the legacy vault were preserved. All 112
focused protocol, history, migration and curator tests passed.
