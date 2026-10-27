# Fable review of the three-chain manual test plan

- Date: 2026-10-27.
- Branch: `plan-lagoon-three-chain-roundtrip`, based on merged PR #1668.
- Model: explicitly selected `claude-fable-5-1`; both smoke test and completed review returned successful result events with this model in usage metadata.
- Method: complete numbered plan supplied inline with tools disabled, after primary-agent repository inspection, following `.claude/docs/agent-tricks-and-troubleshooting.md`.
- Invocation: `timeout 900 claude -p "<complete numbered plan and review instructions>" --model claude-fable-5-1 --effort high --tools '' --safe-mode --permission-mode dontAsk --no-session-persistence --output-format stream-json --verbose --include-partial-messages`.
- Scope: missing steps, money movement, restart handling, simulation and sensible defaults; explicitly requested minimal corrections and no overengineering.

## Findings as returned

These are the reviewer's original claims, not all accepted findings. Line numbers refer to the initial plan. Corrections and evidence follow below.

**Verdict up front:** the plan is sound in shape, but five items would break or stall a default live run. Fix those before implementation. The rest are small tightenings.

## Blockers

- **Default fee ceiling kills the Ethereum return leg (lines 49, 219-222).** Circle's Fast Transfer fee from Ethereum as source is far higher than from Arbitrum, on the order of 14 bps versus 1 bps. On roughly 11 USDC that exceeds the 0.01 ceiling, so day two aborts by design with funds stranded on Ethereum. Smallest fix: use the standard route for the Ethereum return, which has no fee and a wait of under twenty minutes, or make the ceiling per direction and set a sane Ethereum default. Also make a ceiling breach return a pending outcome with the override instruction, not a hard abort.

- **Core actions can fail silently and the plan then blocks forever (lines 159-160, 193-194, 285).** A CoreWriter vault transfer or perp transfer can be accepted by the EVM and rejected on Core with no tx ID to query. Line 194's "never repeat" rule is right for burns and Lighter operations but wrong for Core. Smallest fix: persist a Core balance snapshot before each Core action, define settled as the observed delta appearing within a bounded wait, and permit one retry only when the source balance proves non-execution.

- **Final NAV must exclude satellite residuals or settlement reverts (lines 225-236).** If the posted NAV includes dust still on HyperEVM or Ethereum, the redeem settlement pulls more than the Arbitrum Safe holds. Define NAV as the Arbitrum Safe's native USDC only, define "material" with a number such as residual above the CCTP max fee, and let redemption proceed with the residual disclosed. Also split cost from residual: the Hypercore activation fee is a consumed cost, not an unrecovered balance, otherwise nearly every real run reports incomplete.

- **Lighter signed payloads expire, so "resubmit the persisted payload" is not always possible (lines 164-165, 194, 214-217).** Lighter transactions carry an expiry. Amend line 194: regenerate a Lighter operation only when the tx hash lookup returns not found and the persisted API-key nonce is observed unconsumed. Also persist the withdrawal history baseline before submission so restart matching uses the stored baseline, not a fresh one contaminated by the earlier attempt.

- **Full-balance burn arithmetic is misstated (lines 220-222).** CCTP v2 takes the burn amount and a max fee, and the fee quote is a share of the burn amount, not of a net figure. Quoting on B minus max fee then rebuilding gross can land a cent off either way. Replace with: burn amount B, max fee quoted on B, reject if max fee exceeds the ceiling or B is at or below the max fee.

## Should fix

- **Pre-broadcast persistence is underspecified (lines 94-95, 185-186).** Record the signed raw tx hash and nonce before every broadcast on all three chains. Then a crash between send and persist recovers by nonce lookup without scanning.
- **Core USDC uses 8 wei decimals, HyperEVM USDC uses 6 (lines 207-208, 227).** Round the sendAsset amount down to a 6-decimal representable value and record the sub-cent remainder as Core dust.
- **Activation credit location is unstated (lines 154-155, 206).** Record whether the activation credit lands in spot or perp and whether a creation fee was consumed, then sweep both classes on exit.
- **Lighter mint is denominated in shares, not USDC (line 163).** Convert the 10 USDC target with the current share price and a price bound, then record the actual collateral debited rather than assuming 10.
- **Day-two gas is not rechecked (lines 189-191).** Add native gas verification per chain to unwind reconciliation. Ethereum claim and burn can fail on a drained deployer.
- **Big-block setting after a crash (line 137).** Record the toggle in state so a restart restores it even if day one died mid-deploy.
- **Interrupted deploy has no continuation path (lines 169-170, 187, 196).** Fine as a simplification, but state it: an interrupted deploy is only ever unwound, and unwind on a run with nothing funded must be a read-only success rather than an error.
- **Simulation accepts only `all` (lines 42, 273).** Reject any other action under `SIMULATE=true` and skip the big-block Core call on the fork.

