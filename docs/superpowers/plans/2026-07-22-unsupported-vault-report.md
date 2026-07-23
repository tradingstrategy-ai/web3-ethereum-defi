# Unsupported vault report support plan

> **For agentic workers:** Implement this plan against `origin/master` after eth-defi PRs #1347 and #1349. Use `add-vault-abi-methods` for the new IPOR and Lagoon accessors and any other additions to existing proxy interfaces; use `more-vaults` for the new lifecycle managers and certification work. Do not use `add-vault-protocol` unless implementation reveals a genuinely new detection/metadata integration. Do not mark a vault class as supported until a guarded fork test proves the complete advertised lifecycle.

**Goal:** Classify every outcome in the [trade-executor unsupported-vault report](https://github.com/tradingstrategy-ai/trade-executor/pull/1573#issuecomment-5047946461), then add the missing eth-defi protocol support and regression coverage without treating private, paused, capped, or asynchronous vaults as generic synchronous ERC-4626 vaults.

**Baseline:** The report used eth-defi commit `fbe485d9db2a2c3f05c998d71ed00d4260ee1ed3` from PR #1347. Start implementation from current `origin/master`, which also contains PR #1349's cSigma capacity-aware manager. Rebase this work before editing so that Lagoon's `force_settle()` lifecycle and the cSigma fix are not reimplemented.

**Ownership result:** Of the 129 rows, 17 require new eth-defi work, one cSigma row is already fixed on `origin/master`, 90 require trade-executor changes, and 21 are correctly classified closed-vault outcomes.

| Report category | Rows | Owner | Action |
|---|---:|---|---|
| IPOR restricted caller | 6 | eth-defi | Add caller-aware admission pre-flight and typed rejection |
| Lagoon `XJy8` / `NotWhitelisted()` | 8 | eth-defi | Add whitelist-aware admission pre-flight and typed rejection |
| YieldNest successful receipt not analysed | 1 | eth-defi | Correct the ABI/event analyser and prove the supported lifecycle |
| Accountable `InsufficientAmount()` | 1 | eth-defi | Reproduce the exact entrypoint and pre-flight the minimum amount |
| Upshift multi-asset manager missing | 1 | eth-defi | Add the protocol-specific multi-asset lifecycle manager |
| cSigma immediate redemption capacity | 1 | eth-defi, already fixed | Retain and extend PR #1349 regression coverage |
| Ember successful receipts not analysed | 5 | trade-executor | Route analysis through `EmberDepositManager` |
| Lagoon async simulation missing | 28 | trade-executor | Orchestrate the PR #1347 request/settle/claim API |
| No-trades state inference | 57 | trade-executor | Fix simulated position/trade bookkeeping |
| D2 funding closed and metadata deposit closed | 21 | Neither | Keep as classified unavailable outcomes |

**Shared whitelist-reporting contract:** Add the exact user-requested predicates `VaultBase.is_whitelisted_deposit() -> bool` and `VaultBase.is_account_whitelisted(address: HexAddress) -> bool`. Both base implementations must raise `NotImplementedError`; adapters opt in only when they have a reliable protocol-specific probe. Retaining the `is_*` names is a deliberate exception to the repository's `fetch_*` convention for network reads: these are Boolean capability/admission predicates whose public API names are fixed by this amendment, even though an adapter may answer them with an onchain view call. `is_account_whitelisted()` reports membership in the applicable access policy, not whether a transaction is executable immediately; IPOR membership with a non-zero scheduling delay is therefore true, while the deposit manager must still reject an unscheduled synchronous deposit.

Add a string enum, for example `VaultDepositPermission`, with snake-case members and values `whitelisted`, `permissionless`, and `unknown`. `is_whitelisted_deposit() is True` maps to `whitelisted`, false maps to `permissionless`, and `NotImplementedError` maps to `unknown`. Persist its string value as a `_deposit_permission` field in `VaultRow` scanner metadata because `calculate_lifetime_metrics()` receives stored rows rather than live vault objects.

Do not reuse `_best_effort_vault_read()` for this probe: its legacy allowlist includes programming errors such as `AttributeError`, `KeyError`, `RuntimeError`, and `TypeError`. Add a dedicated permission-read helper whose allowlist is limited to `NotImplementedError` plus the concrete transport, unavailable-method, ABI-decode, and contract-call exceptions observed for these view calls, such as `ConnectionError`, `TimeoutError`, `RequestException`, `DecodingError`, `BadFunctionCallOutput`, `ContractLogicError`, `MismatchedABI`, and `Web3RPCError`. Log these failures and map them to `unknown`; deliberately exclude `AttributeError`, `KeyError`, `RuntimeError`, `TypeError`, broad `ValueError`, and broad `Web3Exception` so programming defects still mark the scan row broken. Leave the existing best-effort helper unchanged for its current callers. Unit-test every downgraded category and representative excluded programming errors. Transaction admission pre-flights are not best-effort and must propagate unexpected RPC or contract errors.

During lifetime-metric calculation, copy an existing non-null nested `deposit_manager` report and add `deposit_permission` beside `deposit_flow` and `redemption_flow`; never mutate the stored capability mapping. A base adapter raising `NotImplementedError` or an allowed best-effort read failure must already have been stored by the scanner as `unknown`; lifetime metrics also default a legacy non-null manager lacking `_deposit_permission` to `"deposit_permission": "unknown"`. If `deposit_manager` is null, keep it null rather than creating a manager object solely for this enum. This is a vault metadata/pickle extension, not a `vault-prices-1h.parquet` schema change; if the field is later moved into Parquet, add it with a null-default migration and test that migration against production data. Keep whitelist policy separate from pause, capacity, balance, allowance, liquidity, and synchronous/asynchronous lifecycle state.

## cSigma immediate redemption capacity

**Symptom:** At Ethereum block `25,588,603`, a deposit into cSigma USD (`0xd5d097f278a735d0a3c609deee71234cac14b47e`) succeeds, but immediate full redemption asks for `908,021` raw shares when `maxRedeem()` is only `45,402`. The generic `redeem_4626()` assertion aborts instead of explaining that only about 5% is immediately redeemable.

**Cause:** cSigma's ERC-4626 surface reports a caller-specific immediate redemption capacity below the share balance. A generic synchronous manager assumes the full newly minted balance can be redeemed. This is a liquidity/admission condition, not permission to silently clip the requested redemption.

**Necessary support:** Do not implement this again. PR #1349 on `origin/master` adds `CsigmaDepositManager`, capacity-aware `maxRedeem()` pre-flight, and a typed `VaultFlowUnavailable` result. Verify the rebased implementation still refuses an unavailable full redemption before broadcast and never converts a full-exit request into an unnoticed partial exit. Keep capability reporting dependent on the same live pre-flight.

**Integration test extension:** Extend `tests/erc_4626/vault_protocol/test_csigma.py` with the reported cSigma USD address and block if the condition remains reproducible there. Deposit through the manager, request the complete newly minted share balance, and assert a typed capacity rejection, the requested and available raw amounts, no redemption transaction, and unchanged share/asset balances. Retain one existing public cSigma full deposit/redeem fork test so the negative regression does not replace happy-path coverage.

## YieldNest receipt analysis and lifecycle

**Symptom:** The deposit transaction for YieldNest RWA MAX (`0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8`) succeeds at Ethereum block `25,588,742` (`0x4e82efdbcd9c24016d8b5bd756038bbaaa1084726b5d1abf27fc3a6d08f738bc`), but eth-defi cannot derive the deposited assets and minted shares from the receipt.

**Cause:** `YieldNestVault.vault_contract` loads `eth_defi/abi/yieldnest/Vault.json`, whose trimmed interface lacks event definitions. The generic receipt analyser therefore cannot construct or decode the ERC-4626 `Deposit` event. YieldNest also has no certified protocol-specific deposit manager, so successful event decoding alone must not accidentally advertise an untested two-way lifecycle.

**Necessary support:**

- Resolve the proxy implementation at the historical report block and record the canonical verified source in the YieldNest ABI README. Update `eth_defi/abi/yieldnest/Vault.json` with the verified interface, including the exact `Deposit` and `Withdraw` event definitions.
- Reproduce the saved successful receipt first. If it emits canonical ERC-4626 events, keep the common analyser; if it emits a wrapper or protocol-specific event, add `eth_defi/erc_4626/vault_protocol/yieldnest/deposit_redeem.py` with a `YieldNestDepositManager` that parses the actual logs and returns exact raw asset/share amounts.
- Establish whether the reported vault supports immediate redemption from its buffer or requires a lock/queue. Add the corresponding pre-flight and typed unavailability result. Do not infer queued-redemption support or add `YieldNestVault` to `CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES` until both advertised directions pass a deterministic fork lifecycle.

**Integration test extension:** Split the regression into two explicit checks. First, fetch and analyse the saved status-1 transaction receipt at block `25,588,742`, asserting its historical event values without rebroadcasting it. Second, fork from block `25,588,718`, fund a hot wallet with the actual denomination asset, submit a fresh deposit through `vault.get_deposit_manager()`, and assert receipt status and exact balance/event deltas calculated for that fresh transaction; do not reuse the saved transaction's minted-share value. Then test the real redemption path: redeem immediately only when the buffer and lock state allow it, otherwise assert the typed unavailable result and add a separate fixture whose ordinary protocol state permits redemption. Add a capability regression to `tests/erc_4626/test_deposit_probe.py`: leave capability false/unknown if only receipt analysis is proven, or pin it to supported only when this work proves the complete lifecycle.

## IPOR restricted caller admission

**Symptom:** Six Ethereum IPOR Fusion vaults revert deposits with selector `0x068ca9d8` and the simulated wallet `0xa2b04c6a053ab2efbc699f5dd0f0957742a41629` encoded as the argument. A representative vault is BL USDC WSR Loop (`0x95b2ed8f821570f85fd0e3e6e7088c6296587088`) at block `25,588,603`.

**Cause:** `0x068ca9d8` is `AccessManagedUnauthorized(address)`. A historical read of the vault's IPOR access manager reports `canCall(wallet, vault, deposit(uint256,address)) == (false, 0)`. The generic certified manager checks ERC-4626 capacity but not IPOR's selector- and caller-specific access policy, so it broadcasts a transaction that is guaranteed to revert. This is a private-vault admission restriction, not evidence that another public deposit entrypoint should be guessed.

Use this complete historical fixture matrix from the report. The caller for all six rows is `0xa2b04c6a053ab2efbc699f5dd0f0957742a41629`, as encoded in every revert:

| Vault | Address | Fork block |
|---|---|---:|
| BL USDC WSR Loop | `0x95b2ed8f821570f85fd0e3e6e7088c6296587088` | `25,588,603` |
| TESS USDC Ethena Loop Vault | `0x888e1d3c509c80e24cab8a4872e164b7e5a6eb10` | `25,588,627` |
| TESS USDC Lending Optimiser | `0xc825779c89120eeef746c51130b362478e181d39` | `25,588,627` |
| TESS USDC wsrUSD Loop | `0x4c5a611694c426cae9335d53e95b885090cf8c31` | `25,588,627` |
| TESS sUSDe PYUSD (USDC) Loop Vault | `0x32f07401eb177f2c0fc4f95f3928050d88dae7ed` | `25,588,627` |
| Tesseract USDC Lending Optimizer | `0xc2a119ea6de75e4b1451330321cb2474eb8d82d4` | `25,588,627` |

**Necessary support:**

- Add `eth_defi/erc_4626/vault_protocol/ipor/deposit_redeem.py` with `IPORDepositManager`, derived from the generic manager, and return it from `IPORVault.get_deposit_manager()`.
- Implement `IPORVault.is_whitelisted_deposit()` by asking the existing IPOR access manager for the role assigned to `deposit(uint256,address)` and comparing it with `PUBLIC_ROLE`: public role means `permissionless`; any other role means `whitelisted`. If an older deployment does not expose a usable access manager, raise `NotImplementedError` so the report exports `unknown` rather than guessing.
- Implement `IPORVault.is_account_whitelisted(address)` with `canCall(address, vault, deposit(uint256,address).selector)`. Return true for immediate access or a non-zero scheduling delay because both indicate membership in the applicable access policy; keep the delay in the manager's detailed pre-flight so delayed permission is not mistaken for immediate executability.
- Extend `VaultFlowError` on the `origin/master` baseline with optional `function_selector: HexBytes` and `access_delay: int` fields before using them from a manager. Store them as structured attributes and include them in `__str__()` diagnostics; retain backward compatibility for existing callers. The baseline has no exception serialiser, so do not invent a second report schema solely for this change; if implementation discovers a consumer that serialises `VaultFlowError`, extend that representation with both fields. Add `test_vault_flow_unavailable_preserves_access_context` to `tests/vault/test_deposit_redeem.py` to cover construction, attribute retention, and exact string formatting, plus the existing serialiser if one is found.
- Before creating a deposit, call the existing access-manager `canCall(caller, vault, deposit(uint256,address).selector)`. Convert a false result or non-zero delay into `VaultFlowUnavailable` containing the caller, `function_selector`, decoded access error, and `access_delay`. Distinguish a caller that is never permitted from one that is permitted only after an access-manager scheduling delay; the synchronous manager rejects both, but the latter must remain discoverable for a future scheduled-execution flow. Preserve unexpected RPC/contract errors rather than classifying them as private.
- Apply the same caller-aware policy to the exact redemption selector and combine it with IPOR's existing account lock-time/redemption-delay checks. Do not impersonate a privileged account or bypass access management.
- Keep static protocol capability separate from live caller capability: a public IPOR deployment can be supported while each restricted deployment reports that the current caller cannot create the flow.

**Integration test extension:** Create `tests/ipor/test_ipor_whitelist.py` for the Ethereum historical matrix. Build `JSON_RPC_ETHEREUM` Anvil fixtures at blocks `25,588,603` and `25,588,627`, group the six view-only cases by block to avoid six process launches, and use the fixed reported wallet. Assert the exact selector, `is_whitelisted_deposit() is True`, `is_account_whitelisted(reported_wallet) is False`, and `canCall == (false, 0)`. On BL USDC WSR Loop, unlock and fund that wallet on the fork, perform the manager's complete admission attempt, and assert a typed pre-broadcast rejection with the new structured fields, no transaction hash, and unchanged balances.

Refactor the deterministic Base public lifecycle in `tests/ipor/test_ipor_deposit.py` to execute deposit and redemption through `vault.get_deposit_manager()` instead of calling `deposit_4626()` and `redeem_4626()` directly. Assert `is_whitelisted_deposit() is False`, arbitrary-account membership, and exact manager analysis/balance/event results after the ordinary delay advance. Keep `tests/erc_4626/vault_protocol/test_ipor.py` for metadata/accessor coverage rather than treating it as lifecycle coverage. Add unit tests for `PUBLIC_ROLE` mapping, delayed `canCall` policy membership versus immediate executability, and decoding `AccessManagedUnauthorized(address)` so later ABI changes cannot turn the policy back into an opaque selector.

## Lagoon whitelist admission

**Symptom:** Deposits into eight Lagoon vaults, including AltaETF (`0x3be67ba2d3fec744d1d2b5d564c83f57372578e4`) at Ethereum block `25,588,627`, revert with `XJy8` after the current admission check says a request can be created.

**Cause:** ASCII `XJy8` is selector `0x584a7938`, which resolves against the Lagoon ABI to `NotWhitelisted()`. The reported hot wallet is not whitelisted on the representative deployment. `ERC7540DepositManager.can_create_deposit_request()` currently models pause state but not caller/controller whitelist state. Some legacy Lagoon deployments also revert when `isWhitelistActivated()` is queried, so relying unconditionally on that version-specific getter would introduce another failure.

Use this complete historical fixture matrix. The sanitised report does not repeat the sender beside the Lagoon rows; start with the report's simulated Ethereum wallet `0xa2b04c6a053ab2efbc699f5dd0f0957742a41629`, then confirm from the reconstructed `requestDeposit` calldata/trace whether Lagoon validates sender, owner, receiver, or controller and record that exact address in the parameter set:

| Vault | Address | Fork block |
|---|---|---:|
| AltaETF | `0x3be67ba2d3fec744d1d2b5d564c83f57372578e4` | `25,588,627` |
| Block4Block | `0x9fdbaaa76194d56e49cade12c1f216f47d2b865e` | `25,588,647` |
| Der USDC | `0xf10801bcc3deaf467fb8b3dbb7430111822e6dab` | `25,588,647` |
| Der base USDC | `0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87` | `25,588,647` |
| Muchacho USDC | `0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594` | `25,588,672` |
| Noon STS USDC | `0xb993c32f578e5156369330787cf8c8fe033bf40e` | `25,588,672` |
| Strada USDC | `0xcb58582b0d52ce5feecb06ba9ce66598b0d57886` | `25,588,672` |
| pyUSDC | `0x175ea882b492c9b7a6d5852fe9da560dc7af1c72` | `25,588,672` |

**Necessary support:**

- Extend `eth_defi/erc_4626/vault_protocol/lagoon/deposit_redeem.py` with a caller/controller-aware whitelist pre-flight for `requestDeposit`.
- Implement `LagoonVault.is_whitelisted_deposit()` from `isWhitelistActivated()` on versions that expose it. For a verified legacy implementation where that getter reverts, use the documented `isWhitelisted(address)` semantics only when source/version analysis proves that a disabled whitelist returns true for every address; otherwise raise `NotImplementedError` and export `unknown`.
- Implement `LagoonVault.is_account_whitelisted(address)` with `isWhitelisted(address)`. It must return true for arbitrary accounts on permissionless Lagoon deployments and the actual membership value when the whitelist is active.
- On deployments that expose `isWhitelistActivated()`, check activation and the exact address the contract validates through `isWhitelisted()`. Confirm by tracing the reported calldata rather than assuming sender, owner, receiver, and controller are interchangeable.
- For legacy implementations where the activation getter is absent or reverts, simulate the exact `requestDeposit` call only after the caller has sufficient balance and allowance, or use an RPC state override to supply those preconditions. This prevents an ERC-20 transfer failure from masking the later whitelist check when admission is queried before approval. Translate only the known `NotWhitelisted()` result into `VaultFlowUnavailable`; re-raise unknown failures with their original data.
- Do not add the test wallet to the whitelist or impersonate an administrator. These eight vaults should become accurately classified private/unavailable vaults, not successful public deposits.

**Integration test extension:** Extend `tests/lagoon/test_erc_7540_deposit_redeem.py` with `JSON_RPC_ETHEREUM` historical fixtures grouped at blocks `25,588,627`, `25,588,647`, and `25,588,672`. Parameterise the eight view-only policy checks from the table. For AltaETF, unlock and fund the confirmed caller/controller, assert `is_whitelisted_deposit() is True` and `is_account_whitelisted(validated_address) is False`, approve the asset, assert whitelist failure before deposit broadcast, preserve selector `0x584a7938` in the typed diagnostic, and verify unchanged asset/share balances. For a legacy deployment without a source-proven activation getter, explicitly assert `unknown` reporting rather than forcing a Boolean classification. Trace through an Anvil archive fork with step tracing if the configured RPC does not expose `debug_traceCall`; do not abandon the sender/controller distinction merely because the upstream node lacks trace support. Retain a public Lagoon fork test that asserts `permissionless` reporting, arbitrary-account admission, and the complete PR #1347 `requestDeposit -> force_settle(ticket) -> claim` and redemption lifecycle, proving the new check does not block public vaults.

## Accountable minimum deposit

**Symptom:** The simulated Accountable buy for Hyperithm Delta Neutral Vault (`0x7cd231120a60f500887444a9baf5e1bd753a5e59`) on Monad reverts with selector `0x5945ea56` at block `89,437,694`.

**Cause:** The checked-in `AccountableAsyncRedeemVault.json` decodes `0x5945ea56` as `InsufficientAmount()`. `AccountableDepositManager` already reads `MIN_AMOUNT_WEI()` for redemption dust, but deposit construction only checks positivity and `maxDeposit()`. The report describes a satellite open/deposit path, so the exact failing calldata must be traced before concluding whether the minimum applies to the direct ERC-4626 deposit or a surrounding satellite entrypoint.

**Necessary support:**

- Reproduce the call on a current Monad fork because Monad has no archive-node historical state. Capture the exact target, selector, raw input amount, and the contract frame raising `InsufficientAmount()`.
- If direct `deposit(uint256,address)` rejects amounts below `MIN_AMOUNT_WEI()`, use that same onchain value in deposit construction/pre-flight and raise `VaultFlowUnavailable` before approval/broadcast. Do not duplicate the constant in Python.
- If the error belongs to a distinct satellite entrypoint, add its verified ABI and protocol-specific transaction binding only after the trace identifies the required parameters and event shape. Do not route around the satellite by assumption.
- Preserve the existing Accountable queued-redemption lifecycle and minimum redemption checks.

**Integration test extension:** Extend `tests/erc_4626/vault_protocol/test_accountable.py` with the reported vault on a latest Monad fork. Fetch `MIN_AMOUNT_WEI()` anew on every run because the fork head moves; assert that one raw amount below that live value is rejected pre-broadcast and leaves balances unchanged, while the live minimum and a normal amount complete the manager-driven deposit and decode exact assets/shares. If the trace identifies a satellite call, exercise that exact call instead. Retain the existing request/process/claim redemption test and add a unit assertion that `0x5945ea56` decodes to `InsufficientAmount()`.

## Upshift multi-asset lifecycle

**Symptom:** Sentora USD Earn (`0x74ad2f789ed583dbd141bbdafc673fe1f033718b`) is detected as `upshift_multi_asset_like`, but `UpshiftVault.get_deposit_manager()` deliberately raises `NotImplementedError`, so no deposit simulation can start.

**Cause:** This Upshift implementation is not generic ERC-4626. Its verified implementation exposes `deposit(address assetIn,uint256 amountIn,address receiverAddr)`, `previewDeposit(address,uint256)`, a custom `Deposit(assetIn,amountIn,shares,senderAddr,receiverAddr)` event, and scheduled redemption methods `requestRedeem(...)` and `claim(...)` plus `instantRedeem(...)`. The checked-in `MultiAssetVault.json` contains view functions only, and eth-defi has no model for selecting/approving an input asset or carrying a redemption ticket.

**Necessary support:**

- Replace/extend `eth_defi/abi/upshift/MultiAssetVault.json` with the verified implementation interface and record the implementation/source URL in the Upshift ABI documentation. Include all transaction functions, errors, whitelist/cap/pause views, and lifecycle events used by the manager.
- Add `eth_defi/erc_4626/vault_protocol/upshift/deposit_redeem.py` with an `UpshiftDepositManager`. Its deposit request must explicitly carry the chosen input token, validate the token and caller whitelists, check pause/cap state, approve that token, call the three-argument deposit, and analyse the custom event without pretending `asset()` is the only valid input.
- Model redemption as the contract actually exposes it. Prefer `instantRedeem` only when its live preconditions are inspectable and satisfied. Otherwise persist the year/month/day or epoch returned by `requestRedeem`, implement claim analysis, and add an Anvil-only `force_settle(ticket)` only if the ordinary protocol processor can be identified and safely impersonated from onchain state.
- Return `VaultFlowUnavailable` for non-whitelisted assets/callers, exhausted caps, pause state, or an unprocessed redemption. Do not publish capability or remove the existing `NotImplementedError` expectation until both the advertised deposit and redemption paths are complete.

**Integration test extension:** Replace the unsupported-manager assertion in `tests/erc_4626/vault_protocol/test_upshift.py` with a full Ethereum fork lifecycle at block `25,588,810`. Discover a whitelisted input asset from onchain state, fund the wallet, deposit through the new manager, and assert the custom event plus exact token/share balance deltas. Request redemption, process it only through the real scheduled role/state transition, claim, and assert exact returned assets and burned shares. Add negative tests for a non-whitelisted asset/caller and any pause/cap condition. Extend `tests/erc_4626/test_upshift_multi_asset_events.py` with ABI topic/parser tests and add capability coverage only after the full fork test passes.

## Reported issues outside eth-defi

These rows must be fixed or retained in trade-executor; changing eth-defi would either duplicate an existing adapter or misclassify valid live-vault state.

- **Five Ember receipt failures:** `EmberDepositManager.analyse_deposit()` already decodes Ember's `VaultDeposit` event, and `tests/erc_4626/vault_protocol/test_ember_deposit_redeem.py` proves the lifecycle. The report stack calls the generic `analyse_4626_flow_transaction()` directly from `tradeexecutor/ethereum/vault/vault_routing.py`. Trade-executor must settle through `vault.get_deposit_manager().analyse_deposit()` (and the matching redemption analyser), with one reported Ember receipt as its regression fixture.
- **Twenty-eight Lagoon async failures:** PR #1347 already supplies request tickets, status, analysis, and the Anvil `force_settle(ticket)` hook. Trade-executor must persist the ticket and run `request -> force_settle -> claim` in simulation instead of asking a synchronous helper to complete an ERC-7540 flow in one transaction. No new eth-defi protocol implementation is needed.
- **Fifty-seven no-trades failures:** The assertion originates in `tradeexecutor/state/position.py:is_spot` after the adapter work, so trade-executor must attach the simulated vault trade before position-side inference or make the inference valid for a pending simulated trade. These rows span already-supported protocols and are not evidence of 57 eth-defi gaps.
- **Twenty closed metadata rows and one D2 funding-closed row:** Keep the existing `deposit_closed`/unavailable classification. Tests should assert a clean skip, not mutate vault state to make deposits possible.

## Delivery order and verification

Implement in small protocol-specific changes so classification improvements can ship without waiting for Upshift's larger lifecycle:

1. Rebase onto `origin/master`; confirm PR #1349's cSigma tests and PR #1347's Lagoon async tests pass.
2. Add the shared `VaultBase` whitelist methods, deposit-permission enum, narrow permission-read helper, `_deposit_permission` scanner field, and backward-compatible lifetime-metrics export. Unit-test base `NotImplementedError`, all three enum outputs, null-manager preservation, legacy-manager fallback, the narrow exception boundary and excluded programming errors, nested report placement, and non-mutation of stored metadata.
3. Extend `VaultFlowError` with selector/delay context and its diagnostic tests, then add the IPOR and Lagoon method mappings and admission pre-flights, including the complete negative historical fixture matrices and manager-driven public happy paths.
4. Trace and add Accountable's amount/entrypoint pre-flight.
5. Fix YieldNest ABI/event analysis, then prove and advertise only its tested lifecycle.
6. Implement and certify the complete Upshift multi-asset lifecycle.
7. Hand the three trade-executor-owned regressions back with the manager API and fixtures above. Treat the subsequent 129-vault rerun as a joint eth-defi/trade-executor acceptance gate, not as a blocker for completing the eth-defi implementation alone.

Before running pytest, ensure `.local-test.env` exists in this worktree, copying it from the main checkout if needed. Use the repository wrapper and focused tests only:

```bash
source .local-test.env && poetry run pytest tests/vault/test_deposit_permissions.py -v
source .local-test.env && poetry run pytest tests/erc_4626/test_scan_features.py::test_create_vault_scan_record_persists_deposit_permission tests/research/test_vault_metrics.py::test_calculate_lifetime_metrics_exports_deposit_permission tests/research/test_vault_metrics.py::test_calculate_lifetime_metrics_defaults_legacy_deposit_permission_to_unknown tests/research/test_vault_metrics.py::test_calculate_lifetime_metrics_preserves_null_deposit_manager -v
source .local-test.env && poetry run pytest tests/vault/test_deposit_redeem.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_csigma.py -v
source .local-test.env && poetry run pytest tests/ipor/test_ipor_whitelist.py tests/ipor/test_ipor_deposit.py tests/erc_4626/vault_protocol/test_ipor.py -v
source .local-test.env && poetry run pytest tests/lagoon/test_erc_7540_deposit_redeem.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_accountable.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_yieldnest.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_upshift.py tests/erc_4626/test_upshift_multi_asset_events.py -v
source .local-test.env && poetry run pytest tests/erc_4626/test_deposit_probe.py -v
```

Run `poetry run ruff format` on changed Python files before review. The eth-defi work is complete when every exported non-null deposit manager contains one of the three `deposit_permission` enum values, null managers remain null, the 17 open eth-defi rows either complete their supported lifecycle or return a typed, pre-broadcast live-admission result, and the cSigma row remains safely classified. The joint 129-vault acceptance rerun additionally requires trade-executor to fix its 90 rows and retain the 21 closed-vault rows as clean skips.
