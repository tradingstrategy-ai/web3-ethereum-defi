# Manual three-chain Lagoon investment and unwind test

## Scope

Plan a manual tutorial script at `scripts/lagoon/lagoon-multichain-core-roundtrip.py`.
Deploy the main Lagoon vault on Arbitrum (42161), with guarded satellite Safes on
Ethereum (1) and HyperEVM (999). Invest in one open Hypercore user vault and one
Ethereum-settled Lighter public pool. Save everything needed to manually unwind
later, return the proceeds to Arbitrum, and redeem the deployer's Lagoon shares.

This plan has been implemented at the user's request. Local simulation completed
the round trip. On 2026-10-27, a funded mainnet run opened both positions using
explicit recovery after deployment/entry fixes; it was not an uninterrupted run.
The live delayed unwind remains unverified. See the dated evidence in
[the execution guide](../../README-lighter-core-execution.md#funded-entry-observed-on-2026-10-27).
Keep the workflow sequential and reuse existing library helpers.

## Protocol facts affecting the test

- [Hypercore user vaults](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy)
  have a one-day lock; HLP has four days. Select a normal user vault, excluding
  HLP and its parent/child vaults. Read the Safe's actual `locked_until` after
  entry and again before exit; tomorrow is not an unconditional exit guarantee.
- [Lighter public pools](https://docs.lighter.xyz/trading/public-pools) currently
  have no lock-up. LLP has separate cooldown/staking constraints, so exclude LLP
  from automatic selection. Leave the selected public-pool position open until
  the operator invokes unwind, regardless of its immediate redeemability.
- Lighter share redemption and [secure withdrawal to Ethereum](https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals)
  are separate operations. Use secure withdrawal and the guarded L1 claim;
  EOA-only fast withdrawals do not work for the Safe route.
- [CCTP source support](https://developers.circle.com/cctp/concepts/supported-chains-and-domains)
  permits Fast Transfer from Arbitrum and Ethereum. HyperEVM uses its already-fast
  standard route. Query fees and allowance, including for return transfers.
- Existing guards cover the custody routes. Hypercore selection is explicitly
  whitelisted before deployment. Lighter API-signed investment is not constrained
  by Ethereum's guard; the test trusts its generated, protected API key.

## Operator interface and defaults

Use environment variables, logging and tabulated balances like the existing
Lagoon tutorials. No argument parser and no required routine configuration.

| Input | Default or requirement |
| --- | --- |
| `ACTION` | `deploy`; supports `deploy`, `status`, `unwind`; simulation defaults to `all` |
| `SIMULATE` | `false`; strict boolean parsing |
| `STATE_PATH` | `~/.tradingstrategy/lagoon-multichain-core-roundtrip/mainnet/state.json`; simulation uses a separate `simulation` directory |
| `LAGOON_MULTCHAIN_TEST_PRIVATE_KEY` | Required only for live transactions; retain existing tutorial spelling; never save it |
| `JSON_RPC_ARBITRUM`, `JSON_RPC_ETHEREUM`, `JSON_RPC_HYPERLIQUID` | Required user-provided RPCs, including for forks; support fallback URL format |
| `USDC_AMOUNT` | 25 USDC subscription on Arbitrum; default-position funding floor is 23.02 USDC |
| `POSITION_USDC` | 10 USDC target per protocol |
| `CCTP_MAX_FEE_USDC` | 0.01 USDC ceiling per transfer; abort if current quote exceeds it |
| `MAX_RESIDUAL_USDC` | 0.01 USDC total measured cash dust by default; an explicit unwind override up to 1 USDC is allowed after inspection. Zero prevents redemption with residual cash; it does not eliminate the Core fee margin. Vault positions are never dust. |
| `HYPERCORE_VAULT`, `LIGHTER_POOL_INDEX` | Optional target overrides; otherwise discover eligible open targets |
| `LOG_LEVEL` | `info` |

Select and persist targets once. `status` and `unwind` load the stored deployment
and amounts; they do not rediscover targets or accept silently conflicting
configuration. An explicit `CCTP_MAX_FEE_USDC` override on `unwind` may update
only future, unsigned burns; validate and persist it before use. Never modify a
saved burn's terms. All other financial settings come from saved state on unwind.
`status` needs RPCs but not the EOA private key. Once completed,
the default state remains available for audit; another deployment requires an
explicit new `STATE_PATH`, not overwriting or automatically archiving it.

Example commands after loading the private environment:

```shell
# Day one: deploy, subscribe, bridge, activate and open both positions, then exit.
poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

# Read current positions, locks, pending operations and balances.
ACTION=status poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

# Tomorrow: resume closing/bridging/claiming; repeat if liquidity or settlement is pending.
ACTION=unwind poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

# Full local fork lifecycle, with mocked Core systems and no lock-up wait.
SIMULATE=true poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py
```

Keep the topology fixed to these three mainnets; do not add a `CHAINS` setting,
testnet deployment or a separate preflight command. Preflight is the first phase
of `deploy`, with reusable validation inside the workflow.

| Mode/action | Behaviour |
| --- | --- |
| Live `deploy` (default) | Fresh deployment and investment; save state and exit |
| Live `status` | Read-only observations from saved state; tolerate incomplete activation and show unavailable data |
| Live `unwind` | Reconcile and recover the existing run; never deploy or open positions |
| Simulation `all` (default when `SIMULATE=true`) | Same phases against forks/mocks in one process, with no delay |

Reject unsupported combinations, including live `all`, simulation-only individual
actions and testnet. Dispatch before action-specific checks: status/unwind must
not require an unused state path, a newly funded subscription wallet or a completed
activation report. Status must not request an EOA signing key.

Use exit code 0 for a successful command (including day-one `invested`, read-only
status and terminal `redeemed_with_dust`), 2 for an unwind awaiting lock/liquidity/
settlement, and 1 for failure or unresolved ambiguous execution. Always print the
outcome name so exit code 0 does not imply every lifecycle stage is complete.
Live deploy prints the saved path, actual unlock time and exact unwind command.
Never sleep overnight, schedule an automatic unwind, or automatically unwind on error.

## Existing building blocks and implementation gaps

Inspect and reuse these rather than importing hyphenated scripts:

- `scripts/lagoon/deploy-lagoon-multichain.py`: three-chain configuration,
  Anvil setup and forged CCTP attestations.
- `scripts/lagoon/deploy-lagoon-lighter-multichain.py`: strict native-USDC
  configuration, deterministic Safes, sane defaults and activation preflight.
- `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py`:
  `deploy_multichain_lagoon_vault()` and deployment serialisation.
- `eth_defi/erc_4626/vault_protocol/lagoon/funding.py`: normal subscription,
  settlement and share claim; `vault.py` and `withdraw-lagoon-vault.py`: final
  redemption request, valuation, settlement and claim.
- `eth_defi/cctp/{fees,transfer,attestation,receive,testing}.py`: quoted burns,
  message recovery, nonce-consumption checks and fork attestations. The high-level
  bridge helper returns hashes too late for crash-safe persistence; use its
  underlying preparation functions and a small recorded sender.
- `eth_defi/lighter/{activation,lagoon,execution,testing}.py`: activation,
  guarded custody, optional SDK signing/reconciliation and L1 fork overrides.
- `eth_defi/hyperliquid/{api,vault,core_writer,evm_escrow,testing}.py` and
  `scripts/hyperliquid/manual-test-lagoon-account-mode-roundtrip.py`: account
  activation, account-mode checks, phased deposits and linked-USDC returns.

Use one small reusable workflow/state module under Lagoon and a thin script.
Add a testing module for fake Core state only if necessary. No generic job engine,
distributed lock, automatic transaction replacement or broad deployment refactor.
If helpers hide necessary transaction boundaries, add a narrow optional observer
or use existing bound calls; preserve default behaviour for other callers.

Do not invoke coordinated `LagoonLighterActivation` for this tutorial: it performs
a direct 1-USDC deployer contribution on Ethereum, outside the tutorial subscription. Instead deploy contracts
with automatic activation disabled, subscribe once for 25 USDC, then reuse
`activate_lighter_safe()` on the already funded Ethereum satellite. This avoids
a second subscription with an incorrectly supplied NAV. Sole deployer ownership
means no additional ownership-handover ceremony is needed.

## Tutorial structure and operator guidance

Follow the [manual tutorial guide](../../../.claude/docs/manual-tutorial-script.md)
for module-docstring order, reference scripts, logging, state protection and
troubleshooting. Keep that shared guidance there rather than duplicating it in
this plan. The closest implementation references are the multichain deployment,
Hypercore account-mode and Lighter pool round-trip scripts listed above.

Keep the implementation to three responsibilities:

| Location | Responsibility |
| --- | --- |
| `scripts/lagoon/lagoon-multichain-core-roundtrip.py` | Tutorial docstring, validated environment settings, connections, mode/action dispatch, compact tables and cleanup |
| One small Lagoon workflow module | Named phase functions and saved state, composed from existing deployment, funding, bridge and protocol helpers |
| Existing testing modules, plus a small tutorial helper only if needed | Fake Core balances/API responses and fork setup; never a second copy of the investment/unwind orchestration |

Make the main flow readable as `deploy_and_invest()`, `fetch_status()` and
`unwind_and_redeem()`. Inside them, keep deployment, subscription, bridge,
protocol entry/exit and final redemption as ordinary named functions. One
recorded EVM sender and one bridge routine serve both directions. Use explicit
state and function calls, not a generic action registry, backend class hierarchy,
plugin architecture or execution scheduler. Check that reused Core helpers work
with satellite `AutomatedSafe` instances; generalise narrow type assumptions if
needed rather than deploying an unnecessary satellite Lagoon contract.

The terminal output needs four views: a startup funding/configuration table,
numbered progress with transaction evidence, a current cash/position/pending-work
table, and an end-of-run result with the next command. Match stage names between
code, saved state and output. Reuse the same status renderer after deployment,
on a pending unwind and for `ACTION=status`. Keep Lighter equity separate from
pool valuation, and show timestamps, Core escrow and actual lock times.

Capture initial gas balances before deployment. Reconcile overnight wallet deltas
against recorded subscription/claim transfers so unrelated deployer activity is
not called investment PnL. Keep native costs per chain, fees and residuals explicit.
No mandatory explorer/Hypersync tokens, dollar gas-price feeds, or extra reporting
framework. Required guard/role readback remains onchain.

Add a short dedicated section to the multichain narrative tutorial with the four
commands above, funding table and a small troubleshooting table. Link the new
script/guide instead of duplicating its complete source or the older script's
capability claims. No changes to unrelated manual tutorials are required.

## Minimum USDC budget

The deployer needs **25 native USDC on Arbitrum at the defaults**, plus
**1 native USDC on each satellite** (27 USDC total), all prefunded before
deployment alongside native gas. Satellite reserves remain in the deployer
wallet, outside vault NAV and the hub subscription. Use
`scripts/lifi/feed-cross-chain.py` for gas and its opt-in
`ETHEREUM_USDC_AMOUNT` transfer; fund HyperEVM USDC separately. The hub
subscription floor for the planned two 10-USDC positions is **23.02 USDC**
(**25.02 USDC** including satellite reserves), under the activation assumptions below:

| Allocation | USDC |
| --- | ---: |
| Hypercore investment | 10.00 |
| Hypercore activation deposit | 2.00 |
| Lighter investment collateral | 10.00 |
| Lighter activation deposit | 1.00 |
| Two outbound CCTP fee allowances | 0.02 |
| **Minimum initial subscription for these allocations** | **23.02** |
| Unallocated Arbitrum balance with the 25-USDC default | 1.98 |

For a common position target `P` and per-bridge ceiling `F`, calculate this
floor as `2 * P + 2 + 1 + 2 * F`, using `Decimal`/token conversions. Compare
the configured subscription against it before deployment.

These are allocations, not all consumed fees. The existing Hypercore activation
helper uses 2 USDC to cover the documented-in-code 1-USDC account creation fee;
observe and recover any remaining credit. Its `MINIMUM_VAULT_DEPOSIT` constant
enforces a 5-USDC vault entry, based on observed frontend behaviour. Revalidate
the activation route and selected vault before execution rather than confusing
these with the separate Arbitrum-to-Hypercore bridge minimum. Lighter's direct
Ethereum deposit and secure withdrawal minimum is 1 USDC (see its linked
withdrawal documentation above); the activation deposit remains collateral.
That is not proof of a universal minimum for public-pool minting.

An optional smaller run with `POSITION_USDC=5` has a **13.02-USDC funding floor**
and can use `USDC_AMOUNT=15`. This is conditional on the chosen Lighter pool's
current share sizing/capacity and both protocols' current rules; do not advertise
it as a proven absolute protocol minimum. Keep 10-USDC positions as the default
until a funded lifecycle validates smaller sizes. A still smaller Lighter-only
allocation would require separate position settings and is outside this script.

Return CCTP fees and Core withdrawal fees come out of recovered satellite funds;
do not add four bridge ceilings to the initial funding floor or budget the
recoverable activation collateral twice. Keep uninvested activation credit and
actual outbound fee leftovers available for withdrawal costs. The 23.02 figure
is a startup floor, not a guarantee against investment losses or future fee/minimum
changes. If a return amount falls below a minimum, retain its pending/residual
status rather than promise completion.

Read-only Circle fee checks on 2026-10-27 returned 1.4 bps for both Arbitrum
outbound routes, 1 bp for Ethereum → Arbitrum, and 0 bps for HyperEVM standard.
The default gross outbound burns of 12.01 and 11.01 USDC therefore quote fees
of 0.001682 and 0.001542 USDC respectively, inside their 0.01 ceilings. These
are dated observations, not hardcoded execution fees; use the [Circle fee API](https://developers.circle.com/cctp/concepts/fees)
again before each burn.

Native gas is additional: ETH on Arbitrum and Ethereum, HYPE on HyperEVM, for
both deployment and next-day unwind. It is not included in the USDC figures.
Simulation needs zero real USDC and zero real gas tokens; forks supply test funds.

## Day one: deploy and invest

1. Acquire an exclusive local process lock and refuse an existing state file for
   `deploy`. Validate all RPC chain IDs, contract deployments, optional signer
   installation, native gas on all chains, permissions and deployer's complete
   Arbitrum USDC requirement before broadcasting. Preflight both target protocols
   and all four bridge directions, including protocol entry/withdrawal minima.
   Do not request USDC on either satellite.
2. Discover eligible targets using current APIs: Hypercore must explicitly accept
   deposits, have valid leader/capacity data and be a normal user vault; Lighter
   must be an open, positive-equity Ethereum public pool with capacity for the
   amount and must not be LLP. Reject missing data. Sort candidates deterministically
   (e.g. descending current equity then identifier); select the first eligible
   target, without claiming this is an investment recommendation. Revalidate
   before entry; if it has closed, stop and retain state rather than silently
   choosing another after deployment. Overrides undergo identical checks.
3. Persist run ID, chain identities, deployer, deterministic Safe salt, target
   identifiers, budget and initial balances before any deployment. Deploy one
   Lagoon vault plus all three Safes/modules. Deployer is sole owner, asset
   manager and valuation manager. Use zero management/performance fees for this
   isolated test and no other depositors. Keep `any_asset=False`, allow native
   USDC, selected Hypercore vault, canonical Lighter custody, and CCTP outbound
   and return domains with Safe recipients and zero `destinationCaller`.
   Set the settlement cap to accommodate the subscription and positive PnL
   (use the existing unlimited setting for this sole-owner test).
   Use existing HyperEVM big-block handling, record the previous setting before
   changing it, and restore it afterwards or on the next recovery invocation.
   Record each completed deployment immediately; verify all before funding.
4. Subscribe 25 USDC by default through Arbitrum's normal request → settlement → claim
   lifecycle. Verify deployer shares and Safe capital. Record each transaction
   boundary so an interrupted subscription cannot be repeated. No later
   subscription or NAV settlement while funds are outside Arbitrum.
5. Budget, at defaults: deliver at least 12 USDC to HyperEVM (2 for activation,
   10 for investment) and 11 USDC to Ethereum (1 for activation, 10 additional
   collateral). Gross outbound burns include each 0.01 fee allowance, leaving
   1.98 USDC initially on Arbitrum. Actual fee leftovers stay in satellites.
   Check current activation costs/minima and reserve enough proceeds for return
   fees. Abort before deployment if adjusted amounts exceed the subscription;
   never raise spending automatically. Refresh quotes before each burn.
6. Bridge sequentially with CCTP Fast Transfer from Arbitrum, save burn/hash/message
   evidence, wait for destination receipt and actual native USDC. Recognise an
   external relayer using the exact message nonce; a timeout never causes a new
   burn. Both satellites must be funded before opening positions.
7. Hypercore: activate the Safe via guarded `depositFor` using the 2-USDC budget;
   confirm activation, consumed creation fee and actual credit in each account
   class (spot/perp). Check account abstraction mode
   and use a supported spot/perp path, stopping on unsupported modes. Deposit
   investment USDC via CoreDepositWallet, wait for EVM escrow to clear, transfer
   the needed spot amount to perp, then deposit to the selected vault. Confirm
   actual vault equity and lock timestamp through Core reads. A successful EVM
   receipt only confirms action submission, not successful Core execution.
8. Lighter: use the 1-USDC activation helper, persist its key before registration,
   verify the exact public key and account identity, then deposit the additional
   10 USDC and wait for credited collateral. Mint shares for the 10-USDC target
   through the optional pinned SDK adapter using current share-price sizing and
   available collateral. This is an estimate: the signed share transaction has
   no enforceable USDC price bound. Record actual collateral debited and do not
   assume it equals the target. Persist signed hash/payload/nonce
   before submission; reconcile execution and confirm holdings. Keep unused
   activation collateral accounted separately from invested shares.
9. Save confirmed holdings, collateral, timestamps, unlock information and
   separate account-equity observations. Mark `invested` only when both positions
   are observed. Otherwise record partial success and permit `unwind` to recover
   whichever balances/positions actually exist. Print the tomorrow command.

## State and restart contract

Use a versioned slotted dataclass serialised to protected JSON. Store decimal
amounts as strings and raw amounts/shares as integers; UTC timestamps explicitly.
Create the run directory with mode 0700 and files with 0600. Update via a temporary
file, flush/fsync and atomic replace; use a simple local file lock to prevent two
writers. Keep the API key in the existing protected activation report and store
its path in state. Do not log credentials, signed payloads or authenticated URLs.

State contains deployment JSON/addresses, chain IDs and Safe salt, selected
targets, role identities, subscription/share evidence, protected key reference,
per-leg stages, transaction hashes/nonces, CCTP messages and receipts, Lighter
operation records and withdrawal history ID, observed positions, earliest retry
time, fees and final realised balances. Store state before and after every
financial side effect, including approvals. The recorded sender must save the
signed EVM transaction hash and nonce before broadcast on every chain, not only
the transaction returned after submission. A deployment failure may need address recovery from
recorded deployer nonce ranges; do not advertise automatic redeployment recovery.

On `unwind`, reconstruct contract bindings and signers from this state, verify
RPC identities and onchain owner/module/recipient configuration, check native gas
for remaining transactions, refresh wallet nonces and reconcile pending operations
before making new ones. Refresh the Lighter API nonce only after resolving its
saved predecessor. Resume only after observed receipts/holdings/queue records
establish what happened. Unknown
submission outcomes block that leg; never regenerate a Lighter operation or
repeat a deposit/burn because a receipt is missing. Persist pre-action Core
balances/holdings and compare them with post-action observations. An unchanged
balance alone does not prove a queued Core action was rejected; use explicit
failure evidence where available, otherwise stop that leg for manual recovery.
Do not add automatic Core retries. Track Lighter signed expiry: a missing hash or
unchanged nonce alone does not authorise a fresh signature. Expired uncertain
operations require reconciliation of nonce, status and actual effects, or explicit
manual recovery; this tutorial does not add transaction replacement.
Pending Lagoon subscription or share claims must be recovered before final
redemption. Repeating `unwind`
after completion is a read-only success. `deploy` never resumes an existing run:
interrupted deployments are recovered/unwound, not continued into new investments.
A run with no funded assets needs no financial transactions, but still restore
any recorded big-block setting and report its partially deployed contracts.
If Lighter activation stopped after deposit but before a usable key was registered,
recover the existing key/registration without a second deposit, or use the existing
guarded L1 withdrawal request and pending-balance claim route. Do not require a
successfully generated API key to recover deposited collateral.

## Tomorrow: unwind and redeem

1. Load and reconcile state. If a position is still locked or lacks liquidity,
   report it and return a distinct pending outcome, with a retry command. Process
   the independently available leg; do not wait a day or mark the run complete.
2. Hypercore: read current equity, actual unlock and account-specific withdrawal
   capacity. Size one withdrawal from the lesser of current equity and the
   account's withdrawable amount. Do not automatically loop over partial exits.
   Require a corresponding perp credit and equity reduction before marking that
   action complete; a later manual invocation may withdraw the remainder under
   a new recorded identity. Unobserved/rejected actions require investigation.
   Confirm the Core result and zero remaining
   vault equity before moving this leg to cash withdrawal. If the protocol
   produces a partial result, record it and reconcile the remainder on the next
   manual invocation; never treat it as a full exit. Move all available perp
   collateral (including unused activation funds) to spot, then
   use the linked-USDC `sendAsset` route back to the HyperEVM Safe. Reserve the
   protocol fee using the existing fee helper; wait for actual EVM receipt.
   Use existing conversion helpers between Core linked-token units and native
   ERC-20 units; round to representable EVM units and record any remainder.
   Track any non-withdrawable remainder explicitly, never assume original
   invested amount equals exit proceeds.
3. Lighter: request a burn of all remaining raw shares when liquidity permits;
   otherwise leave this leg pending. Confirm zero shares and available collateral.
   Preserve any observed partial result for reconciliation on the next invocation.
   Securely withdraw all available collateral, including activation leftovers;
   reconcile
   the signed operation. Use the existing exclusive-account history-baseline
   matching to persist one exact secure-withdrawal ID. Save the complete history
   baseline before signing/submitting; never rebuild that baseline after restart.
   Reject ambiguous matching.
   Poll dynamically with a bounded deadline; persist and return pending on
   timeout. On the next invocation continue that withdrawal, not another request.
   Claim actual settled L1 amount through the guarded Safe path and verify receipt.
4. Once a leg has no remaining pool position or protocol receivable, return its
   complete available native USDC to the Arbitrum Safe. Prefer one protocol cash
   withdrawal and one return bridge per satellite; do not repeatedly sweep small
   increments while an investment is still pending. A failed-entry leg can return
   its idle funds after confirming that no queued action can still consume them.
   Ethereum uses Fast Transfer; HyperEVM standard. For a full-balance burn `B`,
   quote using `net_amount = B - max_fee`, so the gross burn remains `B`, not
   `B + max_fee`: the existing helper computes its percentage fee on that gross
   amount. Assert `quote.amount_raw == raw_balance` and pass the returned raw
   fields directly, without reconstructing or rounding with floats.
   A quote over the ceiling leaves this leg pending with an explicit fee-override
   instruction; never silently downgrade Fast Transfer. For `B <= max_fee`,
   check whether a smaller valid fee ceiling can bridge the balance before
   classifying any remainder as non-recoverable dust.
   Reuse recorded CCTP messages and confirm destination mint, actual fee and
   balance. Serialise Arbitrum relay transactions through one nonce-managed wallet.
5. Before final valuation require no positions, unsettled protocol withdrawals,
   bridge receivables, pending subscriptions or unreturned material satellite
   balances. Query all sources afresh; missing data blocks settlement. Track dust
   per asset in native precision, separately from consumed fees. Active positions
   and pending claims never qualify as dust. After confirmed exits, measured
   non-recoverable cash residuals may be written down only within the configured
   aggregate 0.01-USDC default ceiling; disclose the amount and location before
   settlement. After inspecting measured cash, an explicit unwind override can
   raise this to at most 1 USDC. The budget never suppresses recoverable transfers.
   Larger or unexplained residuals leave the run pending. This avoids stranding
   the recovered capital because the Core bridge's 0.01-USDC fee reserve exceeds
   the actual fee, without claiming that this reserve was entirely spent.
6. Once all recoverable capital is on Arbitrum and external exposure is resolved,
   request redemption of all deployer shares, post actual recovered NAV, settle
   through the module and claim to the deployer. Resume request/settle/claim as
   separate persisted steps using pending/claimable request data, not only wallet
   share balance (shares can already be escrowed). Include existing silo/claimable
   assets exactly once: before initial redemption settlement, NAV is the actual
   Arbitrum Safe cash after disclosed dust write-downs; after settlement, claim
   existing silo entitlements instead of reposting that amount as new NAV.
   Do not sweep the Safe directly to bypass Lagoon accounting.
7. Verify deployer shares and pending/claimable redemption are zero, satellite
   balances and protocol holdings are resolved, and Arbitrum Safe/silo have no
   unexplained remainder. Report initial subscription, returned USDC, actual
   bridge/activation/withdrawal fees, realised investment gain/loss and residuals;
   native gas expenditure is separate. Return value need not equal principal.
   Set `completed` only with zero residuals; use `redeemed_with_dust` when bounded
   disclosed cash dust remains. Both are terminal/read-only on repeated unwind;
   retain residual recovery evidence. Never label the latter “everything returned”.

## Simulation

`SIMULATE=true` runs deploy → invest → unwind → redeem in one process, without
lock-up or settlement-delay sleeps. Fork the three configured mainnets through
the existing Anvil helpers; use a generated test wallet funded on forks, never
the supplied real key. Verify Anvil identity before any storage overrides. Keep
all transaction endpoints local; no signed request reaches a live Core API.
Skip live big-block toggles and use deterministic local target discovery.

Exercise real Lagoon/Safe/module/guard calls, native-USDC transfers and CCTP
burn/receive contracts with forged attestations on all destination forks,
including Arbitrum for the return. Use deterministic mock quotes/allowance.
Persist the same stages and reread saved state between investment and unwind.

Hypercore: reuse `setup_anvil_hypercore_mocks()` for guarded call validation.
Its mocks only record calls and do not debit USDC. Add a small simulation ledger
that consumes each confirmed mock action once and explicitly debits/credits
fork USDC at the Core boundary. Model spot/perp balances, vault shares, fees and
zero-duration locks. Never leave invested money counted in the Safe too.

Lighter: exercise the real forked L1 deposit, Safe-owner key-registration and
guarded claim; use existing account-registration and pending-balance overrides
to represent sequencer settlement. Mock account discovery, key verification,
nonce/transaction status, pools/holdings and withdrawal history through a narrow
injected API/signer boundary. Account for L1 deposit debits and claim credits
exactly once. Do not call the live activation wait loop or pretend mock mint/burn
proves real signer or sequencer compatibility.

Persist simulation evidence separately and label every report. Simulation forks
are ephemeral; close them in `finally`. A saved simulation report cannot resume
on mainnet or after its forks are gone. Separate next-day commands are the live
workflow; do not add Anvil dump/restore infrastructure. Test restart reconciliation
using new workflow objects and persisted state against still-running forks.

## Validation and documentation

- State/default tests: mandatory inputs only, first-run protection, permissions,
  atomic writes, no secret logging, mode/identity mismatch, lock-held rejection;
  status works on an incomplete run without a signing key, unwind does not
  reapply fresh-deployment funding checks, and fee overrides affect only new burns.
- Workflow tests: exact funding budget (23.02 at default positions, reject one
  raw USDC unit below it, accept the 25-USDC default; 13.02 for eligible 5-USDC
  positions); both eligible targets selected before
  deployment; every contract ready before funding; observed position confirmation;
  no premature settlement; return fee included in full-balance burn.
- Recovery tests: stop after subscription request/settlement/share claim; after
  burn but before relay; after accepted-but-unconfirmed Core operation; after
  Lighter withdrawal request/claim; after Lagoon redemption request/settlement.
  Rerunning unwind must not duplicate any successful side effect. Include one
  failed investment with the other funded, locked positions, insufficient liquidity
  causing a pending full exit without automatic slicing, an observed partial result,
  externally relayed CCTP messages, interrupted activation without an API key,
  signed expiry, recorded big-block restoration, and residuals below/above the
  explicit aggregate dust ceiling. Check raw full-balance burn arithmetic.
- Full three-fork simulation: actual hub subscription and final redemption,
  both mocked positions, both return bridges, injected fees and non-zero PnL;
  prove cash movement and conservation, not just recorded call counts.
- New pytest fork coverage must follow the shared `anvil_fork_pool` module
  docstring: canonical midnight blocks, warm RPC cache, snapshot isolation and
  xdist grouping; no independent latest-block fixtures. Read it before coding.
- Run focused tests using `source .local-test.env && timeout 180 poetry run pytest
  <focused paths>`, format changed Python with Poetry/Ruff. Do not build Sphinx.
- Document the two-day commands, defaults, partial recovery and simulation limits
  in the script and `docs/source/tutorials/lagoon-multichain.rst`; update the
  Lighter execution guide and add API stubs for any new public module.
- A real run needs the funded environment and two manual invocations. Record exact
  commands, date, redacted addresses/provider, results and remaining balances in
  the eventual PR comment. Simulation does not replace this evidence. Planning
  and implementing the script do not themselves authorise a funded mainnet run.

## Review status

Claude CLI `claude-fable-5-1` reviewed the original plan on 2026-10-27; valid
corrections were incorporated and its final pass found no blocking findings.
The [review record](2026-10-27-lagoon-three-chain-roundtrip-review.md) preserves
findings, verified claims and dispositions.

Subsequent user-directed changes reduced default funding to 25 USDC, added the
checked 23.02-USDC startup floor, and aligned the script with the shared tutorial
guide. This cleanup also makes mode dispatch explicit and prefers full exits over
automatic partial-withdrawal loops. Those revisions have not received another
Claude review. Implementation and local simulation have now been completed;
no funded live lifecycle test has been performed.


## Implementation and local verification

Implemented the manual script, `eth_defi.erc_4626.vault_protocol.lagoon.roundtrip`
and `eth_defi.testing.lagoon_roundtrip`. The Hypercore builders now accept satellite
Safes. Lighter activation can use an injected read session, and pool submission
can use an injected simulation transport; default live behaviour is preserved.
Updated the narrative tutorial, API stubs and manual-script guide.

On 2026-10-27 the following manual command completed with exit code 0:

```shell
source .local-test.env && PYTHONPATH="$PWD" SIMULATE=true \
STATE_PATH="$HOME/.tradingstrategy/lagoon-multichain-core-roundtrip/simulation/completed-run/state.json" \
timeout 180 poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py
```

This run used canonical Arbitrum 487039644, Ethereum 25598869 and HyperEVM
41271954 forks, with the existing Goldsky/dRPC/Alchemy fallback providers.
No RPC credentials are included here. The protected state and generated local
API-key backup remain at the selected path; owned Anvil processes were closed.
Use a fresh `STATE_PATH` for another simulation rather than overwrite this report.

Results: 25-USDC subscription, both 10-USDC target investments, declared simulated
net gains of 0.20 USDC, all four real fork CCTP burns/receipts, real guarded Lighter
L1 deposit/key-registration/claim, and normal Lagoon redemption to the deployer.
Actual fork claim was **24.185220 USDC**, with **0.004780 USDC** CCTP fees,
**1 USDC** simulated account-creation cost, **0.001 USDC** simulated Core return
fee and **0.009 USDC** disclosed Core cash dust. All three Safe USDC balances,
pool shares, available Lighter collateral and Lagoon shares/claims were zero.
The outcome was `redeemed_with_dust`. The conservation assertion and repeat
terminal-unwind nonce assertion passed. The simulation loads saved state into
fresh workflow/executor bindings before unwinding.

Focused unit/regression command:

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180 poetry run pytest \
  tests/lagoon/test_core_roundtrip.py tests/lighter/test_activation.py \
  tests/lighter/test_execution.py tests/hyperliquid/test_core_writer_fee_margin.py -q
```

Result: **75 passed**. The shared-fork regression below separately passed,
including a mocked one-day Core lock, independent Lighter recovery, no premature
Lagoon redemption, state reload and terminal no-op replay:

```shell
source .local-test.env && PYTHONPATH="$PWD" timeout 180 poetry run pytest \
  tests/lagoon/test_core_roundtrip_fork.py -q --log-cli-level=info
```

Result: **1 passed**. Changed Python was formatted; focused Ruff checks passed.
Sphinx was not built. Native signing/execution on either live Core sequencer and
the funded overnight lifecycle remain unverified by these local results.


The optional small run was also executed successfully on 2026-10-27:

```shell
source .local-test.env && PYTHONPATH="$PWD" SIMULATE=true POSITION_USDC=5 USDC_AMOUNT=15 \
STATE_PATH="$HOME/.tradingstrategy/lagoon-multichain-core-roundtrip/simulation/small-run/state.json" \
timeout 180 poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py
```

Exit code 0; **14.137327 USDC** claimed, **0.15 USDC** declared simulated gains,
**0.002673 USDC** CCTP fees and the same 1-USDC account creation, 0.001-USDC Core
return fee and 0.009-USDC cash residual. Conservation and terminal nonce checks
passed. The gain model uses the actual selected raw-share quantity rather than
assuming the default position size. This does not prove the live minimum pool size.


### Prefunding update verification (2026-10-27)

All manual multichain examples now check the full hub subscription, 1-USDC
satellite deployer reserves and native gas before deployment. The three-chain
fork regression passed with both reserves unchanged after final redemption.
Focused funding, LI.FI and recovery coverage passed (44 tests).

The LI.FI script's real quote-only command was run with the GMX test wallet
assigned privately to `PRIVATE_KEY`:

```shell
SOURCE_CHAIN=arbitrum TARGET_CHAINS='' ETHEREUM_USDC_AMOUNT=5 DRY_RUN=true poetry run python scripts/lifi/feed-cross-chain.py
```

It returned an executable Arbitrum-USDC → Ethereum-USDC quote: 5 USDC source
spend, 4.936926 USDC estimated/minimum receipt. No approvals or transfers were
sent. The real transfer and destination receipt remain unverified live.
