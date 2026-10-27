# Lighter Core vault support for multichain strategies

Date: 2026-10-27. Status: implementation is in the workspace following user authorisation. The [implementation guide](../../README-lighter-core-execution.md) records the supported APIs and manual commands; funded lifecycle and production NAV verification remain open. Earlier [Opus feedback](2026-10-27-lighter-core-vault-guard-review.md) and the [Fable 5.1 re-review](2026-10-27-lighter-core-vault-guard-fable-review.md) remain preserved; the user directed implementation of this plan without applying the latest review suggestions.

## Summary and confirmed decisions

Support investment in Ethereum-settled Lighter Core pools alongside Hypercore vaults and ERC-4626 vaults, using an Ethereum satellite Safe.

Decisions agreed:

- The strategy holds the Lighter API private key.
- Pool selection is dynamic, with software validation.
- Activate Lighter and register the API key during multichain deployment.
- Fund activation through a primary Lagoon subscription and a cross-chain USDC transfer.
- Require the deployer to hold the full bootstrap subscription in native USDC on the hub chain before any deployment transaction.
- Prefer CCTP Fast Transfer where the source chain supports it; HyperEVM uses its already-fast standard route.
- Finish contract deployment and configuration on every chain before starting the final funding and Lighter activation phase; final Safe ownership setup follows key verification.
- Give the manual deployment script sane defaults; require only inputs that cannot safely be defaulted or inferred, such as credentials and selected-chain RPC connections.
- Implement guard configuration and support helpers in this repository; document the trade-executor hand-off separately.
- Do not depend on Lighter EVM becoming available.

Keep this an extension of the existing integration: reuse guard contracts, deployment reports, CCTP helpers and the signer. No new Solidity policy layer, automatic deployment recovery engine, cross-chain rollback, or general portfolio-accounting framework is required.

**The existing Solidity guard already supports the required L1 custody operations.** The principal work is satellite activation, API-signed pool transactions, withdrawal orchestration and accounting.

