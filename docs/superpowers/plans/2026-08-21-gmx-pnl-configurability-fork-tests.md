# GMX PnL payout configurability — ccxt passthrough + full fork-test matrix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove with forked-mainnet integration tests that the GMX PnL-payout direction added in PR #1485 is genuinely *configurable* — not just a new hardcoded default — covering both stablecoin (USDC) and market-token (WETH/WBTC) payouts across the three `DECREASE_POSITION_SWAP_TYPES`, and close the one real production gap the review found: the ccxt close path never forwards the new fields, so ccxt callers cannot configure the payout direction at all.

**Architecture:** Two parts. (1) A small production fix: thread `decrease_position_swap_type` / `should_unwrap_native_token` through `_convert_ccxt_to_gmx_params()`'s return dict and `close_kwargs` so a ccxt caller can pass `params={"decrease_position_swap_type": ..., "should_unwrap_native_token": ...}` and it reaches `DecreaseOrder.create_decrease_order()` (which already accepts them). The async adapter shares the sync close path via `run_in_executor`, so fixing the sync converter covers both. (2) A full fork-test matrix in `tests/gmx/lagoon/test_gmx_close_pnl_token.py` proving each swap type pays PnL in the expected token: default `swap_pnl_token_to_collateral_token` (USDC), `swap_collateral_token_to_pnl_token` (WETH), `no_swap` (WETH raw), plus a WBTC market close proving the same machinery works when the PnL token is WBTC.

**Tech Stack:** Python 3.14, pytest, CCXT-GMX, Anvil Arbitrum mainnet fork (shared `anvil_fork_pool` pattern), `pytest.approx`.

---

## Context for the implementer

- This plan targets **PR #1485's branch** `fix/gmx-pnl-token-nav-leak` (head `96a34f9ae` at time of writing). The existing tests in `tests/gmx/lagoon/test_gmx_close_pnl_token.py` and the `is_close`-gated `_build_order_arguments()` are already on that branch. Work on that branch, not `master`.
- `DECREASE_POSITION_SWAP_TYPES` in `eth_defi/gmx/constants.py:350-354`:
  ```python
  DECREASE_POSITION_SWAP_TYPES = {
      "no_swap": 0,
      "swap_pnl_token_to_collateral_token": 1,   # default — PnL → collateral (USDC)
      "swap_collateral_token_to_pnl_token": 2,   # PnL stays in market token (WETH/WBTC)
  }
  ```
