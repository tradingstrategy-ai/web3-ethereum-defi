# Lagoon Lighter API-key deployment plan

> **Scope:** Implement this only in the `web3-ethereum-defi` repository and
> the `eth-defi` Python package. Do not add trade-executor configuration or
> runtime trading support; that work belongs to another agent.

> **Follow-up:** The later, separately requested
> `scripts/lagoon/lagoon-lighter-trade-example.py` uses the official Lighter
> SDK only as a tutorial-time dependency for order signing. The deployment
> path and the `eth-defi` package remain independent of that SDK.

**Goal:** Add an opt-in Lagoon deployment flag which makes the minimum proper
Lagoon deposit needed to activate the Safe-owned Lighter account, creates and
registers a trading API key while the deployment signer still controls the
Safe, and stores the key and activation details in the deployment report.

**Baseline:** This plan was written against
`5d7633ec229fdba94ea38129019b4ca9c571e57e`. Rebase before implementation and
adjust locations if the Lagoon or Lighter modules have moved.

## Design decisions

- Add one flag: `LagoonConfig.generate_lighter_api_key: bool = False`.
- When enabled, use API-key index `MIN_API_KEY_INDEX` by default and allow the
  caller to override only the index.
- Deposit exactly `LIGHTER_MIN_MAINNET_USDC`, currently 1 USDC. Do not add a
  configurable activation amount or a second activation flag.
- Generate the key with native Python code in `eth-defi`. Do not add or retain
  a dependency on the Lighter Python SDK.
- Port only the Goldilocks extension-field and ECgFp5 scalar multiplication
  needed to derive a Lighter public key from a random private scalar. Do not
  port order signing, authentication tokens, nonces, withdrawals, Poseidon2,
  API models, or an asynchronous API client.
- Register the public key during deployment with the existing direct Safe
  multisig `changePubKey` transaction. The Safe guard evaluates this
  transaction; it is not sent through `TradingStrategyModuleV0`. Registration
  must happen before the deployer adds the final Safe owners and raises the
  Safe threshold.
- Use the package's existing `requests`-based `LighterSession` for account,
  collateral and registered-key polling.
- Fund the Safe through Lagoon's normal request, valuation, settlement and
  share-claim lifecycle. Never transfer USDC directly to the Safe as an
  unaccounted donation.
- Return a successful deployment report only after the Lighter API exposes the
  account, credits the collateral and reports the registered public key.
- Do not introduce recovery records, resumable stages, status enums, partial
  result exceptions, idempotency machinery or a second deployment entry point.
  A failed deployment raises at the failing operation and retains the existing
  transaction logging.
- Never place the private key in logs, `repr()`, exception messages or the
  default public JSON representation.
- Requiring both manager roles to equal the deployer is a deliberate first
  version limitation. Deployments configured with separate production manager
  addresses cannot enable this flag because the deployment wallet could not
  execute the Lagoon funding lifecycle; role handover is outside this plan.

No Solidity or ABI changes are needed.

## Preconditions

Run a read-only validation before the first deployment transaction when the
flag is enabled. Require all of the following:

- `lighter_deployment` is the canonical Ethereum Lighter deployment;
- Web3 chain ID is 1 and the Lagoon underlying token is native Ethereum USDC;
- this is a new, full Lagoon deployment, not guard-only, satellite, or an
  existing Safe/vault configuration;
- `deployer` is a `HotWallet`, because the flow needs ERC-20 transfers and the
  initial 1-of-1 Safe transaction;
- the effective valuation manager and primary asset manager both equal the
  deployer address;
- `lighter_api_key_index` is within the range accepted by `changePubKey`;
- `max_settlement_amount`, if supplied, permits at least
  `LIGHTER_MIN_MAINNET_USDC`; and
- the deployer holds at least `LIGHTER_MIN_MAINNET_USDC`, in addition to enough
  ETH for the normal deployment transactions.

Do not run these Lighter-specific checks when the flag is false. Existing
Lagoon deployments must behave exactly as before.

