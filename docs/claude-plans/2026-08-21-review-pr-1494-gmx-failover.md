# Review: PR #1494 — GMX API failover (issue #1491)

- PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1494
- Issue: https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1491
- Reviewed: 2026-08-21, head `aeffdd454`, base `origin/master` (`1ca3e95ba`)
- Method: two-axis review (standards vs spec) with independent sub-agents, then hand verification of the flagged code paths.

## Verification run

- `tests/gmx/test_failover.py`: **22 passed in 0.29 s** (fully mocked, no secrets needed).
- `ruff format --check`: clean. `ruff check` on changed lines: clean; the remaining errors in `api.py` (FBT001/002, PLW0602) are pre-existing on master.
- Every `# noqa` in the diff suppresses a rule that is actually enabled (`preview = true` + `PLR/E/N/ARG/EM/RUF` selected); `RUF100` reports none unused.

## Overall verdict

The PR fixes the *crash* described in the issue — sync `fetch_ticker` no longer raises a bare `ValueError`, async `fetch_ticker` no longer raises `ExchangeError`, and degraded 200 payloads are no longer cached. That is the right minimal fix for the incident and is safe to merge after the blocking items below are addressed. However it delivers roughly Phase 1.4, 4.1, 4.3, 5.1 (partially) and 5.2 of the issue's plan; the structural items (endpoint pool, `FailoverPolicy`, breaker, jitter, distribution) are deferred and clearly labelled as such in the PR text. Two items are deferred *silently* and one new escape hatch for the exact crash class remains.

**Recommendation: request changes** for items B1–B3; the rest can be follow-ups.

## Blocking

### B1. `GMXInvalidPayloadError` is a `ValueError` and can still escape `fetch_ticker`

- `eth_defi/gmx/ticker_validation.py:11` — `class GMXInvalidPayloadError(ValueError)`.
- `eth_defi/gmx/api.py:421` — `get_tickers()` raises it when the post-transport re-validation fails twice.
- `eth_defi/gmx/ccxt/exchange.py:3859-3862` — `fetch_ticker` only converts `GMXAPIUnavailable`; `GMXInvalidPayloadError` propagates as a `ValueError` to Freqtrade, which is the precise crash class from #1491.
- `tests/gmx/test_failover.py::test_get_tickers_raises_when_retry_also_degraded` *enshrines* this escape with `pytest.raises(GMXInvalidPayloadError)`.

The docstring rationale ("subclasses `ValueError` so callers that already catch `ValueError` keep working") is the opposite of spec §5.1, which reserves `ValueError` for programmer errors. With the real transport this branch is unreachable (the driver wraps validation failures into `GMXAPIUnavailable`), so the only path that exercises it is a mocked `_make_request`. Fix: either make `GMXInvalidPayloadError` a subclass of `GMXAPIUnavailable`/`RuntimeError`, or delete the post-transport re-validation (see B2) and have `fetch_ticker` catch both.

### B2. Test-only production control flow in `get_tickers()`

`eth_defi/gmx/api.py:410-421` re-runs `validate()` after `_make_request()` and retries once. The code comment says this exists "for drivers that bypass the transport `validate` hook (e.g. `_make_request` mocked in tests)". The transport already validates and fails over per endpoint, so in production this branch can only fire if `validate` is non-deterministic (it is not). Mock `requests.get` (as the driver tests already do) and remove the branch. It also introduces a second, different failure type (B1).

### B3. Async driver still diverges from sync — and the docstring now claims parity

`eth_defi/gmx/ccxt/async_support/async_http.py:27-29`: "Mirrors `make_gmx_api_request` with the same tier list, retry defaults … and final exception." Not true:

| | sync `retry.py` | async `async_http.py` |
|---|---|---|
| Tiers | 5 (incl. `FALLBACK_3`) | 4 |
| Full-cycle retry | `full_cycle_retries=2` | none |
| Backoff | `initial_delay=2.0`, `max_delay=30` | `retry_delay=0.1`, `0.1 → 0.2 → 0.4` |
| Config | `GMXRetryConfig` | ad-hoc kwargs |
| `min_expected_tickers` honoured | yes (`api.py:402`) | no — `async_support/exchange.py:1628` calls `validate_tickers_payload(p)` with defaults and no `last_good_count` |

