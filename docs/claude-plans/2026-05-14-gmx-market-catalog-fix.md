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

### Task 2.1: `pick_market` with three flexible selection strategies

**Files:**
- Modify: `eth_defi/gmx/core/market_catalog.py`
- Test: `tests/gmx/test_market_catalog.py`

**Design rationale (post-user-clarification 2026-05-14):**

The operator wants control over which pool an order routes to. Three strategies,
default is the canonical "USDC-paired" pool because that's where the bot's
intent (USDC-margined perps) lines up most cleanly with the deepest liquidity
and simplest risk profile.

**Collateral handling is OUT of pick_market.**  The GMX router auto-swaps
collateral via the order's ``swap_path`` parameter.  The strict
``_check_if_valid_collateral_for_market`` validation that currently raises
``Not a valid collateral for selected market!`` will be **removed** in Task 2.2.

**Selection strategies:**

| Strategy | When chosen | Example for BTC base |
|---|---|---|
| ``USDC_PAIRED`` (default) | Strategy code wants the canonical pool — USDC stable on one side, base/wrapped-base on the other. Default for everything. | Returns WBTC-USDC pool. |
| ``HIGHEST_LIQUIDITY`` | Operator opts in to TVL-only ranking. Useful when a synthetic or WETH-paired pool is deeper than the USDC-paired one. | Returns whichever BTC pool has highest ``liquidity_usd``. |
| ``EXPLICIT`` | Operator passes ``explicit_market_key`` directly. Used for backtesting against a specific market, operator-forced routing, or research. | Whatever the caller supplies. |

**Fallback:** ``USDC_PAIRED`` falls through to ``HIGHEST_LIQUIDITY`` when no
USDC-paired pool exists for the base (rare; happens for some synthetic-only
listings). Logs INFO when this fires — flagworthy.

- [ ] **Step 1: Define `MarketSelection` enum + `NoMarketFoundError`:**

```python
from enum import Enum

class MarketSelection(str, Enum):
    """Strategy for picking a GMX market when multiple pools exist for a base.

    :cvar USDC_PAIRED: Default. Prefer the standard two-sided pool where USDC
        is one side. WBTC-USDC for BTC, WETH-USDC for ETH, BONK-USDC for BONK,
        etc. Falls back to ``HIGHEST_LIQUIDITY`` if no USDC-paired pool exists
        for the base.
    :cvar HIGHEST_LIQUIDITY: Rank all candidate pools by ``liquidity_usd``
        descending and return the top. Use when synthetic / WETH-paired pools
        are acceptable.
    :cvar EXPLICIT: Caller supplies an exact ``explicit_market_key``. For
        backtesting against a specific market or operator-forced routing.
    """
    USDC_PAIRED = "usdc_paired"
    HIGHEST_LIQUIDITY = "highest_liquidity"
    EXPLICIT = "explicit"


class NoMarketFoundError(LookupError):
    """No catalog entry matched the requested base symbol or explicit key."""
```

- [ ] **Step 2: Failing tests** for selection scenarios:
  - BTC base, default (USDC_PAIRED) → returns WBTC-USDC even if tBTC-tBTC has temporarily-higher TVL
  - BTC base, HIGHEST_LIQUIDITY → returns whichever pool wins on liquidity_usd
  - BTC base, USDC_PAIRED but only synthetic markets exist → falls back to HIGHEST_LIQUIDITY, logs INFO
  - Explicit key set → returns that exact market regardless of `selection`
  - Explicit key that doesn't exist → raises `NoMarketFoundError`
  - Unknown base → raises `NoMarketFoundError`
  - BONK base, USDC_PAIRED → returns BONK-USDC
  - Operator picks HIGHEST_LIQUIDITY for ETH on a day when a deep synthetic pool exists → returns the deeper pool, not WETH-USDC
- [ ] **Step 3: Implement `pick_market`:**