## Deployment sequence

Keep the operation inside `deploy_automated_lagoon_vault()` so it is part of
the same deployment ceremony:

1. Validate all Lighter preconditions before broadcasting anything.
2. Deploy the Safe, Lagoon vault, module and guard using the existing flow.
3. Transfer module ownership to the Safe and perform the existing guarded Safe
   configuration while the deployer is its sole threshold-1 owner.
4. Subscribe `LIGHTER_MIN_MAINNET_USDC` through Lagoon, update valuation,
   settle the deposit and claim the deployer's Lagoon shares.
5. Deposit that amount from the Lagoon Safe to Lighter through the guarded
   module.
6. Poll Lighter's public API until the account exists and its collateral is at
   least `LIGHTER_MIN_MAINNET_USDC`, retaining the existing 0.000010 USDC
   rounding tolerance.
7. Generate a fresh private/public API-key pair locally.
8. Register the public key by calling the existing
   `execute_change_pubkey()` helper through the still-threshold-1 Safe.
9. Poll `/api/v1/apikeys` until the selected slot contains the exact generated
   public key.
10. Add the configured final Safe owners and set the production threshold.
11. Return `LagoonAutomatedDeployment` with the Lighter setup details.

Steps 7–10 must remain adjacent. In particular, no unrelated deployment work
should be added between key registration and final Safe ownership setup.

## Implementation tasks

### 1. Add native Lighter API-key generation

Create `eth_defi/lighter/api_key.py` with a frozen, slotted value object:

```python
@dataclass(slots=True, frozen=True)
class LighterApiKey:
    api_key_index: int
    private_key: str = field(repr=False)
    public_key: str
```

Add:

```python
def generate_lighter_api_key(
    api_key_index: int = MIN_API_KEY_INDEX,
) -> LighterApiKey:
    ...
```

The generator must:

- validate the API-key index using the existing limits in
  `eth_defi.lighter.pubkey`;
- sample a uniformly random, non-zero scalar as
  `secrets.randbelow(ECGFP5_SCALAR_ORDER - 1) + 1`;
- serialise the private scalar in the 40-byte little-endian form used by
  Lighter;
- derive the 40-byte Goldilocks ECgFp5 public key and return both values in the
  same hex form accepted by Lighter; and
- pass the public key through `validate_lighter_pubkey()` before returning.

Port the minimum arithmetic from pinned revisions of the official Apache-2.0
`elliottech/lighter-go` and `elliottech/poseidon_crypto` implementations.
Record the upstream URLs, commit hashes and licence in the module docstring and
comments beside any copied constants. Keep the arithmetic private to this
module unless another existing `eth-defi` call site needs it.

This Python implementation is for one-off local key generation, not a general
Lighter signer. Document that it is not constant-time and do not expose a
generic curve API.

### 2. Remove the Lighter SDK surface

Refactor `eth_defi/lighter/api.py` to use synchronous `LighterSession` calls
for only the deployment operations:

- select the lowest-index account returned by
  `/api/v1/accountsByL1Address?l1_address=<safe>`, matching the existing SDK
  behaviour;
- reuse `fetch_lighter_account_by_index()` and
  `parse_lighter_account_equity()` for collateral polling; and
- fetch `/api/v1/apikeys` with `account_index` and `api_key_index`, then confirm
  that the requested slot contains the expected public key after decoding both
  values to bytes, avoiding false mismatches from hex prefix or case.

Use the existing polling interval and timeout constants. Log each wait without
logging key material.

Remove `import_lighter()` and SDK-backed account wrappers. Remove the SDK-only
manual trade sizing, order round-trip and withdrawal helpers after confirming
with `rg` that their only caller is
  `scripts/lagoon/lagoon-lighter-example.py`. Simplify that script to the
  activation/key-registration verification described in task 7, including
  deleting its deployment-resume/load machinery. Remove the
  `LIGHTER_TUTORIAL_DEPLOYMENT_FILE`, `LIGHTER_RECOVERY_WITHDRAW_USDC` and
  `LIGHTER_WITHDRAW_TIMEOUT` inputs, their branches and helpers, and all
  "continue failed run" metadata and messages. Do not recreate trading or
  withdrawal signing in this plan.