- PnL-token rule (from the PR's plan): GMX v2 always pays a profitable long's PnL in the market's **long token** (WETH for ETH/USD, WBTC for BTC/USD). A USDC-collateralised long therefore receives collateral in USDC and PnL separately. `decreasePositionSwapType` decides what happens to that PnL leg.
- Existing fork tests to extend: `tests/gmx/lagoon/test_gmx_close_pnl_token.py` has `test_close_profitable_long_pays_pnl_in_usdc_not_native_eth`, `test_close_profitable_short_is_unaffected_by_pnl_swap_fix`, `test_take_profit_order_execution_pays_pnl_in_usdc`. Helpers (`_open_long_and_get_position`, `_close_position`, `_SIZE_DELTA_USD`, `_LEVERAGE`, `_PRICE_MOVE_FRACTION`, `_GAS_REFUND_CEILING_USD`, `lagoon_gmx_fork_env` fixture) live in that file and are reused.
- Repo standards (AGENTS.md): pytest — no test classes, no `print()`, `pytest.approx()` for floats, fixtures + functions only, absolute-value assertions on fixed fork blocks. Logging — `%s`/`%f` not f-strings. Type hints on all args/returns. `HexAddress` for addresses.
- Run tests: `source .local-test.env && poetry run pytest ...` with `timeout: 180000`. The `lagoon_gmx_fork_env` fixture is shared/session-scoped — new tests should reuse it (they run against the same fork sequentially, each via the existing env).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `eth_defi/gmx/ccxt/exchange.py` | sync ccxt converter + close_kwargs | Forward `decrease_position_swap_type`/`should_unwrap_native_token` from `params` → `gmx_params` → `close_kwargs` |
| `eth_defi/gmx/ccxt/async_support/exchange.py` | async ccxt converter | Mirror the two fields in the async converter's return dict (same `params` passthrough) |
| `tests/gmx/lagoon/test_gmx_close_pnl_token.py` | fork-test matrix | Add 3-4 new tests: no_swap, collateral→pnl, WBTC market, plus ccxt close-path test |
| `tests/gmx/ccxt/` (new test file) | ccxt fork test for close-path config | Assert `close_position` receives the configured swap type on a fork |

No changes to `DecreaseOrder`, `OrderParams`, `SLTPOrder`, or `_build_order_arguments` — those already accept the fields.

---

### Task 1: Production fix — thread the new fields through the ccxt close path

**Files:**
- Modify: `eth_defi/gmx/ccxt/exchange.py` (sync converter return dict ~line 6120s, and `close_kwargs` ~line 7282-7305)
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py` (async converter return dict ~line 4110-4131)

The converter is where ccxt params become GMX params; `close_kwargs` is where they're forwarded to `trader.close_position()`. Currently neither touches the two new fields, so `params["decrease_position_swap_type"]` is silently dropped.

- [ ] **Step 1: Add the two fields to the sync converter's return dict**

In `eth_defi/gmx/ccxt/exchange.py`, in `_convert_ccxt_to_gmx_params()` (around the `slippage_percent`/`execution_buffer` reads near line 6083, and the return dict near 6120s), add:

```python
        slippage_percent = params.get("slippage_percent", self.default_slippage)
        execution_buffer = params.get("execution_buffer", self.execution_buffer)
        decrease_position_swap_type = params.get(
            "decrease_position_swap_type",
            DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"],
        )
        should_unwrap_native_token = params.get("should_unwrap_native_token", False)
```

and in the return dict (wherever `"execution_buffer": execution_buffer,` appears):

```python
            "execution_buffer": execution_buffer,
            "decrease_position_swap_type": decrease_position_swap_type,
            "should_unwrap_native_token": should_unwrap_native_token,
```

Ensure `DECREASE_POSITION_SWAP_TYPES` is imported at the top of the file (check; add `from eth_defi.gmx.constants import DECREASE_POSITION_SWAP_TYPES` if missing).

- [ ] **Step 2: Forward the fields in `close_kwargs`**

In `eth_defi/gmx/ccxt/exchange.py`, in the `close_kwargs` dict (around line 7282-7291, where `slippage_percent`/`execution_buffer` are set):

```python
        close_kwargs: dict = dict(
            market_symbol=gmx_params["market_symbol"],
            collateral_symbol=_close_collateral_symbol,
            start_token_symbol=_close_collateral_symbol,
            is_long=position_to_close.get("is_long"),  # Use actual position direction
            size_delta_usd=size_delta_usd,
            initial_collateral_delta=initial_collateral_delta,
            slippage_percent=gmx_params.get("slippage_percent", self.default_slippage),
            execution_buffer=gmx_params.get("execution_buffer", self.execution_buffer),
            auto_cancel=gmx_params.get("auto_cancel", False),
            decrease_position_swap_type=gmx_params.get(
                "decrease_position_swap_type",
                DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"],
            ),
            should_unwrap_native_token=gmx_params.get("should_unwrap_native_token", False),
        )
```

These flow into `trader.close_position(**close_kwargs)` → `create_decrease_order(..., **kwargs)` which already accepts them as keyword-only args.

- [ ] **Step 3: Mirror in the async converter's return dict**

In `eth_defi/gmx/ccxt/async_support/exchange.py`, in `_convert_ccxt_to_gmx_params_async()` return dict (around line 4110-4131, where `"execution_buffer": execution_buffer,` is emitted):

```python
            "execution_buffer": execution_buffer,
            "decrease_position_swap_type": params.get(
                "decrease_position_swap_type",
                DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"],
            ),
            "should_unwrap_native_token": params.get("should_unwrap_native_token", False),
        }