```python
def pick_market(
    self,
    base_symbol: str,
    selection: MarketSelection = MarketSelection.USDC_PAIRED,
    explicit_market_key: str | None = None,
) -> MarketEntry:
    """Pick a GMX V2 market for ``base_symbol``.

    Three operator-selectable strategies:

    +---------------------+--------------------------------------------------+
    | ``USDC_PAIRED``     | Default. Standard pool with USDC on one side.    |
    | (default)           | WBTC-USDC for BTC, WETH-USDC for ETH, BONK-USDC  |
    |                     | for BONK, etc. Falls back to HIGHEST_LIQUIDITY   |
    |                     | when no USDC-paired pool exists for this base.   |
    +---------------------+--------------------------------------------------+
    | ``HIGHEST_LIQUIDITY``| Top by ``liquidity_usd`` regardless of pool type.|
    +---------------------+--------------------------------------------------+
    | ``EXPLICIT``        | Caller passes the exact ``market_key``.          |
    +---------------------+--------------------------------------------------+

    Collateral handling is intentionally outside this function.  The GMX
    router auto-swaps collateral via the order's ``swap_path``, so callers
    pass any collateral and the router converts as needed.

    :param base_symbol: Normalised base token (e.g. ``'BTC'``, ``'BONK'`` —
        never ``'kBONK'``).
    :param selection: Selection strategy.  Defaults to ``USDC_PAIRED``.
    :param explicit_market_key: When supplied, this exact market is returned
        regardless of ``selection`` — explicit always wins.
    :returns: The chosen :class:`MarketEntry`.
    :raises NoMarketFoundError: When no markets match the request.
    """
```

Logic:
1. If `explicit_market_key is not None`: scan catalog for that exact key. Return or raise `NoMarketFoundError`. `selection` is ignored — explicit always wins.
2. Filter catalog: `candidates = [e for e in catalog if e.index_token_symbol == base_symbol]`. Empty → raise `NoMarketFoundError(base_symbol)`.
3. Branch:
   - `USDC_PAIRED`: `usdc = [e for e in candidates if "USDC" in {e.long_token_symbol.upper(), e.short_token_symbol.upper()}]`. If non-empty → `return max(usdc, key=liquidity_usd)`. Else → log INFO "USDC_PAIRED fell back to HIGHEST_LIQUIDITY for {base_symbol}", continue to `HIGHEST_LIQUIDITY` branch.
   - `HIGHEST_LIQUIDITY`: `return max(candidates, key=lambda e: e.liquidity_usd)`.
   - `EXPLICIT` without key: raise `ValueError("EXPLICIT requires explicit_market_key")`.

- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

### Task 2.2: Wire into `OrderArgumentParser` + remove strict collateral validation

**Files:**
- Modify: `eth_defi/gmx/order_argument_parser.py:235-337` (`_handle_missing_market_key`)
- Modify: `eth_defi/gmx/order_argument_parser.py:447-465` (`_check_if_valid_collateral_for_market` — remove or relax)
- Modify: `eth_defi/gmx/order_argument_parser.py:484-518` (`find_all_market_keys_by_index_address` — deprecate)
- Test: `tests/gmx/test_order_argument_parser.py`

- [ ] **Step 1: Failing tests:**
  - Submit BTC/USDC:USDC order → expect WBTC-USDC market_key from catalog, NOT tBTC-tBTC.
  - Submit BTC/USDC:USDC order against tBTC-tBTC market via explicit override → expect no exception, swap_path populated for USDC→tBTC conversion.
  - Confirm `_check_if_valid_collateral_for_market` no longer raises (or is removed entirely).
- [ ] **Step 2: Replace `find_all_market_keys_by_index_address`** call sites with `MarketCatalog.pick_market(base_symbol, explicit_market_key=params.get("market_key"))`. Add `DeprecationWarning` on the legacy function.
- [ ] **Step 3: Remove `_check_if_valid_collateral_for_market`** — the router handles collateral via `swap_path`. If we want a defensive check, keep it as a DEBUG-level log only, never raise.
- [ ] **Step 4: Tests pass; full `pytest tests/gmx/` clean.**
- [ ] **Step 5: Commit.**

### Task 2.3: Ensure `swap_path` is populated when collateral ≠ long/short token

**Files:**
- Modify: `eth_defi/gmx/order/base_order.py` (or wherever the order params are built)
- Test: `tests/gmx/test_base_order.py` (extend)