Remove the SDK import/check from the real valuation test. NAV already comes
from the public REST endpoint and does not require a trading key.

Do not add a `lighter-sdk` Poetry dependency or optional extra.

### 3. Promote the Lagoon funding helper

Move the live-capable implementation of `fund_lagoon_vault()` from
`eth_defi/erc_4626/vault_protocol/lagoon/testing.py` to
`eth_defi/erc_4626/vault_protocol/lagoon/funding.py`.

Keep a compatibility import in `lagoon.testing`. Preserve the existing
transaction sequence, polling and return contract. Use
`TokenDetails.convert_to_raw()` and do not introduce a result or
transaction-inventory dataclass.

The deployer is the activation depositor and receives Lagoon shares for the
`LIGHTER_MIN_MAINNET_USDC` supplied. Before the Lighter deposit, verify that the
Safe received the settled assets.

Add the new production module to the Lagoon API documentation index.

### 4. Make the guarded Lighter deposit observable

Update `deposit_usdc_from_lagoon_safe_into_lighter()` in
`eth_defi/lighter/lagoon.py` to return the successful Lighter deposit
transaction hash. Keep the approval hash in normal transaction logging rather
than creating a result object.

Pass the configured Lighter contract address into the approval and deposit
calls instead of relying on a second hard-coded address. Retain route 0 and
resolve the asset index from the configured Ethereum deployment.

### 5. Register the key before final Safe ownership

Add these fields to `LagoonConfig` and mirror them in the direct keyword
interface of `deploy_automated_lagoon_vault()`:

```python
generate_lighter_api_key: bool = False
lighter_api_key_index: int = MIN_API_KEY_INDEX
```

After the account is credited:

- call `generate_lighter_api_key()`;
- call the existing `execute_change_pubkey()` with the deployer-owned Safe;
- verify the exact public key through the REST API; and
- only then call `add_new_safe_owners()` and raise the threshold.

Do not leave key registration to a later command. `execute_change_pubkey()`
builds, signs and executes a direct Safe transaction which is checked by the
guard. A production threshold may require multiple owners, whereas the
deployment signer can execute it while the Safe is still 1-of-1.

### 6. Extend the deployment report without leaking the key

Add one frozen, slotted `LighterAccountSetup` value object with only:

- account index;
- API-key index;
- private key, typed as `str | None` and declared with `repr=False`;
- public key;
- activation amount;
- Lighter deposit transaction hash;
- `changePubKey` transaction hash; and
- observed collateral.

Add `lighter_account_setup: LighterAccountSetup | None` to
`LagoonAutomatedDeployment`.

Keep `as_json_friendly_dict()` secret-free by default. Add an explicit
`include_secrets: bool = False` argument; only the true form includes the
private key. Ensure `pformat()`, `repr()`, logs and exceptions always use the
secret-free form. A successful live deployment always has a private key, while
hydrating a redacted report sets it to `None`; code needing credentials must
reject that value explicitly. Preserve backwards-compatible deserialisation
when the whole new field is absent.

Add one `LagoonAutomatedDeployment.write_json_file()` helper which calls the
secret-bearing form when explicitly requested and creates the file with mode
`0600` and exclusive-create semantics. Both tutorials must use this one writer.
Console rendering must use `pformat()`, which never includes the private key;
never print the report contents. Do not add a persistence framework.

### 7. Keep one real verification path

Simplify `scripts/lagoon/lagoon-lighter-example.py` so the relevant mode:

- deploys a fresh Lagoon vault with the new flag;
- performs the fixed `LIGHTER_MIN_MAINNET_USDC` activation deposit;
- registers the key during deployment;
- confirms the report contains the account and key metadata; and
- fetches account equity through `LighterSession`.

