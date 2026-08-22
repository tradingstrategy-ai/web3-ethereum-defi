# GMX vault NAV is wrong in both directions — P0

Status: **plan only — no code, state or contracts modified**.
Priority: **P0**. Written 2026-08-20, revised 2026-08-20 after settling the
collateral question and confirming a second, independent defect.

## TL;DR

A USDC-denominated Lagoon vault trading GMX perps has two independent NAV
defects that were previously conflated:

- **Defect A — profit leaks out of NAV.** Profitable long closes pay PnL in the
  market's long token (WETH/WBTC/BNB), delivered as native ETH or the raw ERC-20
  into the Safe. NAV cannot count it. Reported NAV *falls* by roughly the profit
  on every winning long. **Understates NAV, permanently, cumulatively.**
- **Defect B — open positions are valued gross.** Position value is computed by
  hand as `collateral + PnL`, ignoring borrowing fees, funding fees, position
  fees and price impact. **Overstates NAV** for as long as a position is open.

Funds are **not lost**. Everything is in the Safe. This is a NAV-correctness and
unintended-exposure problem. But NAV drives Lagoon deposit and redemption share
pricing, so while it is live, redeemers are underpaid and new depositors buy in
too cheaply, diluting existing holders. That is what makes it P0.

## Defect A — PnL paid in the long token, invisible to NAV

### Root cause chain (settled — each step verified in code)

1. Longs are opened with **USDC collateral**, not WETH.
   `open_position()` is documented and called with `collateral_symbol="USDC"`,
   `start_token_symbol="USDC"` (`eth_defi/gmx/trading.py:66-67`).
   `_classify_collateral_support()` in
   `eth_defi/gmx/order/order_argument_parser.py` returns `True` when the
   collateral matches the market's long **or short** token. USDC is the ETH/USD
   market's short token, so it is accepted directly. `start_token == collateral`,
   so `_handle_missing_swap_path()` builds no swap leg and `swap_path` stays `[]`.

   **The position is long ETH while holding USDC collateral.** This is a valid
   GMX v2 configuration, and it is the precondition for the whole bug: in GMX a
   long's PnL is always denominated in the market's **long token**. When
   collateral is also the long token they merge harmlessly. When collateral is
   USDC they are two different tokens, and GMX pays them out separately.

2. `decreasePositionSwapType = NoSwap` is hardcoded, so GMX will not convert the
   WETH PnL into the USDC collateral token.
   `eth_defi/gmx/order/base_order.py:707`, `eth_defi/gmx/order/sltp_order.py:475`.
   Both are literal `DECREASE_POSITION_SWAP_TYPES["no_swap"]` with no parameter
   or override path.

3. **There is no `swapPath` fallback on close either.** `swap_path` is absent
   from the `is_decrease` required-key list
   (`order_argument_parser.py:203-214` — compare the `is_increase` list at
   190-202, which does include it). The parser therefore never populates it, and
   every close call site falls through to `.get("swap_path", [])` → `[]`:
   `eth_defi/gmx/trading.py:1009`, `eth_defi/gmx/order.py:319` and `:469`.

   With `NoSwap` **and** an empty `swapPath`, nothing anywhere in the close path
   can convert the profit. This is definitive, not inferential.

4. `shouldUnwrapNativeToken = True` is hardcoded
   (`base_order.py:709`, `sltp_order.py:477`), so a WETH payout is unwrapped and
   arrives as **native ETH**. For BTC and BNB markets the payout is the raw
   ERC-20 (WBTC, BNB) instead.

5. NAV is stablecoin reserves plus open-position value
   (`fetch_gmx_total_equity()`, `eth_defi/gmx/valuation.py:76`). On close the
   position value is removed and the USDC collateral is counted, but the
   ETH/WBTC/BNB profit is invisible.

6. It is invisible **by construction, not by caller choice**:
   `eth_defi/gmx/valuation.py:126` *asserts* every reserve token is
   `is_stablecoin_like()`. Passing WETH raises `AssertionError`. Non-stable Safe
   assets are unrepresentable through this API.