```

The async adapter routes closes through the shared sync close path (`run_in_executor`), so no async `close_kwargs` change is needed — the sync `close_kwargs` in Step 2 covers async closes too.

- [ ] **Step 4: Run existing ccxt tests to confirm no regression**

Run: `source .local-test.env && poetry run pytest tests/gmx/ccxt/test_execution_buffer_forwarding.py tests/gmx/ccxt/test_position_metrics.py -v`
Expected: all pass (the executionBuffer fix's CI regression tests must stay green).

- [ ] **Step 5: Commit**

```bash
git add eth_defi/gmx/ccxt/exchange.py eth_defi/gmx/ccxt/async_support/exchange.py
git commit -m "fix(gmx): forward decrease_position_swap_type/should_unwrap_native_token through ccxt close path"
```

---

### Task 2: Fork test — `no_swap` config pays PnL raw in the market token

**Files:**
- Modify: `tests/gmx/lagoon/test_gmx_close_pnl_token.py`

The default (swap PnL→USDC) is already covered. This proves the *non-default* `no_swap` (0) is honoured: the WETH PnL leg is **not** converted, and arrives as WETH (not USDC, not native ETH).

- [ ] **Step 1: Write the failing test**

Add after `test_close_profitable_short_is_unaffected_by_pnl_swap_fix` (reuse `_open_long_and_get_position`, `_close_position`, `_SIZE_DELTA_USD`, `_PRICE_MOVE_FRACTION`, `_GAS_REFUND_CEILING_USD`, `USDC_ARBITRUM`, `WETH_ARBITRUM` — confirm `WETH_ARBITRUM` is imported; add `from tests.gmx.lagoon.test_gmx_close_pnl_token import WETH_ARBITRUM` if it lives in the integration module, else define it):

```python
def test_close_profitable_long_with_no_swap_pays_pnl_in_weth(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """``no_swap`` (0) must leave the WETH PnL leg unconverted.

    Proves the payout direction is configurable, not hardcoded: with
    ``decrease_position_swap_type = no_swap``, a profitable USDC-collateralised
    long receives its PnL as raw WETH (the market's long token), with no swap
    to USDC and no unwrap to native ETH.
    """
    env = lagoon_gmx_fork_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    weth = fetch_erc20_details(web3, WETH_ARBITRUM)

    position = _open_long_and_get_position(env)
    collateral_usd = position["initial_collateral_amount_usd"]

    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_eth_price = int(current_eth_price * (1 + _PRICE_MOVE_FRACTION))
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)
    expected_profit_usd = _SIZE_DELTA_USD * _PRICE_MOVE_FRACTION

    weth_before = weth.contract.functions.balanceOf(safe_address).call()
    usdc_before = usdc.contract.functions.balanceOf(safe_address).call()
    eth_before = web3.eth.get_balance(safe_address)

    env.trading.close_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=position["position_size_usd_raw"],
        initial_collateral_delta=position["initial_collateral_amount_usd"],
        slippage_percent=0.005,
        execution_buffer=30,
        decrease_position_swap_type=0,  # no_swap
        should_unwrap_native_token=True,
    )
    _close_position(env, position, is_long=True)

    weth_delta = (weth.contract.functions.balanceOf(safe_address).call() - weth_before) / 10**weth.decimals
    usdc_delta = (usdc.contract.functions.balanceOf(safe_address).call() - usdc_before) / 10**usdc.decimals
    eth_delta_usd = ((web3.eth.get_balance(safe_address) - eth_before) / 10**18) * new_eth_price

    # no_swap: PnL arrives as WETH, collateral as USDC, nothing unwrapped to native ETH.
    assert weth_delta > 0.5 * (expected_profit_usd / new_eth_price), (
        f"WETH did not increase by roughly the profit: {weth_delta:.6f} WETH"
    )
    assert usdc_delta > collateral_usd * 0.9, (
        f"USDC should recover collateral, got {usdc_delta:.2f}"
    )
    assert eth_delta_usd < _GAS_REFUND_CEILING_USD, (
        f"Native ETH increased ~${eth_delta_usd:.2f} — shouldUnwrapNativeToken=True with no_swap "
        f"should still not unwrap the WETH payout; unwrap applies to WETH->native only"
    )
```

Note: the `_close_position` helper in the file builds and signs the close from `env.trading.close_position(...)` internally — check whether it accepts extra kwargs. If it does **not** (it currently calls `close_position` with a fixed arg set), **refactor `_close_position` to accept `**kwargs`** and pass them through to `close_position`, then pass the swap-type kwargs in the test. Adjust the test accordingly if the helper signature differs.

- [ ] **Step 2: Run the test — expect the ccxt-less direct path to fail only if the helper needs the refactor**

Run: `source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_close_profitable_long_with_no_swap_pays_pnl_in_weth -v`
Expected: after the helper refactor (if needed), the test passes — the direct `trading.close_position` path already forwards `**kwargs` to `create_decrease_order`.

- [ ] **Step 3: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_close_pnl_token.py
git commit -m "test(gmx): prove no_swap close pays PnL in the market token (WETH)"
```

