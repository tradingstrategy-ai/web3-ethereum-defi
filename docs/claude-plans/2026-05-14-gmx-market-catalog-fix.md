# GMX Market Catalog + Order Lifecycle Fix Implementation Plan

> **For agentic workers:** REQUIRED — use `superpowers:subagent-driven-development` (or `superpowers:executing-plans`) to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. Per global CLAUDE rule: **NO code edits, NO git operations until the user explicitly approves this plan**.

**Goal:** Fix GMX order placement so it never lands on the wrong market again, fills are reliably tracked, and limit orders are not zombie-cancelled. Build a liquidity-aware market catalog as the single source of truth for market + collateral selection. Re-land reverted Issue #67 fixes on top.

**Architecture:** New `MarketCatalog` in `eth_defi/gmx/core/market_catalog.py` (sync) + `eth_defi/gmx/core/market_catalog_async.py` (async). Catalog enumerates GMX V2 markets via REST `/markets` (primary) and Reader contract (fallback), augments with liquidity + OI from Subsquid (with graceful degradation), persists to disk + memory with TTL, and exposes `pick_market(base, requested_collateral)` that ranks markets by liquidity USD. All call sites in `OrderArgumentParser`, sync `ccxt/exchange.py`, and async `ccxt/async_support/exchange.py` route through the catalog. Resolver tier order in `_resolve_order_from_sources` is rebalanced (REST first, Subsquid fallback) and order_key caching becomes eager. Reverted PR #1000 + PR #1001 fixes are re-applied on top.

**Tech Stack:** Python 3.12, web3-ethereum-defi, `eth_defi.gmx.*`, pytest, hypothesis (optional), `dataclass(slots=True)`, Sphinx docstrings.

**Branch base:** `fix/issue-67-combined` (HEAD `6c7391c5`). Work in a new branch or continue on the combined branch — user decides at execution time.

**Lockstep rule:** Every change to `eth_defi/gmx/ccxt/exchange.py` must mirror to `eth_defi/gmx/ccxt/async_support/exchange.py` in the same commit. Same for any helper in `eth_defi/gmx/core/`.

---

## Chunk 1: Market Catalog Foundation

### Task 1.1: Define `MarketEntry` dataclass + module skeleton

**Files:**
- Create: `eth_defi/gmx/core/market_catalog.py`
- Test: `tests/gmx/test_market_catalog.py`

- [ ] **Step 1: Write failing test** for `MarketEntry` construction + invariants.

```python
# tests/gmx/test_market_catalog.py
from eth_defi.gmx.core.market_catalog import MarketEntry

def test_market_entry_synthetic_flag():
    e = MarketEntry(
        market_key="0xabc",
        index_token_symbol="BTC",
        index_token_address="0x111",
        long_token_symbol="tBTC",
        long_token_address="0x222",
        short_token_symbol="tBTC",
        short_token_address="0x222",
        liquidity_usd=0.0,
        oi_long_usd=0.0,
        oi_short_usd=0.0,
        refreshed_at=0,
    )
    assert e.is_synthetic is True
    assert e.accepts_collateral("USDC") is False
    assert e.accepts_collateral("tBTC") is True
```

- [ ] **Step 2: Run test, verify it fails** with `ImportError`.

Run: `pytest tests/gmx/test_market_catalog.py -v`

- [ ] **Step 3: Implement `MarketEntry`**:

```python
# eth_defi/gmx/core/market_catalog.py
from dataclasses import dataclass

@dataclass(slots=True, frozen=True)
class MarketEntry:
    """Single GMX V2 market with liquidity-aware metadata.

    :param market_key: On-chain market token address (case-sensitive).
    :param index_token_symbol: Normalised symbol (k-prefix stripped) of index token.
    :param liquidity_usd: Pool TVL in USD at refresh time. 0.0 if augmentation failed.
    :param oi_long_usd: Long-side open interest at refresh time. 0.0 if unknown.
    :param refreshed_at: Unix seconds when entry was last refreshed.
    """

    #: 0x-prefixed checksum market_key.
    market_key: str
    index_token_symbol: str
    index_token_address: str
    long_token_symbol: str
    long_token_address: str
    short_token_symbol: str
    short_token_address: str
    liquidity_usd: float
    oi_long_usd: float
    oi_short_usd: float
    refreshed_at: int

    @property
    def is_synthetic(self) -> bool:
        """True when long_token == short_token (single-sided synthetic market)."""
        return self.long_token_address.lower() == self.short_token_address.lower()

    def accepts_collateral(self, collateral_symbol: str) -> bool:
        """Whether the given collateral symbol matches long or short token."""
        cs = collateral_symbol.upper()
        return cs in {self.long_token_symbol.upper(), self.short_token_symbol.upper()}
```

- [ ] **Step 4: Run test, verify pass.**
- [ ] **Step 5: Commit.**

```bash
git add eth_defi/gmx/core/market_catalog.py tests/gmx/test_market_catalog.py
git commit -m "feat(gmx/catalog): MarketEntry dataclass with synthetic + accepts_collateral"
```

### Task 1.2: Market enumeration (REST primary, Reader fallback)

**Files:**
- Modify: `eth_defi/gmx/core/market_catalog.py`
- Test: `tests/gmx/test_market_catalog.py`

- [ ] **Step 1: Failing test** for `enumerate_markets(chain)` against a mocked REST response (use `responses` or `pytest-httpx`).
- [ ] **Step 2: Reuse `_fetch_markets_from_rest()` + `_fetch_markets_from_onchain()`** from existing `eth_defi/gmx/core/markets.py` (already implemented per code archaeology). Wrap into `enumerate_markets()` returning list[dict] of raw market metadata.
- [ ] **Step 3: Apply `SYMBOL_NORMALISE`** to `index_token_symbol`, `long_token_symbol`, `short_token_symbol` at this stage so downstream sees `BONK` not `kBONK`.
- [ ] **Step 4: Test passes.**
- [ ] **Step 5: Commit.**

### Task 1.3: Liquidity + OI augmentation (Subsquid w/ graceful fallback)

**Files:**
- Modify: `eth_defi/gmx/core/market_catalog.py`
- Test: `tests/gmx/test_market_catalog.py`