Net: **every profitable long close reduces reported NAV by roughly the profit.**

### Scope

Affected: profitable **long** closes with USDC collateral — normal closes and
SL/TP closes alike, on every market traded (hence ETH, WBTC *and* BNB in the
Safe). The worst-hit path is take-profit, which by construction only ever fires
in profit.

Not affected: **shorts** (PnL token is the short token, which equals the
collateral token — no split payout); **losing longs** (the loss is deducted from
collateral, there is no positive PnL to pay in the long token).

### Second-order consequence

Even once the NAV maths is fixed, a USDC-denominated vault that silently
accumulates ETH, WBTC and BNB is **long those assets without anyone deciding to
be**. Its value then swings with spot price between trades. That is a strategy
defect, not just a bookkeeping one, and it is the reason the fix must be at the
payout layer rather than only in the accounting layer.

## Defect B — open positions are valued gross

`_calculate_position_value()` (`eth_defi/gmx/valuation.py:247`) reads raw
positions via `Reader.getAccountPositions()` (`valuation.py:214`) and computes:

```
position_value = collateral_usd + pnl_usd        # valuation.py:320
```

with `pnl_usd` derived by hand from entry price versus oracle mark price. It
accounts for **no borrowing fee, no funding fee, no position fee, no price
impact, no liquidation fee**. The value it reports is what the position would be
worth if closing were free. It is not.

GMX's Reader already exposes the correct figure, and **the ABI is already
committed to this repo**. `eth_defi/abi/gmx/Reader.json` contains both
`getPositionInfo` and `getAccountPositionInfoList`, returning:

- `positionValueInUsd` (`int256`) — the canonical net figure
- `pnlAfterPriceImpactUsd` and `basePnlUsd`
- a full `fees` struct: `funding.fundingFeeAmount`, `borrowing.borrowingFeeUsd`,
  `positionFeeAmount`, `totalCostAmount`, `liquidation.*`, plus
  `collateralTokenPrice.min`/`.max`
- `executionPriceResult.priceImpactUsd` and `executionPrice`

We are simply calling the wrong Reader function. This is a low-risk swap with a
large correctness gain, and it also removes our hand-rolled entry-price and
oracle-mid arithmetic (`valuation.py:247-335`) along with its wstETH
special-casing.

**Direction of error:** Defect B *overstates* NAV while positions are open;
Defect A *understates* it permanently after each winning long close. They do not
cancel — they are different magnitudes on different clocks, and fixing only one
leaves NAV wrong.

## Verdict on the reported diagnosis

| Claim | Status |
|---|---|
| GMX pays profitable longs in the market's long token unless the close swaps the PnL token | **Correct.** Applies here because collateral is USDC, not the long token — a precondition the report omits. |
| `decreasePositionSwapType = NoSwap` hardcoded | **Confirmed** — `base_order.py:707`, `sltp_order.py:475`. |
| `shouldUnwrapNativeToken = True` hardcoded | **Confirmed** — `base_order.py:709`, `sltp_order.py:477`. Not a *cause*: it does not change which token PnL is paid in, only that WETH is delivered as native ETH. It is a severity amplifier. |
| Present in both normal and SL/TP closes | **Confirmed** — the two call sites are exactly those paths. |
| NAV = Safe USDC + GMX open-position value | **Confirmed** — `valuation.py:76`. |
| Passes an empty reserve-token list | **Unverifiable here** — `fetch_gmx_total_equity()` has **no callers in this repository**. The caller is in trade-executor. |
| NAV never prices other Safe assets | **Confirmed, and stronger than stated** — `valuation.py:126` asserts stablecoin-only, so it is impossible, not merely unconfigured. |
| Use `positionValueInUsd` / detailed Reader calculation | **Confirmed as a real second defect.** ABI already present. |
| Worked example (919.446 → 889.628) | **Internally consistent.** 919.446 − 178.84 + 149.03 = 889.636 vs. 889.628 (rounding). Removed minus returned = 29.81 against 28.60 stated profit; residual 1.21 = fees and drift. |

