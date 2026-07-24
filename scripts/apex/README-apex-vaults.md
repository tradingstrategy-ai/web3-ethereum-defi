# ApeX native vault metrics

This standalone reader stores every native ApeX Omni vault exposed by the
public API in DuckDB. It is intentionally separate from the unified ERC-4626
vault parquet and pickle pipeline.

See the package-level
[`README-apex.md`](../../eth_defi/apex/README-apex.md) for the architecture,
identity model, endpoint fields, lifecycle policy and DuckDB schema. This
document focuses on operating the standalone command.

The command uses two public endpoints:

- [Vault ranking](https://omni.apex.exchange/api/v3/vault/ranking) supplies the
  complete paginated vault list and current NAV, TVL and share count.
- [Fund net values](https://omni.apex.exchange/api/v3/vault/fund-net-values)
  supplies the historical NAV and total-value points retained by ApeX.

No API key is required.

## Source behaviour

The ranking endpoint uses zero-based pages. The reader makes two complete
passes, verifies stable totals and identical vault-ID membership, and only then
stores the second pass. The ranking source was observed on 2026-07-23 to update
on an approximately 30-second platform tick.

The vault endpoints are not currently described in the official OpenAPI
documentation or SDK. The history response was rechecked directly against the
live web-application API on 2026-07-23. It returns one `data.timeValue` array
and exposes no cursor, page, limit, range or completeness metadata. An initial
backfill can therefore recover only the history ApeX still returns. The
database records both response bounds and the cumulative retained history
bounds; it does not claim earlier omitted data is complete.

Observed history resolution is age-adaptive rather than fixed. Young vaults may
have approximately hourly points, medium-aged vaults daily points and older
vaults weekly points, normally followed by a current trailing point. The reader
stores every actual source timestamp without interpolation or cadence buckets.

The platform's NAV and TVL values are assumed to be denominated in ApeX's USDT
terms. The units of `purchaseFeeRate` and `shareProfitRatio` are not documented
authoritatively, so they are retained as raw strings.

## Scheduling model

There is one command and one database. The command scheduler controls current
ranking observations through `SCAN_INTERVAL`, which defaults to four hours.
Each scan invocation records every selected non-terminal vault. Unchanged
terminal vaults are not repeatedly written.

History has a separate internal eligibility gate controlled by
`HISTORY_REFRESH_INTERVAL`, which defaults to 24 hours. New vaults are
backfilled immediately. Non-terminal vaults are refreshed when due. Terminal
vaults receive one final non-empty history sync after their terminal
observation. A disappeared non-terminal vault receives a separate final sync;
a disappeared terminal vault is fetched only when its terminal final sync is
still incomplete.

Both defaults are operational settings only. Any positive seconds, minutes,
hours or days duration can be selected later without a schema migration or
rewriting previous rows.

## Initial backfill

Run one complete initial fetch with:

```shell
poetry run python scripts/apex/vault-metrics.py
```

The default database is:

```text
~/.tradingstrategy/vaults/apex-vaults.duckdb
```

Use a temporary or custom path with:

```shell
DB_PATH=/tmp/apex-vaults.duckdb \
  poetry run python scripts/apex/vault-metrics.py
```

Force an append-and-correct refresh of all recoverable history with:

```shell
HISTORY_MODE=refresh poetry run python scripts/apex/vault-metrics.py
```

Run only selected platform vault IDs with:

```shell
VAULT_IDS=2044287989957394432,1914612863780126720 \
  poetry run python scripts/apex/vault-metrics.py
```

The full ranking is still validated before the target filter is applied.

## Environment configuration

- `DB_PATH`: DuckDB path. Defaults to
  `~/.tradingstrategy/vaults/apex-vaults.duckdb`.
- `LOG_LEVEL`: console log level. Defaults to `info`.
- `VAULT_IDS`: optional comma-separated targeted platform vault IDs.
- `MAX_WORKERS`: history reader threads. Defaults to `8`.
- `REQUESTS_PER_SECOND`: shared API rate. Defaults to `5`.
- `CONNECT_TIMEOUT`: connection timeout in seconds. Defaults to `10`.
- `READ_TIMEOUT`: socket inactivity timeout in seconds. Defaults to `30`.
- `REQUEST_DEADLINE`: monotonic request-attempt budget. Defaults to `60`.
- `RANKING_DEADLINE`: operation budget shared by both ranking passes. Defaults
  to `300`.
- `HISTORY_DEADLINE`: operation budget shared by all attempts for one vault. Defaults
  to `120`.
- `MAX_RETRY_DELAY`: longest retry or `Retry-After` wait. Defaults to `10`.
- `MAX_RESPONSE_BYTES`: largest accepted JSON response. Defaults to `16777216`.
- `HISTORY_MODE`: `incremental`, `refresh` or `none`. Defaults to
  `incremental`.
- `HISTORY_REFRESH_INTERVAL`: positive duration. Defaults to `24h`.
- `LOOP`: set to `1` to repeat sequentially. Defaults to one scan.
- `SCAN_INTERVAL`: ranking cadence used by loop mode. Defaults to `4h`.

Duration examples include `30s`, `30m`, `1.5h` and `2d`.

## Database tables

- `vault_metadata` stores one logical row per ApeX platform vault ID.
- `vault_prices` stores actual historical and ranking timestamps.
- `history_sync` stores attempts, recoverable response bounds, retained
  canonical bounds and terminal/disappearance lifecycle state.

The stable reader address is `apex-vault-{vault_id}`. The
`vaultEthAddress` returned by ApeX is retained only as metadata because one
reported Ethereum address may be shared by multiple platform vault IDs.