Remove the SDK, trade round-trip, withdrawal, deployment loading, all three
recovery/timeout environment variables named in task 2, and "continue failed
run" paths. The script writes the secret-bearing report once with mode `0600`,
but only prints a separately generated redacted form. Guard this manual mainnet
check with the remaining deployment environment variables and never print the
private key. This is the required real-provider check for the external
integration; record the dated, redacted result in the eventual pull request.

## Tests

### Native key tests

Add `tests/lighter/test_lighter_api_key.py`:

- fixed private scalars produce golden public keys generated independently by
  the pinned official Go implementation;
- the smallest and largest valid API-key indices are accepted and adjacent
  invalid indices are rejected;
- monkeypatched `secrets.randbelow()` proves non-zero scalar handling and byte
  order; and
- neither `repr(key)` nor validation errors contain the private key.

The golden vectors are essential: round-tripping solely through the new Python
code would not detect a compatible-but-wrong curve implementation. Store the
pinned Go commit hash beside the vectors so an implementer can regenerate them.

### REST helper tests

Use a small fake `LighterSession` response object to cover:

- account discovery by Safe address;
- empty and malformed account responses;
- collateral polling, including the existing rounding tolerance and timeout;
- registered-key polling with exact index/public-key matching; and
- HTTP or malformed-response failures without key leakage.

No test may import or install the Lighter SDK.

### Deployment orchestration test

Add a focused test under `tests/lagoon/` with mocked Lighter HTTP responses and
mocked transaction helpers. Run the real deployment orchestration far enough
to assert this order:

```text
Lagoon subscription and settlement
→ guarded LIGHTER_MIN_MAINNET_USDC Lighter deposit
→ Lighter account/collateral visible
→ native key generation
→ Safe changePubKey execution
→ REST key verification
→ final Safe owners and threshold
→ deployment report returned
```

Assert the amount is exactly `LIGHTER_MIN_MAINNET_USDC`, the configured contract
address is used, and no external HTTP request or SDK import occurs. Also assert
that a failure before REST key verification prevents final Safe ownership setup
and prevents a successful report from being returned.

Parameterise preflight failures so each invalid configuration is rejected
before any transaction helper is called: wrong chain, token or Lighter
deployment; non-`HotWallet` deployer; existing/partial deployment; manager
mismatch; insufficient settlement cap or USDC balance; and invalid key index.

### Report tests

Extend the existing Lagoon deployment-report tests to cover:

- old reports without `lighter_account_setup` still load;
- public JSON omits the private key;
- a new redacted report loads with `private_key is None`;
- explicit secret-bearing JSON round-trips the private key;
- `repr()` and formatted output omit the private key; and
- the report writer uses mode `0600`, refuses to overwrite an existing file,
  and the formatted deployment summary remains redacted.

Keep the existing Anvil tests for the guarded deposit and `changePubKey`
execution. They already cover the onchain boundaries; do not duplicate the
whole Lagoon deployment on a second fork fixture.

Run only the focused test files with the repository's required environment:

```shell
source .local-test.env && poetry run pytest tests/lighter/test_lighter_api_key.py tests/lighter/test_valuation.py tests/lighter/test_lighter_change_pubkey.py tests/lagoon/<deployment-test-file>.py
```

Then format only the touched Python files with `poetry run ruff format` and run
the corresponding focused Ruff checks. Do not build Sphinx locally.

## Completion criteria

- With the flag false, Lagoon deployment output and transaction flow are
  unchanged.
- With the flag true on Ethereum, deployment accounts for and deposits exactly
  `LIGHTER_MIN_MAINNET_USDC`, observes the Lighter account, generates and
  registers an API key before final Safe ownership setup, verifies it by REST,
  and returns it in the protected deployment report.
- The opt-in path explicitly rejects configurations whose valuation manager or
  primary asset manager differs from the deployer.
- The package and its tests contain no Lighter SDK import or dependency.
- The private key is absent from default serialisation, formatting, logs and
  errors.
- There is no recovery state machine or general Lighter trading implementation.