### Corrections to the report's framing

1. **"Takes profit in ETH" overstates.** Collateral returns as USDC; only the
   positive PnL is paid in the long token.
2. **`shouldUnwrapNativeToken` is miscategorised as a cause** — see table.
3. **`README-GMX-Lagoon.md` is wrong, not the code.** Line 125 documents longs as
   swapping USDC → WETH via `swapPath: [market]` so that "the native long
   collateral is WETH", and the close section at 145-171 shows
   `swapPath: [] // (or [market] for long→USDC)`. Neither happens. The parser
   accepts USDC directly on open and never populates `swap_path` on decrease.
   **This previously-open question (old D6) is now settled: fix the README.**

## Immediate operational response (before any code)

These are operator actions on production state, not repository changes. They
need explicit human authorisation — this plan does not perform them.

### O1 — Freeze

Pause deposits, redemptions and NAV settlement on affected vaults. The published
share price is materially wrong right now; every transaction against it
mispays someone.

### O2 — Quantify

Read-only, safe to do immediately. For each affected Safe, at current block:

- native ETH balance, WETH, WBTC, BNB and any other GMX payout token
- current `fetch_gmx_total_equity()` output versus a corrected figure that
  includes those balances and uses `positionValueInUsd`
- the size of the gap, per vault, in USD

This number determines how urgent O1 and O3 are, and is required input for O4.
Until it exists, the severity is asserted rather than measured.

### O3 — Sweep and republish

Convert the Safe's stranded ETH/WBTC/BNB back to USDC and post a corrected NAV.

Two things to check before attempting this:

- Native ETH cannot be moved by an ERC-20 transfer or swap. It needs a
  `WETH.deposit()` wrap first, which the **Guard must whitelist**. Confirm before
  planning the route.
- The Safe's native ETH balance doubles as its **gas float** for GMX execution
  fees (`eth_defi/gmx/lagoon/wallet.py:298`). Do not sweep it to zero.

### O4 — Historical equity curve

Every past profitable long close produced an artificial downward step. Decide
between backfilling a corrected series from onchain history and resetting the
curve with a documented discontinuity. Backfill is preferable if the payout
transfers are recoverable from Hypersync event history; per `CLAUDE.md`, use
Hypersync rather than `eth_getLogs` for this.

## Code fix plan

### Phase 0 — Sync

Pull latest `master` on **both** this repo and trade-executor before starting.
Non-negotiable here because Phase 5 is cross-repo and the NAV caller is not in
this tree.

### Phase 1 — Reproduce before fixing

Failing test first, per `superpowers:test-driven-development`. Must use the
shared Anvil fork pattern documented in the module docstring of
`eth_defi/testing/anvil_fork_pool.py` — shared `anvil_fork_pool` fixture, the
chain's `*_MIDNIGHT_BLOCK` constant, `xdist_group` marker, `evm_snapshot_revert`
for isolation.

- Open a long ETH/USD with USDC collateral through the Lagoon Safe path.
- Drive it into profit; execute with the keeper harness in
  `eth_defi/gmx/testing/keeper.py` (whose docstring at line 33 already documents
  the `shouldUnwrapNativeToken = True` receiver behaviour).
- Close.
- Assert the **broken** state: Safe native ETH increased, Safe USDC increased by
  collateral only, and `fetch_gmx_total_equity()` total *lower* after the close
  than before.

This must fail on `master` before anything is changed. It is the regression gate.

### Phase 2 — Payout fix (Defect A, source)

- `eth_defi/gmx/order/base_order.py`: add `decrease_position_swap_type` and
  `should_unwrap_native_token` fields to the `OrderParams` dataclass
  (`class OrderParams` at line 137; `swap_path` at 152 is the neighbouring
  precedent), defaulting to `swap_pnl_token_to_collateral_token`
  (value `1`, already defined at `eth_defi/gmx/constants.py:352`) and `False`.
  Replace the literals at lines 707 and 709 with the field values.
