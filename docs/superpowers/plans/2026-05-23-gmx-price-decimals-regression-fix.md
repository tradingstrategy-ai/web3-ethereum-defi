# GMX price-decimals regression fix

**Date:** 2026-05-23
**Branch:** `fix/issue-67-no-key-resolver` (stack onto PR #1030 as commit #5)
**Severity:** Production — cascade misclassifies legitimately-pending limit orders
as cancelled when index-token decimals ≠ 18 (DOGE/TAO/WBTC).

## Goal

Eliminate the `/1e30` price-conversion bug in three areas of the GMX
limit-order plumbing so all index-token decimals (8, 9, 18, …) round-trip
to human-readable USD prices.

## Root cause

GMX V2 prices use the **30-decimal-precision** format scaled by
`10^(30 - token_decimals)`. Five hot sites apply a hard `/1e30` divisor
instead of the decimals-aware helper that already exists in the repo
(`_convert_price_to_usd`, introduced by PR #853 / `417e243d`).

Commit `367af42c` (May 12, 2026) added the new `_resolve_order_from_sources`
cascade + 3 builders without using the helper — reintroducing the same bug
class for the cascade's output path. PR #1030's matcher (`_match_rest_pending_order`)
also uses `/1e30`. The Reader matcher uses `PendingOrder.trigger_price_usd`,
which is hard-coded for 18-decimal tokens only and silently wrong for DOGE (8)
and TAO (9) — same bug class, different surface.

Empirically observed corruption in production multistrategy DB:

| Pair | Token decimals | Correct divisor | Buggy `/1e30` overshoots | Observed `orders.price` |
|---|---|---|---|---|
| DOGE | 8  | 10^22 | by `10^8`  | 1.0086e-09 (correct 0.10086) |
| FIL  | 18 | 10^12 | by `10^18` | 9.1901e-19 (correct 0.91901) |
| TAO  | 9  | 10^21 | by `10^9`  | 2.6664e-07 (correct 266.638) |

For 18-decimal index tokens (ETH and most ERC-20 alts), the `/1e30` arithmetic
happens to cancel the chain's `10^(30 - 18) = 10^12` scale because
`/1e30 = /1e12 × /1e18` — but the per-token-decimals factor only equals 1
when token_decimals = 0, which never happens. The reason 18-decimal tokens
"work" is coincidental: their chain scale `10^12` and bug divisor `10^30`
both produce small float values, but the comparison-side bug uses the same
`/1e30`, so two equally-corrupted values still match. Non-18-decimal tokens
(DOGE 8, TAO 9 synthetic, BTC/WBTC 8) break loudly because the chain stores
their prices at a different magnitude, breaking the accidental cancellation.

## Scope

### Sites to fix

| # | File | Line | Function | Change |
|---|---|---|---|---|
| 1 | `eth_defi/gmx/freqtrade/gmx_exchange.py` | 1272 | `_match_rest_pending_order` | `/1e30` → `self._api._convert_price_to_usd(raw, market)` |
| 2 | `eth_defi/gmx/freqtrade/gmx_exchange.py` | 1174 | `_match_reader_pending_order` | `trigger_price_usd` → `trigger_price_usd_for_decimals(decimals)` |
| 3 | `eth_defi/gmx/ccxt/exchange.py` | 9106 | `_build_order_from_trade_action` | `/1e30` → `_convert_price_to_usd`; derive amount from `sizeDeltaInTokens` when present |
| 4 | `eth_defi/gmx/ccxt/exchange.py` | 9161 | `_build_order_from_rest_order` | `/1e30` → `_convert_price_to_usd`; derive amount as `sizeDeltaUsd / triggerPrice` |
| 5 | `eth_defi/gmx/ccxt/async_support/exchange.py` | 2285 | (async sibling of #3) | lockstep |
| 6 | `eth_defi/gmx/ccxt/async_support/exchange.py` | 2340 | (async sibling of #4) | lockstep |
| 7 | `eth_defi/gmx/freqtrade/gmx_exchange.py` | 1220-1221 | docstring | fix wording: not "raw 1e30 USD" |

### Tests

User has already pre-committed test scaffolding (uncommitted local diff):

- `tests/gmx/freqtrade/test_fetch_order_no_key_reconcile.py`:
  - `_rest_order` fixture: `token_decimals` param (default 8), `price * 10^(30 - decimals)` formula
  - `_fake_gmx` fixture: exposes `index_token` per market + `_token_metadata` for DOGE (8) and FIL (18)
  - New test `test_no_key_open_limit_matches_fil_raw_trigger_price_with_18_decimals` — **fails on current production code**; passes after the fix.

The cascade behavioural contract is fully covered by this failing test +
existing 16 tests. No new test file is strictly required, but a focused unit
test on the builders is recommended for completeness (see "Stretch").

## Approach

1. **Make the failing FIL test pass.** This drives sites #1 and #2.
2. **Patch the builders (sites #3-#6) in lockstep.** Same template per the
   adapter's existing `_convert_price_to_usd` calls in `parse_trade` etc.
3. **Fix docstring (#7).**
4. **Run full test suite.** Confirm 17/17 pass (was 16 + new FIL).
5. **Confirm older bot DBs are unaffected** (no `orders.price` corruption on
   PingPong/IchiV2/IchiV3 — they never hit the cascade builders).
6. **Show diff to user, get review approval, commit.**
7. **Push to PR #1030 branch as commit #5.**
8. **Update PR body** to include the new commit and the bug class story.

## Files touched (estimate)

```
eth_defi/gmx/ccxt/exchange.py                   ~25 lines (2 funcs)
eth_defi/gmx/ccxt/async_support/exchange.py     ~25 lines (lockstep)
eth_defi/gmx/freqtrade/gmx_exchange.py          ~12 lines (2 matchers + docstring)
tests/gmx/freqtrade/test_fetch_order_no_key_reconcile.py   already-staged by user
```

## Validation plan

- **Unit:** `pytest tests/gmx/freqtrade/test_fetch_order_no_key_reconcile.py` →
  17/17 pass. Plus existing `test_cached_order_key_patch.py` (6) +
  `test_order_key_cache.py` (17) + `test_reduce_only_sizing.py` (10).
- **Integration:** rebuild Docker image with new pin, restart multistrategy bot,
  observe at least one cycle of limit orders. Assert `orders.price ≈ ft_price`
  for newly-created limits.
- **Production receipt:** at next UTC midnight cycle, log lines should show
  the cascade matching on FIL trigger price 0.91901 (not 9.19e-19) on the
  restored trade 19.

## Out of scope (deferred)

- `_build_order_from_rest_position` (line 2352 / 9173 area) also uses `/1e30`
  for `sizeInUsd` — but that field IS pure USD, so the math is correct.
  No change needed.
- `_convert_price_to_usd` heuristic short-circuit (`0.01 <= v <= 1_000_000`).
  Works for current GMX universe; refactor if sub-cent tokens (PEPE/SHIB) get
  whitelisted.
- Subsquid path (`_resolve_order_from_sources` Tier-D) uses
  `_build_order_from_trade_action` — fixed by site #3, no separate change.

## Risks

1. **Markets not loaded.** `_convert_price_to_usd(raw, None)` returns the
   raw value unchanged when `market is None`. Acceptable — the cascade only
   fires after `load_markets()` completes during Freqtrade startup.
2. **Async lockstep drift.** Mitigated by adding the same edits in the same
   PR and keeping the diffs structurally identical.
3. **Stretch test file deferred.** Direct unit tests on the builders would
   tighten coverage; the FIL/DOGE end-to-end cascade tests already give
   regression protection.

## Stretch (optional, can be follow-up)

- `tests/gmx/ccxt/test_build_order_decimals.py` — 6 direct unit tests on
  the builders for DOGE/FIL/TAO round-trip of price + amount.

## Success criteria

- [ ] FIL 18-decimals test passes
- [ ] All 17 reconcile tests pass
- [ ] No regression in `test_cached_order_key_patch` / `test_order_key_cache`
- [ ] `git diff` reviewed by user before commit
- [ ] Commit pushed to PR #1030 branch
- [ ] PR #1030 body updated
- [ ] Older bot DBs sanity-checked (no corruption)

## Links

- PR #1030: <https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1030>
- Regression-introducing commit: `367af42c` (May 12, 2026)
- Original fix template: `417e243d` (PR #853, March 17, 2026)
- Failing test: `tests/gmx/freqtrade/test_fetch_order_no_key_reconcile.py::TestNoKeyRestOrderResolver::test_no_key_open_limit_matches_fil_raw_trigger_price_with_18_decimals`