Lighter pool entry and exit use `mint_shares` and `burn_shares`, signed with an API key. They do not pass through the Ethereum guard. Consequently, adding a Solidity pool allowlist would not enforce restrictions on these transactions. [Official signer implementation](https://github.com/elliottech/lighter-python/blob/main/lighter/signer_client.py)

The accepted security model protects the L1 custody route but trusts the strategy's API key for Lighter activity. A compromised key can misuse broader trading permissions and cause losses. Software pool checks cannot provide Hypercore-style onchain enforcement. Lighter documents that secure withdrawals return to the account's L1 owner; transfers to another L1 owner and fast withdrawals also require L1 authorisation. Do not describe the API key as allowing arbitrary withdrawal recipients. [API-key permissions](https://apidocs.lighter.xyz/docs/api-keys)

## Existing support and required changes

| Area | Existing implementation | Required work |
|---|---|---|
| Solidity custody guard | `LighterLib`, `GuardV0Base`, Safe module integration | Reuse existing selectors and validation; no new pool selector |
| Ethereum custody | Guarded USDC deposit and withdrawal claim helpers | Generalise helpers to `AutomatedSafe`, including satellites |
| API-key setup | Native key generation and Safe-owner `changePubKey` | Extract shared activation ceremony and support satellite deployment |
| Multichain deployment | Deterministic Safes, satellite configuration, CCTP | Add coordinated funding and activation before ownership handover |
| Pool data | Discovery, snapshots, historical prices | Add investor holdings and current execution-oriented pool reads |
| Pool execution | No reusable mint/burn implementation | Add signing, submission, confirmation and reconciliation helpers |
| Valuation | Account equity helper | Add pool holdings, withdrawal receivables and accounting reconciliation |

### Guard configuration

Reuse `LighterLib` on Ethereum with:

- Canonical Ethereum `ZkLighter` and native USDC.
- The satellite Safe as the allowed Lighter recipient.
- Existing `deposit`, `withdraw` and `withdrawPendingBalance` permissions.
- `changePubKey` remaining a Safe-owner operation, inaccessible through the asset-manager whitelist.
- `anyAsset=False`; dynamic Lighter pool selection does not require enabling it.

Retain the existing Hypercore and ERC-4626 permissions on their respective execution chains. Configure CCTP destinations and the deterministic Safe recipient for transfers between participating chains, preserving the existing requirement that `destinationCaller` is zero.

The existing CCTP guard does not cap `maxFee` or constrain finality. This remains an existing guard limitation, not a new onchain guarantee. Bootstrap helpers must select the finality supported by the source chain as described below and enforce the configured fee ceiling. A fee ceiling is not the actual charged fee; this review does not establish an arbitrary-fee drain. General CCTP guard hardening is outside this feature. [Circle fee semantics](https://developers.circle.com/cctp/concepts/fees)

Do not introduce a nominal `LighterVaultLib` or Solidity pool registry for API-signed operations. Update documentation to distinguish custody restrictions from API-key investment authority.

### Multichain deployment and activation

Use three phases in the existing multichain deployment function: read-only preflight, parallel contract setup, then a final coordinated funding/activation phase. There is no need for separate deployment commands or a workflow engine. Preserve the existing public single-chain behaviour when satellite Lighter activation is disabled. Here, the **hub chain** means the primary chain with the Lagoon vault that accounts for the bootstrap subscription.

For the new multichain path:

1. **Preflight before launching any chain deployment worker.** Validate RPC chain IDs, shared deterministic Safe parameters, native USDC, Lighter configuration, bridge route/fee budget, settlement budget and deployer permissions. If API-key generation is enabled, read the deployer's native USDC balance using the hub RPC and require at least the full bootstrap subscription (20 USDC by default). Ethereum USDC, bridged USDC variants and balances already in Safes do not satisfy this wallet-balance check. Fail with the hub chain, token, required amount and observed amount before any transaction is broadcast. Check gas funding separately; native gas is not part of the USDC budget.
2. **Complete contract setup on all chains.** Deploy and configure the primary Lagoon vault and all participating Safes/modules in parallel, then join every worker and validate their results. Do not fund, burn USDC or call Lighter while any chain setup remains unfinished or has failed. Keep the Safes in their initial deployer-owned state; owner finalisation is deferred.
3. **Begin the final funding/activation phase.** Recheck the deployer's hub USDC balance and bridge fee/availability immediately before moving funds. Use the normal hub Lagoon subscription, settlement and share-claim lifecycle to create accounted bootstrap capital. This second check handles balances or fees changing during contract deployment.
4. Transfer activation capital from the hub Safe to the Ethereum Safe using CCTP Fast Transfer where supported, or the source chain's already-fast standard route. Reuse the existing burn, attestation and receive helpers. Wait for destination receipt and the actual USDC balance before calling Lighter.
5. Deposit the minimum supported amount from the Ethereum Safe through the guarded asset-manager path; wait for account discovery and credited collateral.
6. Generate the API key and persist it to a protected file before registration. Register it through a direct Safe `changePubKey` transaction and verify the exact public key through Lighter. Reuse the existing configurable key index (default 4); abort if the selected slot already contains a different non-zero key.
7. Persist the protected setup result, then apply the configured owners and thresholds using the existing Safe helper. Preserve its policy of retaining the deployer; removal, if desired, remains an explicit transaction by the owners. Do not claim that handover removes the deployer.
8. Read back each Safe's owner set and threshold, checking the configured owners plus retained deployer. Return a completed multichain deployment only after every finalisation succeeds.

Use the existing 20-USDC bootstrap subscription as the default funding envelope. Activation uses the existing 1-USDC minimum, subject to current protocol validation. The gross bridge amount is the activation amount plus the configured maximum bridge fee, so the destination can still receive the activation minimum when that fee is charged. Require this gross amount to fit within the subscription and retain unused capital in the Safes. Fees are covered by the subscription, not added to the required deployer balance a second time. Account for actual fees and destination receipt; do not assume transfers will always be free.

Expose the bootstrap source chain and bridge fee allowance through a small multichain activation configuration. The default topology is HyperEVM as the primary Lagoon chain and Ethereum as the Lighter satellite; other supported chains may hold ERC-4626 positions. Ethereum and HyperEVM are supported CCTP domains. [Circle supported chains](https://developers.circle.com/cctp/concepts/supported-chains-and-domains)

### Manual deployment script defaults

Follow the [repository's command line script rules](../../../CLAUDE.md#command-line-scripts) and use `scripts/lagoon/deploy-lagoon-multichain.py` as the example. Keep this a small manual entry point to the deployment helpers. Required configuration is limited to the deployer private key and RPC connections for the selected chains, using existing environment variable names. Deployment generates the Lighter API key; it does not require a pre-existing one.

Use HyperEVM as the hub and Ethereum as the Lighter satellite by default, with the existing Hypercore/ERC-4626 configuration conventions. Default to the 20-USDC subscription, 1-USDC activation amount, API-key slot 4, a fresh random Safe salt, a generated protected report path and existing bounded polling/logging settings. Default the bridge fee ceiling to `Decimal("0.01")` USDC within the subscription: `CCTP_MAX_FEE_USDC` is an optional override, not a required input. The ceiling is a local spending limit, not a fee quote; validate it against the live route quote and abort before burning if insufficient. Do not automatically raise it.

Chain selection, fee allowance, key slot, salt, report path and read-only preflight mode remain optional overrides. Document the normal invocation as `poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py` after loading the private environment. A separate read-only example may set `PREFLIGHT_ONLY=true`; it must not need a fee assignment or other routine settings. No new configuration framework or mandatory acknowledgement variables are needed.

### Bridge execution and recovery

For a source supporting Fast Transfer, pass the existing `FINALITY_THRESHOLD_FAST` (1000) to `prepare_deposit_for_burn()` and a `max_fee` within the configured allowance. For HyperEVM, pass `FINALITY_THRESHOLD_STANDARD` (2000): Circle does not offer source-side Fast Transfer there and documents standard attestation at about five seconds. Select by source-chain capability, not by the Ethereum destination. These are attestation estimates; destination inclusion and Lighter processing still take additional time. [Circle finality and timings](https://developers.circle.com/cctp/concepts/finality-and-block-confirmations)

Fetch the current route fee and, for Fast Transfer, available Circle allowance during preflight and again before the burn; a configured fee ceiling must cover the quoted fee on the gross amount. Abort before burning if the quote exceeds the ceiling or fast capacity is insufficient. Do not silently choose a slow standard route to avoid a fast-transfer fee. If service conditions change after the burn, report the delay through the existing CCTP status polling and retain the original transfer on timeout; never burn again to retry. Reuse the existing Iris HTTP conventions with small `fetch_` helpers for the [fee endpoint](https://developers.circle.com/api-reference/cctp/all/get-burn-usdc-fees) and [fast allowance](https://developers.circle.com/cctp/concepts/fast-transfer-allowance); no bridge-routing framework is needed.

Keep deployment-time activation limited to fresh deployments. Extend the protected deployment report with the current step, per-chain addresses, prepared transaction identifiers and confirmed results. Save evidence before broadcasts and update it after confirmation. On failure, stop further work, preserve successful transactions and report the last known step; do not restart deployment automatically. An activation failure leaves the Safes awaiting owner finalisation. A later failure during finalisation can leave different chains at different owner configurations, which must be reported.

Document manual recovery using the saved report and existing helpers: reconcile receipts and Lighter state, relay an already-burned CCTP message without burning again, check an existing deposit/key before proceeding, and complete owner setup using each Safe's current signing threshold. Funds remain in their actual Safe, Lighter account or pending bridge transfer; do not attempt automatic rollback or add public resume/abort APIs.

Extend deployment reporting to include source funding, bridge receipts and satellite Lighter setup. Preserve secret-free default serialisation, backwards-compatible loading and protected secret-file permissions. Persist the generated key securely before registration so an interruption cannot lose it.

## Support APIs and execution behaviour

### Custody and pool helpers

Generalise the existing helpers in `eth_defi/lighter/lagoon.py` to accept `AutomatedSafe`, preserving existing Lagoon callers. Separate funding through Lagoon from depositing an already-funded Safe balance.

Add synchronous helpers for:

- Fetching investor pool holdings and current pool state.
- Preparing pool entry and exit amounts.
- Signing mint/burn operations through an injected signer interface.
- Submitting signed transactions and fetching their execution status.
- Requesting secure withdrawals and fetching matching withdrawal history.
- Preparing an L1 withdrawal request as a recovery route for available collateral.
- Claiming settled withdrawals through the existing guarded Safe path.

Use the official signer's synchronous signing methods through a small adapter. Keep the SDK optional, following the existing pinned installation pattern; do not implement new signing cryptography or add a mandatory SDK dependency. Perform submission and polling through the synchronous HTTP session; keep state-changing submissions outside the authentication-refresh retry wrapper. No async-to-sync framework is needed.

Use slotted dataclasses for account identity, pool holdings, prepared operations and execution results. Keep Ethereum chain ID, Lighter signing-network identity and synthetic dataset chain ID distinct. Preserve existing `lighter-pool-<index>` identifiers and include deployment identity in execution records.

### Pool execution

Support dynamically selected Ethereum Lighter pools. Before entering, validate deployment, pool identity, current status, available collateral and applicable capacity restrictions. Allow exits from held pools even when they are no longer selected by the strategy.

Use integer share quantities and `Decimal` monetary amounts. Establish share-unit conversions against the pinned signer and real transaction results; do not assume shares use USDC decimals. Current execution sizing must use current pool data, not daily historical chart prices.

Treat amount estimates as estimates where the protocol supplies no enforceable slippage parameter. Do not advertise an atomic spending or redemption guarantee that the signed transaction cannot express.

Read pool-specific constraints. Public pools and LLP have different rules; LLP also has cooldown and staking-related restrictions. Automatic LIT staking is outside this implementation. [Public-pool rules](https://docs.lighter.xyz/trading/public-pools)

### Transaction lifecycle and withdrawals

Represent each stage separately:

```text
Primary Safe
→ CCTP transfer
→ Ethereum Safe
→ Lighter account collateral
→ pool shares
→ Lighter account collateral
→ secure withdrawal pending
→ claimable L1 balance
→ Ethereum Safe
→ optional CCTP return
```

An API acceptance response is not execution confirmation. Store the transaction hash and nonce before submission; reconcile uncertain outcomes before resubmitting. Use one submitting process per account/API-key pair, with serial submissions and an explicit next nonce; the library does not need distributed locking or a job queue. Let downstream callers persist operation records in their existing state. Reuse authentication-refresh helpers only for replay-safe reads. [Lighter transaction lifecycle](https://apidocs.lighter.xyz/docs/core-concepts)

Pool redemption and withdrawal to Ethereum are separate operations. After burning shares, wait for the resulting available balance, request secure withdrawal, wait for the matching claimable record, and claim the actual claimable amount.

Use dynamic withdrawal-delay estimates and bounded polling with progress logging. Fast withdrawals are excluded from the Safe route. [Deposit and withdrawal documentation](https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals)

### Accounting and trade-executor hand-off

Return investor share quantities, entry/principal information, current value, fee information and observation timestamps. The official account model exposes investor holdings separately in `shares`. [Investor share model](https://github.com/elliottech/lighter-python/blob/main/lighter/models/public_pool_share.py)

Expose account equity and pool valuations separately. Verify their inclusion relationship with real before/after mint and burn observations before enabling aggregate NAV. Do not blindly add pool values to `total_asset_value`.

Require reconciliation across settled balances, pool holdings and in-flight transfers. The new execution/valuation path must validate the fields it uses before calling the existing equity parser: missing required equity, balance or holdings data raises a descriptive error, rather than falling back to zero or collateral plus PnL. Keep legacy permissive readers compatible. Distinguish gross share value from estimated withdrawal proceeds after operator fees.

After the initial accounted subscription, the deployment flow performs no further Lagoon settlement. The example must stop after activation and owner finalisation; automated settlement remains disabled operationally until trade-executor can supply verified multichain NAV, including satellite funds and receivables. This is a downstream launch condition, not a new smart-contract settlement lock or an interim NAV implementation.

Document the downstream contract:

- Route Lighter pool identifiers through the Ethereum satellite.
- Persist operation stages and reconcile them after restart.
- Treat bridging, investment and withdrawal as separate executions.
- Include satellite assets and receivables once in global Lagoon NAV.
- Keep ERC-4626 routing and Hypercore routing independent.
- Load the API key from protected configuration.

Changing hyper-ai's universe construction, routing and portfolio state belongs to the trade-executor follow-up.

## Tests, delivery and acceptance

- **Guard regression:** Existing custody calls succeed; foreign recipients, wrong assets, wrong targets and asset-manager key rotation fail. Confirm Hypercore, ERC-4626 and CCTP permissions coexist.
- **Funding preflight:** Insufficient hub-wallet USDC fails before any deployment worker or transaction, even if the deployer has enough USDC on Ethereum. Test exact required balance, the fee-inclusive funding envelope, disabled key generation and a balance change between preflight and the funding phase.
- **Script defaults:** With only the required private key and selected-chain RPC inputs supplied, the manual deployment uses the documented defaults without requiring fee, chain, salt, key-index or report-path variables. Check optional overrides and reject invalid or insufficient fee ceilings before burning. Read-only preflight must not send transactions.
- **Satellite deployment:** Exercise the real orchestration with mocked external responses. Verify all-chain contract setup completes before subscription → bridge receipt → activation → protected key persistence → registration/verification → ownership handover. A failed or unfinished chain must prevent funding/Lighter calls. Cover final owner sets/thresholds, occupied key slots, failure before registration, partial finalisation and redacted progress reports. Assert failures do not trigger automatic replay or another settlement.
- **CCTP speed and fees:** A fast-capable hub uses threshold 1000; HyperEVM uses 2000 without requiring Fast Transfer allowance. Test fresh quotes, insufficient fast allowance, fees exceeding the ceiling, at least the activation amount received after fees and an attestation timeout after a successful burn. There must be no silent slow-route substitution or second burn.
- **Pool execution:** Cover integer conversion, insufficient collateral, closed/full pools, LLP constraints, partial/full exits, nonce collisions, API rejection, accepted-but-unconfirmed transactions and restart reconciliation.
- **Withdrawal/accounting:** Cover fees, claimable amounts differing from requests, delays, missing required fields and prevention of double counting. For the bootstrap with no pool exposure, reconcile the initial subscription against all Safe balances, Lighter collateral and unsettled bridge receivables, less actual bridge fees. Use exact raw USDC for onchain values and the existing `LIGHTER_COLLATERAL_TOLERANCE` for API rounding; do not hide discrepancies behind a percentage tolerance.
- **Fork coverage:** Use the shared Anvil fork pool with `ETHEREUM_MIDNIGHT_BLOCK` and `HYPERLIQUID_MIDNIGHT_BLOCK`, warm cache and snapshot isolation. Reuse `eth_defi.cctp.testing` for fork-only attester replacement and message signing. Fork tests prove L1 behaviour; they do not simulate Circle's live service or Lighter's sequencer.
- **Real integration:** Provide an environment-controlled manual command using a supplied API key and a small funded Safe. Exercise mint → confirmed holdings → burn → secure withdrawal → L1 claim, plus deployment-time activation and CCTP funding. Record dated, redacted results and exact commands in the eventual PR comment. An authenticated read alone is insufficient.
- **Repository checks:** Run focused tests through the required `.local-test.env` and Poetry wrapper. Format changed Python. Rebuild contract artifacts only if Solidity changes become necessary. Do not build Sphinx locally.

Deliver the library changes, deployment example, API documentation and trade-executor hand-off. Production readiness requires a successful real lifecycle and demonstrated NAV conservation across the transitions above.

No production transactions or tests were run during this planning investigation.