- [ ] **Step 1: Failing test** — building a USDC-collateral order against tBTC-tBTC market produces a `swap_path` of `[USDC_market_key]` so the router swaps USDC → tBTC at order time.
- [ ] **Step 2: Implement** swap_path inference: if collateral_token ∉ {long_token, short_token} of the target market, look up a swap market that bridges them (e.g. WBTC-USDC pool can swap USDC ↔ WBTC). Use the catalog to find candidate bridge markets, picked by liquidity.
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

## Chunk 5: Order Resolution Rewrite — Root Cause of Issue B

### Investigation findings (2026-05-14)

The "no order_key stored" warnings (10,614 post-fix) are caused by a flawed
storage model, not a resolver bug:

1. ``create_order`` stores ``order_key`` in ``self._orders`` dict
   (``eth_defi/gmx/ccxt/exchange.py:6448``) — **in-memory only**.
2. ``__init__`` initialises ``self._orders = {}`` on every bot start
   (``exchange.py:1554``). **Cache wiped on restart.**
3. ``fetch_order`` requires ``order_key`` to call ``_resolve_order_from_sources``
   (``exchange.py:9038-9042``). With no cached key, it logs the warning and
   returns the order **unchanged** — status stays ``open`` forever.

Compounding factors: Subsquid is Tier A and is broken (818 × 404, 818 × 400);
REST ``/v1/orders?address=wallet`` is Tier B but is never reached because the
function exits before tier dispatch when ``order_key`` is missing.

**Three-pronged fix:**

1. Persist ``order_key`` to disk so it survives restart.
2. Add a wallet-scoped recovery path that resolves orders **without** a
   cached ``order_key`` — enumerate all of the wallet's orders via REST
   ``/v1/orders?address=...`` and match by tx_hash / symbol / timestamp / size.
3. Reorder tiers: REST → Reader → EventEmitter logs. **Subsquid removed
   entirely** per user directive (positions/orders only — Subsquid can stay
   for other tradeAction-style historical queries that have no replacement).

### Task 5.1: Disk-persisted `order_key` cache

