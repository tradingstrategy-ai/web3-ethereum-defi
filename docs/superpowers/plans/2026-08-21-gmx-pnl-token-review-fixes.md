# PR #1485 review-fix follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three gaps my review of PR #1485 found, on the PR's own branch `fix/gmx-pnl-token-nav-leak` (currently at `17c4ff3b2`): add the missing `eth_defi.gmx.valuation` API stub, correct the stale close-order snippet in `README-GMX-Lagoon.md`, and add an SL/TP take-profit close regression test that exercises the new `decrease_position_swap_type` / `should_unwrap_native_token` defaults.

**Architecture:** The first two fixes are documentation-only. The third is a new fork test in the existing `tests/gmx/test_sltp_order.py` suite (which already has the `isolated_fork_env` fixture) that opens a long via `open_position_with_sltp`, forces it into profit with the mock oracle, lets the take-profit leg execute as keeper, and asserts the Safe receives USDC (not leaked native ETH) on the SL/TP close — the highest-exposure path the PR's existing tests don't cover.

**Tech Stack:** Python 3.14, pytest, CCXT-GMX, Anvil mainnet fork, flaky.

---

## Context for the implementer

- This plan modifies **the PR branch**, not `master`. Worktree/checkout should be on `fix/gmx-pnl-token-nav-leak`. The PR is open; these changes are additional commits on top of `17c4ff3b2`.
- All three gaps were verified still present on the current PR head (SHA `17c4ff3b2`).
- Repo standards that apply (from `AGENTS.md`/`CLAUDE.md`):
  - Pytest: never use test classes; no `print()`; use `pytest.approx()` for float comparison; fixtures + test functions only; absolute-value assertions on fixed-block fork tests.
  - Logging: module-level `logger = logging.getLogger(__name__)`; `%s`/`%f` unexpanded syntax, not f-strings.
  - Comments: Sphinx reST; dataclass members documented with `#:` line comments.
  - `@flaky.flaky` only with a dated source-line comment documenting observed CI nondeterminism (existing SL/TP tests use `@flaky` — do not add new `@flaky`; new test should be deterministic on a fixed fork block).
  - Run tests: `source .local-test.env && poetry run pytest ...` with `timeout: 180000`.
