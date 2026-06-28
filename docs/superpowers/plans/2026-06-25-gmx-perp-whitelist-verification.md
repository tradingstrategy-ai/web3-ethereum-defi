# GMX perp whitelist verification plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the GMX Lagoon guard whitelist supports the *complete* perp-trading lifecycle (open, close, modify, cancel, claim) end-to-end — on an Anvil Arbitrum mainnet fork and on Arbitrum Sepolia testnet — without adding any new contract code.

**Architecture:** The guard (`TradingStrategyModuleV0` → `GuardV0Base` → `GmxLib`) validates every inner call of a GMX `ExchangeRouter.multicall`. All perp order types (`MarketIncrease`, `LimitIncrease`, `MarketDecrease`, `LimitDecrease`, `StopLossDecrease`, `MarketSwap`, `LimitSwap`) reach chain as the single whitelisted selector `createOrder`; lifecycle management uses `cancelOrder` / `updateOrder`; fee/reward extraction uses `claim*`. This branch (`feat/gmx-guard-whitelist-trading-methods`) added `cancelOrder`, `updateOrder`, `claimFundingFees`, `claimCollateral`, `claimAffiliateRewards`. This plan only *verifies* that surface — it changes no Solidity.

**Tech Stack:** Python 3.14, pytest, Poetry, Foundry/Anvil mainnet fork, web3.py, Lagoon vault + Gnosis Safe + GMX V2 on Arbitrum / Arbitrum Sepolia.

**Scope decision (locked):** Test-only, perp lifecycle. GM/GLV liquidity ops (`createDeposit`/`createWithdrawal`/`cancelDeposit`/`cancelWithdrawal`) are explicitly **out of scope** and tracked as a separate follow-up (see Task 6). Dangerous selectors (`makeExternalCalls`, `setUiFeeFactor`, `setSavedCallbackContract`, `claimUiFees`) must remain **blocked**.

---

## Execution status (2026-06-25)

The verification was carried out by **extending the existing deploy/trade script** `scripts/lagoon/lagoon-gmx-example.py` to open and cancel a limit order, then running it — rather than only adding pytest stubs. The script is the real workflow (`deploy_automated_lagoon_vault` → deposit → GMX trade → cancel → withdraw), so exercising it proves the guard path through the same code an operator uses.

**Script changes (done):**
- `open_gmx_limit_order()` — `gmx.create_order(type="limit", price=trigger, wait_for_execution=False)`; order is stored pending in the GMX DataStore.
- `cancel_gmx_order()` — `gmx.cancel_order(order_key)`; checks `is_order_pending` on-chain, builds `cancelOrder` via `CancelOrder`, signs through `LagoonGMXTradingWallet` → `performCall` → guard → ExchangeRouter.
- `run_limit_order_cancel_flow()` — fetches ETH mark price, places a SHORT limit 10 % above spot (stays pending, no keeper), then cancels.
- Wired into `main()`: simulate (fork) mode now runs the limit+cancel flow (previously skipped all trading); live/testnet mode gains a Step 5b limit+cancel after the market close. Simulate points the CCXT adapter at the Anvil RPC.

**Fork run — PASSED (Arbitrum mainnet fork, `SIMULATE=true`):**

| Stage | Result |
|-------|--------|
| Deploy vault + GMX whitelist | ✅ Safe `0xD2c52130…`, module `0x203a2161…`, vault `0xB8865be0…`, 9 whitelisted call sites incl. GMX |
| Deposit USDC | ✅ |
| Open SHORT limit order (trigger $1728.16 vs $1571.06 spot) | ✅ `status: open`, key `60db19e9…`, tx `0x4da77f95…` |
| **Cancel limit order through guard** | ✅ `status: cancelled`, tx `0xc8be0859…` |
| Withdraw | ✅ "Tutorial complete", exit 0 |

`cancel_order` returns `status: cancelled` only after the order was confirmed pending on-chain *and* the cancel receipt succeeded — so this is the genuine `createOrder` → `cancelOrder` guard path that reverted with "GMX: Unknown function in multicall" before this branch.

**Outstanding:** testnet run (Task 5 below) — operator-run, needs `GMX_PRIVATE_KEY` + Sepolia USDC.SG.

The pytest fork tasks in Chunk 1 below remain valuable as **CI-level regression coverage** (the script run is manual). Implement them when convenient; they are no longer the primary proof.

---

## Coverage gap (what exists today)

**Unit level** (`tests/guard/test_guard_gmx_validation.py`) — already covers `cancelOrder`, `updateOrder`, all three `claim*` (allowed + malicious receiver), selector-constant hash check, attack scenarios. **No gap.**