**Files:**
- Create: `eth_defi/gmx/ccxt/order_key_cache.py`
- Modify: `eth_defi/gmx/ccxt/exchange.py` (replace `self._orders` writes)
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py` (mirror)
- Test: `tests/gmx/ccxt/test_order_key_cache.py`

**Cache file:** `~/.cache/eth_defi/gmx/order_keys_{chain_id}_{wallet_lower}.json`.
Schema: `{tx_hash: {order_key, symbol, side, timestamp_ms, amount, price, market_key}}`.

- [ ] **Step 1: Failing tests**:
  - Write order, restart process, read back → same `order_key` returned.
  - Concurrent write from two processes → file lock prevents corruption.
  - Disk-write failure (read-only FS / permission error) → falls back to in-memory only, logs WARNING, never raises.
  - Stale entries (>30 days, settled orders) → pruned on load.
- [ ] **Step 2: Implement** `OrderKeyCache` class with:
  - JSON file on disk + bounded in-memory dict
  - `filelock` for cross-process safety (or `fcntl` if filelock not already a dep)
  - Atomic write (write to `.tmp`, rename) to prevent partial corruption
  - Eager flush on every `put()` (durability > throughput for this volume)
  - Prune on load: drop entries with `timestamp_ms` older than 30 days
- [ ] **Step 3: Replace** `self._orders[tx_hash.hex()] = order` writes in `create_order` with `self._order_key_cache.put(tx_hash, order_info)`. Replace lookups in `fetch_order` likewise.
- [ ] **Step 4: Mirror in async.**
- [ ] **Step 5: Tests pass; bot restart no longer drops order_keys.**
- [ ] **Step 6: Commit.**

### Task 5.2: Wallet-scoped recovery — find orders WITHOUT a cached `order_key`

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py` (`fetch_order` + new `_recover_order_from_wallet`)
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py` (mirror)
- Test: `tests/gmx/ccxt/test_wallet_recovery.py`

**Recovery flow when `order_key` is missing for a given trade:**

1. Query `GMXAPI.get_orders(address=wallet)` — returns all live orders for the wallet from REST `/v1/orders?address=...`.
2. Match each returned order against the freqtrade trade by (a) market_key + side + size with 0.5% tolerance, OR (b) tx_hash if available, OR (c) original price within 0.5%.
3. If a match is found → backfill `order_key` into disk cache, proceed with normal status-check flow.
4. If REST fails / returns empty → fall through to on-chain `Reader.getAccountOrders(...)` enumeration.
5. If both fail → log structured WARNING and return order unchanged (current behaviour, but logged with full context, not just "no order_key").

- [ ] **Step 1: Failing tests**:
  - Cache empty + REST returns matching order → recovery succeeds, key backfilled.
  - Cache empty + REST empty + Reader returns matching order → recovery via on-chain.
  - Cache empty + REST returns multiple ambiguous matches → log WARNING, pick highest-confidence match (tx_hash > size+side > price proximity).
  - Cache empty + nothing matches → log structured WARNING with wallet, chain_id, trade snapshot, attempted tiers.
- [ ] **Step 2: Implement `_recover_order_from_wallet(trade)`** as the new fallback path inside `fetch_order` when `order_key` is None.
- [ ] **Step 3: Mirror in async** (uses `aiohttp` for REST + `AsyncWeb3` for Reader call).
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

### Task 5.3: Reorder resolver tiers — Subsquid demoted to last-resort

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py` (`_resolve_order_from_sources`, currently line 8586)
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py` (mirror)
- Modify: `eth_defi/gmx/core/open_positions.py` (positions tier order — Subsquid Tier 2 → Tier 4)
- Test: `tests/gmx/ccxt/test_order_resolution.py`

**Scope clarification (user directive 2026-05-14):**

Subsquid is only flaky for **positions** and **order confirmation/resolution**.
Other Subsquid uses (historical ``tradeActions``, per-account P&L, etc.) work
fine and stay untouched. This task only reorders the tiers used by the two
flaky paths and keeps Subsquid as a last-resort backup so we never go
worse-than-current.

**New tier order for order resolution:**

| Tier | Source | Endpoint / Call |
|---|---|---|
| A | GMX REST v2 | `get_orders(address)` → `/v1/orders?address=...` |
| B | On-chain Reader | `Reader.getAccountOrders(datastore, account, 0, 1000)` |
| C | EventEmitter logs | Chunked RPC log scan (existing, slow but exhaustive) |
| D | Subsquid (last resort) | `tradeActions` query — only fires when A–C all miss |

**New tier order for positions** (mirror in ``open_positions.py``):

| Tier | Source |
|---|---|
| 1 | REST `/v1/positions?address=...` (already correct) |
| 2 | Reader `getAccountPositions(...)` (promote from Tier 3) |
| 3 | EventEmitter logs (if available for positions) |
| 4 | Subsquid (demote from Tier 2 — last resort) |

**Output normalisation contract:**

REST, Reader, EventEmitter, and Subsquid return **different shapes**.  Add an
``_OrderRecord`` / ``_PositionRecord`` dataclass per domain plus a normaliser
function per tier so callers always see the same shape regardless of source.

Per-tier shape differences to handle:

| Source | Shape | Notes |
|---|---|---|
| REST `/v1/orders` | JSON object per order: `{key, orderType, sizeDeltaUsd, triggerPrice, ...}` | snake/camel mixed, decimal as string |
| `Reader.getAccountOrders` | `list[tuple]` — index access; tuple position varies by Reader ABI version | tuple[0] is bytes32 order_key |
| EventEmitter logs | `LogEntry` event with topics + data bytes | needs ABI decoding |
| Subsquid `tradeActions` | GraphQL JSON with snake_case keys, decimals as strings | nested ``transaction`` object |

- [ ] **Step 1: Failing tests** for each tier:
  - REST happy path → normalised `_OrderRecord` with all fields populated.
  - Reader happy path → normalised `_OrderRecord` matching REST output.
  - EventEmitter happy path → normalised `_OrderRecord` matching REST output.
  - Subsquid happy path → normalised `_OrderRecord` matching REST output.
  - REST returns 404, Reader works → Reader output used, no exception.
  - REST returns 404, Reader returns empty, EventEmitter works → EventEmitter output used.
  - All higher tiers fail, Subsquid works → Subsquid output used (last-resort).
  - All tiers fail → structured WARNING logged, returns None.
- [ ] **Step 2: Implement `_OrderRecord` dataclass + four normaliser functions** (`_normalise_rest_order`, `_normalise_reader_order`, `_normalise_event_order`, `_normalise_subsquid_order`).
- [ ] **Step 3: Reorder branches** in `_resolve_order_from_sources`: REST first, Reader second, EventEmitter third, Subsquid last. Log at each tier transition.
- [ ] **Step 4: Mirror in async.**
- [ ] **Step 5: Same treatment for `open_positions.py`** — promote Reader to Tier 2, demote Subsquid to Tier 4. Add `_PositionRecord` normaliser.
- [ ] **Step 6: Audit other Subsquid call sites** — list them, document each as "flaky for this case → migrate" or "works → leave alone". Expected outcome: only positions + order-confirmation are migrated; the rest stay.
- [ ] **Step 7: Tests pass.**
- [ ] **Step 8: Commit.**

### Task 5.4: On-chain position reconciliation inside `Gmx.fetch_order`

**Files:**
- Modify: `eth_defi/gmx/freqtrade/gmx_exchange.py` (`Gmx.fetch_order`, line 569 area)
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py` (mirror, if an async `fetch_order` wrapper exists)
- Test: `tests/gmx/freqtrade/test_fetch_order_position_reconcile.py`