Issue §2.3 ("Async gains full-cycle retry and tier coverage") is not listed among the PR's deferred items, and the live bot runs the async path. Either implement it or add it explicitly to the deferred list and correct the docstring. At minimum, thread `min_expected_tickers` through the async validate call so the two drivers cannot disagree about what a "healthy" payload is.

## Should fix before merge

### S1. Siblings still raise raw `GMXAPIUnavailable` (`RuntimeError`) to Freqtrade

Spec §5.1 says "`fetch_ticker()` and siblings". Unconverted call sites that sit in the Freqtrade hot loop:

- `eth_defi/gmx/ccxt/exchange.py:4029` — `fetch_tickers()` → `self.api.get_tickers()`
- `eth_defi/gmx/ccxt/async_support/exchange.py:1840` — `fetch_ohlcv()` → `/prices/candles`
- `eth_defi/gmx/ccxt/async_support/exchange.py:1108` (`/markets/info`), `:1745` (`/apy`), `:1031` (`/tokens`)

A total outage during candle refresh still surfaces as a non-ccxt `RuntimeError`. Cheapest fix: convert at the `GMXAPI._make_request` / `async_make_gmx_api_request` boundary inside the ccxt exchange classes, not per call site.

### S2. Async `max_retries` default 2→3 is an unannounced behaviour change

`async_http.py:22`. Affects every async caller above with one extra attempt per failing tier. Not mentioned in the changelog. Fine if intended; say so.

### S3. Stale-fallback gating does not match spec §4.2

- Spec keys stale serving to read-only *paths*; the PR keys it to the `GMXAPI` instance's `retry_config.allow_stale_prices`. `eth_defi/gmx/order/base_order.py:585` constructs `GMXAPI(config=None, chain=...)` and calls `get_tickers()` on the order path; today that gets `DEFAULT_RETRY_CONFIG` so stale is off, but if a consumer ever mutates the default the order path will silently accept stale prices. A path-level guard (or a separate `get_tickers(allow_stale=...)` argument) is more robust.
- `api.py:423-433`: stale snapshot is served even to callers that passed `use_cache=False`, and a successful `use_cache=False` fetch never refreshes the snapshot (`api.py:436`), so `last_good_count` and the stale snapshot can silently age out for such callers.
- No test asserts that `/signed_prices/latest` never serves stale (it holds structurally — `get_signed_prices()` has no stale path — but the spec lists it as a required test).

### S4. `min_expected_tickers=100` hard floor is a new outage vector

`ticker_validation.py:48`: `threshold = max(min_expected_tickers, 0.8 * last_good_count)`. Live count is ~124. If GMX delists ~25 tokens, *every* endpoint returns an "invalid" payload, the whole ticker path fails on every tier and cycle, and all consumers go down on a healthy upstream. The ratio guard was designed to survive delistings; the fixed floor defeats it. Consider a lower floor (e.g. 50) or make the floor only apply when `last_good_count is None`.

### S5. Final failure is no longer logged at ERROR

CLAUDE.md "Logging retries": log `ERROR` with traceback after the final retry. `async_http.py` previously called `logger.error(...)` before raising; the PR removed it. `retry.py:383` raises `GMXAPIUnavailable` with no ERROR log either. Callers may log, but the driver should.

### S6. Dead tier still routed for `/prices/tickers`

Issue Finding 1 is mitigated, not fixed: the 404 now costs one request instead of 3 × backoff, but `fallback-3` (`gmxapi.ai`) is still tried on every cycle, and `test_make_gmx_api_request_attempts_summary_covers_all_five_tiers` asserts it is. Capability-based routing is deferred (OK), but a one-line skip of `FALLBACK_3` for `/prices*` and `/signed_prices*` paths is cheap and would stop the noise.

