# Lighter Core pool execution and satellite deployment

The [existing guard integration](../eth_defi/lighter/README-lighter-guard.md) restricts Ethereum custody calls to native USDC and the Safe recipient. Pool mint/burn and secure-withdrawal requests use the strategy's Lighter API key. Dynamic pool selection is a software policy; the Ethereum guard cannot enforce it. The strategy key therefore retains broader Lighter trading authority, as described in [Lighter's API-key documentation](https://apidocs.lighter.xyz/docs/api-keys).

## Deployment

Pass `LagoonLighterActivation()` as `lighter_activation` to `deploy_multichain_lagoon_vault()`. Its default hub is `hyperliquid`, satellite is `ethereum` and API-key slot is 4. Before any worker starts, the deployer needs **1 native USDC on Ethereum** and native gas on every selected chain. No hub USDC, subscription, CCTP quote, burn or relay is required for Lighter activation. Native-balance preflight rejects empty wallets; keep enough gas for contract deployment, activation and owner transactions.

When selecting HyperEVM, the deployer must also be an activated Hypercore user.
The coordinator checks this before starting any chain worker: holding native
HYPE on HyperEVM alone does not enable the large-block API. Activate the deployer
separately through a [HyperEVM-to-Core transfer](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/hypercore-less-than-greater-than-hyperevm-transfers)
or `eth_defi.hyperliquid.evm_escrow.activate_account_sponsored()` with additional
HyperEVM USDC. This prerequisite is distinct from activating the satellite Safe.

All contract workers finish before the Ethereum deployer transfers exactly 1 USDC to the new satellite Safe. The coordinator records the transfer hash before broadcast, checks the credited balance, deposits through the custody guard, backs up/registers the API key and verifies it. It then finalises Safe owners and reads back owner sets and thresholds. A failed activation prevents owner finalisation; partial progress is preserved in the protected report.

The 1 USDC is a **deployer contribution**, not a Lagoon subscription: no investor shares are issued and no reimbursement is created. The report records `funding_source=ethereum_deployer`, `contribution_usdc`, `funding_tx_hash` and `subscription_usdc=0`. The resulting Lighter collateral belongs to the vault; include it once in initial multichain NAV before settling future investor subscriptions. The coordinator verifies contribution conservation but does not initialise trading NAV or settle the hub.

Configure native USDC explicitly (`any_asset=False`) and Lighter's canonical Ethereum deployment. CCTP permissions are optional for activation and remain useful for subsequent strategy transfers. The manual example keeps them configured alongside Hypercore and ERC-4626 permissions. Single-chain activation retains its existing local 1-USDC Lagoon subscription and sub-account behaviour.

After loading credentials and selected RPCs from a private environment:

```shell
PREFLIGHT_ONLY=true poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py
poetry run python scripts/lagoon/deploy-lagoon-lighter-multichain.py
```

Only the deployer key and selected RPCs are mandatory environment inputs. Chain selections, report paths, API-key slot and logging have defaults. `CHAINS=arbitrum,hyperliquid,ethereum` selects an Arbitrum hub without adding a deployment-time bridge. The old `CCTP_MAX_FEE_USDC` deployment setting and `LagoonLighterActivation.max_bridge_fee` field have been removed. Existing serialised funding reports still load.

Use [the gas distribution script](source/tutorials/lifi-feed-crosschain.rst) before deployment if needed. For the default topology, or an Arbitrum hub with Ethereum/HyperEVM satellites, start with approximately 30 native USDC and source ETH on Arbitrum. The maximum nominal spend is USD 10 for gas on each low-gas destination plus 5 USDC to Ethereum. After loading the deployment key and RPCs privately, run from the repository root:

```shell
# Inspect quotes without sending transactions.
PRIVATE_KEY="${LAGOON_MULTCHAIN_TEST_PRIVATE_KEY:?Load the deployment key first}" \
SOURCE_CHAIN=arbitrum TARGET_CHAINS=ethereum,hyperliquid SOURCE_TOKEN=usdc \
MIN_GAS_USD=5 TOP_UP_GAS_USD=10 ETHEREUM_USDC_AMOUNT=5 DRY_RUN=true \
poetry run python scripts/lifi/feed-cross-chain.py

# Execute after reviewing the quotes; the script asks for confirmation.
PRIVATE_KEY="${LAGOON_MULTCHAIN_TEST_PRIVATE_KEY:?Load the deployment key first}" \
SOURCE_CHAIN=arbitrum TARGET_CHAINS=ethereum,hyperliquid SOURCE_TOKEN=usdc \
MIN_GAS_USD=5 TOP_UP_GAS_USD=10 ETHEREUM_USDC_AMOUNT=5 DRY_RUN=false \
poetry run python scripts/lifi/feed-cross-chain.py
```

Add extra selected destinations such as `base` to `TARGET_CHAINS`, allowing another USD 10 per low-gas chain. Arbitrum's source ETH must already be funded. Set `ETHEREUM_USDC_AMOUNT=0` if Ethereum already has its 1-USDC activation amount: a positive value requests a new transfer on every invocation. Verify the final credited USDC after fees.

The shared deployment preflight asserts native gas on every selected chain and at least 1 native USDC in the Ethereum deployer wallet **before workers start or the activation report is created**. Funding failures include this guide and a distribution command for the selected chains. The funding phase repeats the checks after contract deployment, before the contribution; a failure there requires explicit recovery from the saved deployment, not a fresh deployment rerun. The positive native-balance check does not estimate the entire gas bill.


## Pool execution

`eth_defi.lighter.execution` exposes strict account and investor-holding reads, current pool observations, sizing, signing, submission and receipt reconciliation. Use the pinned optional SDK installation instructions in `scripts/lagoon/lagoon-lighter-trade-example.py`. Inject its synchronous signer methods into `LighterPoolExecutor`; the library adds no mandatory SDK dependency or signing cryptography.

Use one submitting process and executor per account/API-key pair. Supply a durable `persist(operation)` callback. `prepare_pool_deposit()` and `prepare_pool_redemption()` fetch current data, validate capacity/holdings and save the signed hash, payload and nonce before submission. `submit()` sends once through an HTTP session without automatic POST retries. Acceptance is `submitted`, not `confirmed`; call `reconcile()` and confirm actual holdings with `wait_for_pool_shares()`. An uncertain submission blocks subsequent preparation. After restart, load `LighterOperation.from_dict()` and reconcile the original hash before doing anything else.

Share amounts are raw integers passed directly to `sign_mint_shares` and `sign_burn_shares`. Current monetary sizing divides pool equity by raw share supply; it never uses USDC decimals or daily chart prices for shares. Operator fees are estimated separately on positive investor profit. The API reports both operator fees and minimum operator share rates in percentage points; the execution model converts both to decimal fractions. Signed share transactions have no enforceable USDC slippage field, so estimates are not atomic spending or redemption guarantees. Public-pool capacity and LLP cooldown/staking constraints follow [Lighter's pool rules](https://docs.lighter.xyz/trading/public-pools). LLP entry reads authenticated `accountLimits`; automatic LIT staking is excluded.

Missing required equity or holdings fields raises an error on this execution path. Historical readers retain their existing permissive behaviour. Keep account equity and pool holdings valuations separate until real mint/burn observations establish whether equity includes pool shares.

## Secure withdrawal

After confirmed pool redemption, wait for available collateral. `prepare_secure_withdrawal()` validates the available monetary amount and signs the secure USDC request. Submit/reconcile it separately. Fetch authenticated withdrawal history and identify the matching history ID from execution evidence; do not select a withdrawal merely because its amount or timestamp looks similar. `wait_for_claimable_withdrawal()` uses the exact ID, bounded logged polling and a refreshable read-only token callback.

Secure history remains `claimable` even after Lighter automatically claims to
the L1 owner, according to the [withdrawal-history reference](https://apidocs.lighter.xyz/reference/withdraw_history).
Check the owner's L1 pending balance before claiming. When it is positive, claim
the actual settled amount through `claim_usdc_to_lagoon_safe_from_lighter()`;
fees/precision may make it differ from the request. When it is zero, verify the
matching credit to the Safe against saved balance or receipt evidence before
recording an automatic claim. A ready history row alone proves neither an
unclaimed L1 balance nor receipt by the Safe.

The three-chain tutorial also handles an automatic claim racing its own L1
claim. If the original claim reverted, a later unwind verifies its failed receipt
and the matching Safe credit before returning funds. It preserves the failed
hash and sends no replacement claim. A missing receipt remains uncertain; a
positive pending balance must cover the history amount before a new claim is signed.

`request_usdc_withdrawal_from_lighter_account()` sends the existing guarded L1 recovery request; `fetch_lighter_pending_balance()` reads the owner’s settled claimable balance. It still requires separate settlement and claim. Neither route uses fast withdrawals, which require an L1 EOA key unavailable to a Safe. The delay estimate is dynamic, as described in [Lighter's withdrawal documentation](https://apidocs.lighter.xyz/docs/deposits-transfers-and-withdrawals).

## Protected progress and manual recovery

The coordinator creates an exclusive mode-0600 JSONL report before deployment. The generated API key is saved there before registration. Default JSON serialisation of deployment results omits private keys; explicit secret export must remain protected. Reports also contain completed chain addresses, initial worker nonces, signed funding/deposit/registration hashes and confirmed funding. A partial contract deployment can require recovering additional addresses from transaction logs using those initial nonces. Signed Ethereum transactions for funding/deposit/key registration are recorded before broadcast.

On interruption, read the last stage and reconcile the original Ethereum funding, deposit and key-registration receipts before any further action. Do not repeat a transfer after losing its acknowledgement. Finish owner setup using each Safe's current threshold. There is no automatic rollback or replay. Hub settlement is a later operation requiring complete multichain NAV.

## Trade-executor hand-off

- Route `lighter-pool-<index>` through the Ethereum satellite, preserving API deployment identity, custody chain 1, signing chain 304 and synthetic dataset chain 9998 separately.
- Load the API private key from protected configuration and persist operation records in existing strategy state.
- Treat bridging, pool transactions and withdrawals as separate executions. Reconcile each original operation after a restart.
- Include satellite USDC, Lighter assets and unsettled receivables once in global Lagoon NAV. Remove a bridge receivable when its destination receipt is counted. Expose account equity and pool values separately until their inclusion relationship is verified.
- Keep Hypercore and ERC-4626 routing independent. Universe construction, routing and portfolio-state changes belong to trade-executor.

For bootstrap with no pool exposure, conservation is `initial funding = all Safe USDC + Lighter collateral + net unsettled receivables + actual fees`. Initial funding is the recorded deployer contribution for satellite activation, or the subscription for an investment round trip. The comparison applies within the existing API rounding tolerance. `reconcile_bootstrap()` checks this equality and rejects missing values or double counting. This is not an aggregate trading NAV implementation.

## Protocol assumptions to verify

The funded three-chain lifecycle below confirmed millisecond `entry_timestamp` values, raw integer mint/burn sizing and a secure withdrawal with a guarded Ethereum claim for one public pool. Positive-profit operator fees and other transaction-status outcomes still require verification. LLP sizing currently treats `accountLimits.max_llp_amount` as a total monetary allocation cap and subtracts current holdings; confirm that interpretation and any percentage limit against a funded account. Unknown transaction statuses retain the original pending operation and never authorise a replay. These checks are not replaced by fixture-only tests.

## Real lifecycle verification

The authenticated, funded manual command is:

```shell
poetry run python scripts/lagoon/lagoon-lighter-pool-roundtrip.py
```

Supply the protected deployment report, deliberately selected pool index, Ethereum RPC and claim wallet through the private environment described in its module docstring. Use an idle Safe-owned Lighter account with at least 10 USDC already-accounted collateral by default. The test withdraws the account’s entire available collateral, including unused bootstrap collateral. It signs mint → confirms actual shares → burns → confirms zero shares and available collateral → requests secure withdrawal → waits for the exact claimable history record → claims actual USDC to the Safe. A protected dated report saves separate equity and holdings observations before/after each transition. The script requires exclusive use of the account, scans every withdrawal-history page, rejects existing pending withdrawals and requires exactly one new secure-USDC history ID after confirmation. It then matches that exact ID. The API history schema does not expose a transaction hash, so this test-only identification depends on the exclusive-writer assumption; shared accounts need stronger execution-event evidence.

For read-only provider decoding, run `source .local-test.env && poetry run pytest tests/lighter/test_execution_live.py -q`. These public reads do not substitute for the funded lifecycle. Production readiness requires the funded lifecycle and demonstrated NAV conservation; record its exact command, date, provider and redacted outcome in the eventual PR comment. The funded entry below does not establish a complete round trip.

Focused unit checks, native offline signing and public API reads cover the support code; the shared Ethereum fork checks the existing guarded custody route. A fork cannot process transactions through Lighter’s live sequencer. The three-chain lifecycle below was verified on mainnet; the separate standalone prefunded activation coordinator was not exercised by that run. Record exact commands and dated results in the PR comment instead of treating public reads as proof of a financial lifecycle.


## Three-chain manual tutorial

The [multichain tutorial](source/tutorials/lagoon-multichain.rst) now includes
`scripts/lagoon/lagoon-multichain-core-roundtrip.py`: Arbitrum investor vault,
Ethereum Lighter satellite and HyperEVM Hypercore satellite. It uses a single
25-USDC subscription (23.02-USDC default allocation floor), saves protected state,
and leaves positions open until `ACTION=unwind` is invoked manually. `ACTION=status`
is read-only. Repeated unwind reconciles saved operations rather than redeploying.

`SIMULATE=true` exercises real forked custody/bridge/Lagoon contracts with explicit
Core/API settlement doubles and no overnight wait. It does not verify either live
sequencer. Measured non-recoverable cash dust is disclosed separately; generated
API keys remain protected alongside the state file. See the script docstring for
mode, amount, funding, SDK and recovery details.

### Funded entry observed on 2026-10-27

Manual mainnet execution completed deployment, the 25-USDC Arbitrum subscription,
both fast CCTP transfers, Hypercore investment and a confirmed Lighter mint.
It required explicit continuation of the saved deployment after correcting two
live-only assumptions: the deployer needed Hypercore activation before large-block
deployment, and the `userAbstraction` API returns `default`/`disabled` for manual
accounts. No contracts, subscriptions or confirmed deposits were repeated.

The initial command was:

```shell
source .local-test.env
LAGOON_MULTCHAIN_TEST_PRIVATE_KEY="$GMX_PRIVATE_KEY" SIMULATE=false ACTION=deploy \
  USDC_AMOUNT=25 POSITION_USDC=10 LOG_LEVEL=info PYTHONUNBUFFERED=1 PYTHONPATH="$PWD" \
  poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py
```

The Arbitrum vault is `0x71Fee8C1B8D4087C6b6d6003a9eD4dccf9e71a13`;
the shared Safe address is `0xA32F5518F1963f0187103C7f019c1dBDe8177ff2`.
At entry, Hypercore held about 10 USDC in `0x1e37a337Ed460039D1B15Bd3bC489DE789768D5e`,
locked until **2026-10-28 20:35:19 UTC**. Lighter account **764445** minted
**6,810 shares** in pool **281474976688087**, spending **9.999580 USDC**.
The mint hash is `e7f8da4042eba2f0caa0102c4bc4fcabcb06075670cd9a39116794730d15059d7bc8a8541531cc2f`.
The API reported `entry_timestamp=1793133715587`, which the parser now decodes
as milliseconds. Its `total_asset_value` fell from 11 to 1.00042 USDC after
minting and excluded the separately reported pool shares for this account.
This observation does not establish the inclusion relationship for every account mode.

At the final read, gross assets were approximately 23.99769 USDC, including
current pool values. The 1-USDC Hypercore Safe activation fee and 0.003222 USDC
of outbound CCTP fees explain the principal reduction; market values fluctuate.
Deployer gas funding and the separate deployer activation remain outside NAV.

Protected state, key backup, verification observations, logs and exact recovery
scripts are saved under `~/.tradingstrategy/lagoon-multichain-core-roundtrip/mainnet/`.
The entry checkpoint outcome was `invested`; both holdings were readable through
`ACTION=status`. The following section records their subsequent unwind.

### Funded unwind observed on 2026-10-28

The original saved deployment completed its manual unwind, including the
Ethereum guarded claim, both return bridges and normal Lagoon investor
redemption. The final claim was confirmed at **21:59 UTC**, with outcome
`redeemed_with_dust`.

```shell
source .local-test.env
LAGOON_MULTCHAIN_TEST_PRIVATE_KEY="$GMX_PRIVATE_KEY" SIMULATE=false ACTION=unwind \
  PYTHONUNBUFFERED=1 PYTHONPATH="$PWD" \
  poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py
```

This was a recovered run, not an uninterrupted pass. Three distinct Hypercore
withdrawals were verified as rejected: two quoted full amounts raced changing
equity, and a 99% withdrawal left a disallowed small remainder. Their original
hashes and rejection evidence were retained. A new full exit using the official
interface's `usd=0` convention succeeded. The Lighter share burn succeeded before
an empty withdrawal-history page exposed its non-empty terminal cursor; correcting
pagination allowed continuation without another burn. Its secure withdrawal took
approximately 25 minutes to become claimable. The API can retain `claimable`
after an automatic claim, but this run used the explicit guarded claim.

| Accounting item | USDC |
| --- | ---: |
| Original subscription | 25 |
| Hypercore PnL after leader commission | +0.035825 |
| Lighter realised PnL | -0.086186 |
| Hypercore Safe activation fee | -1 |
| All CCTP fees | -0.004314 |
| Hypercore return fee | -0.001617 |
| Remaining Core spot cash | -0.008383 |
| Claimed by the deployer on Arbitrum | **23.935325** |

These values reconcile exactly. ETH/HYPE gas costs are separate. Final API and
contract reads confirmed zero vault positions, Lighter collateral and L1 pending
balance; zero USDC in all three Safes and the Lagoon vault/silo; zero investor
shares and pending redemption. Both return messages were consumed. Repeating
the terminal unwind changed no deployer nonce.

Public transaction evidence: [Hypercore full exit](https://hyperevmscan.io/tx/0xb1c44a2b0a95e76c51356dbe3e84a82465b0dc72773dc47683b012f73e6ac82b),
[Lighter Ethereum claim](https://etherscan.io/tx/0xb9a8792c746a42ee8db5812e7ca4aea90527be97bd5d2b1465f0fd5fe71e09cb)
and [final Lagoon redemption claim](https://arbiscan.io/tx/0xde4470ede1243535d3b52ee23ddcd4ae9a8fa841e7b920807ca25405c3ac4355).
Protected `unwind-verification.json`, state and logs remain in the original run
directory. Preserve that state; do not redeploy or reuse it as a fresh example.