**Replaces the original "watchdog" design (2026-05-14):**

Investigation confirmed that freqtrade's main loop uses ``fetch_order(order_id)``
for fill detection — never ``fetch_positions``.  ``Wallets.update()`` does call
``fetch_positions`` but is rate-limited to once per hour (too slow to catch
stuck fills).  A separate periodic watchdog adds threading/asyncio complexity
the simpler design avoids.

Instead, fold the reconciliation inline into ``Gmx.fetch_order``:

1. Call ``super().fetch_order()`` as today.
2. If the result is a non-market order still ``"open"``, cross-check by
   calling ``self._api.fetch_positions([pair])`` (already on-chain Reader
   truth via ``GetOpenPositions``).
3. Match returned positions against the order by ``(symbol, side, size ±0.5%)``.
4. If a matching position exists → the limit/SL/TP order has filled; return
   the order with ``status="closed"``, ``filled=order["amount"]``, and
   ``remaining=0.0``.  Carry the position data into ``order["info"]`` for
   downstream visibility.
5. If no match → return the order unchanged (still ``"open"`` is correct).

**Why this beats a separate watchdog:**

- Triggers exactly when freqtrade asks for fill state (no extra loop).
- Zero overhead when the existing resolver works correctly.
- Synchronous, no threading/asyncio scheduling complexity.
- Reuses ``fetch_positions`` which is already on-chain-truth (Reader-backed).
- Sync + async path can be mirrored identically.

- [ ] **Step 1: Failing test** in `tests/gmx/freqtrade/test_fetch_order_position_reconcile.py`:
  - Mock ``Gmx._api.fetch_positions`` to return a matching position.
  - Mock ``super().fetch_order`` to return an ``open`` limit order.
  - Assert ``Gmx.fetch_order`` returns ``status="closed"`` with ``filled=amount``.
  - Negative case: no matching position → status stays ``open``.
  - Market order case: skipped (zombie path owns it).
- [ ] **Step 2: Implement** the reconciliation block at line 569 of
  ``gmx_exchange.py``, right after ``super().fetch_order()``.  Use a helper
  ``_reconcile_via_positions(order, pair) -> dict | None`` returning the
  patched order or ``None`` when no match.
- [ ] **Step 3: Mirror in async** — if there's an async ``Gmx`` equivalent;
  otherwise the sync path is sufficient since freqtrade calls sync
  ``fetch_order``.
- [ ] **Step 4: Add structured log** when reconciliation flips a stuck order:
  ``logger.warning("fetch_order: reconciled %s from open→closed via on-chain
  position match (cached order_key was %s)", order_id, key)``.
- [ ] **Step 5: Tests pass.**
- [ ] **Step 6: Commit.**

### Task 5.5: Structured logging for resolver outcomes

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py`
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py`

- [ ] **Step 1: Failing test** — when all tiers miss, log includes pair, chain_id, order_id, tried tiers, last error per tier, wallet, trade snapshot.
- [ ] **Step 2: Implement** with `logger.warning("order resolution miss", extra={...})`. Avoid f-strings in log calls (project rule).
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
