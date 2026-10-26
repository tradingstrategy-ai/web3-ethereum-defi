# Infinit maintenance

See the [integration and operational guide](../../docs/source/vaults/infinit/index.rst)
for environment settings, isolated local commands and mounted one-shot production
instructions. Run **leads → metadata → history**. All three entry points default
to `DRY_RUN=true`; apply requires `DRY_RUN=false`.

## Local validation record

Manual checks on 2026-10-26 used `.local-test.env`, configured Arbitrum RPC,
the supplied Hypersync token and Infinit/Hyperliquid public endpoints. No token
or endpoint credential is recorded here. Production deployment was not run.

- The shared `ARBITRUM_MIDNIGHT_BLOCK` fork returned exactly
  **506,013.231165 inUSDC**, with six decimals and distinct vault/share addresses.
- Public read-action discovery, application history parsing and combined native
  plus `xyz` account-equity validation passed. The authenticated one-block
  Hypersync creation check passed too.
- Dry-run, apply and repeated apply were rehearsed in
  `/tmp/infinit-rehearsal-20261026` against copies of the 2026-09-22 production
  snapshot, with exclusive `END_BLOCK=505781464` and an isolated dense timestamp
  cache. This covers source observations through 2026-09-16 14:00 UTC.
- The context retained **5,969** original fifteen-minute prices and **57**
  events: one creation, 28 deposits and 28 receipt mints. Supply matched RPC.
  Export produced **1,493** hourly raw and **874** cleaned rows.
- Repeated apply retained identical Infinit price values and counts, excluding
  the expected raw `written_at` update. Both reviewed gaps remained excluded.
  Real public-metric generation retained the valid one-week return and withheld
  the one-month, three-month and lifetime returns spanning a reviewed gap.
- Counts and order-independent row-value hashes matched for all **22,552,978**
  unrelated raw rows and **10,116,623** unrelated cleaned rows. All pre-existing
  context tables matched. The **51,907** unrelated catalogue rows, **50,364**
  unrelated leads, discovery cursors, reader-state bytes and older native
  Hyperliquid vault were preserved.

Hypersync reported a **15 requests/minute** account quota during overlapping
checks. Subsequent checks ran sequentially with `HYPERSYNC_RPM=10`, after the
reset; do not repeat a rate-limited request burst. The initial cache helper also
migrated the local timestamp cache's legacy ART schema transactionally,
preserving all 485,041,305 rows. The final event reader snapshots required
cached headers into temporary storage before invoking cache-aware helpers, so
dry runs cannot migrate or append to the operator's timestamp cache.

Focused commands:

```shell
source .local-test.env
INFINIT_LIVE_TEST=true HYPERSYNC_RPM=10 timeout 180s poetry run pytest \
  tests/erc_4626/vault_protocol/test_infinit.py \
  tests/erc_4626/vault_protocol/test_infinit_history.py \
  tests/erc_4626/vault_protocol/test_infinit_live.py \
  tests/research/test_clean_prices.py -q --override-ini='addopts='
timeout 180s poetry run pytest tests/research/test_vault_metrics.py \
  -k 'sparse_share_prices_are_regularised_once or launch_returns_are_absolute or sparse_daily_return_preparation or daily_vault_resampling or flat_initial_interval or lifetime_metrics_normalises' \
  -q --override-ini='addopts='
```

The final review additionally exercised the recurring Arbitrum coordinator with
the real contextual reader and Parquet writer: late API observations, repeated
scans, an unrelated row and reader-state sentinel, source timeouts/RPC errors,
and loss of context followed by a shortened API response. Missing historical
coverage now refuses publication. Normal scans extend a warm dense timestamp
cache before fetching source prices; manual migrations keep it read-only.

Ruff checks and formatting passed for the new Python modules and scripts.
Protocol metadata parsed successfully, including both official processed
256×256 logo variants. Sphinx was not built.
