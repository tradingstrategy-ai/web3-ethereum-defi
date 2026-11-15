# T3tris settlement delay estimates

## Goal

Give T3tris vaults the same non-binding settlement estimate that Lagoon vaults
publish, so backtests and the exported vault metrics use a T3tris-specific
deposit and redemption delay instead of the generic two-day default. Then
record T3tris settlement events, so backtests can complete queued deposits at
the actual onchain settlements, as they do for Lagoon and D2.

- **Part 1:** publish T3tris's offchain average settlement interval as
  `WithdrawalPeriod.estimated_settlement`.
- **Part 2:** add T3tris to the vault-settlement event pipeline.

Both parts need a trade-executor change before backtests use them; that change
is listed separately and is out of scope for this repository's PR.

## Implementation status

Implemented on 2026-11-15 on branch `t3tris-delay-estimates`, both parts.
Deviations from the steps below:

- Intervals under one hour are discarded (`MIN_AVERAGE_SETTLEMENT_SECONDS`),
  not only intervals ≤ 0. This is a heuristic: the live Kingfisher response
  gave about 20 seconds, its zero average plus server lag.
- `_fetch_vault_detail()` keeps returning the JSON dict and attaches the
  response time under a private key, so existing callers and stubs work.
- `create_t3tris_withdrawal_period()` in `vault.py` is shared by the adapter
  and migration 079.
- The README presents the recorded `DepositsSettled` markers as the
  preferred timing source and the offchain interval as a weak fallback.
- The Hypersync failure check needed no code: a log-fetch error raises before
  `upsert_scan_state()`, and `continues_after_failed_chain` covers it.
- The scanner test serves First USDC's real logs, copied from two
  transaction receipts, at the log-fetch boundary: a `settle()` gives one
  `DepositsSettled` row and a `settleRedemptions()` gives none. It makes no
  RPC reads.
- `_fetch_vault_detail()` has a `strict` mode. Migration 079 uses it, so a
  timeout or server error fails the migration for a retry instead of being
  recorded as a vault without an estimate.

The trade-executor follow-up below is not done.

## How Lagoon does it

- `LagoonVault.get_withdrawal_period()` returns
  `WithdrawalPeriod(min_period=None, max_period=None,
  delay_type=WithdrawalDelayType.delay, estimated_settlement=...)` from the
  app's offchain `averageSettlement` (seconds), or `None` when the value is
  missing or ≤ 0 (`eth_defi/erc_4626/vault_protocol/lagoon/vault.py:737`).
- `LagoonDepositManager.get_estimated_lock_up()` returns the same estimate
  (`lagoon/vault.py:1488`).
- `create_vault_scan_record()` persists `_withdrawal_period`
  (`eth_defi/erc_4626/scan.py:473`); `vault_metrics.py:3506` exports
  `estimated_settlement` and sets the legacy `lockup` to `max_period`.
- trade-executor copies `estimated_settlement` to
  `pair.other_data["vault_estimated_settlement"]` and, only for families in
  `resolve_vault_protocol_rules()` (`lagoon_like`, `upshift_like`, and those
  with a `VAULT_SETTLEMENT_DELAY_FALLBACKS` entry), uses it as both the
  deposit and redemption delay.
- Lagoon and D2 are also "event gated": their deposits complete in backtests
  only at a recorded `vault_settlement_at` from `vault-settlements.duckdb`
  (`deposit_requires_settlement_event` in trade-executor).

## Verified T3tris facts

Checked on 2026-11-15.

- `GET https://earn.t3tris.finance/api/vaults/{chainId}/{vaultAddress}` returns
  a top-level `estimatedRedemptionTs`. The T3tris app shows it as "Approx.
  redemption date" with the tooltip "One average settlement interval from
  today; not a scheduled or guaranteed date" (app bundle chunk
  `0vryvzfx9mu10.js`). It moved forward 16 seconds between two fetches, so
  it is computed as response time plus an average interval.
- The response carries a `Date` header. The vault-list endpoint
  (`/api/vaults`, `items`) does not include `estimatedRedemptionTs`.
- Values today (estimate minus response time):

  | Vault | API interval | Observed |
  | --- | --- | --- |
  | Arbitrum First USDC `0x98e4…` | 5.5 days | settlement transactions median 7.9 days apart; redemption settlements 5.5–42 days apart |
  | Robinhood Morini `0x5b93…` | 0.27 days | last settlement 7 August 2026, three months ago |
  | Robinhood Kingfisher `0xd4d6…` | 0.0 days | two settlements in one block |
  | Arbitrum Gami USDC `0x9984…`, `0x394c…` | `null` | no or one settlement |

  The average is opaque: it could not be reproduced from the 100-event
  `cadenceActivity` window. It ignores time since the last settlement.
- Curators settle with `settle()` (both directions), `settleDeposits()` or
  `settleRedemptions()`. First USDC's history uses all three, so a settlement
  is not always for both directions. The API publishes a single interval,
  which trade-executor applies to both directions.