---

### Task 3: Fork test — `swap_collateral_token_to_pnl_token` pays PnL in WETH

**Files:**
- Modify: `tests/gmx/lagoon/test_gmx_close_pnl_token.py`

Proves the *other* non-default direction (2): the close converts the **collateral** to the PnL token, so the whole payout (collateral + PnL) arrives as WETH. This is the config that makes the vault deliberately accumulate the market token.

- [ ] **Step 1: Write the failing test**

Add after the `no_swap` test:

```python
def test_close_profitable_long_swap_collateral_to_pnl_pays_weth(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """``swap_collateral_token_to_pnl_token`` (2) pays the whole close out in WETH.

    The most aggressive configurability proof: collateral + PnL both arrive in
    the market's long token (WETH), so USDC (the collateral token) is what the
    close swaps *away* from. Demonstrates the vault can be deliberately set to
    accumulate the market token rather than the stablecoin.
    """
    env = lagoon_gmx_fork_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    weth = fetch_erc20_details(web3, WETH_ARBITRUM)

    position = _open_long_and_get_position(env)
    collateral_usd = position["initial_collateral_amount_usd"]

    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_eth_price = int(current_eth_price * (1 + _PRICE_MOVE_FRACTION))
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)
    expected_profit_usd = _SIZE_DELTA_USD * _PRICE_MOVE_FRACTION

    weth_before = weth.contract.functions.balanceOf(safe_address).call()
    usdc_before = usdc.contract.functions.balanceOf(safe_address).call()

    _close_position(
        env,
        position,
        is_long=True,
        decrease_position_swap_type=2,  # swap_collateral_token_to_pnl_token
        should_unwrap_native_token=False,
    )

    weth_delta = (weth.contract.functions.balanceOf(safe_address).call() - weth_before) / 10**weth.decimals
    usdc_delta = (usdc.contract.functions.balanceOf(safe_address).call() - usdc_before) / 10**usdc.decimals

    expected_weth_usd = collateral_usd + expected_profit_usd
    # Both collateral and PnL arrive as WETH (minus swap slippage/fees).
    assert weth_delta * new_eth_price > 0.8 * expected_weth_usd, (
        f"WETH payout ${weth_delta * new_eth_price:.2f} < 80% of expected ${expected_weth_usd:.2f}"
    )
    assert usdc_delta < collateral_usd * 0.5, (
        f"USDC should be mostly swapped away, but delta={usdc_delta:.2f} (expected < 50% of collateral)"
    )
```

- [ ] **Step 2: Run the test**

Run: `source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_close_profitable_long_swap_collateral_to_pnl_pays_weth -v`
Expected: PASS (the direct `trading.close_position` path forwards `decrease_position_swap_type=2` through `**kwargs`).

