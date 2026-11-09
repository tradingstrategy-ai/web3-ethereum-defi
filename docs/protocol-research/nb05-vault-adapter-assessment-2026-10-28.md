# NB05 vault adapter assessment (2026-10-28)

The principal missing components are a dedicated Atoma deposit/redemption
adapter in `eth_defi` and a Lighter public-pool execution route in
`trade-executor`. Most other protocol adapters already exist. Their remaining
work concerns settlement modelling, deployment-specific validation and
completion of redemption support.

This assessment covers the expanded NB05 strategy from
[vault-notebooks PR #3](https://github.com/tradingstrategy-ai/vault-notebooks/pull/3).
Its minimum execution scope is the **54 distinct vaults actually traded**,
including Fluid cash management, rather than every member of the 347-source
universe. Vault identity is the chain/address pair; each deposit or redemption
counts as one trade. The implementation snapshot reviewed here is `web3-ethereum-defi`
`41d66c4738b4e047a87414f71d68be028c7c7f7b` and `trade-executor`
`09cc233bbd4f2c8976a8fdd932b789d780ae75f7`. These are the code-review revisions,
not the revisions used for the recorded financial runs.

## Recorded backtest result

The [published run result](https://github.com/tradingstrategy-ai/vault-notebooks/pull/3#issuecomment-6437450660)
reports the following whole-wallet metrics before unmodelled native external
performance fees:

| Metric | Recorded value |
| --- | --- |
| Evaluation, UTC | 1 April 2025 to 20 October 2026 inclusive |
| Initial capital | $100,000 |
| Final equity | $117,861.79 |
| Total return | 17.86% |
| Reported CAGR | 11.16% |
| Reported Sharpe | 9.12 |
| Maximum drawdown | -0.49% |
| Successful trades | 797: 597 vault trades and 200 CCTP bridge trades |
| Traded vaults / protocol slugs | 54 / 17 |

The four newly added tagged vaults received no allocations. All executed
trade economics match the earlier NB04 ledger, despite different inputs; this
does not establish a matched-input experiment or an improvement from adding
the tags. The only allocated native vaults were Steady Wealth Builder on
Lighter and Stratwise Multi-Asset Public on Hyperliquid.

## Adapter requirements

The counts below reconcile to 54 vaults and 597 vault trades. CCTP bridge
trades are counted separately. Existing family support does not establish
that every selected deployment is currently open, liquid or fully validated.

| Protocol | Vaults / trades | Work needed |
| --- | ---: | --- |
| **Atoma** | 1 / 8 | **Build a dedicated adapter.** Standard deposit → `requestWithdrawal(shares)` → wait for processed epoch → `claimWithdrawal(epochId)`. Add persistent tickets, actual payout/0.5% exit-fee analysis and Guard support. The generic manager remains callable even though the vault disables ordinary redemption; no public capability is advertised. |
| **Lighter** | 1 / 2 | **Build the executor's public-pool route.** Reuse the mint/burn, signing and withdrawal helpers merged in **[#1668](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1668)**. Connect funding, operation persistence, actual holdings, fees and withdrawal claims. The complete funded round trip and aggregate NAV remain unverified. |
| **Morpho V1 / V2** | 10 / 45 | Complete and certify redemption handling. V1 has residual-share/capacity issues. V2 already performs exact-call simulation because its maximum getters return zero, but immediate redemption remains unproven on the tested fork. Neither advertises a certified complete synchronous lifecycle. |
| **Yearn** | 5 / 22 | Reuse existing ERC-4626 machinery. Four traded vaults use `YearnCompounderVault`, which lacks certified capability metadata. The fifth is classified through the existing V3 adapter. Validate all five deployments' guarded round trips and account-specific capacity checks. |
| **D2 HYPE++** | 1 / 10 | Existing phase-aware adapter and guarded fork-test coverage. Replace synthetic fortnightly windows with actual funding, custody and redemption phases. Preserve mapping-based admission or the required `whitelistAsset` balance for both deposit and redemption; validate real epoch transitions. |
| **Accountable, Upshift, Ember, Plutus** | 7 / 47 | Existing specialised managers. Validate the selected deployments' complete settlement paths, persist pending operations and recognise actual payouts. Upshift also needs explicit accepted-deposit-asset selection. Some current settlement evidence uses protocol mocks. |
| **Lagoon and Gains** | 11 / 103 | Existing request/settlement/claim adapters. Wire their actual lifecycle into the strategy and backtest. |
| **Fluid, 40acres, IPOR, Tokemak, Euler** | 17 / 359 | Reuse existing adapters; check the exact deployments' admission and redemption liquidity. Fluid is particularly important because it funds rebalances from parked cash. |
| **Hyperliquid / Hypercore** | 1 / 1 | Existing native execution route. Integrate real satellite funding, account lockups, fee-adjusted proceeds and reconciliation. Stratwise had only a deposit in this run, so its exit was never exercised. |

The [Atoma reader](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/41d66c4738b4e047a87414f71d68be028c7c7f7b/eth_defi/erc_4626/vault_protocol/atoma/vault.py#L119)
documents its custom withdrawal flow, but has no dedicated manager override.
Atoma Index is on Arbitrum at
`0xcc56410e1a136af0eceb7241c6ae394f4d8b581c`; the ledger contains four deposits
and four redemptions. A seven-day backtest delay does not prove that an
operator-processed epoch will settle at that deadline.

The [Lighter execution guide](../README-lighter-core-execution.md) describes
the existing helpers, custody boundaries and executor hand-off. Pool
`lighter-pool-281474976552443` is Steady Wealth Builder. Its route must preserve
Ethereum custody chain 1, Lighter signing chain 304 and synthetic dataset
chain 9998 separately. The inspected executor's
[routing dispatch](https://github.com/tradingstrategy-ai/trade-executor/blob/09cc233bbd4f2c8976a8fdd932b789d780ae75f7/tradeexecutor/strategy/generic/default_protocols.py#L44)
has no native public-pool route; its Lighter exchange-account route serves a
different position type. Pool operations use an API key, and the Ethereum
guard cannot enforce pool selection. Pool holdings and account equity must
not be combined until their inclusion relationship has been demonstrated
with actual transactions.

The [lifecycle evidence and coverage matrix](../../eth_defi/erc_4626/README-vault-protocol-support.md#evidence-and-certification)
distinguishes callable managers, guarded deposits, complete lifecycles and
settlement limitations. Morpho comprises seven V1 vaults with 34 trades and
three V2 vaults with 11 trades. Its deliberately unpublished complete-lifecycle
capability must not be inferred from a successful deposit or an Anvil-only
liquidity injection. Four Yearn compounders use the
[compounder reader](../../eth_defi/erc_4626/vault_protocol/yearn/compounder.py),
while the fifth, StrategyGearboxLenderUSDC at
`0xf6e2d36c489e5b361cdc962d4568cea663ad5ddc` on Ethereum, has both compounder
and tokenised-strategy features and is classified through the V3 reader.
Inherited V3 capability metadata does not prove this deployment's lifecycle.
The [exact-class capability registry](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/41d66c4738b4e047a87414f71d68be028c7c7f7b/eth_defi/erc_4626/vault.py#L65)
does not certify `YearnCompounderVault`.

The [D2 phase-aware manager](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/41d66c4738b4e047a87414f71d68be028c7c7f7b/eth_defi/erc_4626/vault_protocol/d2/vault.py#L36)
already checks phases and account eligibility. Its docstring says a successful
transaction path has not been fork-proven, but the
[guarded standard lifecycle test](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/41d66c4738b4e047a87414f71d68be028c7c7f7b/tests/guard/test_guard_simple_vault_standard_erc4626.py#L276)
contains a HYPE++ deposit/redemption case at Arbitrum block 387,000,000.
That case keeps 1,000,001 raw USDC outside the deposit to satisfy the
strictly-greater-than-one-USDC balance condition during redemption, and
separately checks refusal when the deposit consumes the eligibility balance.
Explicitly whitelisted accounts take the other admission path. This coverage
does not establish arbitrary live availability or settlement across a trading
epoch; those remain validation work. The
[Hypercore routing implementation](https://github.com/tradingstrategy-ai/trade-executor/blob/09cc233bbd4f2c8976a8fdd932b789d780ae75f7/tradeexecutor/ethereum/vault/hypercore_routing.py)
already handles native deposits and withdrawals. Synthetic dataset chain
9999 must be distinguished from the HyperEVM custody chain 999; these dataset
identifiers do not themselves establish CCTP funding routes.

## Settlement and fee findings

The saved ledger records **31 redemptions with identical request and execution
timestamps** in families whose live managers describe delayed settlement:

| Protocol | Same-cycle redemptions | Live lifecycle |
| --- | ---: | --- |
| Gains | 17 | Epoch-gated withdrawal request followed by redemption claim |
| Upshift | 13 | Operator-settled dated request/claim by default; the optional instant path still needs validation for the selected vault and amount |
| Ember | 1 | Queued withdrawal paid directly by the operator; no depositor claim call |

These conclusions follow the implemented
[Gains manager](../../eth_defi/erc_4626/vault_protocol/gains/deposit_redeem.py),
[Upshift manager](../../eth_defi/erc_4626/vault_protocol/upshift/deposit_redeem.py)
and [Ember manager](../../eth_defi/erc_4626/vault_protocol/ember/deposit_redeem.py).
The ledger does not demonstrate an eligible instant Upshift redemption.
All three traded Upshift vaults carry the multi-asset feature; their deposit
asset must be selected explicitly from the accepted tokens. The instant
redemption constructor checks pause/cap state and share balance, but adds no
separate instant-payout liquidity preflight.
The executor's
[delayed-redemption feature set](https://github.com/tradingstrategy-ai/trade-executor/blob/09cc233bbd4f2c8976a8fdd932b789d780ae75f7/tradeexecutor/state/identifier.py#L86)
omits these three families. Their actual settlement must be reflected before
redemption proceeds fund subsequent allocations. Correcting this can change
trade membership and performance; this assessment does not quantify that
effect.

Separately, Steady's one Lighter redemption also executes with zero delay.
A complete return to the Safe requires the pool burn, available collateral,
secure withdrawal, external settlement and claim. Thus the 31 cases above
are not an exhaustive count of idealised same-cycle exits or funding paths.

Other timing assumptions also need review: the ledger delays Accountable
deposits by two days although its adapter implements synchronous deposits,
and gives D2 and Plutus deposits a 16-day delay. Atoma redemptions have the
modelled seven-day delay. Each direction should follow its actual protocol
lifecycle rather than inheriting a blanket asynchronous classification.

Steady's 20% and Stratwise's 10% native external performance fees remain
uncharged, including Steady's one redemption. The published positive-PnL fee
sensitivity is $51.98, giving $117,809.81 after that estimate. This is not a
withdrawal/high-water-mark replay and supplies no fee-net CAGR, Sharpe or
drawdown. Atoma's charged withdrawal fee is separate. Retrospective universe
membership and tags, sparse NAV observations, forward filling, unknown
historical access, synthetic windows and idealised native funding also limit
interpretation of the reported risk metrics.

## Recommended follow-up

1. Build the Atoma adapter, its guarded request/claim surface and receipt
   analysis, including persistent epoch tickets and actual fee-net payouts.
2. Connect the existing Lighter pool helpers to executor routing, state,
   valuation and satellite funding. Demonstrate the funded round trip and
   reconcile holdings, collateral and transfers exactly once in NAV.
3. Correct directional settlement modelling, replace synthetic HYPE++ windows
   and implement native fee accounting before rerunning the strategy.
4. Complete Morpho redemption support and validate the exact Yearn and
   operator-settled deployments through their supported guarded lifecycles.

Live selection from the wider source universe also needs an execution-capability
filter so future ranking cannot allocate to an unsupported lifecycle. Lighter
LLP remains excluded: this strategy does not supply its required staked LIT.

## Evidence provenance

This was a saved-ledger and implementation review on 28 October 2026. No new
financial simulation, fork lifecycle or live transaction was run. Reported
performance metrics come from the published financial run. A local comparison
independently verified the final equity, all 797 trade records and all 568
daily portfolio-statistics records against the saved module replay.

The published NB05 financial run used executor `83a0dd1b9`. The saved module
replay receipt records engine `7734bec89655e9ae0656e6beed1adce40fd7f522`
with local source changes and an eth-defi submodule at
`b83020ffa2d49615f16b5510e8b47c8b34ec5958`; it is not a clean-master
reproduction. Its saved strategy module is
`strategies/strategy/master-vault-multichain-v2.py`, with SHA-256
`18d3065a4aceefd94cdec08a246afbe10b18396176e8fbb552b5c2636079c6b7`.

The comparison covered chain/address, trade identity, request and execution
timestamps, executed quantities, reserves, prices and failure state. Vault
counts were deduplicated across open, closed, frozen and pending positions.
The local inputs were:

- Notebook reference: `vault-notebooks/data/notebook-to-strategy/nb05/reference/notebook-backtest-state.json`.
- Reference SHA-256: `b462d6755b69c9e545f0fe6510d065ce8db0ba2a8ed3262b23cb4f2798027b07`.
- Module replay: `vault-notebooks/data/xchain3/master-vault-multichain-v2/attempt-02/backtest-state.json`.
- Replay execution receipt: `vault-notebooks/data/xchain3/master-vault-multichain-v2/attempt-02/execution-receipt.json`.
- Input metadata generation time: `2026-10-27T07:54:19Z`.

These ignored local artefacts are not downloadable attachments or files
provided by cloning either repository. Their matching saved economics do not
establish compatibility with a fresh simulation on current master.
