# Upshift support through trade-executor

Date: 2026-10-30. Status: implementation added in both repositories; validation and rollout limits are recorded below.

## Outcome and baseline

Make the existing Upshift adapters usable through the generic vault execution path, including deposits, withdrawals, guarded Lagoon custody, persisted settlement and the existing `perform-test-trade` command. Cover permissionless EVM vaults accepting USDC. Keep restricted deployments in the diagnostic inventory, excluded from the strategy shortlist. Reuse the existing managers, tickets, routing, state, settlement resolver and CLI commands.

Both repositories were fetched from `origin/master` before planning:

| Repository | Reviewed master commit | Working copy |
|---|---|---|
| web3-ethereum-defi | `743eb51e57fae7dee76779e82c8be7ab150bc484` | Current `check-upshift-deposit-adapter` worktree; already at fetched master |
| trade-executor | `30f42c2e366b3a2abdf1485375683ca70ed39050` | Isolated `/tmp/trade-executor-upshift-plan-2026-10-30`; preserves the original checkout's modified dependency |

Executor master pins eth-defi submodule `f377761f2df3dc4cec5e32f1e4f8829d35887ea2`. Its Upshift manager and shared deposit/redeem base have no diff against the reviewed eth-defi master. Updating the executor repository alone will not deliver the proposed fixes: update its submodule to the merged adapter/guard revision as part of implementation.

The design follows the [vault protocol support guide](../../../eth_defi/erc_4626/README-vault-protocol-support.md), [redeemable liquidity guide](../../../eth_defi/erc_4626/vault_protocol/README-vault-redeemable.md), [ABI guidance](../../../eth_defi/abi/README.md), and the module documentation in [the shared Anvil fork pool](../../../eth_defi/testing/anvil_fork_pool.py). Executor references are relative to that repository: `.claude/docs/vault-deposit-redeem.md`, `tradeexecutor/ethereum/vault/README-vault-position-manual.md`, `docs/state-persistence.md`, and `.claude/skills/sanity-check-plan/SKILL.md`.

## What exists and what is broken

There is already an `UpshiftMultiAssetDepositManager`, an `UpshiftQueuedRedemptionTicket`, multi-asset selection in executor routing/pricing, and a `whitelistUpshift()` guard method. This is a repair and integration task. Existing tests include mocked Upshift events and permissions plus protocol metadata reads. No dedicated Upshift executor round-trip integration or successful `perform-test-trade` evidence was found in the reviewed code.