- [ ] **Step 1: Failing test** for `augment_with_liquidity(markets, chain)` — covers (a) happy path returning liquidity_usd > 0, (b) Subsquid 404 → liquidity_usd = 0.0, no exception, (c) Subsquid timeout → liquidity_usd = 0.0, no exception.
- [ ] **Step 2: Implement augmentation** with `requests.get(...)` + 5s timeout, retry once, then degrade silently. WARN log on failure (don't ERROR — degradation is acceptable).
- [ ] **Step 3: Use `eth_defi.gmx.core.open_interest.get_open_interest()`** for OI per pool (already exists).
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

### Task 1.4: Disk + memory cache with TTL

**Files:**
- Modify: `eth_defi/gmx/core/market_catalog.py`
- Test: `tests/gmx/test_market_catalog.py`

- [ ] **Step 1: Failing test** — write catalog to disk, reload from disk, verify TTL invalidation after `refreshed_at + 86400`.
- [ ] **Step 2: Implement `MarketCatalog`** class with:
  - In-memory `_cache: dict[int, list[MarketEntry]]` keyed by chain_id, 5min TTL
  - Disk cache at `~/.cache/eth_defi/gmx/market_catalog_{chain_id}.json`, 24h TTL
  - Single-flight guard via `threading.Lock` per chain to prevent stampede
  - `refresh()` method to force re-fetch
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

---

## Chunk 2: Selection Logic

### Task 2.1: `pick_market(base, requested_collateral)` with liquidity ranking

**Files:**
- Modify: `eth_defi/gmx/core/market_catalog.py`
- Test: `tests/gmx/test_market_catalog.py`

- [ ] **Step 1: Failing test** for selection scenarios:
  - BTC with USDC → returns WBTC-USDC market (highest liquidity that accepts USDC), not tBTC-tBTC
  - BTC with tBTC → returns tBTC-tBTC if higher liquidity, else WBTC-USDC if no tBTC-tBTC
  - BONK with USDC → returns the USDC-supporting BONK market
  - SOL with USDC where no SOL/USDC pool exists → falls back to highest-liquidity SOL market, returns `(entry, fallback_collateral_symbol)` tuple, logs WARNING
- [ ] **Step 2: Implement:**

```python
def pick_market(
    self,
    base_symbol: str,
    requested_collateral: str,
    chain_id: int,
) -> tuple[MarketEntry, str]:
    """Pick the best GMX market for a base + collateral.

    :param base_symbol: Normalised base token (e.g. 'BTC', 'BONK' — not 'kBONK').
    :param requested_collateral: Preferred collateral symbol (e.g. 'USDC').
    :returns: (chosen entry, actual collateral symbol). When the requested
        collateral isn't supported by any pool for this base, falls back to
        the highest-liquidity pool's long_token and emits a WARNING.
    :raises NoMarketFoundError: When no markets exist for base_symbol at all.
    """
```

Logic:
1. Filter catalog by `index_token_symbol == base_symbol`
2. If empty → raise `NoMarketFoundError`
3. Partition into `accepting` and `not_accepting` by `entry.accepts_collateral(requested_collateral)`
4. If `accepting` non-empty → return `max(accepting, key=lambda e: e.liquidity_usd), requested_collateral`
5. Else → pick `top = max(not_accepting, key=lambda e: e.liquidity_usd)`, log WARNING, return `(top, top.long_token_symbol)`

- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

### Task 2.2: Wire into `OrderArgumentParser`

**Files:**
- Modify: `eth_defi/gmx/order_argument_parser.py:235-337` (`_handle_missing_market_key`)
- Modify: `eth_defi/gmx/order_argument_parser.py:484-518` (`find_all_market_keys_by_index_address` — to be deprecated)
- Test: `tests/gmx/test_order_argument_parser.py` (extend or update existing test)

- [ ] **Step 1: Failing test** — submit a BTC order with USDC collateral, expect catalog-selected WBTC-USDC market_key, not tBTC-tBTC.
- [ ] **Step 2: Replace pool-type sort** with `MarketCatalog.pick_market()` call. Deprecate `find_all_market_keys_by_index_address` (keep with `DeprecationWarning` until callers migrate).
- [ ] **Step 3: Update `_check_if_valid_collateral_for_market`** error message to suggest the catalog's recommendation (e.g. "Try collateral_symbol='tBTC' — it's the only one this synthetic market accepts.").
- [ ] **Step 4: Tests pass; full `pytest tests/gmx/` clean.**
- [ ] **Step 5: Commit.**

### Task 2.3: Synthetic-market explicit handling

**Files:**
- Modify: `eth_defi/gmx/order_argument_parser.py`
- Test: `tests/gmx/test_order_argument_parser.py`

- [ ] **Step 1: Failing test** — when the only available market for base X is synthetic (long==short==Y), and the user requested collateral Z ≠ Y, ensure the adapter (a) logs a clear WARNING explaining the override, (b) uses Y as collateral, (c) does NOT raise. Strategy code may then opt out at a higher level.
- [ ] **Step 2: Implement** that branch using `entry.is_synthetic` + fallback collateral logic from Task 2.1.
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

---

## Chunk 3: Async Mirror

### Task 3.1: Async `MarketCatalog` wrapper

**Files:**
- Create: `eth_defi/gmx/core/market_catalog_async.py`
- Test: `tests/gmx/test_market_catalog_async.py`

- [ ] **Step 1: Failing test** for `AsyncMarketCatalog(chain_id).pick_market(...)` returning the same shape as sync.
- [ ] **Step 2: Implement** using `aiohttp` for REST, `AsyncWeb3` for Reader fallback. Share the dataclass + sort logic via direct import — only the IO layer is async. Cache layer shared (file IO is sync — wrap with `asyncio.to_thread`).
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

### Task 3.2: Port `_resolve_market_info` + `fetch_pools_for_symbol` to async

**Files:**
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py`
- Test: `tests/gmx/ccxt/test_async_market_resolution.py`

- [ ] **Step 1: Failing test** for `await async_exchange._resolve_market_info("BTC/USDC:USDC")` returning the same `MarketEntry` fields as sync.
- [ ] **Step 2: Port** the sync `_resolve_market_info` (sync exchange.py:5508) to async, replacing `Web3` calls with `AsyncWeb3`, swapping `requests` for `aiohttp`. Use `AsyncMarketCatalog` underneath.
- [ ] **Step 3: Port `fetch_pools_for_symbol`** similarly.
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

### Task 3.3: Wire async `_convert_ccxt_to_gmx_params_async` through catalog

**Files:**
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py:4029` (existing function)
- Test: `tests/gmx/ccxt/test_async_market_resolution.py`

- [ ] **Step 1: Failing test** — submit BTC/USDC:USDC order via async path, verify it lands on WBTC-USDC market.
- [ ] **Step 2: Update** the async converter to call `AsyncMarketCatalog.pick_market()` instead of any first-match logic.
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

---

## Chunk 4: Symbol Normalisation Completeness

### Task 4.1: Extend `SYMBOL_NORMALISE` for kPEPE, kFLOKI + audit

**Files:**
- Modify: `eth_defi/gmx/symbols.py:26-32`
- Test: `tests/gmx/test_symbols.py`

- [ ] **Step 1: Failing test** asserting `normalise("kPEPE") == "PEPE"`, `normalise("kFLOKI") == "FLOKI"`.
- [ ] **Step 2: Add entries** to `SYMBOL_NORMALISE`:

```python
SYMBOL_NORMALISE = {
    "XAUT.v2": "XAUT",
    "kBONK": "BONK",
    "kSHIB": "SHIB",
    "kPEPE": "PEPE",
    "kFLOKI": "FLOKI",
}
```

- [ ] **Step 3: Add audit script** `scripts/audit_gmx_k_symbols.py` that fetches live `/markets` and prints any token symbol starting with lowercase `k` that's not in the map. Run it; capture output in the commit message.
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

---

## Chunk 5: `order_key` Storage Fix (Issue B root cause)

### Task 5.1: Re-rank resolver tiers (REST primary, Subsquid fallback)

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py` (`_resolve_order_from_sources`)
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py` (async mirror)
- Test: `tests/gmx/ccxt/test_order_resolution.py`

- [ ] **Step 1: Failing test** — mock Subsquid returning 404, REST v2 returning a valid order; verify resolver returns the REST result, not None.
- [ ] **Step 2: Reorder tiers** in `_resolve_order_from_sources`:
  1. REST v2 (gmxapi.io → gmxapi.ai)
  2. Reader contract
  3. EventEmitter (RPC log scan)
  4. Subsquid (fallback only)
- [ ] **Step 3: Mirror in async.**
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

### Task 5.2: Eager-cache `order_key` on first successful resolution

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py` (`fetch_order`, `_resolve_order_from_sources`)
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py`
- Test: `tests/gmx/ccxt/test_order_resolution.py`

- [ ] **Step 1: Failing test** — first `fetch_order(id)` from REST stores `order_key` in cache; second `fetch_order(id)` does not call REST.
- [ ] **Step 2: Add cache write** at the moment ANY resolver succeeds (not only when the order is settled). Cache key: `(chain_id, order_id)`. Bounded LRU, 1000 entries default.
- [ ] **Step 3: Mirror in async.**
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

### Task 5.3: Structured logging for resolver failures

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py`
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py`

- [ ] **Step 1: Failing test** — when all resolvers miss, log includes pair, chain_id, order_id, tried tiers, last error per tier.
- [ ] **Step 2: Implement** with `logger.warning("no order_key resolved", extra={...})`. Avoid f-strings in log calls (project rule).
- [ ] **Step 3: Mirror in async.**
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

---

## Chunk 6: Re-land Reverted Fixes

### Task 6.1: Re-apply zombie-cancellation fix (originally PR #1000)

**Files:**
- Modify: `eth_defi/gmx/freqtrade/gmx_exchange.py` (`Gmx.fetch_order`)
- Test: `tests/gmx/freqtrade/test_zombie_orders.py` (already exists per `f55e6764`)

- [ ] **Step 1: Cherry-pick or re-implement** commit `82dda919`: gate zombie heuristic on `order["type"] == "market"`.
- [ ] **Step 2: Run existing regression suite** `tests/gmx/freqtrade/test_zombie_orders.py` — verify pass.
- [ ] **Step 3: Commit.**

### Task 6.2: Re-apply cache-miss timestamp fix (originally PR #1001)

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py`
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py`
- Test: `tests/gmx/ccxt/test_fetch_order_cache.py`

- [ ] **Step 1: Cherry-pick or re-implement** commit `df1703b8`: synthetic-order timestamp uses `block.timestamp`, not `block.number`.
- [ ] **Step 2: Run regression tests** — pass.
- [ ] **Step 3: Commit.**

### Task 6.3: End-to-end smoke test combining both fixes + catalog

**Files:**
- Create: `tests/gmx/test_e2e_order_lifecycle.py`

- [ ] **Step 1: Test** simulating: place limit BONK order → REST returns order_key → key cached → 10min passes → fetch_order still returns "open" (not zombie-cancelled because type != market) → keeper "fills" via simulated event → fetch_order returns "closed" with correct settle_amount.
- [ ] **Step 2: Tests pass.**
- [ ] **Step 3: Commit.**

---

## Chunk 7: Deployment Consistency

### Task 7.1: Pin eth_defi to SHA across pyproject.toml + Dockerfile

**Files (in strategy repo `/Users/avik/Work/tradingstrategy/gmx-strategies-livebt/`):**
- Modify: `pyproject.toml` line 21
- Modify: `Dockerfile` line 23

- [ ] **Step 1: Determine final SHA** (after Chunks 1-6 merge to eth_defi master).
- [ ] **Step 2: Pin pyproject.toml** to that SHA via `rev = "..."`:

```toml
web3-ethereum-defi = {git = "https://github.com/tradingstrategy-ai/web3-ethereum-defi.git", rev = "FINAL_SHA_HERE", extras = ["data", "ccxt"]}
```

- [ ] **Step 3: Update Dockerfile** to install from the same SHA:

```dockerfile
RUN pip install --user "git+https://github.com/tradingstrategy-ai/web3-ethereum-defi.git@FINAL_SHA_HERE#egg=web3-ethereum-defi[data,ccxt]"
```

- [ ] **Step 4: Commit in strategy repo** (separate PR).

### Task 7.2: Doc SHA-bump procedure

**Files (strategy repo):**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add section** "Bumping eth_defi" with instructions: find SHA on master, update both `pyproject.toml` and `Dockerfile`, run `poetry lock --no-update`, smoke-test, commit.
- [ ] **Step 2: Commit.**

---

## Chunk 8: CI Fix + Validation

### Task 8.1: Diagnose Python 3.14 test suite failure

**Files:** TBD after diagnosis.

- [ ] **Step 1: Run** `act` (or push to a draft branch) to reproduce locally. Capture failing test names.
- [ ] **Step 2: Triage** — is the failure related to our changes or pre-existing? Document.
- [ ] **Step 3: Fix or skip with rationale** + tracking issue.
- [ ] **Step 4: Commit.**

### Task 8.2: Full test suite green

- [ ] **Step 1:** `pytest tests/gmx -v` — all pass.
- [ ] **Step 2:** `pytest -q tests/` — repo-wide pass (skip non-GMX longs if needed).
- [ ] **Step 3: Lockstep grep** — `git diff master..HEAD -- eth_defi/gmx/ccxt/exchange.py eth_defi/gmx/ccxt/async_support/exchange.py | grep '^+' | sort -u | head` — manually verify symmetry.

### Task 8.3: CHANGELOG + PR body

**Files:**
- Modify: `CHANGELOG.md`
- Update: PR #1008 body via `gh api repos/tradingstrategy-ai/web3-ethereum-defi/pulls/1008 -X PATCH -f body=...`

- [ ] **Step 1: Add CHANGELOG entry** dated 2026-05-14 with all 6 chunks summarised.
- [ ] **Step 2: Update PR body** with Why / Summary / Lessons learnt sections (project rule).
- [ ] **Step 3: No `git push` until user explicitly approves.**

---

## Test Strategy Summary

| Layer | Tests |
|---|---|
| `MarketEntry` | construction, is_synthetic, accepts_collateral |
| Enumeration | REST happy + Reader fallback + symbol normalisation |
| Augmentation | Subsquid happy + 404 + timeout + degradation |
| Cache | disk write, disk read, TTL invalidation, single-flight |
| `pick_market` | liquidity ranking, collateral filter, synthetic override |
| `OrderArgumentParser` | BTC/USDC routes to WBTC-USDC not tBTC-tBTC |
| Async parity | same shape, same behaviour as sync |
| Symbol normalisation | kBONK, kSHIB, kPEPE, kFLOKI, audit |
| Order resolution | REST primary, Subsquid fallback, eager caching |
| Zombie regression | limit orders never auto-cancelled, market orders still aged |
| Cache-miss timestamp | block.timestamp not block.number |
| E2E lifecycle | BONK limit order open → fill → close |

## Acceptance Criteria

1. Live error `Not a valid collateral for selected market!` does not reproduce on BTC/USDC:USDC, ETH/USDC:USDC, or any market with both synthetic + non-synthetic pools.
2. BONK and SHIB fills are detected within 1 polling cycle of on-chain execution. `no order_key stored` warnings drop to zero in 24h of post-deploy logs.
3. Sync + async adapters return identical `MarketEntry` for the same base symbol.
4. All existing GMX tests pass. New tests added per task. PR #1008 CI green.
5. Production Docker image and Poetry env install the same eth_defi SHA.

## Out of scope (do not touch in this plan)

- Funding history (graceful fallback already correct).
- wstETH / AI16Z / WELL / OM whitelist filtering (separate pair-validity work).
- SPX6900 / MKR / KTA historical data download (separate data pipeline work).
- Subsquid endpoint repair (we degrade gracefully — fixing the endpoint is a separate operational task).

## Risks + Mitigations

| Risk | Mitigation |
|---|---|
| Catalog rewrite touches hot path | TDD per task, never skip; full pytest after each commit |
| Subsquid demotion may slow some queries | Acceptable; correctness > speed |
| Force-refresh stampede on TTL expiry | Single-flight `threading.Lock` (sync) / `asyncio.Lock` (async) per chain |
| Sync/async drift | Lockstep rule enforced via grep step + manual review |
| Re-applying reverted fixes may conflict with combined branch | Cherry-pick + manual conflict resolution; existing regression tests confirm correctness |

## Manual Review Gates

Per global CLAUDE rule **"always ask for manual review of the work"**:

1. **Before code edits start:** This plan must be acked by the user.
2. **After Chunk 2 (selection logic):** Pause for review — this is the core behavioural change.
3. **After Chunk 5 (order_key fix):** Pause for review — this resolves Issue B.
4. **Before any `git push`:** User explicit approval required (per global rule).
5. **Before PR #1008 merge:** User explicit approval required.