- `eth_defi/gmx/order/sltp_order.py`: same substitution at 475 and 477 so SL/TP
  closes inherit identical behaviour. Confirm the SL/TP path routes through the
  same `OrderParams`; if it builds its own tuple, thread the fields explicitly.
- `eth_defi/gmx/order/decrease_order.py`: expose both as optional arguments on
  `create_decrease_order()` (line 62), pass through at line 112.
- Leave increase orders alone — `decreasePositionSwapType` is ignored on
  increases.
- Audit `eth_defi/gmx/ccxt/` and `eth_defi/gmx/freqtrade/` close paths, plus
  `eth_defi/gmx/utils.py:593`, for independent argument construction that would
  bypass the new default.

Configurable rather than a new hardcoded constant — hardcoding a different value
repeats the original mistake in the opposite direction.

### Phase 3 — NAV reserves (Defect A, defence in depth)

- `eth_defi/gmx/valuation.py`: replace the blanket assert at line 126 with two
  buckets — stablecoins summed at face value, non-stablecoins converted via
  `_get_mark_price()` (line 376). The pricing machinery already exists and is
  already used for position collateral in `_collateral_to_usd()` (line 337); only
  the reserve loop refuses to use it.
- Include the Safe's **native ETH** balance. Recommend counting it in full and
  treating gas as an expense when spent, rather than inventing a gas-reserve
  heuristic — but note the O3 caveat that it must not be swept to zero.
- Keep `GMXEquity`'s shape; consider a third field separating stable from
  non-stable reserves for observability.
- Update the module docstring, which currently states "Designed for
  USDC-collateralised accounts" as a hard constraint.

Phase 2 alone is not sufficient: execution-fee refunds, claimable funding fees
and dust will still deposit non-USDC value into the Safe.

### Phase 4 — Net position value (Defect B)

- Replace `Reader.getAccountPositions()` (`valuation.py:214`) with
  `getAccountPositionInfoList()`, or `getPositionInfo()` per position. Both are
  already in `eth_defi/abi/gmx/Reader.json`.
- Use `positionValueInUsd` as the position value, or build it explicitly from
  `pnlAfterPriceImpactUsd` and the `fees` struct if a breakdown is wanted for
  observability.
- This deletes most of `_calculate_position_value()` (247-335), including the
  hand-rolled entry-price arithmetic, the oracle-mid derivation and the
  `_WSTETH_MARKET` special case at 155-163 — verify the Reader path covers the
  wstETH zero-index-token case before removing it.
- Note these Reader calls need market and token price inputs; confirm what the
  signature requires and where those prices come from at a historical block.
  Our current oracle source is the **live** signed-prices API, which is already a
  documented limitation of historical valuation (`valuation.py` module docstring)
  and should not be quietly worsened.

### Phase 5 — Cross-repo (trade-executor)

Phases 3 and 4 are **inert without this**. The NAV caller lives in
trade-executor. Once `fetch_gmx_total_equity()` accepts non-stable reserves, that
caller must pass WETH/WBTC/BNB and opt into native ETH. Landing Phase 3 alone
looks like a fix while NAV stays blind.

### Phase 6 — Guard and whitelist review

- Confirm the Guard permits the Safe to receive WETH once
  `shouldUnwrapNativeToken` is `False`. `setup_gmx_whitelisting()`
  (`eth_defi/gmx/whitelist.py:443`) whitelists the Safe via `allowReceiver()`;
  verify no per-token restriction blocks a WETH inbound.
- If the O3 sweep route needs `WETH.deposit()` or a swap router, scope the Guard
  changes here.

### Phase 7 — Documentation

- Correct `eth_defi/gmx/README-GMX-Lagoon.md:125` and the close section at
  145-171 to match actual behaviour, and state the PnL-token rule explicitly with
  a link to the GMX payout documentation.
- `CHANGELOG.md` entry dated `2026-08-20`.
- `docs/source/api` stubs for any new public symbol.

## Test plan

- Phase 1 test inverted: after the fix the close returns USDC only, Safe native
  ETH is unchanged bar gas, and NAV is preserved across the close within fee
  tolerance.