## Optional

- **One Lighter deposit instead of two (lines 161-162).** If the activation helper accepts an amount, a single 11 USDC deposit halves the sequencer waits and credit checks.
- **Minimums in preflight (lines 122-125, 147).** Read any vault or pool minimum deposit and any secure withdrawal minimum from the APIs and abort before deployment if the 10 USDC target violates them.
- **Refresh the Lighter API nonce on restart (line 190).** Treat it like a wallet nonce.
- **Approval is a tx boundary (line 139).** Record the USDC approve separately from the deposit request.

## Residual risks after fixes

- Attestation or sequencer delays can leave a leg pending across several invocations. The plan handles this but a real run may need more than two days.
- The selected Hypercore vault may close deposits or lose capacity between discovery and entry. Line 128's stop-and-retain is correct but leaves funds parked on HyperEVM until unwind.
- Lighter custody for a Safe depends on the existing registration machinery from the merged pull request. Simulation mocks that boundary, so only the funded run proves it.
- Investment PnL and leader profit share mean the returned amount will not match principal. The plan already says so.

## Assessment and changes

- **Ethereum fee claim rejected.** Read-only Circle Iris queries on 2026-10-27 returned Fast Transfer fees of 1.4 bps for Arbitrum → Ethereum and Arbitrum → HyperEVM, 1 bps for Ethereum → Arbitrum, and standard 0 bps for HyperEVM → Arbitrum. At an approximately 11-USDC Ethereum return, 1 bps is approximately 0.0011 USDC, below the 0.01 ceiling. This agrees with [Circle's fee documentation](https://developers.circle.com/cctp/concepts/fees). Retain Fast Transfer, fresh quotes and the ceiling. Accept the recommendation to report a later ceiling breach as pending with an explicit override instruction.
- **Full-balance arithmetic claim rejected.** `eth_defi/cctp/fees.py:fetch_cctp_fee_quote()` computes `amount_raw` from `net_amount + max_fee` and calculates fees on that gross amount. Supplying `net_amount = B - max_fee` therefore yields exactly `B`. Added an explicit raw-balance equality assertion and instruction to pass returned raw fields directly. No new quote abstraction is needed.
- **Core uncertainty concern partially accepted.** Persist before/after observations and explicit failure evidence. Do not accept the proposed retry based only on unchanged source balance: a queued action may still execute later. Unknown outcomes stop the affected leg for manual recovery. No automatic retry framework.
- **Residual accounting accepted.** The existing Hypercore fee helper reserves 0.01 USDC, and its documented observed actual fee was smaller. Explicitly separate spent fees from leftover funds. Add a total 0.01-USDC cash-dust limit, only after positions and receivables are resolved; disclose/write down bounded non-recoverable dust and return the remaining capital through Lagoon. Use a distinct `redeemed_with_dust` result, not an unqualified full-return claim. Zero ceiling requires an exact drain.
- **Lighter expiry/history concern partially accepted.** Save the withdrawal baseline before submission, track expiry and reconcile original operations. Reject replacement based merely on a missing hash and unchanged nonce; these can reflect indexing delay. No automatic re-signing added.
- **Accepted small additions:** pre-broadcast EVM evidence including approvals; Core/EVM unit conversion; activation credit location and actual consumed fee; actual Lighter collateral debit; day-two gas checks; recorded big-block restoration; explicit simulation-only `all`; no funded deployment continuation; protocol minima and API-nonce reconciliation; settlement cap allowing positive PnL.
- **Price-bound suggestion rejected as stated.** Lighter mint uses integer shares and has no signed USDC slippage parameter. Current sizing and observed debit are documented without promising an enforceable price bound.
- **Single-deposit activation deferred.** Existing `activate_lighter_safe()` deposits the fixed supported minimum and accepts no amount argument. Reusing it plus one additional custody deposit keeps this tutorial scoped; avoid modifying activation solely to save one call.
- **Additional primary-agent correction:** interrupted Lighter activation must permit existing-key recovery or guarded L1 withdrawal without requiring a successfully registered API key. Do not lose access to the deposit by assuming activation completed.

No implementation, fork test or funded mainnet transaction was performed during this plan review. The Circle queries above were public, read-only API calls.

## Final review

Re-reviewed the complete updated plan using `claude-fable-5-1`, medium effort, with the verified helper semantics and live fee observations supplied inline. The CLI returned a successful final result and confirmed the requested model.

No blocking findings.

The plan is consistent with the verified helper semantics: the full-balance return quote (`net = B - max_fee`, assert `amount_raw == raw_balance`), the 0.01 USDC fee ceiling against current 1.4/1/0 bps quotes, the 23.02 USDC outbound gross with 26.98 USDC remaining, the separate 1 USDC Lighter activation followed by a distinct 10 USDC deposit, the bounded and disclosed Core fee-reserve residual, no automatic Core retries, and the explicit unbounded-slippage disclosure for Lighter signed share mints. Recovery, state persistence and simulation boundaries are specified tightly enough to implement without further design decisions.

Residual risk: correctness still rests on live behaviour that cannot be verified from the repo, chiefly Hypercore activation fee and deposit minima, Lighter pool capacity and secure-withdrawal settlement latency, and Circle Fast Transfer allowance on the day, so the first funded two-day run remains the real validation, with Ethereum gas rather than USDC fees dominating the cost of the test.

## Subsequent minimum-funding check

After the Fable review, the user requested a minimum-USDC check. The primary
agent verified the repository's 5-USDC Hypercore vault-entry constant, 2-USDC
activation allocation and Lighter 1-USDC direct-deposit/secure-withdrawal minimum,
and queried all four Circle fee routes again on 2026-10-27. The plan now records
23.02 USDC as the startup floor for its two 10-USDC positions, reduces the default
subscription from 50 to 25 USDC, and changes the idle hub balance from 26.98 to
1.98 USDC. A conditional 5-USDC-per-position configuration needs 13.02 USDC;
15 USDC is a rounded funding envelope for that optional configuration. Gas is
separate and simulation requires no real funds. This budget clarification was
arithmetically checked, but was not a new Fable review or funded lifecycle test.

## Manual tutorial structure review

At the user's request, inspected the existing Lagoon multichain, GMX, CowSwap,
Velora, Lighter deployment/trade/round-trip tutorials, the Hypercore deployment
and account-mode investigation scripts, the read-only Hypercore diagnostic,
and the multichain Sphinx tutorial. Added a source-to-pattern table and a compact
operator-guidance contract to the plan: documentation order, phase-oriented
functions, startup/position/transaction tables, bounded wait progress, exact
recovery commands, live explorer links, and measured costs with an overnight
wallet-activity caveat.

The plan explicitly excludes stale or unsuitable examples: 1-USDC Hypercore
vault deposits, truthy-string simulation flags, conflicting displayed bridge
amounts, mandatory explorer credentials, bare Python commands, blind retries,
fixed sleeps and direct Safe sweeps. No legacy scripts were changed. This was
a repository inspection by the primary agent, not another Claude review, and
no implementation or financial transaction was run.

## Tutorial simplification

Aligned the plan with `.claude/docs/manual-tutorial-script.md` and replaced its
long duplicated tutorial-guidance section with that reference and a concrete
script/workflow/testing responsibility split. Kept ordinary named phase functions,
one recorded EVM sender and one bridge routine for both directions. Added an
explicit mode/action matrix and outcomes, action-specific validation, and a
shared status view. Status and recovery no longer risk inheriting fresh-deployment
funding or completed-activation requirements. An explicit unwind fee override
changes only future unsigned burns, never saved operations.

Prefer a full exit when liquidity permits, otherwise leave that leg pending for
the next manual invocation. Reconcile observed partial results without adding
automatic withdrawal slicing or repeated small return bridges. Recover the other
independent leg normally. Preserve the original custody, confirmation, persistence,
fee/dust accounting and final Lagoon redemption requirements, including the
25-USDC default and 23.02-USDC initial funding floor. These are primary-agent
planning changes; the historical Fable verdict does not cover this revision.
No script implementation or financial transaction was performed.


## Implementation verification

The user subsequently authorised implementation and execution in simulation mode.
The script, small reusable workflow and explicit Core simulation module are now
implemented, with narrative/API documentation. A three-mainnet-fork script run
completed successfully on 2026-10-27, returning 24.185220 of the 25 subscribed
USDC after declared simulated gains, fees and 0.009 USDC disclosed cash dust.
The source plan now records its exact command and protected evidence location.
The run reloaded saved state before unwind, verified the actual Lighter claim
transfer receipt and checked conservation and terminal nonce stability. Focused
unit/regression coverage passed (74 tests), and the shared three-fork test passed
with a locked Core position, independent Lighter recovery and next-invocation
redemption. This is local simulation evidence, not a funded live lifecycle or
an external-agent code review.