## Standards (CLAUDE.md)

Hard violations:

- Docstrings must document args and returns: `retry.py:33-42` `is_retryable_http_status` has neither; `retry.py:57` `GMXAPIUnavailable.__init__` has no docstring or `-> None`.
- Type hints on all functions: `tests/gmx/test_failover.py` — `_ticker() -> dict`, `_healthy_tickers() -> list` (bare generics), every `_Fake*` method and every `test_*` lacks a return annotation.
- Prefer fixtures: `test_failover.py:164,184,199,212,226` each call `_TICKER_PRICES_CACHE.clear()` inline with no teardown; module-global state leaks into other GMX tests in the same session. Use an autouse fixture that clears before and after.
- "Never use test classes in pytest": `_FakeResponse`, `_FakeClientResponse`, `_FakeSession`, `_Stub` are helper classes not test classes, so technically compliant, but `_Stub.api()` (`:257`) is dead, and `_FakeSession._last_response` (`:307`) is assigned lazily and raises `AttributeError` if the queue is empty.

Judgement calls (Fowler baseline):

- **Duplicated Code / Repeated Switches** — `retry.py:317,333,349,365,381` adds the same `attempts.append(f"{tier}: {error}")` hunk to an already-unrolled five-tier cascade. A `for tier_name, url in tiers:` loop removes ~60 lines and the `PLR0917` noqa. Sync and async now duplicate 4xx classification + validation + attempt-summary logic.
- **Divergent Change** — `GMXRetryConfig` now carries retry policy, validation thresholds and stale-serving policy: three reasons to change.
- **Primitive Obsession / magic numbers** — `ticker_validation.py:48-51`: `0.8` ratio and `[:5]` sample are unnamed.
- **Naming** — `GMXAPIUnavailable` (N818 suppressed) vs `GMXInvalidPayloadError` in the same PR; pick one convention.
- Avoidable `noqa`s: `api.py:402` `E731` → use `functools.partial`; `async_support/exchange.py:1628` `PLW0108` → pass `validate_tickers_payload` directly (the lambda adds nothing); `async_http.py:77` and `retry.py:170` `PLR2004` — the `>= 400` guard is redundant because `is_retryable_http_status()` already returns `True` below 400.

## Spec coverage summary

| Issue item | Status |
|---|---|
| 1.1–1.3 endpoint pool, capability routing, shims | Deferred (stated) |
| 1.4 4xx non-retryable, immediate failover | Done, both drivers |
| 2.1–2.2 `FailoverPolicy`, thin drivers | Deferred (stated) |
| 2.3 async full-cycle retry + 5 tiers | **Missing, not stated as deferred** (B3) |
| 3.x distribution, breaker, jitter | Deferred (stated) |
| 4.1 per-endpoint validation, never cached | Done; floor risk (S4) |
| 4.2 bounded stale fallback, read-only only | Done with different gating (S3) |
| 4.3 cache stores only validated payloads | Done |
| 5.1 `ExchangeNotAvailable` from `fetch_ticker` and siblings | `fetch_ticker` only (S1); `ValueError` subclass reintroduced (B1) |
| 5.2 `GMXAPIUnavailable` with attempt summary | Done |
| Tests parametrised over sync+async | Not done; coverage asymmetric (no async stale/429/408 driver tests; 429/408 only unit-tested on the classifier) |
| Live endpoint contract test | Deferred (stated) |
| Observability one-line WARNING on failover | Only on total failure via the exception message; successful failovers still emit only interleaved per-attempt lines |

## What is good

- Correct diagnosis in "Lessons learnt": `ExchangeNotAvailable` is the right retryable type; `TemporaryError` does not exist in ccxt.
- `except Exception` → `except requests.RequestException` in `retry.py:196` fixes a documented repo violation.
- Attempt summary in `GMXAPIUnavailable` makes the next incident diagnosable from one log line.
- Stale fallback defaults off, so behaviour is unchanged until opted in.
- Changelog entry present, dated, UK spelling, correct format.