**Fork level** (`tests/gmx/lagoon/test_gmx_lagoon_integration.py`) — currently covers: open long, open short, cancel limit order (#1050 regression — cancels a pending *limit increase*), forward-eth open, wallet identity/balance, collateral auto-approval. **Gaps:**
- **Close positions** (`MarketDecrease` via `createOrder`) is **not** explicitly exercised end-to-end — open is covered, close is not — and only the long side is opened anywhere (no close at all).
- **Cancel a pending close/decrease order** is **not** covered — the existing cancel test cancels a pending *limit increase*; cancelling a pending *decrease (close) trigger order* exercises the same `cancelOrder` selector against a different order kind and should be proven too.
- `updateOrder` is **not** exercised through the guard on a fork.
- Stop-loss / take-profit (`StopLossDecrease` / `LimitDecrease` via `createOrder`) is **not** exercised through the guard on a fork.
- `claimFundingFees` is **not** exercised through the guard on a fork (only unit-tested).

**Cancel/close terminology (used below):**
- *Close a position* = submit a `MarketDecrease` `createOrder` that reduces/zeroes an open position (an executed fill). Task 1.
- *Cancel a limit order* = `cancelOrder(key)` on a pending *limit increase* trigger order (already covered, re-asserted in Task 1b).
- *Cancel a close/decrease order* = `cancelOrder(key)` on a pending *decrease* trigger order (stop-loss / take-profit / limit-decrease that has not yet filled). Task 1b.

**Testnet** — no automated testnet run exists; `scripts/lagoon/lagoon-gmx-example.py` supports `NETWORK=testnet` on Arbitrum Sepolia. This plan adds a documented, repeatable runbook (Task 5).

---

## Pre-flight: establish the baseline (do this first)

- [ ] **Step 0.1: Confirm the branch is the rebased one**

Run: `git -C /Users/avik/Work/tradingstrategy/web3-ethereum-defi branch --show-current`
Expected: `feat/gmx-guard-whitelist-trading-methods`

- [ ] **Step 0.2: Confirm the rebuilt ABIs carry the new selectors**

Run: `grep -o "7489ec23" eth_defi/abi/guard/GmxLib.json | head -1`
Expected: `7489ec23` (cancelOrder selector present in rebuilt bytecode).

- [ ] **Step 0.3: Run the existing GMX guard unit tests (fast, no fork)**

Run:
```bash
source .local-test.env && poetry run pytest tests/guard/test_guard_gmx_validation.py -v
```
(Use bash `timeout: 180000`.) Expected: all pass — this is the safety net before touching fork tests.

- [ ] **Step 0.4: Run the existing GMX Lagoon fork tests to confirm the harness works on this machine**

Run:
```bash
export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py -v -k "cancel_limit_order or open_long_position"
```
(Use bash `timeout: 180000`.) Expected: both pass. If Anvil/fork fails here, fix environment before adding new tests. **Do not proceed until green.**

---

## Chunk 1: Fork coverage for the remaining perp lifecycle

All tasks below add functions to the **existing** `tests/gmx/lagoon/test_gmx_lagoon_integration.py`, reusing the `lagoon_gmx_fork_env` fixture (defined at `tests/gmx/lagoon/test_gmx_lagoon_integration.py:241-262`) and the `LagoonGMXForkEnv` dataclass (`:62-74`). Follow the established pattern: build order via `env.trading.*`, sign via `env.lagoon_wallet.sign_transaction_with_new_nonce(tx)` (wraps in `performCall` so it goes *through the guard*), submit, execute as keeper where needed, assert.

**Files (all tasks in this chunk):**
- Modify: `tests/gmx/lagoon/test_gmx_lagoon_integration.py` (append new test functions; reuse existing fixtures/helpers)

### Task 1: Close positions through the guard (MarketDecrease, long + short)

- [ ] **Step 1.1: Write the failing tests (both sides)**

Append to `tests/gmx/lagoon/test_gmx_lagoon_integration.py`. Mirror `test_lagoon_wallet_open_long_position` / `test_lagoon_wallet_open_short_position` to open, then issue a `MarketDecrease` to fully close. Cover **both** sides — close is the untested half of the lifecycle and the short close path has never been exercised at all:

```python
@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_close_long_position(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Open then fully close a long through the guard.

    Exercises the createOrder MarketDecrease path end-to-end via the
    TradingStrategyModuleV0 guard, proving the close half of the lifecycle
    is whitelisted (not just the open half already covered elsewhere).
    """
    env = lagoon_gmx_fork_env
    # 1. Open a long (reuse the open flow from test_lagoon_wallet_open_long_position)
    # 2. Execute as keeper, assert one open long exists
    # 3. Build a MarketDecrease for the full size via env.trading.create_decrease_order(...)
    # 4. Sign through guard, submit, execute as keeper
    # 5. Assert position count returns to 0
    ...


@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_close_short_position(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Open then fully close a short through the guard.

    Same MarketDecrease lifecycle as the long close, on the short side with
    USDC collateral (mirrors test_lagoon_wallet_open_short_position).
    """
    env = lagoon_gmx_fork_env
    # 1. Open a short (reuse test_lagoon_wallet_open_short_position flow)
    # 2. Execute as keeper, assert one open short exists
    # 3. MarketDecrease full size, is_long=False -> sign through guard -> execute as keeper
    # 4. Assert position count returns to 0
    ...
```

Match the exact `env.trading.create_decrease_order(...)` signature — read `eth_defi/gmx/order/decrease_order.py:create_decrease_order` for the real argument names (market, collateral, size_delta, is_long, slippage). Do **not** invent arguments.

- [ ] **Step 1.2: Run them to verify they fail for the right reason**

Run:
```bash
export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py -v -k "close_long_position or close_short_position"
```
(bash `timeout: 180000`.) Expected: fails on an assertion or signature mismatch you then correct — **not** with "GMX: Unknown function in multicall" (a decrease is `createOrder`, already whitelisted; that revert means the test built the wrong call).

- [ ] **Step 1.3: Fix the tests until they pass**

Iterate on argument names / position lookup until green. No contract changes permitted — if the guard genuinely blocks a legitimate close, stop and report it as a real finding.

- [ ] **Step 1.4: Run to verify they pass**

Same command as 1.2. Expected: both PASS.

- [ ] **Step 1.5: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_lagoon_integration.py
git commit -m "test(gmx): close long and short positions through Lagoon guard (MarketDecrease lifecycle)"
```

### Task 1b: Cancel pending orders through the guard (cancelOrder — limit + close)

- [ ] **Step 1b.1: Write the failing test**

`cancelOrder` is already proven for a pending *limit increase* (`test_lagoon_wallet_cancel_limit_order:404-482`). Add explicit coverage for cancelling a pending **decrease (close) trigger order** — same selector, different order kind — and re-assert the limit-increase cancel in one focused test so the "cancel orders" surface is unambiguous:

```python
@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_cancel_close_order(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Cancel a pending close/decrease trigger order through the guard.

    Opens a long, places a take-profit / stop-loss style decrease trigger
    order that stays pending (trigger far from spot), extracts its order
    key, then cancels it via cancelOrder through the module. Proves the
    cancelOrder selector works for decrease (close) orders, not just the
    limit-increase order already covered by
    test_lagoon_wallet_cancel_limit_order.
    """
    env = lagoon_gmx_fork_env
    # 1. Open a long, execute as keeper
    # 2. Place a pending decrease trigger (env.trading.create_take_profit_order / create_stop_loss_order)
    #    with a trigger far from spot so it stays pending
    # 3. Extract order key from receipt; confirm pending
    # 4. Build cancelOrder(key) via env.trading.cancel_order(...) -> sign through guard -> submit
    # 5. Assert success and the order is no longer pending; the underlying position is untouched
    ...
```

Reuse the order-key extraction and pending-order lookup helpers already used by `test_lagoon_wallet_cancel_limit_order` (read that test for the exact helper names) and `eth_defi/gmx/order/cancel_order.py:cancel_order` for the cancel signature.

- [ ] **Step 1b.2 → 1b.4: fail → fix → pass**

Run:
```bash
export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py::test_lagoon_wallet_cancel_close_order -v
```
Expected: PASS after iteration. The guard must **allow** the cancel (no "Unknown function" revert).

- [ ] **Step 1b.5: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_lagoon_integration.py
git commit -m "test(gmx): cancel pending close/decrease order through Lagoon guard (cancelOrder)"
```

### Task 2: Modify a pending order through the guard (updateOrder)

- [ ] **Step 2.1: Write the failing test**

Model on `test_lagoon_wallet_cancel_limit_order` (`tests/gmx/lagoon/test_gmx_lagoon_integration.py:404-482`) — it already shows how to create a pending limit order and extract the order key. Instead of cancelling, call `updateOrder`:

```python
@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_update_limit_order(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Modify a pending GMX limit order through the guard.

    Opens a limit order that stays pending (trigger far from spot),
    then submits updateOrder via the guard to change the trigger price.
    Before this branch updateOrder reverted with
    "GMX: Unknown function in multicall"; this is the fork-level proof.
    """
    env = lagoon_gmx_fork_env
    # 1. Open a pending limit order (copy the setup from test_lagoon_wallet_cancel_limit_order)
    # 2. Extract order key from receipt
    # 3. Confirm pending
    # 4. Build updateOrder via the GMX layer that emits the updateOrder selector
    # 5. Sign through guard, submit, assert success
    # 6. Assert the order is still pending with the new economics
    ...
```

Find the Python entry point that emits `updateOrder` — search `eth_defi/gmx` for `updateOrder` / `update_order`. If no Python helper builds `updateOrder`, build the multicall calldata directly with the `ExchangeRouter.json` ABI (mirror `_wrap_multicall` from the unit test at `tests/guard/test_guard_gmx_validation.py:562-575`) and route it through `env.lagoon_wallet`. Document which approach you used in the docstring.

- [ ] **Step 2.2: Run to verify it fails (then fix)**

Run:
```bash
export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py::test_lagoon_wallet_update_limit_order -v
```
Expected: red first, then iterate. The guard must **allow** the call (no "Unknown function" revert).

- [ ] **Step 2.3: Run to verify it passes**

Same command. Expected: PASS.

- [ ] **Step 2.4: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_lagoon_integration.py
git commit -m "test(gmx): update pending limit order through Lagoon guard (updateOrder lifecycle)"
```

### Task 3: Stop-loss / take-profit through the guard

- [ ] **Step 3.1: Write the failing test**

`StopLossDecrease` and `LimitDecrease` are both `createOrder` calls. Use `env.trading` SL/TP builder (read `eth_defi/gmx/order/sltp_order.py` — `create_stop_loss_order` / `create_take_profit_order`, lines ~540/592). One test is enough since both are `createOrder`; assert the order is created and pending:

```python
@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_stop_loss_order(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """Place a stop-loss (StopLossDecrease) through the guard.

    Opens a long, then attaches a stop-loss decrease order. Confirms the
    StopLossDecrease orderType is accepted (it is a createOrder, already
    whitelisted) and lands as a pending trigger order.
    """
    env = lagoon_gmx_fork_env
    # 1. Open a long position
    # 2. Build a stop-loss decrease via env.trading.create_stop_loss_order(...)
    # 3. Sign through guard, submit
    # 4. Assert the SL order is pending (use the pending-orders helper)
    ...
```

Read `eth_defi/gmx/order/pending_orders.py` for the pending-order lookup helper, and match the real `create_stop_loss_order` signature.

- [ ] **Step 3.2 → 3.4: fail → fix → pass**

Run:
```bash
export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py::test_lagoon_wallet_stop_loss_order -v
```
Expected: PASS after iteration. No "Unknown function" revert.

- [ ] **Step 3.5: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_lagoon_integration.py
git commit -m "test(gmx): stop-loss decrease order through Lagoon guard (StopLossDecrease)"
```

### Task 4: Claim funding fees through the guard (fork level)

- [ ] **Step 4.1: Write the failing test**

`claimFundingFees` is unit-tested but never run through the real Safe→module→ExchangeRouter path on a fork. Build the `claimFundingFees(address[],address[],address)` multicall (receiver = Safe) and route it through `env.lagoon_wallet`. The position need not have accrued fees — the goal is to prove the guard **allows** a Safe-receiver claim and **blocks** a non-Safe receiver, on-chain through the module.

```python
@flaky(max_runs=3, min_passes=1)
def test_lagoon_wallet_claim_funding_fees(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """claimFundingFees through the guard: Safe receiver allowed, attacker blocked.

    Builds claimFundingFees with receiver == Safe (must succeed through the
    module) and receiver == a random attacker address (guard must revert
    "GMX: receiver not allowed").
    """
    env = lagoon_gmx_fork_env
    # 1. Build claimFundingFees multicall with receiver = Safe -> sign through guard -> assert success
    # 2. Build claimFundingFees multicall with receiver = attacker -> assert guard revert
    ...
```

Reuse the `_wrap_multicall` calldata-building pattern from `tests/guard/test_guard_gmx_validation.py:562-575` for the inner call, then send the outer `performCall` via `env.lagoon_wallet`. For the negative case assert with `assert_transaction_success_with_explanation` raising / a revert check matching the existing negative tests.

- [ ] **Step 4.2 → 4.4: fail → fix → pass**

Run:
```bash
export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py::test_lagoon_wallet_claim_funding_fees -v
```
Expected: PASS — positive claim succeeds, attacker claim reverts with `GMX: receiver not allowed`.

- [ ] **Step 4.5: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_lagoon_integration.py
git commit -m "test(gmx): claimFundingFees through Lagoon guard, Safe vs attacker receiver"
```

- [ ] **Step 4.6: Run the whole GMX Lagoon fork file once**

Run:
```bash
export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py -v
```
(bash `timeout: 180000`; GMX fork tests run serially — they conflict in parallel per `tests/gmx/README.md`.) Expected: all green, including the new tests (close long, close short, cancel close order, updateOrder, stop-loss, claimFundingFees).

---

## Chunk 2: Full deploy workflow + testnet runbook

### Task 5: Arbitrum Sepolia testnet runbook (manual, operator-run)

Testnet needs real keys/funds and live GMX keepers, so it is **not** a CI test — it is a documented, repeatable runbook the operator executes once to confirm "all works" against a real chain. Capture it as a markdown runbook checked into the repo.

**Files:**
- Create: `scripts/lagoon/README-gmx-testnet-runbook.md`

- [ ] **Step 5.1: Pre-req — fund the deployer on Arbitrum Sepolia**

Document in the runbook:
- ETH faucet: https://learnweb3.io/faucets/arbitrum_sepolia/
- Mint test collateral **USDC.SG** (`0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773`, 6 decimals) by calling `mint(deployer, 1000000000)` on Arbiscan Sepolia. The vault underlying **must** be USDC.SG, not regular Sepolia USDC — GMX markets validate the collateral token against the market's long/short token (per `eth_defi/gmx/README-GMX-Lagoon.md`).
- WETH test token: `0x980B62Da83eFf3D4576C647993b0c1D7faf17c73`.

- [ ] **Step 5.2: Deploy a vault on testnet**

Document the exact command (the script auto-detects chain 421614 → `from_the_scratch=True`, `use_forge=True`, no Uniswap):
```bash
NETWORK=testnet \
JSON_RPC_ARBITRUM_SEPOLIA="https://sepolia-rollup.arbitrum.io/rpc" \
GMX_PRIVATE_KEY="0x..." \
poetry run python scripts/lagoon/lagoon-gmx-example.py -t ETH
```
Record the deployed Safe / vault / module / GmxLib addresses in the runbook.

- [ ] **Step 5.3: Verify whitelisting on the live module**

Document the post-deploy assertions the script (or a follow-up snippet) checks: `module.isAllowedTarget(GMX_EXCHANGE_ROUTER) == True`, `module.isAllowedApprovalDestination(GMX_SYNTHETICS_ROUTER) == True`, market whitelisted, Safe receiver whitelisted, USDC.SG/WETH approvals == 2²⁵⁶−1.

- [ ] **Step 5.4: Run the full lifecycle against real keepers**

In the runbook, enumerate the operator steps and expected result for each — open long, update/cancel a pending limit, place stop-loss, close, claim funding fees — each routed through the module. Note that unlike Anvil, testnet keepers execute orders for real, so allow time for keeper execution between submit and assert.

- [ ] **Step 5.5: Commit the runbook**

```bash
git add scripts/lagoon/README-gmx-testnet-runbook.md
git commit -m "docs(gmx): Arbitrum Sepolia testnet runbook for Lagoon guard perp lifecycle"
```

### Task 6: Record the LP-ops follow-up (out of scope here)

- [ ] **Step 6.1: File a tracking note**

Add a one-line entry to the GMX README's future-work section (or open a GitHub issue if the operator prefers) stating that GM/GLV liquidity ops (`createDeposit`/`createWithdrawal`/`cancelDeposit`/`cancelWithdrawal`) are intentionally **not** whitelisted and would be a separate PR with its own receiver/market validation and tests. Reference this plan.

**Files:**
- Modify: `eth_defi/gmx/README-GMX-Lagoon.md` (append a "Future work / out of scope" note)

- [ ] **Step 6.2: Commit**

```bash
git add eth_defi/gmx/README-GMX-Lagoon.md
git commit -m "docs(gmx): note GM/GLV liquidity ops are out of scope for the perp guard"
```

---

## Definition of done

- [ ] `tests/guard/test_guard_gmx_validation.py` green (baseline).
- [ ] `tests/gmx/lagoon/test_gmx_lagoon_integration.py` green, including the new lifecycle tests: close long, close short, cancel close/decrease order, updateOrder, stop-loss, claimFundingFees (plus the pre-existing cancel-limit-order regression).
- [ ] `poetry run ruff format` applied; no diff on re-run.
- [ ] Testnet runbook committed and (ideally) executed once by the operator with addresses recorded.
- [ ] LP-ops follow-up noted.
- [ ] No Solidity / ABI changes introduced by this plan (verify `git diff --stat` touches only tests + docs).
