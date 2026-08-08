# Vault simulation round 3 hand-off plan

## Delivery status

This document remains the cross-repository completion plan; it is not a claim
that every matrix row is implemented by this PR. The current eth-defi changes
deliver the shared `preflight_result`/unsupported-settlement contract and
focused paths for Ember Third Eye, Accountable Hyperithm, cSuperior, Gains
Arbitrum, Plutus Hedge, and generic ERC-7540. Exact-address coverage for the
remaining Ember, Gains Base, and Lagoon matrix rows remains pending, as does
the trade-executor matrix consumer and final report.

## Purpose and boundary

This is the binding eth-defi hand-off for the trade-executor acceptance matrix
in [trade-executor#1578, comment 5078633641](https://github.com/tradingstrategy-ai/trade-executor/pull/1578#issuecomment-5078633641).
It supersedes the earlier suggestion-led approach. The work is complete only
when the trade-executor matrix reports an allowed result for every exact vault
ID below and no listed row has `transaction_reverted`, `broadcast_failed`,
`execution_failed`, or `receipt_analysis_failed`.

Eth-defi owns adapter behaviour, ABI coverage, capabilities, structured
preflights, settlement proof and exact-vault fork tests. Trade-executor owns
the matrix runner, result-string mapping, report persistence, and the final
end-to-end command. Neither side may substitute a same-ABI deployment for an
ID in this document.

Eth-defi acceptance is independently complete when its exact-address focused
tests pass and expose the schema below. The cross-repository programme is
complete only after trade-executor consumes the released eth-defi revision and
passes the final matrix. This distinction lets an eth-defi PR be reviewed on
its own evidence without weakening the final matrix gate.

The contract between the repositories is:

- A predictable protocol state must raise `VaultFlowUnavailable`,
  `WhitelistingRequired`, or `UnsupportedVaultSimulation` *before* broadcast.
  Populate `protocol`, `vault_address`, `direction`, `phase`, and the relevant
  decoded error and raw amount fields.
- `supports_anvil_settlement=True` is a proof claim. A matching asynchronous
  ticket passed to `force_settle()` must end with
  `status_after is AsyncVaultRequestStatus.claimable`; assert that in the
  manager and raise `UnsupportedVaultSimulation` if it cannot be achieved.
- A protocol which cannot safely settle on Anvil must publish
  `supports_anvil_settlement=False` and a stable concrete unsupported reason.
  Trade-executor must emit `simulation_unsupported_async` before it builds or
  broadcasts a settlement transaction.
- `supports_anvil_settlement=None` means the *selected direction* is
  synchronous. Trade-executor follows the ordinary request/receipt analyser
  path and must not call `force_settle()`. For a mixed manager, inspect the
  selected direction's flow first: the flag is relevant only when that flow is
  asynchronous. `False` therefore must not block an independent synchronous
  direction of the same manager. This directional inspection is already
  implemented in trade-executor's `has_async_vault_lifecycle`; preserve it
  while adding the preflight-result consumer.
- `VaultForcedSettlementResult.synthetic_assets_injected_raw` already exists
  in eth-defi revision `38fa4f945`. Managers must keep populating it and
  trade-executor must copy it unchanged to the same report field. Forced-fork
  asset top-ups must therefore be exposed in the report as
  `synthetic_assets_injected_raw > 0`. This is test-only liquidity, not a
  statement about live redemption capacity.

### Stable preflight-to-result mapping

Do not infer results from exception prose. Add the optional
`preflight_result: str | None` field to `VaultFlowError` and serialise it with
the existing structured fields. During the cross-repository migration every
adapter must set both its protocol-level `decoded_error` and the exact
result-string `preflight_result` below. Trade-executor must prefer a present
`preflight_result`, then use its existing `decoded_error` mapping as a
backwards-compatible fallback. The fallback remains until the minimum eth-defi
dependency includes this new field. This is the authoritative mapping:

| Eth-defi signal | Required structured evidence | Result string |
|---|---|---|
| `WhitelistingRequired`, or `VaultFlowUnavailable(preflight_result="whitelisting-needed")` | `direction`, `phase`, protocol and vault address; access data when available | `whitelisting-needed` |
| `VaultFlowUnavailable(preflight_result="below_minimum")` / `decoded_error="InsufficientAmount"` | `minimum_raw_amount`, `requested_raw_amount`, direction | `below_minimum` |
| `VaultFlowUnavailable(preflight_result="redemption_capacity_limited")` / `decoded_error` `WithdrawalPending` or `ExceededMaxRedeem` | requested and available raw shares/amounts, direction=`redeem` | `redemption_capacity_limited` |
| `VaultFlowUnavailable(preflight_result="redemption_paused")` / `decoded_error="WithdrawalsArePaused"` | direction=`redeem`, protocol and vault address | `redemption_paused` |
| `VaultFlowUnavailable(preflight_result="redemption_window_closed")` / `decoded_error="EndOfEpoch"` | direction=`redeem`, `next_open` | `redemption_window_closed` |
| `VaultFlowUnavailable(preflight_result="redemption_unavailable")` | direction=`redeem` and concrete protocol reason | `redemption_unavailable` |
| `VaultFlowUnavailable(preflight_result="deposit_closed")` | direction=`deposit` and concrete close/cap reason | `deposit_closed` |
| `UnsupportedVaultSimulation` with selected async flow and `supports_anvil_settlement=False` | concrete `unsupported_reason`, protocol, vault and direction | `simulation_unsupported_async` |

`success (simulated)` is emitted only after a synchronous receipt analyser
succeeds, or an asynchronous request has a proved `claimable` forced-settlement
result and its claim receipt analyser succeeds. Any signal outside this table
is a defect to fix in eth-defi or explicitly map in a future version; it may
not fall through to a forbidden generic failure result.

`VaultForcedSettlementResult` already provides
`synthetic_assets_injected_raw: int = 0`. The manager returns the exact raw
amount injected by its own Anvil setup, and trade-executor writes the same
numeric field and name in each `report.json` row. A successful forced
settlement that required top-up must have a value greater than zero; a path
that needed none records zero.

## Acceptance matrix

The following exact IDs are the required eth-defi test cases and final
trade-executor inputs. “Typed” means a pre-broadcast exception with the
structured data named in the work order; it must not reach an RPC broadcast.

| # | Exact `vault_id` | Adapter work and allowed result |
|---:|---|---|
| 1 | `1-0x9be9294722f8aad37b11a9792be2c782182cafa2` | Ember Earn: proven successful redemption lifecycle, or false settlement capability with typed `simulation_unsupported_async`. |
| 2 | `1-0x0b9342c15143e8f54a83f887c280a922f4c48771` | Ember Polymarket: same as #1. |
| 3 | `1-0xf3190a3ecc109f88e7947b849b281918c798a0c4` | Ember Third Eye: same as #1. |
| 4 | `1-0x373152feef81cc59502da2c8de877b3d5ae2e342` | Ember UDL: same as #1. |
| 5 | `1-0x2b13311fd553e74b421d4ccc96e348f71e179dcf` | Ember Apollo ACRED: typed redeem `below_minimum`, including `minimum_raw_amount` and `direction="redeem"`. |
| 6 | `1-0x438982ea288763370946625fd76c2508ee1fb229` | cSuperior: typed `redemption_capacity_limited` or `redemption_paused` before a redeem transaction is built. |
| 7 | `143-0x7cd231120a60f500887444a9baf5e1bd753a5e59` | Accountable Hyperithm: decode `0x5945ea56` on this address and return typed `below_minimum`, `whitelisting-needed`, or typed unsupported. |
| 8 | `42161-0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0` | Gains gTrade: `redemption_window_closed` with decoded `EndOfEpoch` and `next_open`, or successful lifecycle. |
| 9 | `8453-0xad20523a7dc37babc1cc74897e4977232b3d02e5` | Gains gTrade Base: successful lifecycle with settlement proof, or false-capability typed async unsupported. |
| 10 | `8453-0x2bff679b1a9fbcc202316c1402172747ba2fbf56` | Lagoon For Yield v2: successful lifecycle or typed unsupported; never an allowance broadcast failure. |
| 11 | `8453-0x63b04d3ce2c14f6d308657ab73ac92fc1a0b1075` | Lagoon RB Capital: same as #10. |
| 12 | `8453-0xbe7db44f4ce20dac83b578b94fd35087f66e9754` | Lagoon TruMarket: same as #10. |
| 13 | `1-0xa00f63e85b3d242568a9edecb48f5e2cf879b07b` | Lagoon Moon Digital: successful lifecycle or typed unsupported, never `pending -> pending`. |
| 14 | `42161-0x1723cb57af58efb35a013870c90fcc3d60174a4e` | Lagoon Angmar: same as #13. |
| 15 | `42161-0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a` | Plutus Hedge: successful lifecycle with proof, or false-capability typed async unsupported. |

Regression controls: Syntropia
(`1-0xd17049ed25d8f99fe3bfd10cef2263da9995cfd8`) remains
`success (simulated)`; cSigma USD
(`1-0xd5d097f278a735d0a3c609deee71234cac14b47e`) and YieldNest
(`1-0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8`) remain
`redemption_capacity_limited`. Upshift Sentora is explicitly executor-side
`incompatible_deposit_asset`, not an eth-defi target in this change.

## Implementation sequence

### 1. Establish a repeatable exact-address baseline

1. Add a dedicated round-3 parametrised test module under
   `tests/erc_4626/vault_protocol/`, split by chain if that makes fixtures
   clearer. Keep a table containing every full `chain_id-address` identifier,
   protocol, fork block, requested direction and required outcome.
2. Use `AnvilForkPool`, the matching `*_MIDNIGHT_BLOCK`, matching
   `xdist_group`, and `evm_snapshot_revert` for every mutating mainnet-fork
   test. Never launch a private `latest` fork. Test an exact target address in
   each case; existing representative tests are supplementary only.
3. Monad has no archive-complete historical state. Use the exact Accountable
   address at a recorded provider-supported recent block and state-relative
   assertions, retaining the observed block and selector in failure output.
   Do not attempt an old-state backfill or replace it with another Accountable
   deployment. This is the repository-required exception to fixed historical
   value assertions.
4. Capture, per target, the request ticket, status before/after, thrown typed
   fields, every transaction hash, and fork top-up amount. The test must fail
   if an unexpected broadcast occurs, a known selector is undecoded, or a
   forced settlement returns while still pending.

### 2. Make the generic contract enforceable

1. In `eth_defi/vault/deposit_redeem.py`, add `preflight_result` to the
   structured error schema and add schema tests for the dual-field migration
   mapping above. `synthetic_assets_injected_raw` is already present in
   `VaultForcedSettlementResult` from `38fa4f945`; retain and populate it, do
   not add a duplicate field. Document and validate capability publication so
   `True` is only published by implementations that own a matching ticket-level
   `force_settle()` proof. Keep `None` for a selected synchronous flow; use
   `False` plus a stable `unsupported_reason` for an advertised asynchronous
   flow that has no safe driver.
2. Add a reusable helper or focused contract tests for the forced-settlement
   result: it must carry the original ticket, `settlement_required=True`,
   transaction hashes when a settlement transaction was attempted, and
   `status_after == claimable`. Raise `UnsupportedVaultSimulation` with the
   protocol, exact vault, direction and `unsupported_reason` otherwise; do not
   return an ambiguous pending result.
3. Audit predictable adapter-boundary failures in the target managers. Replace
   request-time `assert`, `ValueError`, missing ABI function and known custom
   revert paths with structured exceptions. Keep programmer-contract asserts
   only where inputs cannot originate from user simulation state.
4. Add exact decoding coverage for `0xa73449b9` (`EndOfEpoch`),
   `0xb8b8b59c` (`ExceededMaxRedeem`), `0xb34f5c6c` (`WithdrawalPending`),
   `0x5945ea56` (`InsufficientAmount`), and Plutus
   `UseRequestRedeem`/`WithdrawalsArePaused`. The raised exception must include
   the selector/name in `decoded_error` and all meaningful raw amount/window
   fields.

### 3. Repair Ember and Accountable exact-address paths

1. In `ember/deposit_redeem.py`, convert paused withdrawals, below-minimum
   redemption shares, balance/capacity checks and every predictable queue
   refusal into structured typed exceptions. In particular, mirror the
   `InsufficientAmount` treatment on the redemption path for Apollo ACRED;
   include `minimum_raw_amount`, `requested_raw_amount`,
   `direction="redeem"`, `phase="preflight"`, protocol, vault address and
   `preflight_result="below_minimum"`.
2. Add an Ember Anvil settlement implementation only after proving, for each
   #1–#4 target, that the actual operator transaction changes that ticket from
   pending to claimable/terminally payable. The driver itself must re-read the
   ticket and enforce the status assertion. If operator privileges or the
   off-chain queue make that impossible, publish `False` with the concrete
   reason and make `force_settle()` raise typed unsupported before any
   speculative broadcast. Set `preflight_result`/unsupported reason to the
   exact false-capability contract. Do not advertise a partial driver as
   success.
3. In `accountable/deposit_redeem.py`, perform the Hyperithm exact-address
   minimum/admission/access preflight before `deposit()` and decode
   `0x5945ea56` as `InsufficientAmount`. Populate `minimum_raw_amount`,
   requested amount, direction, phase and `preflight_result="below_minimum"`.
   If Monad's present state makes the vault unusable, return an address-scoped
   typed unsupported condition instead of testing the same ABI elsewhere.
4. Extend `test_ember_deposit_redeem.py`, `test_ember_settlement.py`, and
   `test_accountable.py` with the exact target IDs and direct assertions on
   every reported structured field and no-broadcast guarantee.

### 4. Repair cSuperior, Gains and Plutus preflights

1. In `csigma/deposit_redeem.py`, make the cSuperior exact pool’s
   `maxRedeem(owner)`/queue state the authoritative preflight. A pending FIFO
   withdrawal must produce `VaultFlowUnavailable(decoded_error="WithdrawalPending",
   preflight_result="redemption_capacity_limited")` before construction of
   `redeem()`, with requested and available raw shares. If the authoritative
   state is a pause instead, use `preflight_result="redemption_paused"`.
   The fixed acceptance matrix deliberately maps the FIFO queue state to
   `redemption_capacity_limited`; preserve `decoded_error="WithdrawalPending"`
   so consumers do not mistake queueing for economic capacity exhaustion. A
   future `redemption_queued` result needs a deliberate cross-repository matrix
   change, not an ad-hoc new result here. Retain the current cSigma USD
   behaviour as a regression case.
2. In `gains/deposit_redeem.py` and its vault helpers, turn `EndOfEpoch` and
   `ExceededMaxRedeem` into typed request refusals. Set
   `preflight_result="redemption_window_closed"` for `EndOfEpoch` and expose
   `next_open`; use `redemption_capacity_limited` for `ExceededMaxRedeem`.
   Remove user-state assertions from the request path. Cover the exact Arbitrum
   address (#8) and inspect the Base deployment (#9) rather than assuming its
   ABI/version behaviour matches Arbitrum.
3. In the Plutus manager/vault, decode `UseRequestRedeem` and
   `WithdrawalsArePaused`; map the latter to `redemption_paused`. Either supply
   a proved ticket settlement path or publish false Anvil capability with a
   concrete role/queue reason. The exact Hedge vault must never defer into an
   onchain revert.
4. Add exact-target tests in `test_csigma.py`, `tests/gains/`, and
   `test_plutus.py` that prove the accepted result and inspect exception fields
   or `status_after` rather than merely asserting an exception type.

### 5. Make Lagoon settlement deployment-aware

1. Keep `LagoonDepositManager.force_settle()`'s post-settlement claimable
   assertion as mandatory, but test #10–#14 individually. Verify the exact
   vault version, safe, valuation manager, asset, allowance target and
   settlement role before setting any capability claim.
2. In the Anvil settlement path, provision the approval required by the exact
   `settleDeposit()` flow from the impersonated Safe/manager. Re-read allowance
   and surface a typed pre-broadcast unsupported condition when the fork cannot
   make that approval or role transition. Do not let a failed settlement
   transaction become `broadcast_failed`.
3. If an exact Lagoon deployment cannot be safely driven because of an
   operator-only/off-chain component, publish false capability for that
   deployment (not a blanket success claim) and raise
   `UnsupportedVaultSimulation` with the role/queue reason before settlement.
4. Add exact-ID deposit and redemption lifecycle tests alongside
   `tests/lagoon/test_erc_7540_deposit_redeem.py`. Each successful test must
   assert pending → claimable, claim completion, receipt analysis and
   `synthetic_assets_injected_raw > 0` where a fork top-up was needed.

### 6. Consumer integration and release hand-off

1. Publish the eth-defi branch/revision only after the focused exact-vault
   tests pass. Give the trade-executor agent the revision, this matrix, and a
   machine-readable fixture/report containing capability, exception fields,
   selectors, ticket statuses and synthetic liquidity values.
2. In trade-executor, consume the typed exceptions before generic error
   handling. Prefer `preflight_result` and map it only through the
   authoritative table above; retain the existing `decoded_error` mapping as a
   fallback until all supported eth-defi revisions expose `preflight_result`.
   Do not change the existing direction-aware `has_async_vault_lifecycle`
   handling: honour false capability before calling a settlement driver, while
   a selected synchronous direction (`None`) uses normal receipt analysis and
   skips `force_settle()`. Retain every raw structured field, the capability
   fields, `unsupported_reason` and `synthetic_assets_injected_raw` verbatim in
   `report.json`.
3. Run the mandatory command from the work order against all fifteen IDs in
   one state directory. Paste the per-vault report table into the PR, including
   success settlement status and synthetic liquidity or typed refusal fields.
   Re-run the three accepted-now controls in the same dependency revision.

## Verification and review gates

Before implementation review, run focused eth-defi tests with
`source .local-test.env && poetry run pytest …` (with a three-minute tool
timeout), never the complete suite. At minimum cover the dedicated round-3
module plus Ember, Accountable, cSigma, Gains, Plutus and Lagoon targeted
modules. Run Ruff formatting before a PR.

Before requesting final review, trade-executor must run:

```shell
source .local-test.env
ASSET_MANAGEMENT_MODE=lagoon \\
PYTHONPATH="$PWD:$PWD/deps/web3-ethereum-defi" \\
poetry run trade-executor vault-test-trade \\
  --id acceptance --state-file /tmp/acceptance/state.json \\
  --auto-simulated --settle-async-on-anvil --amount 1.0 \\
  --vault-id "<all 15 IDs above>" \\
  --report-json /tmp/acceptance/report.json
```

Review checklist:

- [ ] All fifteen exact IDs have a focused test and a final report row.
- [ ] No report row has one of the four forbidden failure strings.
- [ ] Every true Anvil capability has an in-code `claimable` postcondition and
  a target-specific proof.
- [ ] Every unreproducible async lifecycle is false-capability and typed before
  broadcast, with its concrete unsupported reason asserted.
- [ ] All six named protocol selectors/errors decode to structured fields.
- [ ] Synthetic liquidity and the three accepted-now regression results are
  present in the final report.