The contract findings below come from the official [vault API](https://docs.upshift.finance/rest-api/public/vaults), [legacy vault interface](https://docs.upshift.finance/contracts/vault-interface), verified [multi-asset implementation](https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code) and verified [Core USDC implementation](https://etherscan.io/address/0xf2cd14f02b4fdc0d26681fbc7f60a11b8378f96d#code). Match each proxy's implementation at the tested block before applying an ABI family to it.

| Observed gap | Required correction |
|---|---|
| `fetch_all_denomination_tokens()` omits the reference asset when the extra asset whitelist is non-empty | Return the reference asset plus whitelisted extras, deduplicated by address. Sentora and Clear RWA must include USDC. |
| Legacy `UpshiftVault` selects the generic ERC-4626 manager with a view-only `TokenizedAccount.json` | Bind verified transaction functions and use an Upshift manager for its non-standard withdrawal lifecycle. Standard `redeem()` reverts. |
| Multi-asset `requestRedeem(shares, receiver)` transfers LP shares from the owner | Include approval of the separate LP token before the request. |
| `getWithdrawalEpoch()` is decoded as epoch/year/month/day | Correct ABI names and decoding to year/month/day/epoch; derive the ticket at the successful request's block. |
| A positive dated burnable amount is interpreted as claimability | It is an outstanding request. Check maturity and executable payout liquidity separately. |
| Documentation and `force_settle()` assume an operator must process before the user claims | A mature funded request can be claimed directly. `processAllClaimsByDate()` pays and clears claims; it does not unlock a later claim. |
| Sender permission is described as universally permissionless | Read the optional sender whitelist for the actual custody address through the existing admission hooks. Multi-asset requests and claims can also require it; legacy blacklists need separate checks. USPC has a configured whitelist. |
| Lagoon deployment selects specialised Atoma whitelisting but generic ERC-4626 whitelisting for Upshift | Use the existing Upshift guard policy and cover both ABI families and the separate LP token. |
| Executor creates an extra approval using the denomination token, while the manager embeds an approval for the selected input | Agree approval ownership in the shared request/router path; approve the actual input and correct spender once, retaining other managers' behaviour. |

Manual investigation on a shared Ethereum fork at `ETHEREUM_MIDNIGHT_BLOCK = 25,598,869` reproduced the failures. These are diagnostic observations, not automated test results:

- Sentora: a 10 USDC deposit minted 9,696,832 raw shares. The manager's withdrawal request reverted without LP approval and succeeded after approval. The contract returned `[2026, 7, 27, 1785110400]`; the manager constructed year `7`, month `27`, day `1785110400`, epoch `2026` and stayed pending. The burnable amount was positive before maturity, but claiming reverted. Advancing Anvil time past maturity allowed a direct claim returning 9,999,999 raw USDC, leaving zero shares.
- Core USDC, Gamma USDC and Upshift USDC: generic manager deposit construction failed with `ABIFunctionNotFound`; direct verified `deposit(uint256,address)` succeeded. Standard `redeem()` reverted in all three. Their full withdrawals were not proven by this diagnostic.
- Only test-owner funding and local time advancement were used for the Sentora round trip; withdrawal liquidity was not injected. No production transactions were sent. These checks bypassed executor and custody guards and do not establish executor support.

Turn the diagnostics into reproducible tests in the steps below. Do not use temporary investigation files as permanent acceptance evidence.

## USDC vault inventory

Snapshot: 2026-10-30, from the [official catalogue](https://api.upshift.finance/v1/tokenized_vaults?load_subaccounts=false&load_snapshots=false) and onchain reads. Ethereum reads used block 26,308,580; Monad reads used 118,018,227–118,018,231; HyperEVM used 49,961,847. These are discovery snapshots, separate from the older canonical fork block. “Delay” is the configured lag, not a guaranteed withdrawal completion time. Recheck pauses, permission, implementation, capacity and liquidity before enabling any vault.

| Chain | Vault | Address | Withdrawal asset | Delay | Work group |
|---|---|---|---|---|---|
| Ethereum | Sentora USD | `0x74aD2F789Ed583DBd141bbdafC673fE1F033718b` | USDC | 3 days | First multi-asset round trip |
| Ethereum | NEMO USDC Prime | `0x955256B31097dDf47a9E47A95aDfDFB4460D8522` | USDC | 30 days | Multi-asset, longer queue |
| Ethereum | USPC Custody Vault | `0x0A152C5b8D48C26619FF3ae85Ef1B54A79e20870` | USDC | 3 days | Permissioned; excluded from strategy shortlist |
| Ethereum | SharpByte RWA Ecosystem | `0x5776B576474ad2513E550190C5855613859F3A26` | USDC | 3 days | Multi-asset |
| Ethereum | Sentora Protected USD | `0x02EA41404CCA842dF16e7fd327756192d576F503` | USDC | 3 days | Multi-asset |
| Ethereum | Upshift Clear RWA | `0x076f3f14d87eA2D34DB66A4b5b9f091918008552` | USDC | 2 days | Reference-asset discovery regression |
| Ethereum | Upshift Wildcat USD | `0x18EE038C114a07f4B08b420fb1E4149a4F357249` | USDC | 3 days | Resolve failed `depositCap` decoding first |
| Ethereum | Upshift Core USDC | `0xE9B725010A9E419412ed67d0fA5f3A5f40159D32` | USDC | 0 | Legacy zero-lag request path |
| Ethereum | Upshift Gamma USDC Vault | `0x998D7b14c123c1982404562b68edDB057b0477cB` | USDC | 3 days | Legacy queued path |
| Ethereum | Upshift USDC | `0x80E1048eDE66ec4c364b4F22C8768fc657FF6A42` | USDC | 0 | Legacy/lending-pool family verification |
| Monad | earnAUSD Looping Vault | `0x42B3b62a6685E3a3F93483715Ef4300f5E7336CC` | USDC | 3 days | Current-state chain verification |
| HyperEVM | Coinmerce Capital USDC | `0xcfe06d2499aE635830D11859941e76354D5717CC` | USDC | 2 days | Chain/token verification |
| Ethereum | Axis Origin USDx | `0xAD958C4c0c90bf0216e0f5472F074a9AB30f595F` | USDx | 7 days | Different payout token |
| Ethereum | Tori Ecosystem | `0xcd69123b3FBBfC666E1f6a501da27B564C00De54` | trUSD | 7 days | Different payout token |
| Monad | earnAUSD | `0x36eDbF0C834591BFdfCaC0Ef9605528c75c406aA` | AUSD | 3 days | Different payout token |

The diagnostic inventory has 15 catalogue entries: 12 with USDC accounting/payout and three with a different accounting/payout token. USPC is permissioned and excluded from the permissionless strategy shortlist regardless of individual custody admission. Wildcat's cap read failed in the initial snapshot, so its capacity needed verification. Hidden Azure Tide USDC (`0x69FC3f84FD837217377d9Dae0212068cEB65818e`) and hidden, deposit-paused SingularV USDC Max Yield (`0x73B04B604DF15A6d920Ee62E228548cD88DD3549`) remain diagnostic candidates, outside default public enablement. Non-EVM products need separate integrations.

## Reuse map

| Responsibility | Existing mechanism to use |
|---|---|
| ABI, asset metadata, admission and capabilities | `eth_defi/erc_4626/vault_protocol/upshift/vault.py`, shared `VaultDepositManagerCapability` and closure/permission hooks |
| Calls, approval requirements, ticket parsing, claim and receipt analysis | `eth_defi/erc_4626/vault_protocol/upshift/deposit_redeem.py` and `eth_defi/vault/deposit_redeem.py` |
| Guarded execution and configuration | `contracts/guard/src/GuardV0Base.sol`, existing SimpleVault tests, Lagoon deployment/configuration helpers |
| Asset selection, pricing and transaction tagging | Executor `tradeexecutor/ethereum/vault/vault_routing.py` and vault pricing; keep protocol selectors inside eth-defi |
| Pending trades, valuation, settlement and restart recovery | Existing `TradeExecution`, `BlockchainTransaction`, state store, `settlement_retry.py`, `settlement_estimate.py` and `VaultValuator` |
| Operator diagnostics and test trades | Existing `vault-test-trade`, `perform-test-trade` and vault settlement CLI commands |
| Token conversion | Existing spot routing, pricing and normal trade accounting; use an established route only after it is verified |

Do not add an Upshift execution model, executor protocol switch, separate settlement worker, transaction journal, aggregate-claim allocator, or new test-trade command. A small legacy manager subclass or a shared internal Upshift helper is appropriate where call signatures differ; keep the public lifecycle interface unchanged where possible.

## Ordered implementation

### 1. Repair ABI binding, discovery and admission in eth-defi

Change the existing Upshift ABI files, `vault.py` and `deposit_redeem.py` together.

1. Resolve proxy implementations at the canonical test blocks, obtain verified transaction/event ABIs through the shared ABI workflow and record provenance in `eth_defi/abi/upshift/README.md`. Correct the epoch output order. Cover legacy deposit/request/claim and the multi-asset instant signature. Leave the legacy three-argument instant exit out of initial scope: the verified request path already provides zero-lag withdrawal and the existing guard does not validate that instant selector. Verify that the API's `lendingPool` deployment is ABI-equivalent to the legacy family; treat a mismatch as a distinct implementation to verify, not proof that its label is sufficient.
2. Return reference asset plus extra accepted assets, preserving a documented stable order. Keep the accounting asset, deposit input and LP share token distinct. Use chain/address identity and `TokenDetails` conversions, including 6-decimal USDC versus 18-decimal USDx/trUSD.
3. Implement account-specific sender whitelist checks through `check_deposit_whitelist` and the existing closure/admission paths. Check deposit pause, supported input, minimum/maximum amounts and total capacity with the correct units. Include the receiver's allocation limit in `SendersAllocationWhitelist`, and legacy sender/receiver blacklists. Detect removal of claim permission after a request; it is an access blocker, not a liquidity delay. Reject a legacy payout redirected to `settlementAccount` instead of crediting custody. Resolve Wildcat's implementation/return-type mismatch; never treat an unreadable cap as unlimited.
4. Advertise only proven deposit/redemption capabilities. Derive queue/instant behaviour from verified implementation and live configuration. A zero-lag legacy `requestRedeem` can pay in the same transaction; a protocol-wide async flag is insufficient. Ensure universe metadata preserves this behaviour for live planning and generic backtests.

Acceptance: discovery includes USDC for Sentora/Clear RWA; disallowed senders/assets and unreadable required capacity fail before broadcast; legacy transaction calls can be constructed from verified ABIs.

### 2. Repair the existing manager's complete withdrawal lifecycle

1. Add the missing LP approval to multi-asset queued requests. Implement legacy `requestRedeem(shares, receiver, holder)` and its native receipt parsing using the same manager interface. Do not route legacy withdrawals to standard ERC-4626 `redeem()`.
2. Use the verified request/claim route for positive lag. Legacy zero-lag `requestRedeem` pays immediately, whereas the verified multi-asset implementation rejects a zero-lag request with `VaultNotTimelocked` and requires `instantRedeem`. Test that path's fee, liquidity, receipt and synchronous classification before enabling it; otherwise report the configuration as unavailable. Preserve an optional fee-bearing instant exit for positive lag only where proved. The executor currently asks the manager whether redemption is synchronous: ensure that answer describes the actual selected request, including zero-lag behaviour, without creating an Upshift branch in executor.
3. Parse the request event, validate emitting contract, holder, receiver and exact shares, and read the date from the legacy request event where present, or from receipt-block state for multi-asset requests whose event omits it. Persist it with the existing ticket serializer. Validate date/epoch consistency on reconstruction. First inspect existing state read-only for malformed Upshift tickets; do not create a repair migration without an affected ticket. For a discovered ticket, recover from its successful request event and historical state where needed and available; otherwise halt with the exact evidence required for repair. Do not rotate fields merely because the old decoder had a known ordering bug: validate chain evidence and stored transaction identity. Never silently rewrite an unresolved ticket from current state, especially on Monad.
4. Distinguish pending time, mature but insufficient liquidity, claimable and externally completed requests using the existing status interface. `maxWithdrawalAmount` is a cap, not liquidity. Correct both the historical reader's `available_liquidity` export and `fetch_available_liquidity()` in `vault.py`; use truthful immediate liquidity or the existing unknown representation, with queue-aware owner capacity separately. Include persisted values affected by this correction in the migration/backfill assessment. Check request/claim sender permissions and withdrawal pauses as well as liquidity. Calculate owner-specific executable capacity and fees at one block; share valuation, amounts and readiness must use consistent observations. Legacy requests fix the net asset entitlement at request time; multi-asset claims price the escrowed shares at claim time. Preserve that distinction, including fee changes, in pending valuation rather than valuing every queued claim as a current share balance.
5. Implement native withdrawal/claim receipt analysis for both families, including the zero-lag payout in the request transaction. Match the ticket and actual net token transfer; reject mismatched or ambiguous payouts. Credit confirmed assets only once.
6. Implement `fetch_completed_redemption_tx_hash()` using the existing completion-recovery hook when another caller or `processAllClaimsByDate()` has already paid the receiver. Use Hypersync for historical event discovery and robust receipt verification. Multi-asset `WithdrawalProcessed` contains neither date nor shares: correlate the receiver/token transfer with the date in the actual `claim` or batch call data and the request history. For calls nested through a Safe/module, use existing transaction decoding/trace helpers where available; halt as unattributable if the call identity cannot be proved. Do not settle by receiver and amount alone. If a competing batch consumes the claim after preflight and our claim reverts with `NoSharesForReceiver`, recover its payout before submitting any retry. A zero burnable amount alone is not proof of settlement. Indexer lag leaves the trade unresolved, with an observable retry.
7. Keep executor master's existing single pending redemption per custody account and vault. Before submitting, detect an existing receiver/date obligation, including requests outside executor state. During recovery require exact ticket ownership/share attribution; halt on external additions or a payout combining multiple requests for the same receiver/date. A batch paying other independent receivers is valid when this ticket's payout can be proved. Do not allocate one receiver's aggregate payment across trades or let a retry handler turn this invariant failure into an endless warning. A third party can add shares to the same receiver/date and force this conservative halt, even with a dust request. Record this availability limitation and test it; automatic surplus allocation/account correction would change the explicitly unsupported aggregate operating model and is outside this plan.
8. Replace the incorrect mock-only `force_settle()` requirement with the existing Anvil simulation contract: time advancement to maturity followed by ordinary claim/resolver execution when liquid. Report interventions through `VaultForcedSettlementResult` and enable `supports_anvil_settlement` only for proved shapes. Include a 30-day case that detects stale oracle data after time advancement; report unavailable simulation without fabricating prices or liquidity. Never impersonate an operator or inject withdrawal liquidity and describe that as an unassisted round trip.

Acceptance: Sentora queued, Gamma queued and a legacy zero-lag round trip work; pre-maturity and insufficient-liquidity cases remain pending/unavailable; already-paid claims and restart are idempotent; competing/aggregate claims halt. Fees and rounding reconcile to actual transfers.

### 3. Complete guarded custody and deployment

1. Start with Python configuration of existing Solidity primitives; a new guard implementation is not the default. For multi-asset vaults, call existing `whitelistUpshift()` for each selected accepted asset and `whitelistToken()` for the LP token. For legacy vaults, configure the existing deposit, ERC-7540-shaped three-argument request and Upshift claim validators with `allowCallSite()`, `allowApprovalDestination()` and token whitelisting. Extend Solidity only if a required validated selector is actually missing. Whitelist the selected deposit asset and separate LP token with the correct approval destination. Check exact selectors, vault target, input asset, receiver and holder; retain the shared validation helpers. Do not grant operator functions or blanket target access.
2. Make Lagoon's existing deployment and reconfiguration path choose this policy for Upshift. Cover both initial setup and an existing custody module receiving the new policy. Verify whether deployed guard bytecode already contains the required selectors; an ABI update alone cannot add a validator to an old deployment.
3. Rebuild compiler-derived guard and Safe integration artefacts with the existing Makefile targets when Solidity changes. Update only generated outputs required by the change.
4. Add one representative lifecycle per ABI shape through deployed `SimpleVaultV0.performCall()` and `GuardV0`, then through the actual Lagoon Safe/module path used by executor. Include refusal of a wrong receiver/holder, unapproved token and unrelated target. Reuse deployed session fixtures and snapshot isolation.

Acceptance: the asset manager can execute every required approval, deposit, request and claim, and cannot redirect funds. No manual guard bypass is needed by CLI testing.

### 4. Wire and verify the generic executor path

After the eth-defi changes merge, update the executor submodule and verify the loaded package paths/versions in the test environment. Also inspect the pinned `trading-strategy` dependency: `load_single_vault()` uses its bundled vault database. Confirm the selected vault, separate LP token and capability metadata survive that path. Prefer an explicit universe built with existing constructors for the new `strategies/test_only/` Upshift test strategy if the bundle lacks the candidate; production consumers still need the normal bundle/feed refresh. Update that dependency only if the existing metadata format/loader cannot carry the required information, and record that additional scope.

1. Reuse `resolve_multi_asset_deposit_token()` in both pricing and execution. Validate that the selected deposit token matches the trade's actual input and quoted units. Fix the shared approval path so a manager-provided approval is not duplicated and fallback approvals use the selected token and `get_deposit_approval_target()`. Retain `vault_approval`, `vault_request` and request ordinals so receipt selection excludes approvals.
2. Route through manager requests and analyses for both synchronous and queued flows. Preserve `VaultMetadata` through universe/DataFrame construction. Make configured queue behaviour reach `TradingPairIdentifier.has_delayed_vault_redemption()` and the existing planning/funding gates so pending withdrawals cannot fund same-cycle purchases. Current feature sets omit Upshift; carry directional capability metadata through the existing universe path and extend shared classification, rather than declaring every Upshift deposit and redemption asynchronous. Use the generic backtest delay/behaviour mechanisms.
3. Reuse `vault_settlement_pending`, settlement estimates, resolver and persisted tickets. Verify valuation while shares are escrowed or burned: the pending claim is counted once and remains unavailable cash; adapt the existing Atoma entitlement calculation in `vault_valuation.py` into a small shared manager hook for ticket-specific net value, retaining its fallback for ordinary share-valued positions. That hook is proposed work: current master uses an `AtomaDepositManager` type check. Support fixed legacy entitlement from request time and floating multi-asset entitlement without adding another protocol branch. Reuse the existing position/state models and retain Atoma behaviour and aggregate refusal.
4. Save prepared transactions through the existing pre-broadcast persistence callback. On restart, recover a mined request or claim from transaction records and protocol evidence before preparing another. Repeated resolver calls must leave balances and executed quantities unchanged after success. Keep chain selection and custody routing through the existing home/satellite transaction builders.
5. Keep both CLI entry points on this route. `vault-test-trade` provides a strategy-free Lagoon diagnostic; `perform-test-trade` proves the strategy/universe-to-executor route requested here. Extend existing async simulation/reporting only where necessary, with persisted pending state and subsequent generic settlement. In `tradeexecutor/cli/testtrade.py`, generalise the current Atoma-only delayed `--close-only` resume gate using manager capabilities and existing test-position checks; a second invocation must resolve the saved request before considering another withdrawal. Include a two-invocation state-reload test. Do not require a 30-day wall-clock wait for the NEMO fork test.

Acceptance: at least one Upshift lifecycle passes through each CLI entry point on an Anvil fork with real contracts and actual Lagoon-guarded transactions (a hot-wallet-only fixture is insufficient); one representative delayed and one zero-lag path pass executor integration; restart, external payout and refused aggregate requests use existing state and recovery mechanisms.

### 5. Support USDC input with a different payout token

Axis, Tori and earnAUSD need explicit treatment. Accepting USDC does not make their accounting/payout token USDC. Directly relabelling USDx, trUSD or AUSD proceeds as USDC would corrupt reserves and PnL.

1. First use the existing single-denomination vault model with normal spot trades: USDC → accounting token → vault, then vault → accounting token → USDC. Verify an existing supported route, liquidity, slippage, approvals and prices for both conversion directions before admitting that vault to a USDC strategy. An accepted reference token needs no new deposit router.
2. If a verified conversion route is unavailable but direct USDC deposit is useful, record the missing reverse route as an enablement blocker. Supporting direct USDC input still requires the generic trade/analysis path to represent the real input and distinct payout assets; implement the smallest shared extension only if existing multi-leg trades cannot represent it. Do not conceal the payout token behind the deposit override.
3. Test confirmed payout as the actual token, followed by a normal conversion trade. Pending claims and unsold payout tokens must never appear as spendable USDC. Preserve fees, decimals and price changes across legs.

This group remains part of the support target. A refusal with a precise route/liquidity blocker is an honest interim outcome, not a completed USDC round-trip integration. No custom universal-adapter/swap service is planned.

### 6. Complete the vault matrix, documentation and rollout

1. Audit all 15 catalogue entries using the existing discovery and metadata paths. Exclude permissioned USPC from the strategy shortlist. Record implementation, accepted USDC address, payout/denomination token, LP token, configured delay, capacity and observed liquidity. Reuse protocol-family tests, but run a minimal admission/transaction check for each eligible vault to catch deployment-specific restrictions. Record excluded and unavailable cases separately from successful round trips.
2. Prove the Ethereum family cases first, then HyperEVM and Monad using existing chain configuration. Monad uses current-state forks and relative assertions because old state expires; it cannot use a permanently pinned historical state block. Do not provision RPC endpoints or assume another chain's USDC address.
3. Update the existing Upshift ABI notes, manager docstrings and `docs/source/api/upshift/index.rst` with supported flows and limitations. Add only the missing links and Upshift caveats to executor's existing vault guide; avoid another lifecycle framework document.
4. If persisted scanner metadata/capabilities need refresh, add a new numbered migration under the [migration workflow](../../../.claude/docs/migration-scripts.md), with preview and a PR run reminder. If corrected discovery exposes missing early prices, include a fixed-scope backfill with the dense timestamp cache. Preserve existing rows and reader state, including old Monad rows. Metadata-only repairs disable price scanning. If no persisted accessor changes are required, document why no migration is needed.
5. Roll out dependency changes and compatible guard configuration before enabling strategies. Existing malformed pending tickets require reconciliation from evidence before restarting. Begin with a proven same-token vault and small operator-selected funding. A funded live canary is a separate authorised run; this planning task does not send production transactions.

## Focused validation and completion evidence

Use the existing tests as the starting point: eth-defi `tests/erc_4626/vault_protocol/test_upshift.py`, `test_yearn_upshift_deposit_permissions.py`, `tests/erc_4626/test_upshift_multi_asset_events.py`, and adjacent SimpleVault/Guard tests; executor `tests/erc_4626/test_vault_async_cli_commands.py`, `tests/ethereum/test_vault_settlement_lifecycle.py`, `tests/units_tests/test_home_chain_async_settlement.py`, and `tests/cli/test_vault_test_trade.py`.

Keep a small number of meaningful parametrised scenarios rather than duplicating the lifecycle for every name:

| Check | Evidence required |
|---|---|
| Real multi-asset fork lifecycle | Correct asset discovery, guarded approval/deposit/request, correct receipt-block epoch, maturity, real funded claim and exact amounts |
| Legacy queued and zero-lag forks | Correct ABI/signature, synchronous classification where applicable, fees and native receipts |
| Refusal/recovery boundaries | Denied sender, cap/asset failure, immature or illiquid claim, stale malformed ticket, competing external request, duplicate resolver and restart |
| Shared framework regressions | Focused standard ERC-4626/Lagoon and Atoma/Ostium tests covering the changed approval, receipt, pending accounting and settlement paths |
| Executor public commands | Both `vault-test-trade` and `perform-test-trade` invoked by focused integration tests, real guarded transfers, state reload and async settlement; report whether manual or CI |
| Different payout token | Real token conversion route and correctly accounted buy/withdraw/sell; otherwise explicit unresolved support blocker |
| Recovery provider | Minimal real Hypersync query against a known historical completed request, with actual receipt matching; local newly mined fork transactions cannot appear in external Hypersync |
| Per-vault availability | Dated matrix of success, denied sender, unavailable liquidity/capacity or missing route; no blanket “supported” claim from ABI compatibility |

Use `anvil_fork_pool`, the canonical chain midnight block constants, `xdist_group` and `evm_snapshot_revert` as documented; deploy expensive custody fixtures once. For external-payout recovery on Anvil, have an ordinary account execute the actual public batch method, then pass its known receipt through existing recovery hooks or a narrowly mocked indexer lookup. The payout contract call and receipt analysis remain real; only discovery of the new local transaction needs substitution because remote Hypersync cannot index it. Separately test the real historical provider. Do not use `eth_getLogs` for discovery.

Run focused tests with `.local-test.env`, Poetry, worktree `PYTHONPATH` and extended command timeouts. Executor tests must resolve both executor and eth-defi to the intended revised checkouts; confirm imported `__file__` paths. Routine amounts and simulation settings should use the existing CLI defaults; RPC/private credentials remain supplied externally. Final run records must contain exact commands, date, commit IDs, fork blocks, redacted provider details, interventions and results. Include a PR comment with the actual external integration result when implementation is submitted. No Sphinx build is required.

Completion requires corrected adapter lifecycles, real guarded executor round trips through both public commands, restart-safe accounting, and a current result/blocker for every listed vault. Different payout token vaults are complete only when the return to USDC is proved. Neither this plan nor the diagnostic fork observations constitute that acceptance.

## Implementation and validation

The implementation repairs the existing manager and adds a legacy subclass,
verified transaction ABIs, current sender admission and receiver quota checks,
request-block epoch parsing, native payout analysis, ticket valuation and
indexed completion recovery. Guard configuration uses existing Solidity
validators; no new guard bytecode is required. Both Lagoon deployment paths
retain Upshift identity and the separate LP token.

Executor changes reuse shared approval selection, mined-request parsing,
pending state, valuation and settlement recovery. Delayed test trades resume
by manager capability. Different payout assets are refused before dispatch;
no token is relabelled as USDC. The fixed test strategy uses existing universe
constructors, so its public-command tests do not depend on a bundled catalogue
update. The standalone test separately exercises the real licensed catalogue.

Observed coverage on the shared Ethereum midnight fork:

- Sentora, Core USDC, Gamma USDC and Upshift USDC complete a real
  `SimpleVaultV0`/`GuardV0` round trip: 10 USDC in and 9.999999 USDC out.
- Both `perform-test-trade` and `vault-test-trade` exercise real Lagoon custody.
  CLI coverage includes a zero-lag exit, pending-state reload and an ordinary
  external batch paying before the executor claims. Only discovery of that new
  local batch is substituted; its transaction and receipt analysis are real.
- A separate live Ethereum/Hypersync check recovers request
  `0x40efc71d6d4aef920bbadd167a46598adfab13284cc23975f5f271667f4e7901`
  to payout `0x295dd9ff252902841dde27bd87b75d8f49ff29f6c07a9b663e0172ce67271e28`,
  validating 982.352046 shares and 1010.322429 USDC without production writes.
- Receiver, owner, input-token, malformed-ticket and outside-request checks
  refuse invalid execution or aggregate accounting.

The [dated availability report](2026-10-30-upshift-availability.json) contains
dated reads for all 15 audit entries, including Monad and HyperEVM. Its
`account_admission_unverified` status is intentional when no actual custody
address was supplied. It does not claim per-vault transaction success. Its
historical USPC `custody_admission_required` status is superseded by the
shortlist rule: permissioned deployments are excluded, not cleared by account
admission. The current diagnostic reports `permissioned_excluded`. Axis, Tori and earnAUSD still require verified
conversion routes. Those three are not enabled for a USDC strategy. Wildcat's
verified older implementation has a per-deposit limit but no global-cap getter;
that exception is tied to its exact implementation, and unknown cap failures
remain errors.

Migration `017-upshift-lifecycle-metadata.py` refreshes the reviewed persisted
fields and clears historical caps mislabelled as liquidity. The staged Parquet
correction is tested across 200,003 rows, including NaNs, and preserves every
price, timestamp, row and unrelated column. It creates no discoveries or early
price gaps, so it needs no state backfill. It has not been run in production.
Before rollout, stop scanner writers and use the existing migration runbook to
preview and apply 017; refresh the normal feed/bundle afterwards. Deploy or
reconfigure compatible existing guard policies before strategy enablement.

Validation completed on 2026-10-31:

| Focused check | Result |
|---|---|
| Adapter, guarded paths, protocol metadata, permissions, migration preservation and real manager construction | 29 passed |
| Existing metadata consumers | 20 passed |
| Executor settlement, valuation and command helpers | 114 passed |
| Real Lagoon public-command integration, including NEMO's 30-day queue | 5 passed |
| Existing Atoma public-command resume integration | 1 passed |

Each pytest invocation sourced `.local-test.env`, used Poetry and an extended
shell timeout. Worktree tests explicitly selected the revised eth-defi and
executor sources with `PYTHONPATH`; final pin verification uses the executor's
submodule checkout. Tests involving production providers used the configured
Ethereum RPC, Hypersync and licensed metadata API, with no credentials recorded.
The exact focused modules are `tests/guard/test_guard_simple_vault_upshift.py`,
`tests/erc_4626/vault_protocol/test_upshift.py`,
`tests/erc_4626/vault_protocol/test_yearn_upshift_deposit_permissions.py`,
`tests/vault/test_upshift_metadata_repair.py`,
`tests/vault/test_vault_manager_contract.py`,
`tests/erc_4626/test_fix_upshift_vaults_script.py`, and
`tests/erc_4626/test_tori_metadata.py` in eth-defi. Executor modules are
`tests/ethereum/test_atoma_valuation.py`,
`tests/ethereum/test_vault_settlement_lifecycle.py`,
`tests/units_tests/test_home_chain_async_settlement.py`,
`tests/cli/test_vault_test_trade.py`, `tests/erc_4626/test_upshift_cli.py`, and
`tests/erc_4626/test_atoma_async_cli.py::test_atoma_test_trade_resume`.

The independent implementation review used Claude CLI `claude-opus-5-5`.
It found a misplaced abstract-method decorator, overly broad ticket valuation,
and recovery/admission edge cases. Those findings were corrected and covered
by focused regressions; its completed follow-up returned **“No blocking
findings”**. Existing window-gated protocols retain their delayed-funding
classification, while live prices refresh configurable lifecycle metadata.
The guarded fee-bearing instant method is also exercised with exact net
amounts; it is the same multi-asset selector selected by zero-lag defaults.

At the implementation handoff, the executor was pinned to the locally reviewed
adapter revision. Neither repository had been pushed, and no production
migration or canary had been run.

## Permission correction and rollout

The 31 October 2026 admission audit found the old blanket public classification
in all 126 Upshift records in the downloaded production metadata. Direct
onchain reads found 12 sender-gated deployments, including USPC. Five were in
the exported catalogue. USPC is excluded from the permissionless shortlist;
the historical availability report is retained as dated evidence, not clearance.

Migration `018-upshift-deposit-permissions.py` uses the scanner's shared adapter
selection and permission resolver to refresh the admission field across all
stored Upshift deployments. It uses one recent provider-safe block per chain,
preserves unrelated metadata and history, and fails before staging changes if
a policy is unknown or the scanner selects another adapter. Pending 017 covers
only USPC among the 12 affected deployments; 018 then corrects the remaining 11
if policy is unchanged. These counts are observations, not fixed assertions.

Validation on 31 October 2026: 119 focused tests passed, with one optional live
migration test skipped. This includes real fixed-state Ethereum policy and
membership reads, guarded lifecycle regressions, metadata exports and 017/018
sequencing. A manual preview and apply of 018 on a disposable production
metadata copy inspected 126 rows and corrected 12 permission fields through
the real adapter factory and supplied chain RPC providers. Every other
metadata field, lead and discovery boundary was preserved; preview created no
files, the backup matched its input, and the journalled rerun skipped. The
separate tests preserved price and reader-state bytes. Both Claude Opus 5.5
reviews approved the final correction with no blocking findings; prose was
checked against the repository writing guide. No production state was changed.

**Run after merge:** use the [production migration procedure](../../../.claude/docs/migration-scripts.md#production-runbook)
from `~/vault-scanner/web3-ethereum-defi`, inspect the Compose mounts and build
the merged, stamped image first. Stop all scanner writers. Preview the pending
numbered batch, inspect the reported changes, then apply it:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
```

Migration 018 affects only `vault-metadata-db.pickle`; it needs no price
backfill. Pending 017 separately clears the reviewed historical liquidity
field. Validate the journal and normal scanner publication afterwards: USPC
must export `whitelisted` in `deposit_permission`, `whitelist.status` and
`deposit_manager.deposit_permission`. Refresh strategy metadata and confirm
the existing whitelist exclusion. Do not rewrite frozen research inputs.
Production migrations remain unapplied. At the implementation handoff, no
changes had been pushed; publication is tracked in the implementation PRs.

## Review record

The plan was checked against both repositories' vault documentation and existing interfaces using the executor's `sanity-check-plan` guidance. Claude CLI 2.1.296 completed a grounded review with exact model `claude-opus-5-5`, verified both baselines and the untracked plan, and returned “Approve with revisions”. The smoke test and review both returned successful final result events. Corrections were checked against the actual sources:

| Finding | Disposition |
|---|---|
| Payout events lack date/share identity | Require call-data/request correlation and recovery after a competing batch pays first. |
| Legacy fixed entitlement differs from multi-asset floating entitlement | Separate valuation/receipt expectations; extract the existing Atoma calculation into a shared hook only where needed. |
| CLI bundle and custody fixture gaps | Inspect the trading-strategy dependency; name a test strategy and require Lagoon-guarded public-command tests. |
| Guard work and ticket repair may be over-scoped | Prefer existing guard configuration primitives; inspect state before authoring any repair. |
| Allocation limits, blacklists, liquidity exports and zero-lag simulation | Add explicit corrections and regression cases. |
| Suggested automatic allocation of a third party's surplus claim | Not adopted: executor master and its lifecycle guide explicitly prohibit aggregate reconciliation. Keep the halt and document/test the availability risk. |
| Suggested that a real fork batch eliminates indexer substitution | Partly adopted: execute a real public batch; remote Hypersync still cannot discover newly mined fork transactions. |

A final blocking-only Claude CLI review using `claude-opus-5-5` completed successfully after these corrections and returned **“No blocking findings”**. Residual risks are live liquidity, conversion-route availability, historical-state expiry, indexer coverage/lag and the disclosed third-party aggregate-claim halt. Writing-style and repository conformance checks passed, along with local-link, address-format, whitespace and final-newline checks. No implementation tests or Sphinx build were run for this plan-only change.
