# ApeX native vault reader

## Overview

This package reads every native [ApeX Omni](https://www.apex.exchange/) vault
from the exchange's public web API and stores metadata, current observations
and recoverable history in DuckDB. The all-chain scanner can also publish the
same identities and exact-timestamp observations through the shared vault
metadata and Parquet pipeline.

The integration is reader-only:

- no authentication or private account data;
- no deposits, withdrawals or trading;
- optional export into the unified vault Parquet and metadata pickle;
- optional all-chain scanner scheduling through `SCAN_APEX`; and
- no assumption that the Ethereum address reported by ApeX uniquely identifies
  a vault.

The reader is designed for a default four-hour observation schedule, but actual
timestamps are stored without buckets. Changing the schedule later requires no
database migration.

## Perp account metrics availability

The public ranking API yields vault TVL/NAV and feeds the common account
observation table. Current vault positions are not anonymously exposed by the
integrated ApeX API, so position availability is recorded as
`authentication_required`; exposure, position count and concentration remain
null rather than zero. Margin-account information and trader credentials are
intentionally outside this reader's scope. Verified 2026-07-24 against the
public application API and [official API documentation](https://api-docs.pro.apex.exchange/).

## Canonical links

- [ApeX homepage](https://www.apex.exchange/)
- [ApeX Omni application](https://omni.apex.exchange/)
- [ApeX public API documentation](https://api-docs.pro.apex.exchange/)
- [Official Python SDK](https://github.com/ApeX-Protocol/apexpro-openapi)

The shared vault metadata `Link` field targets each platform vault directly:
`https://omni.apex.exchange/vaultInfo/{vaultId}` for user-created vaults and
`https://omni.apex.exchange/vaultInfo/{vaultId}/1` for the curated official
vaults (`10000` and `10001`). The trailing `/1` selects the official-vault API
and view; adding it to a user-created vault shows an empty Insurance Vault.
The exporter and metadata migration use the same link generator, deriving the
URL from `vaultId` rather than using the generic ApeX Omni homepage.

The vault web-application endpoints used here are public but are not
currently described in the official OpenAPI documentation or SDK. Their
response shapes were verified directly against the live application API on
2026-07-23 and are captured in fixture-based tests.

## Architecture

```text
ApeX public web API
===================

/api/v3/vault/ranking                 /api/v3/vault/official-vaults
  user copy-trading vaults               protocol liquidity-provider vaults
  metadata + current NAV/TVL/share count metadata + current NAV/TVL/share count
             |
             | two complete membership-stable passes
             |                    |
             +----------+---------+
                        v
               complete ApeX vault snapshot
             v
      ApexVaultSummary records
             |
             +--------------------------+
             |                          |
             v                          v
      vault_metadata              ranking_snapshot
                                      rows

/api/v3/vault/fund-net-values?vaultId=...
  one bounded history response per ranked vault
  exact timestamp + NAV + total value
             |
             | threaded HTTP reads,
             | serial DuckDB writes
             v
      fund_net_values rows

/api/v3/vault/fund-net-value-batch?vaultIds=10000,10001
  one batched history response for official vaults
  exact timestamp + NAV + total value
             |
             +--------------------------+
                                        v
                              fund_net_values rows
             |
             v
  ~/.tradingstrategy/vaults/apex-vaults.duckdb
      vault_metadata
      vault_prices
      history_sync

/api/v3/vault/vault-config?vaultId=...
  per-vault subscription freeze configuration
             |
             v
      vault_metadata.redemption_delay

/api/v3/vault/profile?vaultId=...
  per-vault investor profit share and subscription fee
             |
             v
      vault_metadata.performance_fee / deposit_fee
```

One command owns both paths. The command schedule controls current ranking
observations, while a persisted independent gate controls history maintenance.
There is no separate high-frequency database or process.

The Docker loop schedules `ApeX=4h` by default. Each scheduled invocation
records a ranking observation, while the DuckDB history gate independently
refreshes non-terminal histories every 24 hours by default. Both durations are
configuration choices and do not constrain stored timestamps.

## Synthetic chain and vault identity

ApeX native vaults use synthetic chain ID `9995`
(`APEX_CHAIN_ID`). It is a dataset namespace, not an EVM JSON-RPC chain.

The platform's string `vaultId` is the database identity. Each vault receives
the stable synthetic address:

```text
apex-vault-{vaultId}
```

`vaultEthAddress` is metadata only. Live data shows that multiple platform
vault IDs may share the same reported Ethereum address, so using it as a
database key would merge unrelated vaults.

## Public endpoints

### Ranking

```text
GET https://omni.apex.exchange/api/v3/vault/ranking?page={page}&limit={limit}
```

Pages start at zero. Important response fields are:

| Field | Stored as | Notes |
|-------|-----------|-------|
| `vaultId` | `vault_id` | Stable platform identity |
| `vaultEthAddress` | `reported_ethereum_address` | Non-unique metadata |
| `name` | `name` | Display name |
| `desc` | `description` | Strategy description |
| `status` | `status` | Raw lifecycle status |
| `collectVaultType` | `vault_type` | Raw source type |
| `vaultNetValue` | `current_nav` / `share_price` | `DOUBLE` |
| `tvl` | `current_tvl` / `total_assets` | `DOUBLE` |
| `share` | `current_share_count` / `total_supply` | `DOUBLE` |
| `createdTime` | `created_at` | Milliseconds, naive UTC |
| `updatedTime` | `source_updated_at` | Milliseconds, naive UTC |
| `finishedTime` | `finished_at` | Zero means unavailable |

The reader performs two complete passes before writing. Each pass must have a
stable `totalSize`, the expected row count and no duplicate IDs. Both passes
must have the same ID set. Metric values are taken from the second pass because
they can legitimately change while the listing is read.

This is a stabilised paginated read, not an atomic source snapshot: ApeX does
not expose a snapshot token.

### Fund net values

```text
GET https://omni.apex.exchange/api/v3/vault/fund-net-values?vaultId={vaultId}
```

The response contains one `data.timeValue` array:

| Field | Stored as | Notes |
|-------|-----------|-------|
| `timestamp` | `timestamp` | Exact milliseconds, naive UTC |
| `netValue` | `share_price` | `DOUBLE` |
| `totalValue` | `total_assets` | `DOUBLE` |
| derived | `total_supply` | `totalValue / netValue` when NAV is positive |

The live endpoint exposes no cursor, page, limit, time range or completeness
token. A first scan can therefore recover only the history the endpoint still
returns. `history_sync` records both the latest response bounds and cumulative
retained bounds. Historical rows leave `source_updated_at` null because this
endpoint does not report a separate update time.

Observed spacing is age-adaptive rather than fixed. Recent vault history may be
hourly, while older history may become daily or weekly. The reader never
interpolates, forward-fills, rounds or resamples these source timestamps.

### Redemption delay

```text
GET https://omni.apex.exchange/api/v3/vault/vault-config?vaultId={vaultId}
```

The configuration endpoint is public but not described in ApeX's OpenAPI
documentation. Its `vaultConfig.freezePurchaseShareDuration` field is the exact
millisecond period for which a newly subscribed share is frozen. ApeX's
application describes this as the period before a subscription becomes
redeemable. The reader stores it as `redemption_delay` and exports it to the
shared vault `_lockup` field. It is requested for each listed non-terminal
ranked vault, so the ingestion tracks a future per-vault configuration change
instead of assuming the currently observed one-day delay. A terminal vault with
a verified stored delay is not requested again; it cannot accept a future
subscription, and a reactivated vault is requested again.

The official liquidity-provider vaults below are intentionally excluded from
this endpoint: ApeX's [Protocol Vault announcement](https://www.apex.exchange/blog/detail/Introducing-Protocol-Vaults-on-ApeX-Omni-Stable-Returns-Backed-by-Real-Fees)
states that Protocol Vaults have no lock-up, and ApeX's [New User Vault
announcement](https://www.apex.exchange/blog/detail/weekly-update-11may2026)
describes the second official vault as the same product family. The per-vault
configuration response is therefore the canonical delay source for ranked
vaults; it must not be inferred from `vault_type`.

### Official liquidity-provider vaults

```text
GET https://omni.apex.exchange/api/v3/vault/official-vaults
GET https://omni.apex.exchange/api/v3/vault/fund-net-value-batch?vaultIds={vaultIds}
```

Official ApeX protocol-operated liquidity-provider vaults are intentionally
not returned by the ranking endpoint. The reader combines this complete
listing with the stabilised ranked-vault listing before lifecycle reconciliation,
so an unfiltered scan neither omits these vaults nor incorrectly marks them as
missing. Their history is fetched using the batch endpoint, which must return
every requested vault ID exactly once. The known Protocol Vault and New Vault
also receive curated descriptions in the shared metadata export because the
endpoint supplies placeholder source text.

### Investor fees

The [ApeX vault guide](https://apex-pro.gitbook.io/apex-pro/apex-omni/apex-vaults)
describes creator profit sharing on realised investor gains. The native
application's subscription and redemption dialogs state that this profit
share is paid **upon redemption**. It is therefore an **externalised
performance fee**: the historical NAV is before this investor-level deduction.
Trading commissions and funding costs already affect NAV and are not charged
again by the investor-return calculation.

The anonymous public profile supplies the actual per-vault schedule:

```text
GET https://omni.apex.exchange/api/v3/vault/profile?vaultId={vaultId}
```

| Field | Export | Units and handling |
|-------|--------|--------------------|
| `data.vault.vaultId` | Identity check | Must match the requested vault |
| `data.vault.shareProfitRatio` | `performance_fee` / `Perf fee` | Fraction: `0.10` means 10%, with no division by 100 |
| `data.vault.purchaseFeeRate` | `deposit_fee` / `Deposit fee` | Verified zero becomes `0.0`; missing or non-zero values remain unknown because non-zero units have not been verified |
| Investor fee model | Management and flat withdrawal fees | `0.0`; the documented redemption charge is modelled as a performance fee, with trading and funding costs already in NAV |

The ranking endpoint can return a blank `shareProfitRatio` even when the
profile reports 10%. The configuration endpoint's `profitShareRatio` is a
creation default, not the canonical per-vault schedule. Neither is used to
guess a profile fee. Failed profile reads preserve the previously verified
rate, while a successful profile with an unverified subscription fee clears
the old deposit-fee certainty. Net returns require a complete known schedule.

Profile and lockup reads run in the same worker phase with separate bounded
operation budgets, so either endpoint can succeed when the other fails. New
nullable DuckDB columns are added without rewriting or removing price history.
Active user vaults refresh
their schedules on each scan; terminal vaults are refreshed until a lockup and
valid profile profit share are stored. An intentionally unknown subscription
fee does not cause endless terminal-vault reads. Raw price exports carry the
current verified performance fee, as the Hyperliquid exporter does; historical
changes to fee rates are not available from these endpoints.

The curated official vaults `10000` and `10001` export a complete zero-fee
schedule. The [Protocol Vault guide](https://apex-pro.gitbook.io/apex-pro/apex-omni/protocol-vaults)
states that redemption returns principal and all accrued yield. These products
do not have a user-vault creator's profit share.

Comparison reviewed on 2026-10-06:

| Product | Current repository export | Investor profit-share treatment |
|---------|---------------------------|---------------------------------|
| ApeX user vaults | `externalised`, actual profile fraction | Deduct the creator's share from positive returns at redemption |
| ApeX official vaults | `feeless`, all fee fields zero | Principal plus accrued yield returned |
| Hyperliquid legacy user vaults | `externalised`, fixed 10% | The [depositor guide](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy) explicitly deducts the leader's share at withdrawal; HLP has zero profit share |
| Lighter public pools | `internalised_skimming`, `operator_fee / 100` | The current exporter assumes the share price already reflects the operator fee; the [current pool guide](https://docs.lighter.xyz/trading/public-pools) instead describes allocation upon participant withdrawals |

Lighter's NAV accounting needs further verification. Its legacy classification
is retained and documented as an assumption, not a confirmed accounting model.

### Metrics and ranking availability

Fee export enables investor net returns once a complete schedule is known.
Other missing metrics can have independent causes in the shared pipeline:

- Monthly and quarterly annualisation require the complete requested history
  window; lifetime annualisation requires at least 30 days of observations.
- Sharpe requires at least 14 days of daily-return history. A seven-day period
  therefore has no Sharpe value.
- The default chain and protocol ranking threshold is $10,000 TVL; the overall
  ranking threshold is $50,000. Ranking also requires a valid annualised return.
- Exposure and concentration require positions, which this reader cannot fetch
  anonymously.

For example, on 2026-10-06 the AI multistrategy vault had about 21 days of
observed history and $9,100 TVL. Its gross return and drawdown were calculable,
but monthly/lifetime annualisation and chain/protocol ranking were unavailable
under these rules. Before this change, its unknown exported fee schedule also
prevented net returns. These rules live in `eth_defi/research/vault_metrics.py`.

## Status handling

Only the verified `VAULT_FINISHED` status is terminal. Every other value,
including `VAULT_IN_PROCESS`, `VAULT_INITIAL_FAILED`,
`VAULT_PAUSE_PURCHASE` and future unknown values, is treated as non-terminal.
This fail-open classification is limited to data collection: it ensures an
unrecognised status continues to receive observations and history maintenance.

Deposit access is classified separately from history collection.
``VAULT_IN_PROCESS`` is publicly open; ``VAULT_FINISHED``,
``VAULT_INITIAL_FAILED`` and ``VAULT_PAUSE_PURCHASE`` are not open for public
deposits and export the native-perp compatibility value ``whitelisted``.
Unrecognised statuses export ``unknown`` rather than guessing their access
meaning. ``whitelist.notes`` clarifies that ``whitelisted`` does not imply an
approved-account deposit route.

A terminal vault receives one final non-empty history sync. If it later becomes
non-terminal, its terminal generation is cleared; a later finish starts a new
generation. Unfiltered scans similarly track disappeared and reappeared vaults
without deleting their stored history.

## Scheduling and history modes

The default ranking cadence is four hours. The ranking endpoint itself was
observed to refresh approximately every 30 seconds, so a separate high-frequency
reader is unnecessary for the requested dataset. `run_scan()` records a current
observation whenever called; its standalone or all-chain caller owns the
interval.

History refresh defaults to 24 hours and supports three modes:

| Mode | Behaviour |
|------|-----------|
| `incremental` | Backfill new vaults and refresh due non-terminal vaults |
| `refresh` | Immediately re-fetch all selected recoverable histories |
| `none` | Store ranking metadata and current observations only |

All history writes are append-and-correct. Returned timestamps replace earlier
values at the same logical key, but a later shortened or empty response never
deletes timestamps that the source omitted.

The all-chain Parquet export applies the same policy by synthetic vault address
and exact timestamp. It preserves unmatched rows already present in Parquet
even if the current DuckDB is partial or rebuilt.

Durations accept positive decimal seconds, minutes, hours and days, for example
`30s`, `30m`, `1.5h` and `2d`.

## DuckDB storage

The default database path is:

```text
~/.tradingstrategy/vaults/apex-vaults.duckdb
```

| Table | Logical key | Purpose |
|-------|-------------|---------|
| `vault_metadata` | `vault_id` | Current source metadata and lifecycle |
| `vault_prices` | `(vault_id, timestamp)` | Historical and ranking values |
| `history_sync` | `vault_id` | Attempt, retained-range and finalisation state |

The tables deliberately have no `PRIMARY KEY` or `UNIQUE` constraints. DuckDB
1.5.0 ART indexes can corrupt file-backed databases under Python 3.14 on macOS
ARM64. The writer enforces logical keys with staged transactional
`DELETE` + `INSERT`, disables automatic WAL checkpoints and checkpoints once
after a completed scan.

HTTP fetches run in worker threads, but workers never access DuckDB. The
creating thread performs every write, checkpoint and close operation.

## Numeric representation

NAV, TVL, share count and derived supply are parsed as finite Python `float`
values and stored as DuckDB `DOUBLE`. Tests use approximate comparisons to
account for normal binary floating-point rounding.

Ranking `purchaseFeeRate` and `shareProfitRatio` remain nullable raw strings.
The separate profile's verified fractional profit share is exported as a
typed performance fee; unverified non-zero subscription fee units remain null.

## Bounded HTTP behaviour

Every worker owns a private `requests.Session`; sessions share one process-wide
rate limiter. Network reads have:

- finite connect and inactivity timeouts;
- monotonic request and enclosing operation budgets checked between phases and
  streamed chunks;
- explicit bounded retries with capped `Retry-After`;
- a maximum streamed JSON response size; and
- response closure on success and failure.

The synchronous `requests` read timeout is an inactivity timeout, not a hard
wall-clock deadline. A server that continuously drips bytes without completing
a streamed chunk can delay budget detection until the socket read yields; the
finite inactivity timeout remains the outer bound for a stalled read.

History worker sessions are closed after every scan cycle. The calling thread's
ranking session is retained until command shutdown so loop mode does not
accumulate connection pools from completed joblib workers. A session pool
allows only one active scan, and exceptional joblib completion waits for
sibling history workers to leave their scopes before their sessions are closed.

Ranking failures abort before any database mutation. Per-vault history failures
are recorded independently and remain retryable without erasing other vaults.
A stabilised empty ranking is also rejected when the database already contains
vaults, preventing one anomalous response from marking the full universe
missing.

## Quick start

Run an initial all-vault backfill:

```shell
poetry run python scripts/apex/vault-metrics.py
```

Run selected vaults:

```shell
VAULT_IDS=2044287989957394432,1914612863780126720 \
  poetry run python scripts/apex/vault-metrics.py
```

Run continuously at a different cadence:

```shell
LOOP=1 SCAN_INTERVAL=30m HISTORY_REFRESH_INTERVAL=12h \
  poetry run python scripts/apex/vault-metrics.py
```

Force an append-and-correct history refresh:

```shell
HISTORY_MODE=refresh poetry run python scripts/apex/vault-metrics.py
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `info` | Console log level |
| `DB_PATH` | `~/.tradingstrategy/vaults/apex-vaults.duckdb` | DuckDB path |
| `VAULT_IDS` | all vaults | Optional comma-separated target IDs |
| `MAX_WORKERS` | `8` | History reader threads |
| `REQUESTS_PER_SECOND` | `5` | Shared request rate |
| `CONNECT_TIMEOUT` | `10` | Connection timeout in seconds |
| `READ_TIMEOUT` | `30` | Socket inactivity timeout in seconds |
| `REQUEST_DEADLINE` | `60` | One request-attempt deadline |
| `RANKING_DEADLINE` | `300` | Deadline shared by both ranking passes |
| `HISTORY_DEADLINE` | `120` | Operation budget for each history, lockup or profile read |
| `MAX_RETRY_DELAY` | `10` | Maximum retry delay |
| `MAX_RESPONSE_BYTES` | `16777216` | Largest accepted JSON response |
| `HISTORY_MODE` | `incremental` | `incremental`, `refresh` or `none` |
| `HISTORY_REFRESH_INTERVAL` | `24h` | Independent history cadence |
| `LOOP` | false | Repeat scans sequentially |
| `SCAN_INTERVAL` | `4h` | Ranking cadence in loop mode |

## Key modules

| Module | Role |
|--------|------|
| `eth_defi.apex.constants` | Synthetic chain, API and operational defaults |
| `eth_defi.apex.config` | Strict environment and duration parsing |
| `eth_defi.apex.session` | Worker-local sessions and bounded HTTP policy |
| `eth_defi.apex.vault` | Typed public endpoint parsing and pagination |
| `eth_defi.apex.metrics` | DuckDB lifecycle and scan orchestration |
| `scripts/apex/vault-metrics.py` | Standalone command |

## Running tests

Run the fixture-based suite without contacting ApeX:

```shell
source .local-test.env && poetry run pytest tests/apex -m 'not live'
```

Run the focused real-provider fee check (public API, no credentials):

```shell
source .local-test.env && poetry run pytest tests/apex/test_apex_export.py::test_live_apex_profile_fees_reach_shared_exports --log-cli-level=info
```

This check reads the AI multistrategy vault's actual profile and verifies
DuckDB persistence, shared metadata/raw-price exports and the investor
net-return calculation. It writes only temporary test files.

Manual result on 2026-10-06: passed against the anonymous ApeX Omni mainnet
profile endpoint. The source reported a 10% profit share and a zero subscription
fee. Migration was also checked on a production DuckDB copy: 676 metadata rows
and 211,874 price rows were preserved, with an unchanged complete price checksum
and null defaults for the new fee columns.