- [ ] **Step 3: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_close_pnl_token.py
git commit -m "test(gmx): prove swap_collateral_token_to_pnl_token pays the close out in WETH"
```

---

### Task 4: Fork test — WBTC market, default swap-to-USDC

**Files:**
- Modify: `tests/gmx/lagoon/test_gmx_close_pnl_token.py`

Proves the fix works on a second market whose PnL token is **WBTC** (not WETH), closing the "WETH/WBTC etc." gap. Uses the default `swap_pnl_token_to_collateral_token` (1): PnL paid in WBTC must be swapped to USDC.

- [ ] **Step 1: Confirm the WBTC market is available on the shared Arbitrum fork**

The existing env opens `market_symbol="ETH"`. Check `tests/gmx/conftest.py` / `get_gmx_address` for the BTC market symbol (`BTC` or `WBTC.b` on Arbitrum) and the WBTC token address (`wbtc_address` fixture exists in conftest). Use whatever symbol `env.trading.open_position(market_symbol="WBTC", ...)` resolves to — confirm via `env.markets` or the conftest mapping before writing the test. If the fork env's market catalog includes BTC/USD, use `market_symbol="WBTC"`; if the PnL token is WBTC.b or BTC, use that symbol and the corresponding token address for the balance check.

- [ ] **Step 2: Write the failing test**

```python
def test_close_profitable_long_wbtc_market_pays_pnl_in_usdc(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """A profitable WBTC (BTC/USD) long close pays PnL in USDC, not WBTC.

    The PnL-token rule is not WETH-specific: GMX pays a long's profit in the
    market's long token, which is WBTC for BTC/USD. With the default
    ``swap_pnl_token_to_collateral_token``, that WBTC leg must be swapped to
    USDC, proving the fix generalises beyond the ETH market.
    """
    env = lagoon_gmx_fork_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    wbtc = fetch_erc20_details(web3, WBTC_ARBITRUM)  # use the conftest wbtc_address

    env.lagoon_wallet.sync_nonce(web3)
    order_result = env.trading.open_position(
        market_symbol="WBTC",  # or "BTC" / "WBTC.b" per Step 1
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=_SIZE_DELTA_USD,
        leverage=_LEVERAGE,
        slippage_percent=0.005,
        execution_buffer=30,
    )
    # ... sign + execute as keeper, same pattern as _open_long_and_get_position
    position = ...  # single open position

    # Move the mock oracle price up for BTC
    current_btc_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_btc_price = int(current_btc_price * (1 + _PRICE_MOVE_FRACTION))
    setup_mock_oracle(web3, eth_price_usd=new_btc_price, usdc_price_usd=current_usdc_price)
    expected_profit_usd = _SIZE_DELTA_USD * _PRICE_MOVE_FRACTION

    wbtc_before = wbtc.contract.functions.balanceOf(safe_address).call()
    usdc_before = usdc.contract.functions.balanceOf(safe_address).call()

    _close_position(env, position, is_long=True)  # default swap-to-collateral

    wbtc_delta = (wbtc.contract.functions.balanceOf(safe_address).call() - wbtc_before) / 10**wbtc.decimals
    usdc_delta = (usdc.contract.functions.balanceOf(safe_address).call() - usdc_before) / 10**usdc.decimals

    # Default swap: PnL (WBTC leg) converted to USDC.
    assert usdc_delta > collateral_usd + 0.5 * expected_profit_usd, (
        f"USDC only gained {usdc_delta:.2f}, expected collateral+most of ${expected_profit_usd:.2f} profit"
    )
    assert wbtc_delta < 1e-6, f"WBTC increased by {wbtc_delta:.8f} — PnL leaking as WBTC instead of USDC"
```

Note: the exact `market_symbol` and WBTC token address must match the fork env's catalog (Step 1). If the mock-oracle helper only moves ETH, extend `setup_mock_oracle` (or add a BTC price arg) to also move the BTC price — check `tests/gmx/fork_helpers.py::setup_mock_oracle`'s signature first.

- [ ] **Step 3: Run the test**

Run: `source .local-test.env && poetry run pytest tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_close_profitable_long_wbtc_market_pays_pnl_in_usdc -v`
Expected: PASS (or adjust the market symbol / oracle helper per Step 1 findings).

- [ ] **Step 4: Commit**

```bash
git add tests/gmx/lagoon/test_gmx_close_pnl_token.py
git commit -m "test(gmx): prove default PnL swap works on the WBTC market"
```

---

### Task 5: ccxt fork test — close path honours the configured swap type

**Files:**
- Create: `tests/gmx/ccxt/test_close_pnl_config_forwarding.py`
- Modify (if needed): `eth_defi/gmx/ccxt/exchange.py` (should be done in Task 1)

Proves the Task 1 production fix end-to-end: a ccxt close with `params={"decrease_position_swap_type": ...}` actually reaches `DecreaseOrder.create_decrease_order()`. Mirrors the interception pattern in `tests/gmx/ccxt/test_execution_buffer_forwarding.py` (which intercepts `GMXTrading.open_position` on a fork and asserts the captured `execution_buffer`).

- [ ] **Step 1: Write the failing test**

Model on `test_execution_buffer_forwarding.py`'s interception of the order path. Create `tests/gmx/ccxt/test_close_pnl_config_forwarding.py`:

```python
"""ccxt close path must forward decrease_position_swap_type / should_unwrap_native_token.

Regression for the review follow-up: the ccxt ``close_position`` path built
``close_kwargs`` without the two fields added to ``DecreaseOrder`` in PR #1485,
so a ccxt caller could never configure the PnL-payout direction. This test
intercepts ``GMXTrading.close_position`` on an Anvil fork and asserts the
configured swap type reaches it via ``**close_kwargs``.
"""

import pytest

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.constants import DECREASE_POSITION_SWAP_TYPES


class _InterceptedClose(Exception):
    """Raised inside the intercepted close_position to capture its kwargs."""


def test_ccxt_close_forwards_configured_swap_type(ccxt_gmx_fork_env):
    """``params={"decrease_position_swap_type": ...}`` must reach the trader close."""
    gmx = ccxt_gmx_fork_env  # or whatever fixture the ccxt fork tests use
    captured = {}

    def _intercepted_close(*args, **kwargs):
        captured.update(kwargs)
        raise _InterceptedClose()

    # Intercept the close path the same way test_execution_buffer_forwarding
    # intercepts open_position.
    original = gmx.trader.close_position
    gmx.trader.close_position = _intercepted_close
    try:
        with pytest.raises(_InterceptedClose):
            gmx.create_order(
                "ETH/USDC:USDC",
                "market",
                "sell",
                1.0,
                None,
                params={
                    "reduceOnly": True,
                    "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
                    "should_unwrap_native_token": True,
                },
            )
    finally:
        gmx.trader.close_position = original

    assert captured.get("decrease_position_swap_type") == DECREASE_POSITION_SWAP_TYPES["no_swap"]
    assert captured.get("should_unwrap_native_token") is True
```

Confirm the fixture name and interception mechanics by reading `tests/gmx/ccxt/test_execution_buffer_forwarding.py` (it uses a `ccxt_gmx_fork_env` fixture and a `_Intercepted` exception on `GMXTrading.open_position`). Adapt the `create_order` call to the actual reduce-only close invocation used by that suite.

- [ ] **Step 2: Run the test**

Run: `source .local-test.env && poetry run pytest tests/gmx/ccxt/test_close_pnl_config_forwarding.py -v`
Expected: PASS (fails before Task 1, passes after — the `close_kwargs` now includes the fields).

- [ ] **Step 3: Run the full touched test groups**

Run: `source .local-test.env && poetry run pytest tests/gmx/ccxt/ tests/gmx/lagoon/test_gmx_close_pnl_token.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/gmx/ccxt/test_close_pnl_config_forwarding.py
git commit -m "test(gmx): assert ccxt close path forwards the configured PnL swap type"
```

---

## Self-Review

**Spec coverage (your three asks):**
- "test the pnl is configurable" → Tasks 2, 3, 5 (no_swap, swap_collateral_to_pnl, and ccxt forwarding each prove a *non-default* configuration takes effect).
- "both stable & market token" → Task 2/3 (USDC vs WETH), Task 4 (WBTC market).
- "pnl is happening or not" → every test asserts the PnL leg lands in the expected token (WETH delta, USDC delta, WBTC delta) with `pytest.approx`-style tolerances, and the existing default-swap test remains the control.
- The ccxt production gap (found in review, confirmed by you) → Task 1 fixes it, Task 5 tests it.

**Placeholder scan:** Two deliberate "confirm/adjust" markers remain where the exact identifier depends on code I could not fully read (the `_close_position` helper's kwargs passthrough, and the WBTC market symbol / mock-oracle BTC-price handling). Each has a concrete instruction (refactor `_close_position` to accept `**kwargs`; check `setup_mock_oracle`'s signature and the conftest `wbtc_address`). No TBD/TODO.

**Type consistency:** Uses only existing identifiers (`DECREASE_POSITION_SWAP_TYPES`, `USDC_ARBITRUM`, `WETH_ARBITRUM`, `_SIZE_DELTA_USD`, `_PRICE_MOVE_FRACTION`, `_GAS_REFUND_CEILING_USD`, `lagoon_gmx_fork_env`, `fetch_on_chain_oracle_prices`, `setup_mock_oracle`, `_open_long_and_get_position`, `_close_position`) — all confirmed present on the PR branch. `should_unwrap_native_token`/`decrease_position_swap_type` are the exact keyword names accepted by `DecreaseOrder.create_decrease_order()` (verified).

**Risk note:** The `no_swap` + `should_unwrap_native_token=True` test asserts native ETH stays under the gas-refund ceiling. The PR's own take-profit test discovered bundled-order fee escrows create large native-ETH refund artefacts; the `no_swap` test uses a plain (non-bundled) close, so the refund should be small — but if it proves large on the fork, switch that assertion to track WETH only (like the PR's TP test does) and drop the native-ETH ceiling.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