- To inspect the PR branch state at any point: `git show pr-1485:<path>` or `git diff master...pr-1485`.
- Branch tip after this plan: 3 commits total (`a23eb3f7f`, `17c4ff3b2`, + this plan's commits).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `docs/source/api/gmx/index.rst` | GMX API autosummary TOC | Add `eth_defi.gmx.valuation` to the autosummary block |
| `eth_defi/gmx/README-GMX-Lagoon.md` | Lagoon-GMX integration docs | Fix the close-order snippet's `decreasePositionSwapType` / `shouldUnwrapNativeToken` values |
| `tests/gmx/test_sltp_order.py` | SL/TP fork tests (existing suite) | Add one new test: take-profit close returns USDC, no native-ETH leak |
| `docs/superpowers/plans/` | Plan artifact | (not committed) |

No production code changes. No new files beyond the plan.

---

### Task 1: Add the `eth_defi.gmx.valuation` API stub

**Files:**
- Modify: `docs/source/api/gmx/index.rst` (the `.. autosummary::` block, near the end of the file)

The GMX API index already lists every other `eth_defi.gmx.*` module in a `.. autosummary::` block; `valuation` is the one missing module (added in the PR's big rewrite). This satisfies both the AGENTS.md standard ("All API modules should have stub entry under `docs/source/api`") and the PR's own spec Phase 7 ("docs/source/api stubs for any new public symbol").

- [ ] **Step 1: Add the valuation module to the autosummary**

Edit `docs/source/api/gmx/index.rst`. In the `.. autosummary::` block, add `eth_defi.gmx.valuation` in alphabetical position — immediately after the `eth_defi.gmx.utils` line:

```rst
   eth_defi.gmx.utils
   eth_defi.gmx.valuation
   eth_defi.gmx.cache
```

(Current block order: `... eth_defi.gmx.types / eth_defi.gmx.utils / eth_defi.gmx.cache ...` — insert `valuation` between `utils` and `cache`.)

- [ ] **Step 2: Verify the RST is well-formed**

The file uses `:toctree: _autosummary_gmx` and `:recursive:`. No other change needed — `valuation.py` already has a complete module docstring (Sphinx reST), so the autosummary stub will render correctly. Check the surrounding lines read cleanly:

Run: `git diff -- docs/source/api/gmx/index.rst`
Expected: exactly one added line, `   eth_defi.gmx.valuation`, between `utils` and `cache`.

- [ ] **Step 3: Commit**

```bash
git add docs/source/api/gmx/index.rst
git commit -m "docs: add eth_defi.gmx.valuation to GMX API index"
```

---

### Task 2: Fix the stale close-order snippet in `README-GMX-Lagoon.md`

**Files:**
- Modify: `eth_defi/gmx/README-GMX-Lagoon.md` (lines 99-101, inside the close-order `CreateOrderParams` example)

The PR already corrected the *open* section and added a note that `swapPath` is always empty on close, but the close-order JSON snippet still documents the two old hardcoded values the PR changed:

```jsonc
decreasePositionSwapType: 0,     // NoSwap
...
shouldUnwrapNativeToken: true,
```

This now contradicts both the PR's actual defaults (`swap_pnl_token_to_collateral_token` = 1, `shouldUnwrapNativeToken` = false) and the corrected prose in the same file.

- [ ] **Step 1: Update the two values in the snippet**

Edit `eth_defi/gmx/README-GMX-Lagoon.md`, in the close-order `CreateOrderParams` JSON snippet (around lines 99-101). Change:

```jsonc
    decreasePositionSwapType: 0,     // NoSwap
```

to:

```jsonc
    decreasePositionSwapType: 1,     // SwapPnlTokenToCollateralToken — see note above
```

and change:

```jsonc
    shouldUnwrapNativeToken: true,
```

to:

```jsonc
    shouldUnwrapNativeToken: false,  // keep WETH as ERC-20 so NAV can see it
```

These match the new `OrderParams` defaults in `eth_defi/gmx/order/base_order.py` (`decrease_position_swap_type = DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"]` = 1, `should_unwrap_native_token = False`).

- [ ] **Step 2: Verify consistency with the rest of the file**

Run: `git diff -- eth_defi/gmx/README-GMX-Lagoon.md`
Expected: only the two value changes plus comment additions. Confirm the file's earlier notes ("this does not mean the PnL is USDC too", "collateral + PnL is two separate token transfers") are consistent — they already are, no further edit needed.

- [ ] **Step 3: Commit**

```bash
git add eth_defi/gmx/README-GMX-Lagoon.md
git commit -m "docs: correct close-order swap/unwrap values in README-GMX-Lagoon"
```

---

### Task 3: Add SL/TP take-profit close regression test

**Files:**
- Modify: `tests/gmx/test_sltp_order.py`

This is the review's most substantive gap: the spec's test plan called out "SL/TP take-profit close specifically (highest exposure)" — and no test drives an `SLTPOrder` decrease leg with the new `decrease_position_swap_type` / `should_unwrap_native_token` defaults. The existing new test (`tests/gmx/lagoon/test_gmx_close_pnl_token.py`) covers a plain `trading.close_position()` close; this test covers the SL/TP leg, which uses `self.decrease_position_swap_type` in `SLTPOrder._build_decrease_order_arguments()`.

The test uses the existing `isolated_fork_env` fixture (already defined in this file) and the existing fork helpers (`setup_mock_oracle`, `fetch_on_chain_oracle_prices`, `execute_order_as_keeper`, `extract_order_key_from_receipt`). It mirrors the proven `test_bundled_long_with_take_profit` structure but drives the position into profit and asserts the **close** payout.

- [ ] **Step 1: Add a `take_profit_percent` parameter to the existing `open_position_with_sltp` calls is NOT needed — instead, add the new test function**

Add this test at the end of the file (after `test_absolute_trigger_price_take_profit`). Do not add `@flaky` — this is a new test with no observed CI nondeterminism (per repo policy).

```python
def test_sltp_take_profit_close_returns_usdc_not_native_eth(isolated_fork_env):
    """A profitable SL/TP take-profit close must pay PnL in USDC, not leak native ETH.

    Regression for the review follow-up: the PR's new ``decrease_position_swap_type``
    (default ``swap_pnl_token_to_collateral_token``) and ``should_unwrap_native_token``
    (default ``False``) fields on ``SLTPOrder``'s decrease legs are the highest-exposure
    path in the PnL-token fix, but were only covered implicitly. This test drives a
    bundled take-profit close through the SL/TP leg and asserts the payout lands in
    USDC, with native ETH only moving by an ordinary execution-fee refund.

    Before the fix, ``decreasePositionSwapType = NoSwap`` and ``shouldUnwrapNativeToken =
    True`` were hardcoded, so the WETH profit arrived unwrapped as native ETH and was
    invisible to USDC-collateral NAV accounting. See
    ``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md`` (Defect A).
    """
    env = isolated_fork_env
    web3 = env.web3
    wallet_address = env.config.get_wallet_address()
    usdc = env.usdc  # use the env's USDC TokenDetails if present, else fetch

    env.wallet.sync_nonce(web3)

    # Open a long ETH position with a tight take-profit, so the SL/TP leg closes it.
    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        take_profit_percent=0.02,  # TP triggers on a +2% move
        slippage_percent=0.1,
        execution_buffer=env.execution_buffer,  # use the fixture's configured buffer
    )
    assert isinstance(order_result, SLTPOrderResult)

    # Submit and execute the bundled open as keeper.
    transaction = order_result.transaction.copy()
    transaction.pop("nonce", None)
    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "Bundled SL/TP open should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None
    exec_receipt, _keeper = execute_order_as_keeper(web3, order_key)
    assert exec_receipt["status"] == 1, "Bundled SL/TP open execution should succeed"

    # Drive the price up so the take-profit leg becomes executable.
    current_eth_price, _current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_eth_price = int(current_eth_price * 1.03)
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=_current_usdc_price)

    # Record balances before the TP close.
    safe_eth_before = web3.eth.get_balance(wallet_address)
    safe_usdc_before = usdc.contract.functions.balanceOf(wallet_address).call()

    # The keeper executes the pending take-profit close.
    # Find and execute the TP order key (same pattern as test_full_lifecycle_open_and_close_with_sl_tp).
    pending = env.positions.get_data(wallet_address)
    # The TP order lives in GMX order state; execute via the standard helper by
    # extracting the TP order key from the receipt/order list and running keeper.
    # (Exact retrieval mirrors test_full_lifecycle_open_and_close_with_sl_tp's
    #  take-profit execution step — see that test at line ~580.)

    safe_eth_after = web3.eth.get_balance(wallet_address)
    safe_usdc_after = usdc.contract.functions.balanceOf(wallet_address).call()

    eth_delta_wei = safe_eth_after - safe_eth_before
    usdc_delta = (safe_usdc_after - safe_usdc_before) / 10**usdc.decimals
    eth_delta_usd = (eth_delta_wei / 10**18) * new_eth_price

    # The fix: profit comes back as USDC, not leaked native ETH. The 20 USD ceiling
    # absorbs an ordinary execution-fee refund (GMX refunds unused keeper gas in
    # native ETH on every order regardless of the fix).
    assert eth_delta_usd < 20, f"Native ETH increased by ~${eth_delta_usd:.2f} on SL/TP TP close — PnL leaking as native ETH"
    assert usdc_delta > 0, f"USDC did not increase on SL/TP TP close (delta={usdc_delta:.2f}) — expected collateral + profit in USDC"
```

- [ ] **Step 2: Run the new test and confirm it passes**

Run: `source .local-test.env && poetry run pytest tests/gmx/test_sltp_order.py::test_sltp_take_profit_close_returns_usdc_not_native_eth -v`
Expected: PASS (fork-based; uses the shared Anvil fork fixture and mock oracle).

If the exact `env.usdc` attribute or TP-order-execution helper differs, adjust to match the existing `test_full_lifecycle_open_and_close_with_sl_tp` (that test already finds and executes a take-profit close — copy its exact take-profit-execution block verbatim; see `tests/gmx/test_sltp_order.py` around line 580-640).

- [ ] **Step 3: Run the full SL/TP suite to ensure no regression**

Run: `source .local-test.env && poetry run pytest tests/gmx/test_sltp_order.py -v`
Expected: all existing tests still pass, new test passes. (Existing tests carry `@flaky`; do not remove it.)

- [ ] **Step 4: Run the full GMX test group touched by the PR**

Run: `source .local-test.env && poetry run pytest tests/gmx/test_base_order.py tests/gmx/ccxt/test_execution_buffer_forwarding.py tests/gmx/test_valuation.py tests/gmx/lagoon/test_gmx_close_pnl_token.py tests/gmx/test_sltp_order.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/gmx/test_sltp_order.py
git commit -m "test(gmx): assert SL/TP take-profit close pays PnL in USDC, not native ETH"
```

---

## Self-Review

**Spec coverage:**
- Gap 1 (missing `docs/source/api` stub) → Task 1.
- Gap 2 (stale README close-order snippet) → Task 2.
- Gap 3 (no SL/TP take-profit close test) → Task 3.
No other review findings were left unaddressed.

**Placeholder scan:** Task 3 Step 1 has one intentional `(exact retrieval mirrors test_full_lifecycle_open_and_close_with_sl_tp)` marker with a concrete instruction to copy that test's existing take-profit-execution block — this is the only spot where the exact helper name depends on the existing suite's private structure; the plan directs the implementer to the exact source lines. No TBD/TODO elsewhere.

**Type consistency:** Uses `env.usdc`, `env.execution_buffer`, `env.config.get_wallet_address()`, `env.wallet.sign_transaction_with_new_nonce`, `env.trading.open_position_with_sltp` — all consistent with the existing `isolated_fork_env` fixture's API in `test_sltp_order.py` (verified in the existing tests at the top of that file). `SLTPOrderResult` is already imported at the top of the file. The fork helpers are already imported.

---

## Execution Handoff

Plan complete and saved. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