- Onchain, the vault emits `DepositsSettled(requestId indexed, …)` and
  `RedemptionsSettled(requestId indexed, …)` and exposes
  `getLastSettlementTimestamp()`.

## Part 1: offchain settlement estimate

### 1. Parse the interval (`t3tris/offchain_metadata.py`)

- Add `average_settlement_seconds: int | None` to `T3trisVaultMetadata`.
- `_fetch_vault_detail()` returns the response `Date` header with the JSON, so
  the parser can compute `estimatedRedemptionTs - Date`. Use the header rather
  than local time so clock skew and slow responses do not change the value.
  Fall back to the local fetch time only when the header is absent.
- Store the **duration**, not the absolute timestamp, in the parsed metadata
  and its disk cache. The cache lives for two days; a cached absolute time
  would shrink the interval with every read.
- `None` when the field is missing or `null`, when the computed interval is
  ≤ 0, and for the vault-list fallback.
- Older cache files without the key read as `None`.

### 2. Publish it (`t3tris/vault.py`, `t3tris/deposit_redeem.py`)

- `T3trisVault.get_withdrawal_period()`: return the Lagoon-shaped
  `WithdrawalPeriod` with only `estimated_settlement`, or `None`.
- `T3trisDepositManager.get_estimated_lock_up()`: return the same
  `estimated_settlement`, as Lagoon's manager does.
- Leave `estimate_redemption_delay()` and `get_redemption_delay_over()` as the
  inherited ERC-7540 sentinels; Lagoon does the same.
- No staleness rule. Publish the API value as T3tris presents it, and document
  in the docstring and README that it ignores time since the last settlement
  (Morini). A stale-cutoff heuristic can be added later if backtests show it
  is needed.

### 3. Migration

`_withdrawal_period` is a persisted accessor value, so add numbered migration
079 (the next free number on master):

- Scope: every existing `t3tris_like` row, on every chain.
- Read each vault's detail response fresh with the module's private
  `_fetch_vault_detail()` and `_parse_vault_detail()`, using the row's chain
  id. Do not call `fetch_t3tris_vault_metadata()`: it needs `web3`, returns an
  in-process cached copy before checking freshness, and writes the disk cache.
  Existing two-day cache files lack the new key and would make the migration
  succeed with only `None`. Preview must not write provider caches.
- Build `WithdrawalPeriod` from the parsed interval with the same helper the
  adapter uses, and stage only `_withdrawal_period`. `_lockup` stays `None`:
  `create_vault_scan_record()` sets it to `max_period` whenever a withdrawal
  period exists (`eth_defi/erc_4626/scan.py:476`).
- Report inspected and changed rows. A vault the API does not list keeps
  `None`.
- Model the runbook docstring on migration 072.

### 4. Tests

No live-API tests, per the repository rules.

- `tests/erc_4626/vault_protocol/test_t3tris_offchain_metadata.py`: a small
  saved detail response plus a `Date` header. Assert the exact interval in
  seconds, `None` for a `null` field, for a ≤ 0 interval and for a list-only
  response.
- The adapter returns the expected `WithdrawalPeriod`, and the manager's
  `get_estimated_lock_up()` matches.
- Migration runner test modelled on
  `tests/vault/test_t3tris_capability_migration.py`, with the detail fetch
  patched: preview changes nothing and writes no cache, apply sets
  `_withdrawal_period`, rerun skips, other rows are untouched, and an
  existing cache file without the new key does not leak into the result.

### 5. Docs

- `README-t3tris.md`: the estimate, its source, its known weaknesses and the
  example values.
- `CHANGELOG.md` entry.

## Part 2: settlement events

### 1. Event reader (new `t3tris/settlement.py`)

Modelled on `accountable/settlement.py`:

- `T3TRIS_PROTOCOL_NAME = "T3tris"`.
- `get_t3tris_settlement_events_by_topic(vault)`: **`DepositsSettled` only.**
  `vault_settlement_at` is direction-blind: `annotate_prices_with_vault_settlements()`
  uses row timestamps without `event_name` (`eth_defi/vault/settlement_data.py`).
  Recording `RedemptionsSettled` would let a `settleRedemptions()` call release
  queued deposits in a deposit-gated backtest. Redemption timing keeps using
  the Part 1 delay. Document this choice in the module docstring.
- `build_t3tris_settlement_rows_from_logs(vault, logs, event_by_topic)`:
  delegate to the generic `build_settlement_rows_from_logs()` in
  `eth_defi/erc_4626/settlement_events.py`, as Lagoon does. No extra
  deduplication or validation.

### 2. Scanner dispatch (`eth_defi/erc_4626/settlement_scan.py`)

- Add `ERC4626Feature.t3tris_like` to `SUPPORTED_SETTLEMENT_FEATURES`.
- Add `T3trisVault` branches to `_prepare_settlement_vault()` and
  `_build_settlement_rows_for_prepared_vault()`, and widen the
  `PreparedSettlementVault.vault` type.
- No backfill step is needed. A vault without scan state starts at its
  first-seen block, so the next scanner cycle reads the full history through
  Hypersync.