- **Short close, asserting no behaviour change** — the main regression risk from
  touching a shared close path.
- Losing-long close: no secondary output, no NAV anomaly.
- SL/TP take-profit close specifically — highest-exposure path.
- `valuation.py` reserve branch: WETH reserve prices via oracle, stablecoin
  reserve does not, unpriceable token **raises** rather than silently
  contributing zero.
- Phase 4: assert `positionValueInUsd` is strictly below the old gross figure for
  a position with accrued borrowing/funding fees, quantifying the old overstatement.
- Fixed-block absolute-value assertions per repo convention, not
  greater-than-zero.
- `source .local-test.env && poetry run pytest ...` with `timeout: 180000`.

## Risks

- **Shorts regressing.** `SwapPnlTokenToCollateralToken` *should* be a no-op when
  PnL token equals collateral token, but that must be proven on a fork, not
  assumed.
- **Swap failure at close.** If the market lacks liquidity to convert PnL to
  collateral, understand GMX's fallback before merging — a close that reverts is
  worse than a close that pays in the wrong token.
- **NAV discontinuity on deploy.** Phase 3 makes NAV **jump up** (previously
  invisible ETH now counted); Phase 4 makes it **drop** (fees now deducted). On a
  live vault both are abrupt share-price moves. Sequence deliberately, behind the
  O1 freeze, and tell the operator the expected direction and magnitude from O2
  beforehand. Do not let this surprise anyone.
- **Partial deploy.** Phase 2 without Phase 3 leaves residual non-USDC value
  uncounted. Phase 3 without Phase 2 leaves the vault accumulating unhedged
  exposure. Phases 3/4 without Phase 5 change nothing observable.
- **Gas starvation.** The Safe currently receives incidental native ETH that tops
  up its gas float (`eth_defi/gmx/lagoon/wallet.py:298`). Setting
  `shouldUnwrapNativeToken = False` removes it. Verify the execution-fee funding
  path does not depend on that.
- **Historical valuation accuracy.** Oracle prices are live, not
  block-historical. Phase 4 must not deepen that gap silently.

## Open decisions

Reduced from seven. These still need answers.

- **D1 — Sweep scope and route (O3).** Which tokens, what route, and how much
  native ETH to retain as gas float. Blocked on O2's numbers.
- **D2 — Native ETH in NAV.** Count in full (recommended) or net off a documented
  gas reserve.
- **D3 — Equity curve: backfill or reset (O4).** Backfill preferred if Hypersync
  history makes it tractable.
- **D4 — Deploy sequencing.** Whether Phases 3 and 4 land together so the two
  opposing NAV corrections net out in a single share-price step, or separately
  with two announced moves. Recommend **together, behind the freeze**.
- **D5 — File as a GitHub issue.** Per `docs/agents/issue-tracker.md`, on
  `tradingstrategy-ai/web3-ethereum-defi`. I checked the open issue list for
  GMX/PnL/profit/swap/WETH/NAV — nothing existing covers this. Recommend one
  P0 issue with Defect A and Defect B as linked children.

Settled since the first revision: longs use USDC collateral and no swap path
(was D6 — the README is wrong, not the code); fix layer is both payout and
accounting (was D1); swap type is configurable defaulting to
`swap_pnl_token_to_collateral_token` (was D2); `shouldUnwrapNativeToken` becomes
`False` (was D3); the atomic swap cost is accepted as cheaper than an
out-of-band sweep (was D4).

## Related

- `eth_defi/gmx/valuation.py` — NAV implementation, both defects
- `eth_defi/abi/gmx/Reader.json` — already contains `getPositionInfo`
- `eth_defi/gmx/README-GMX-Lagoon.md` — Guard architecture; **contains the
  incorrect swap-path description**
- `eth_defi/gmx/testing/keeper.py` — keeper harness for fork tests
- `eth_defi/testing/anvil_fork_pool.py` — mandatory fork-test pattern
- `docs/agents/issue-tracker.md` — where to file this
