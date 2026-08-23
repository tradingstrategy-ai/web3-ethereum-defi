# GMX PnL payout review fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure caller-selected GMX PnL payout behaviour reaches bundled sync and async SL/TP decrease orders, and resolve the remaining review-found testing and type-safety violations.

**Architecture:** Keep `SLTPOrder` as the single builder of SL/TP decrease calldata. The CCXT adapters must pass its existing `decrease_position_swap_type` and `should_unwrap_native_token` constructor arguments from converted order parameters, using the same defaults as `OrderParams`. Keep fork-only Lagoon helpers inside their owning Lagoon test module, while the generic mock-oracle operation belongs in `eth_defi.gmx.testing` and obtains its ABI through the common loader.

**Tech Stack:** Python 3.14, Web3.py, GMX v2 Reader/Router order builders, pytest, Anvil Arbitrum fork fixtures, Poetry, Ruff.

**Spec:** `docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md` (plus the outstanding correctness and standards findings from PR #1485 review).

## Global constraints

- Preserve the existing PR scope: PnL-token payout, NAV valuation and configured execution-buffer behaviour only; introduce no dependencies.
- Use Python 3.14, type every added function argument and return value, and use `HexAddress` for EVM addresses rather than `str`.
- Keep `SLTPOrder`'s existing defaults: `DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"]` and `False` for native-token unwrapping.
- Do not change increase-order behaviour: `BaseOrder._build_order_arguments(..., is_close=False)` must continue to emit `NoSwap` and `shouldUnwrapNativeToken=True`.
- Use `eth_defi.abi.get_contract()` to bind packaged ABIs; do not read ABI JSON directly in test code.
- Do not import one pytest test module from another. Shared production-independent test utilities belong under `eth_defi.gmx.testing`; Lagoon scenario helpers stay in their one owning test module.
- Fork tests must use the existing fixed-block `anvil_chain_fork` fixture and must be run with `source .local-test.env && poetry run pytest ...`; do not add `@flaky` without documented observed nondeterminism.
- Preserve the user-owned uncommitted changes to `CLAUDE.md`, `docs/agents/`, and `scripts/gmx/verify_testnet_pnl_token.py`; stage only files named in the relevant task.

---

## File structure

| Path | Responsibility after this work |
| --- | --- |
| `eth_defi/gmx/ccxt/exchange.py` | Sync CCXT conversion and bundled-SL/TP assembly; forwards payout fields to `SLTPOrder`. |
| `eth_defi/gmx/ccxt/async_support/exchange.py` | Async CCXT conversion and bundled-SL/TP assembly; consumes the already-converted payout fields. |
| `tests/gmx/ccxt/test_close_pnl_config_forwarding.py` | Regression coverage that exercises the real sync bundled-SL/TP assembly without signing or broadcasting. |
| `eth_defi/gmx/testing/oracle.py` | Generic fork-test helper for setting a mock-oracle price through the repository ABI loader. |
| `tests/gmx/lagoon/test_gmx_close_pnl_token.py` | Owns the Lagoon Safe fork fixture and all end-to-end PnL/NAV scenarios. |
| `tests/gmx/test_valuation.py` | Retains only valuation tests that do not need the Lagoon scenario fixture. |
| `eth_defi/gmx/valuation.py` | Uses explicit aliases for GMX oracle payloads and market token/price tuples. |
| `eth_defi/gmx/types.py` | Defines the reusable GMX oracle payload aliases required by `valuation.py`. |

## Task 1: Forward bundled SL/TP payout options through both CCXT adapters

**Files:**

- Modify: `eth_defi/gmx/ccxt/exchange.py:6402-6528`
- Modify: `eth_defi/gmx/ccxt/async_support/exchange.py:3927-4015,4126-4148`
- Modify: `tests/gmx/ccxt/test_close_pnl_config_forwarding.py`

**Interfaces:**

- Consumes: `SLTPOrder.__init__(..., decrease_position_swap_type: int = DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"], should_unwrap_native_token: bool = False)`.
- Consumes: converted CCXT keys `decrease_position_swap_type` and `should_unwrap_native_token` when supplied by the caller.
- Produces: every sync and async `SLTPOrder` constructed for a bundled order has the intended payout fields; omission preserves the `SLTPOrder` defaults.

- [ ] **Step 1: Add the failing sync bundled-SL/TP forwarding regression**

  Extend `tests/gmx/ccxt/test_close_pnl_config_forwarding.py`. Reuse `ccxt_gmx_fork_open_close`, but intercept `SLTPOrder.create_increase_order_with_sltp()` after the real `SLTPOrder` constructor has received its arguments. The interception must raise the existing `_Intercepted` exception before signing or broadcasting.

  ```python
  def test_bundled_sltp_forwards_configured_pnl_payout_options(
      ccxt_gmx_fork_open_close: GMX,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      captured: dict[str, object] = {}

      def _stop_after_sltp_construction(
          sltp_order: SLTPOrder,
          *args: object,
          **kwargs: object,
      ) -> NoReturn:
          captured["decrease_position_swap_type"] = sltp_order.decrease_position_swap_type
          captured["should_unwrap_native_token"] = sltp_order.should_unwrap_native_token
          raise _Intercepted(captured)

      monkeypatch.setattr(SLTPOrder, "create_increase_order_with_sltp", _stop_after_sltp_construction)

      with pytest.raises(_Intercepted):
          ccxt_gmx_fork_open_close.create_order(
              "ETH/USDC:USDC",
              "market",
              "buy",
              0,
              params={
                  "size_usd": 10.0,
                  "leverage": 2.0,
                  "collateral_symbol": "USDC",
                  "takeProfit": {"triggerPercent": 0.10, "closePercent": 1.0},
                  "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
                  "should_unwrap_native_token": True,
              },
          )

      assert captured == {
          "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
          "should_unwrap_native_token": True,
      }
  ```

  Import `NoReturn`, `SLTPOrder`, and the existing `GMX`/swap constants at module level. Keep the current ordinary-close tests unchanged: they cover `GMXTrading.close_position`, whereas this new test covers the previously untested bundled path.

- [ ] **Step 2: Run the new test and verify the current implementation fails**

  Run:

  ```bash
  source .local-test.env && poetry run pytest tests/gmx/ccxt/test_close_pnl_config_forwarding.py::test_bundled_sltp_forwards_configured_pnl_payout_options -v
  ```

  Expected: FAIL because the captured `SLTPOrder` values remain its default swap-to-collateral/`False` pair instead of `no_swap`/`True`.

- [ ] **Step 3: Pass converted payout fields to the sync `SLTPOrder`**

  In `_create_order_with_sltp()`, immediately before constructing `SLTPOrder`, resolve the two values from `gmx_params`:

  ```python
  decrease_position_swap_type = gmx_params.get(
      "decrease_position_swap_type",
      DECREASE_POSITION_SWAP_TYPES["swap_pnl_token_to_collateral_token"],
  )
  should_unwrap_native_token = gmx_params.get("should_unwrap_native_token", False)
  ```

  Add both keyword arguments to the existing constructor call:

  ```python
  sltp_order = SLTPOrder(
      config=self.config,
      market_key=to_checksum_address(market_address),
      collateral_address=to_checksum_address(collateral_address),
      index_token_address=to_checksum_address(index_token_address),
      is_long=is_long,
      decrease_position_swap_type=decrease_position_swap_type,
      should_unwrap_native_token=should_unwrap_native_token,
  )
  ```

  Do not give `_convert_ccxt_to_gmx_params()` unconditional payout defaults: its current conditional keys preserve the `DecreaseOrder` default as the single source of truth for ordinary closes.

- [ ] **Step 4: Pass converted payout fields to the async `SLTPOrder` and correct its comment**

  In async `_create_order_with_sltp()`, use the keys returned by `_convert_ccxt_to_gmx_params_async()` when instantiating `SLTPOrder`:

  ```python
  sltp_order = SLTPOrder(
      config=self.config,
      market_key=to_checksum_address(market_address),
      collateral_address=to_checksum_address(collateral_address),
      index_token_address=to_checksum_address(index_token_address),
      is_long=True,
      decrease_position_swap_type=gmx_params["decrease_position_swap_type"],
      should_unwrap_native_token=gmx_params["should_unwrap_native_token"],
  )
  ```

  Replace the converter’s assertion that no downstream async code reads these keys with a short comment explaining that its return shape is consumed by bundled SL/TP decrease legs. Keep its configured `execution_buffer` fallback intact.

- [ ] **Step 5: Add an offline async assembly regression and verify it fails before Step 4**

  Add a small helper in `tests/gmx/ccxt/test_close_pnl_config_forwarding.py` that creates `AsyncGMX.__new__(AsyncGMX)`, replaces `_convert_ccxt_to_gmx_params_async()` with an async function returning a fixed `gmx_params` mapping, and monkeypatches the network-bound token/oracle/approval calls. Replace `SLTPOrder.create_increase_order_with_sltp()` with the same `NoReturn` interception used by the sync test. The test must call `asyncio.run(gmx._create_order_with_sltp(...))` and assert the captured constructor properties equal `no_swap` and `True`.

  The fixed mapping must include all keys consumed by the method:

  ```python
  {
      "collateral_symbol": "USDC",
      "leverage": 2.0,
      "size_delta_usd": 10.0,
      "slippage_percent": 0.003,
      "execution_buffer": 2.2,
      "decrease_position_swap_type": DECREASE_POSITION_SWAP_TYPES["no_swap"],
      "should_unwrap_native_token": True,
  }
  ```

  Give the fake token details `symbol = "WETH"` and `decimals = 18`, return one positive `maxPriceFull`/`minPriceFull` pair from the fake oracle, and set `gmx.markets["ETH/USDC:USDC"]["info"]` to non-zero syntactically valid market, long-token and index-token addresses. This test must not connect to an RPC or broadcast a transaction.

- [ ] **Step 6: Run the focused forwarding tests and format the touched files**

  Run:

  ```bash
  source .local-test.env && poetry run pytest tests/gmx/ccxt/test_close_pnl_config_forwarding.py tests/gmx/ccxt/test_execution_buffer_forwarding.py -v
  poetry run ruff format eth_defi/gmx/ccxt/exchange.py eth_defi/gmx/ccxt/async_support/exchange.py tests/gmx/ccxt/test_close_pnl_config_forwarding.py
  poetry run ruff check eth_defi/gmx/ccxt/exchange.py eth_defi/gmx/ccxt/async_support/exchange.py tests/gmx/ccxt/test_close_pnl_config_forwarding.py
  ```

  Expected: all focused tests pass; the formatter makes no further changes after its first run; Ruff reports no diagnostics.

- [ ] **Step 7: Commit the independently verified forwarding fix**

  ```bash
  git add eth_defi/gmx/ccxt/exchange.py eth_defi/gmx/ccxt/async_support/exchange.py tests/gmx/ccxt/test_close_pnl_config_forwarding.py
  git commit -m "fix(gmx): forward PnL payout options to bundled SL/TP"
  ```

## Task 2: Remove direct ABI parsing and the cross-test import

**Files:**

- Modify: `eth_defi/gmx/testing/oracle.py`
- Modify: `tests/gmx/lagoon/test_gmx_close_pnl_token.py:29-51,607-660`
- Modify: `tests/gmx/test_valuation.py:1-121`

**Interfaces:**

- Consumes: `get_contract(web3: Web3, fname: str | Path) -> Type[Contract]` from `eth_defi.abi`.
- Produces: `set_mock_token_price(web3: Web3, token_address: HexAddress, price_usd: int, decimals: int) -> None` in `eth_defi.gmx.testing.oracle`.
- Produces: `test_fetch_gmx_total_equity_end_to_end()` in the Lagoon test module, where `lagoon_gmx_fork_env`, `_open_long_and_get_position`, and `_close_position` are local.

- [ ] **Step 1: Preserve the existing WBTC and NAV checks as the pre-change regression baseline**

  Run the two existing end-to-end tests before moving any code:

  ```bash
  source .local-test.env && poetry run pytest \
      tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_close_profitable_long_wbtc_market_pays_pnl_in_usdc \
      tests/gmx/test_valuation.py::test_fetch_gmx_total_equity_end_to_end -v
  ```

  Expected: PASS on the fixed Arbitrum fork. These tests are behavioural baselines for a standards-only refactor; they do not need to fail before the refactor.

- [ ] **Step 2: Add the reusable mock-oracle helper using the ABI loader**

  In `eth_defi/gmx/testing/oracle.py`, import `HexAddress`, `Web3`, and `get_contract` at module level. Add `set_mock_token_price()` alongside `setup_mock_oracle()`:

  ```python
  def set_mock_token_price(
      web3: Web3,
      token_address: HexAddress,
      price_usd: int,
      decimals: int,
  ) -> None:
      """Set a token price and GMX timestamp adjustment on the fork mock oracle."""
      chain = get_chain_name(web3.eth.chain_id).lower()
      provider_address = resolve_contract_address(
          chain,
          ("chainlinkdatastreamprovider", "gmoracleprovider"),
          ARBITRUM_DEFAULTS["chainlink_provider"],
      )
      mock_oracle = get_contract(web3, "gmx/MockOracleProvider.json")(address=provider_address)
      scaled_price = price_usd * 10 ** (30 - decimals)
      # Build/send setPrice, read the DataStore adjustment, then build/send setTimestampAdjustment.
  ```

  Copy the existing transaction values exactly: account `web3.eth.accounts[0]`, gas `500_000`, and `gasPrice=web3.eth.gas_price`; validate each receipt through `assert_transaction_success_with_explanation`. Read the timestamp adjustment from `get_datastore_contract(web3, chain)` with `oracle_timestamp_adjustment_key(provider_address, token_address)`. Do not open or JSON-decode an ABI file in the helper.

- [ ] **Step 3: Replace the test-local ABI implementation with the helper**

  In `tests/gmx/lagoon/test_gmx_close_pnl_token.py`, delete `_set_mock_token_price()` and its `json`/`Path` imports. Import `set_mock_token_price` from `eth_defi.gmx.testing.oracle` and call it from `test_close_profitable_long_wbtc_market_pays_pnl_in_usdc`.

  The test’s WBTC test invocation must still set the same price and token decimals, so the fork behaviour is unchanged:

  ```python
  set_mock_token_price(web3, wbtc.address, price_usd=..., decimals=wbtc.decimals)
  ```

- [ ] **Step 4: Move the Lagoon-dependent NAV test beside its fixture**

  Move `test_fetch_gmx_total_equity_end_to_end()` from `tests/gmx/test_valuation.py` into `tests/gmx/lagoon/test_gmx_close_pnl_token.py`. Move only the imports it uses (`Decimal` and `fetch_gmx_total_equity`); reuse the Lagoon module’s existing `logger`, fixture, constants, open/close helpers, `fetch_on_chain_oracle_prices`, and `setup_mock_oracle`.

  Remove these imports from `tests/gmx/test_valuation.py`:

  ```python
  from decimal import Decimal
  from tests.gmx.fork_helpers import fetch_on_chain_oracle_prices, setup_mock_oracle
  from tests.gmx.lagoon.test_gmx_close_pnl_token import ...
  from tests.gmx.lagoon.test_gmx_lagoon_integration import ...
  ```

  Retain `test_reserves_unpriceable_token_raises()` and its direct imports in `tests/gmx/test_valuation.py`. The resulting module must contain no `from tests...` import.

- [ ] **Step 5: Run the moved and ABI-backed regressions**

  Run:

  ```bash
  source .local-test.env && poetry run pytest \
      tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_close_profitable_long_wbtc_market_pays_pnl_in_usdc \
      tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_fetch_gmx_total_equity_end_to_end \
      tests/gmx/test_valuation.py::test_reserves_unpriceable_token_raises -v
  rg -n "from tests\\.|import json|from pathlib import Path" tests/gmx/test_valuation.py tests/gmx/lagoon/test_gmx_close_pnl_token.py
  ```

  Expected: all three tests pass. The `rg` command produces no match for cross-test imports or direct ABI-loading imports in either changed test module.

- [ ] **Step 6: Format and commit the test-infrastructure repair**

  Run:

  ```bash
  poetry run ruff format eth_defi/gmx/testing/oracle.py tests/gmx/lagoon/test_gmx_close_pnl_token.py tests/gmx/test_valuation.py
  poetry run ruff check eth_defi/gmx/testing/oracle.py tests/gmx/lagoon/test_gmx_close_pnl_token.py tests/gmx/test_valuation.py
  git add eth_defi/gmx/testing/oracle.py tests/gmx/lagoon/test_gmx_close_pnl_token.py tests/gmx/test_valuation.py
  git commit -m "test(gmx): share mock-oracle and Lagoon valuation helpers"
  ```

  Expected: Ruff reports no diagnostics and the commit contains only the three listed files.

## Task 3: Type the valuation helper boundary

**Files:**

- Modify: `eth_defi/gmx/types.py`
- Modify: `eth_defi/gmx/valuation.py:314-405,477-510`
- Test: `tests/gmx/test_valuation.py::test_reserves_unpriceable_token_raises`

**Interfaces:**

- Produces: `OraclePricePayload`, `OraclePriceMap`, `MarketTokenAddresses`, `RawOraclePriceRange`, and `MarketPriceTuple` aliases in `eth_defi.gmx.types`.
- Produces: typed valuation helpers:

  ```python
  def _fetch_market_token_addresses(...) -> dict[HexAddress, MarketTokenAddresses]: ...
  def _oracle_price_tuple(oracle_prices: OraclePriceMap, token_address: HexAddress) -> RawOraclePriceRange: ...
  def _build_market_prices(tokens: MarketTokenAddresses, oracle_prices: OraclePriceMap) -> MarketPriceTuple: ...
  def _native_wrapped_token_address(chain: str) -> HexAddress: ...
  ```

- [ ] **Step 1: Write a narrow type-preserving unit assertion**

  Add a unit test in `tests/gmx/test_valuation.py` for `_oracle_price_tuple` using checksummed values and the new alias-compatible payload:

  ```python
  def test_oracle_price_tuple_reads_raw_gmx_prices() -> None:
      token = HexAddress("0x0000000000000000000000000000000000000001")
      prices: OraclePriceMap = {token: {"minPriceFull": "10", "maxPriceFull": "12"}}

      assert _oracle_price_tuple(prices, token) == (10, 12)
  ```

  Import the aliases and private helper explicitly. This protects the refactor’s integer conversion and case-insensitive address lookup without requiring RPC access.

- [ ] **Step 2: Run the new unit test and verify the existing untyped boundary is exercised**

  Run:

  ```bash
  source .local-test.env && poetry run pytest tests/gmx/test_valuation.py::test_oracle_price_tuple_reads_raw_gmx_prices -v
  ```

  Expected: PASS. This is a characterisation test: it records existing behaviour before replacing raw annotations with aliases.

- [ ] **Step 3: Define the domain aliases and update all valuation signatures**

  In `eth_defi/gmx/types.py`, add explicit type aliases with `TypedDict`, `NotRequired`, and `TypeAlias`:

  ```python
  class OraclePricePayload(TypedDict):
      minPriceFull: str | int
      maxPriceFull: str | int

  OraclePriceMap: TypeAlias = dict[HexAddress, OraclePricePayload]
  MarketTokenAddresses: TypeAlias = tuple[HexAddress, HexAddress, HexAddress]
  RawOraclePriceRange: TypeAlias = tuple[int, int]
  MarketPriceTuple: TypeAlias = tuple[RawOraclePriceRange, RawOraclePriceRange, RawOraclePriceRange]
  ```

  Use those aliases in `valuation.py`. Convert every address originating from Web3/API data to `HexAddress(to_checksum_address(...))` at the helper boundary, including the native wrapped-token and referral-storage return values. Keep the function’s case-insensitive oracle lookup because signed-price payload keys are not guaranteed to arrive checksummed.

- [ ] **Step 4: Run the valuation unit and fork regressions**

  Run:

  ```bash
  source .local-test.env && poetry run pytest \
      tests/gmx/test_valuation.py::test_oracle_price_tuple_reads_raw_gmx_prices \
      tests/gmx/test_valuation.py::test_reserves_unpriceable_token_raises \
      tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_fetch_gmx_total_equity_end_to_end -v
  poetry run ruff format eth_defi/gmx/types.py eth_defi/gmx/valuation.py tests/gmx/test_valuation.py
  poetry run ruff check eth_defi/gmx/types.py eth_defi/gmx/valuation.py tests/gmx/test_valuation.py
  ```

  Expected: all tests pass and Ruff reports no diagnostics.

- [ ] **Step 5: Commit the typed valuation boundary**

  ```bash
  git add eth_defi/gmx/types.py eth_defi/gmx/valuation.py tests/gmx/test_valuation.py
  git commit -m "refactor(gmx): type valuation oracle inputs"
  ```

## Final verification and PR update

- [ ] **Step 1: Inspect the aggregate diff and prevent accidental staging**

  Run:

  ```bash
  git diff --check origin/master...HEAD
  git status --short
  git diff --name-only origin/master...HEAD
  ```

  Expected: no whitespace errors; the three user-owned paths remain unstaged; only the intended GMX implementation, test, type and plan files are in the branch diff.

- [ ] **Step 2: Run the complete focused GMX regression set**

  Run:

  ```bash
  source .local-test.env && poetry run pytest \
      tests/gmx/ccxt/test_close_pnl_config_forwarding.py \
      tests/gmx/ccxt/test_execution_buffer_forwarding.py \
      tests/gmx/test_valuation.py \
      tests/gmx/lagoon/test_gmx_close_pnl_token.py -v
  ```

  Expected: PASS. If a live-fork infrastructure failure occurs, capture its traceback and follow the repository flaky-test escalation policy; do not add a retry marker merely to obtain green output.

- [ ] **Step 3: Confirm repository formatting and push the verified commits**

  Run:

  ```bash
  poetry run ruff format --check eth_defi/gmx tests/gmx
  git log --oneline origin/fix/gmx-pnl-token-nav-leak..HEAD
  git push origin HEAD:fix/gmx-pnl-token-nav-leak
  ```

  Expected: formatting check passes, the log shows the three task commits, and the non-force push fast-forwards PR #1485. Do not push if the remote branch has advanced; fetch, inspect the new commits, and rebase or merge only with an explicit updated review.

## Self-review

**Spec coverage:** Task 1 addresses the P1 sync/async bundled SL/TP configuration loss and adds direct regressions. Task 2 removes both P2 test-infrastructure violations while retaining their end-to-end behavioural coverage. Task 3 closes the remaining P2 address/oracle type-hint gaps. The final section validates the focused suite, formatting, accidental staging and the push.

**Placeholder scan:** This plan contains concrete paths, function names, test names, expected results, implementation snippets and commit commands; it contains no deferred implementation markers.

**Type consistency:** The CCXT adapters use the existing `SLTPOrder` constructor names exactly. `set_mock_token_price()` exposes `Web3`, `HexAddress`, `int`, `int` and `None` consistently. Task 3 defines each alias before `valuation.py` consumes it, and its test uses the same `OraclePriceMap` type.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-22-gmx-pnl-payout-review-fixes.md`.

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task and review each deliverable before continuing.
2. **Inline Execution** — execute the tasks in this session using `superpowers:executing-plans`, with checkpoints after every task.
