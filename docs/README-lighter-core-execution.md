# Lighter Core pool execution and satellite deployment

The [existing guard integration](../eth_defi/lighter/README-lighter-guard.md) restricts Ethereum custody calls to native USDC and the Safe recipient. Pool mint/burn and secure-withdrawal requests use the strategy's Lighter API key. Dynamic pool selection is a software policy; the Ethereum guard cannot enforce it. The strategy key therefore retains broader Lighter trading authority, as described in [Lighter's API-key documentation](https://apidocs.lighter.xyz/docs/api-keys).

## Deployment

Pass `LagoonLighterActivation()` as `lighter_activation` to `deploy_multichain_lagoon_vault()`. Its default hub is `hyperliquid`, satellite is `ethereum`, bridge fee ceiling is 0.01 USDC and API-key slot is 4. The deployer must have 20 native USDC on the hub before any worker starts, plus gas on every chain. Native-balance preflight rejects empty wallets; it does not estimate the full deployment gas bill. Keep enough gas for the later relay, activation and owner transactions as well as contract deployment. Configure native USDC explicitly (`any_asset=False`), CCTP destinations and Lighter's canonical Ethereum deployment. Other chain configurations retain their Hypercore and ERC-4626 permissions.

Single-chain activation retains its existing 1-USDC subscription and sub-account behaviour; the 20-USDC envelope belongs only to this multichain path.

The opt-in coordinator keeps each worker's wallet/nonce state, joins all contract deployments, subscribes the accounted bootstrap capital, bridges 1 USDC plus the fee allowance to Ethereum, waits for receipt (including a message already relayed by a third party), activates Lighter and verifies the backed-up key. It then finalises Safe owners and reads back every owner set and threshold. The existing helper retains the deployer. A failed activation prevents owner finalisation; partial finalisation is visible in the protected report.

The manual example uses the deployer as sole owner and asset manager, following the earlier multichain tutorial. After loading credentials and selected RPCs from a private environment:

```shell
PREFLIGHT_ONLY=true poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py
poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py
```

Scripts require environment inputs only for values that cannot safely be defaulted or inferred: credentials, user RPCs and deliberately selected financial targets. Amounts, fees, chain selections, timeouts, report paths and logging use sane defaults with optional overrides. Only the deployer key and selected RPCs are required for deployment. `CHAINS=base,hyperliquid,ethereum` selects a Base hub to exercise Fast Transfer. HyperEVM uses its already-fast standard finality; this follows [Circle's source-chain support](https://developers.circle.com/cctp/concepts/supported-chains-and-domains). Fresh quotes and Fast Transfer allowance are checked before funds move and before burn. A quote exceeding the configured ceiling aborts; the helper never raises the ceiling or silently selects a slower route.

## Pool execution

`eth_defi.lighter.execution` exposes strict account and investor-holding reads, current pool observations, sizing, signing, submission and receipt reconciliation. Use the pinned optional SDK installation instructions in `scripts/lagoon/lagoon-lighter-trade-example.py`. Inject its synchronous signer methods into `LighterPoolExecutor`; the library adds no mandatory SDK dependency or signing cryptography.

Use one submitting process and executor per account/API-key pair. Supply a durable `persist(operation)` callback. `prepare_pool_deposit()` and `prepare_pool_redemption()` fetch current data, validate capacity/holdings and save the signed hash, payload and nonce before submission. `submit()` sends once through an HTTP session without automatic POST retries. Acceptance is `submitted`, not `confirmed`; call `reconcile()` and confirm actual holdings with `wait_for_pool_shares()`. An uncertain submission blocks subsequent preparation. After restart, load `LighterOperation.from_dict()` and reconcile the original hash before doing anything else.

Share amounts are raw integers passed directly to `sign_mint_shares` and `sign_burn_shares`. Current monetary sizing divides pool equity by raw share supply; it never uses USDC decimals or daily chart prices for shares. Operator fees are estimated separately on positive investor profit. The API reports both operator fees and minimum operator share rates in percentage points; the execution model converts both to decimal fractions. Signed share transactions have no enforceable USDC slippage field, so estimates are not atomic spending or redemption guarantees. Public-pool capacity and LLP cooldown/staking constraints follow [Lighter's pool rules](https://docs.lighter.xyz/trading/public-pools). LLP entry reads authenticated `accountLimits`; automatic LIT staking is excluded.

Missing required equity or holdings fields raises an error on this execution path. Historical readers retain their existing permissive behaviour. Keep account equity and pool holdings valuations separate until real mint/burn observations establish whether equity includes pool shares.

## Secure withdrawal

After confirmed pool redemption, wait for available collateral. `prepare_secure_withdrawal()` validates the available monetary amount and signs the secure USDC request. Submit/reconcile it separately. Fetch authenticated withdrawal history and identify the matching history ID from execution evidence; do not select a withdrawal merely because its amount or timestamp looks similar. `wait_for_claimable_withdrawal()` uses the exact ID, bounded logged polling and a refreshable read-only token callback. Claim the returned actual `amount` through `claim_usdc_to_lagoon_safe_from_lighter()`; fees/precision may make it differ from the request.

`request_usdc_withdrawal_from_lighter_account()` sends the existing guarded L1 recovery request; `fetch_lighter_pending_balance()` reads the owner’s settled claimable balance. It still requires separate settlement and claim. Neither route uses fast withdrawals, which require an L1 EOA key unavailable to a Safe. The delay estimate is dynamic, as described in [Lighter's withdrawal documentation](https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals).

## Protected progress and manual recovery

The coordinator creates an exclusive mode-0600 JSONL report before deployment. The generated API key is saved there before registration. Default JSON serialisation of deployment results omits private keys; explicit secret export must remain protected. Reports also contain completed chain addresses, initial worker nonces, signed bridge/deposit/registration hashes and confirmed funding. A partial contract deployment can require recovering additional addresses from transaction logs using those initial nonces. Signed Ethereum transactions for bridge/deposit/key registration are recorded before broadcast.

On interruption, read the last stage and reconcile receipts. Relay an already-burned CCTP message; do not burn again. Confirm an existing Lighter deposit or key before any further action. Finish owner setup using each Safe's current threshold. There is no automatic rollback or replay. No further Lagoon settlement is performed after the initial subscription until downstream multichain NAV is ready.

## Trade-executor hand-off

- Route `lighter-pool-<index>` through the Ethereum satellite, preserving API deployment identity, custody chain 1, signing chain 304 and synthetic dataset chain 9998 separately.
- Load the API private key from protected configuration and persist operation records in existing strategy state.
- Treat bridging, pool transactions and withdrawals as separate executions. Reconcile each original operation after a restart.
- Include satellite USDC, Lighter assets and unsettled receivables once in global Lagoon NAV. Remove a bridge receivable when its destination receipt is counted. Expose account equity and pool values separately until their inclusion relationship is verified.
- Keep Hypercore and ERC-4626 routing independent. Universe construction, routing and portfolio-state changes belong to trade-executor.

For bootstrap with no pool exposure, conservation is `subscription = all Safe USDC + Lighter collateral + net unsettled receivables + actual fees`, within the existing API rounding tolerance. `reconcile_bootstrap()` checks this equality and rejects missing values or double counting. This is not an aggregate trading NAV implementation.

## Protocol assumptions to verify

Before production use, observe real investor holdings and receipts to confirm `entry_timestamp` units, share sizing, fee estimates and the supported transaction-status mapping. LLP sizing currently treats `accountLimits.max_llp_amount` as a total monetary allocation cap and subtracts current holdings; confirm that interpretation and any percentage limit against a funded account. Unknown transaction statuses retain the original pending operation and never authorise a replay. These checks are not replaced by fixture-only tests.

## Real lifecycle verification

The authenticated, funded manual command is:

```shell
poetry run python scripts/lagoon/lagoon-lighter-pool-roundtrip.py
```

Supply the protected deployment report, deliberately selected pool index, Ethereum RPC and claim wallet through the private environment described in its module docstring. Use an idle Safe-owned Lighter account with at least 10 USDC already-accounted collateral by default. The test withdraws the account’s entire available collateral, including unused bootstrap collateral. It signs mint → confirms actual shares → burns → confirms zero shares and available collateral → requests secure withdrawal → waits for the exact claimable history record → claims actual USDC to the Safe. A protected dated report saves separate equity and holdings observations before/after each transition. The script requires exclusive use of the account, scans every withdrawal-history page, rejects existing pending withdrawals and requires exactly one new secure-USDC history ID after confirmation. It then matches that exact ID. The API history schema does not expose a transaction hash, so this test-only identification depends on the exclusive-writer assumption; shared accounts need stronger execution-event evidence.

For read-only provider decoding, run `source .local-test.env && poetry run pytest tests/lighter/test_execution_live.py -q`. These public reads do not substitute for the funded lifecycle. Production readiness requires the funded lifecycle and demonstrated NAV conservation; record its exact command, date, provider and redacted outcome in the eventual PR comment. No successful funded run is claimed by this document.

Focused unit checks, native offline signing and public API reads cover the support code; the shared Ethereum fork checks the existing guarded custody route. A fork cannot process transactions through Lighter’s live sequencer. The funded multichain deployment, CCTP relay and complete pool round trip remain unverified on mainnet. Record exact commands and dated results in the PR comment instead of treating public reads as proof of a financial lifecycle.