- Robinhood Chain (4663) has a configured Hypersync endpoint
  (`eth_defi/hypersync/server.py`), but backend selection does not check chain
  support and an unavailable server raises. There is no acceptable RPC
  fallback: the repository forbids historical `eth_getLogs`. Confirm with the
  existing scanner that a failure is logged and leaves the vault's scan state
  unadvanced, so the next cycle retries.

### 3. Tests

- `tests/erc_4626/test_settlement_scan.py`: a T3tris row is selected and
  dispatched, and logs for one `settle()` transaction become the expected
  rows.
- A fork test is unnecessary: the T3tris deposit tests already produce real
  `DepositsSettled` and `RedemptionsSettled` logs; reuse one recorded receipt
  as the fixture if a log fixture is needed.

### 4. Docs

`README-t3tris.md`, an `eth_defi.erc_4626.vault_protocol.t3tris.settlement`
entry in `docs/source/api/erc_4626/index.rst` next to the other settlement
modules, and the `CHANGELOG.md` entry.

## trade-executor follow-up (separate PR)

In `tradeexecutor/strategy/vault_protocol/__init__.py`
`resolve_vault_protocol_rules()`:

- Part 1: add `ERC4626Feature.t3tris_like` to the set allowed to use
  `estimated_settlement`. Without it the exported value is ignored.
- Part 2: add it to `event_gated`, so queued deposits complete only at a
  recorded T3tris deposit settlement. This is correct only because Part 2
  records `DepositsSettled` alone.
- Consider a `VAULT_SETTLEMENT_DELAY_FALLBACKS` entry for T3tris vaults with
  no estimate (Gami); otherwise they use the two-day default.
- Confirm that T3tris pairs are classified as asynchronous for both
  directions from the published capability.

## Out of scope

- Computing our own interval from the settlement database. Part 2 makes it
  possible; decide after seeing the collected data.
- Binding withdrawal periods: T3tris has no contractual deadline.
- The pending deposit-status probe rerun from the previous PR.

## Risks

- The API value is weak: 0.27 days for a vault untouched for three months,
  0.0 for a same-block pair of settlements. Backtests will be optimistic for
  such vaults until a staleness rule or our own interval replaces it.
- The field is undocumented and can disappear or change meaning; the parser
  must fail to `None`, never raise.
- Part 1 adds one HTTP request per T3tris vault to the migration; the
  scanner already makes the same request for descriptions.
- Deposit gating assumes every `DepositsSettled` event settles the queued
  deposits it covers. A settlement that skips a request (for example a
  whitelist change) would still mark the row.

## Review record

**Codex CLI, 2026-11-15** (`gpt-6.1-sol`, read-only, grounded in the
repository). Verdict: revise before implementation; the Part 1 export path is
sound and 079 is the next migration number. All findings were checked against
the code and applied:

- Blocking: `vault_settlement_at` is direction-blind and curators call
  `settleRedemptions()` alone, so recording both events could release
  deposits in a gated backtest. Part 2 now records `DepositsSettled` only.
- Should-fix: the migration must read fresh detail responses without the
  public fetcher's process and disk caches, and the test covers an old cache
  file. `_lockup` stays `None`.
- Should-fix: the claimed RPC fallback does not exist and would break the
  no-`eth_getLogs` rule; replaced with a check that a Hypersync failure leaves
  scan state unadvanced.
- Nit: no extra deduplication in the row builder.
- Nit: add the Sphinx API entry for the new module.



**PR cleanup review, 2026-11-15.** Codex CLI `gpt-6.1-sol`, read-only and
grounded, on the implementation. Verdict: revise before handoff. Applied:

- Migration 079 failed open on transient API errors; it now fetches in strict
  mode, with a test that a 500 fails the run and a later run applies.
- The scanner test read historical logs through JSON-RPC, which the
  repository forbids; it now serves saved receipt logs and makes no RPC reads.
- Added tests of `T3trisVault.get_withdrawal_period()` and the manager's
  `get_estimated_lock_up()`, including an old cache entry without the key.
- The one-hour floor is now described as a heuristic.

Not adopted: seeding a legacy cache file in the migration test. The test
already fails if the migration calls the cached fetcher at all.

**Independent subagent review of PR 1784, 2026-11-15.** A fresh
general-purpose subagent, with no earlier conclusions, reviewed the PR
read-only. No blocking findings. Applied:

- The README recommended measured intervals for redemption timing, but only
  `DepositsSettled` is recorded; it now limits measured markers to deposit
  timing and records what adding redemptions later would need.
- Cache files written before the estimate existed, and vault-list fallback
  rows, could make the scanner overwrite migration 079's estimate with `None`.
  The fetcher now refreshes a cache file without the key and does not write
  fallback rows to disk, with tests for both.
- The scan test fixture comment now says the log `data` is a placeholder.
- `_fetch_response_timestamp()` is renamed `_read_response_timestamp()`, as
  it makes no network read, and treats a naive `-0000` date as UTC.
- The migration test also blocks the cached fetcher's binding in `vault.py`.

Not adopted: replacing the migration's private helper imports with a public
strict-fetch function.
