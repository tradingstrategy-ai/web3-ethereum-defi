# Lagoon 1.0 read and scan support

Status: implemented

Date: 2026-09-22

## Goal

Restore production scanning for Lagoon `v1.0.0` vaults and support their read,
fee-reporting, allowlist, and asynchronous deposit/redemption paths without
claiming support for deploying Lagoon 1.0 contracts.

The implementation must be covered by a fixed-block Base Anvil integration
test whose JSON-RPC responses can be replayed from the committed Foundry RPC
cache seed.

## Confirmed production failure

The Base vault `0x7eea189f34e10e7fe5386ba8d49ab41f95b7d54f` returns
`v1.0.0` from `version()` at production block `51_649_628`.
`LagoonVault.fetch_version()` only accepts versions through `v0.6.0`, so vault
construction raises `NotImplementedError` and aborts the whole chain scan.

Fixed-block inspection at Base block `51_649_628` established the following:

- The vault is an EIP-1967 proxy whose implementation is
  `0xF21ECC876afdEE2ed65D4cD4d869D9439c2Bf5fd`; the implementation is not
  verified on BaseScan, so a guessed or reconstructed Lagoon 1.0 ABI must not
  be presented as authoritative.
- The adapter calls exercised by the fixed-block tests—including `version()`,
  `safe()`, `feeRates()`, `isAllowed(address)`, and asynchronous request
  views—decode through Lagoon's official `v0.6.0` ABI. This observation does
  not establish full selector, settlement, or storage compatibility.
- `feeRates()` returns five rates: management, performance, entry, exit, and
  haircut. The vault's values at the fixed block are `(0, 2000, 0, 0, 0)`.
- The legacy `isWhitelisted(address)`, `isWhitelistActivated()`,
  `getRolesStorage()`, and `pendingSilo()` calls revert. Modern access uses
  `isAllowed(address)`, and modern role and silo state is held in ERC-7201
  storage.
- The pending silo is `0xA7e9CD8f80485609368536B82899447212bF2511`.
- The exact block timestamp is 2026-09-22 14:50:03 UTC. This is the block from
  the production failure and is after the vault deployment at block
  `50_835_744`.

The existing v0.5 ABI happens to decode several v1 calls because compatible
selectors and leading return fields were retained, but it hides entry and exit
fees and does not describe the modern access-control surface. That accidental
compatibility is not sufficient as the version-support strategy.

## Compatibility boundary

Treat Lagoon `v0.6.0` and `v1.0.0` as the modern read/interaction family for
the API surface consumed by this repository. Use the official v0.6 ABI as the
documented compatibility interface for both versions, backed by fixed-block
v1 characterisation tests. Do not label that ABI as a verified v1 ABI.

Keep Lagoon deployment defaults on the currently supported v0.5 contracts.
Adding v1 bytecode, constructor/initialiser support, factory integration, or an
upgrade procedure is out of scope until Lagoon publishes verified v1 sources
or canonical deployment artefacts.

## Implementation plan

### 1. Record ABI provenance and model the new version

- Add the official Lagoon v0.6 vault ABI at
  `eth_defi/abi/lagoon/v0.6.0/Vault.json`, copied from a pinned revision of
  Hopper Labs' official `hopperlabsxyz/sdk-v0` repository. The inspected
  revision is `529dabf21c44c5f64013d21295f52ffbfdc59310`, with the ABI exported
  from `packages/v0-core/src/constants/abis.ts`. Preserve the bare ABI-array
  format expected by `get_abi_by_filename()`.
- Pin and link the corresponding `hopperlabsxyz/lagoon-v0` Solidity source
  revision `a8e73f5a5276aa4047b901083cbce127d7f7b470`. Use `Roles.sol` and
  `RolesLib.sol` for the role namespace and field order, and `ERC7540.sol` and
  `ERC7540Lib.sol` for the pending-Silo namespace and offset. The SDK-packaged
  ABI alone cannot establish storage layout.
- Populate `eth_defi/abi/lagoon/README.md` with the pinned upstream URL and
  commit, the existing v0.4/v0.5 provenance, the Base v1 proxy and
  implementation addresses, BaseScan links, the inspection block and date,
  and the explicit caveat that the v1 implementation is unverified.
- Add `LagoonVersion.v_1_0_0` in
  `eth_defi/erc_4626/vault_protocol/lagoon/vault.py`.
- Replace the current two-way ABI selection with an explicit version-to-ABI
  mapping: legacy uses the legacy ABI, v0.4/v0.5 use the v0.5 ABI, and
  v0.6/v1 use the official v0.6 ABI. Unknown versions must continue to fail
  loudly rather than being silently treated as the newest known version.
- Introduce a small named modern-version predicate or constant set and use it
  wherever v0.6 and v1 share behaviour. Avoid scattered one-off comparisons
  that would make the next compatible release easy to miss.

### 2. Make version and storage reads snapshot-consistent

- Change `fetch_version()` to use `self._get_block_identifier()` instead of
  hard-coded `latest`, so scanner records and fork tests cannot mix a historical
  proxy state with the current implementation version.
- Pass the same block identifier to raw storage reads, including the pending
  silo lookup. Review all new direct calls added by this change for the same
  rule.
- Retain the existing error for unsupported versions, but include the vault,
  chain, and block identifier in the message to make future production failures
  actionable.

### 3. Extend modern access, role, silo, and fee handling

- Route both v0.6 and v1 allowlist checks through `isAllowed(address)` in
  `is_whitelisted_deposit()` and `is_account_whitelisted()`. Preserve the
  sentinel-address policy check used to distinguish public and restricted
  vaults.
- Treat both v0.6 and v1 deposit-request reverts as the modern
  `AddressNotAllowed` custom error in `deposit_redeem.py`, retaining the
  selector-level fallback needed for provider-dependent error shapes.
- Include v1 in the ERC-7201 pending-silo storage path. Keep legacy public
  getter behaviour unchanged.
- Add a version-aware roles reader for modern vaults because
  `getRolesStorage()` is no longer public. Decode the first five address slots
  from the official v0.6 roles ERC-7201 namespace—whitelist manager, fee
  receiver, safe, fee registry, and valuation manager—at the selected block.
  Continue using the public struct getter for older versions. Assert/document
  the namespace calculation and field order against the separately pinned
  official v0.6 Solidity source so layout drift is visible during review. In
  the fixed-block v1 test, independently cross-check the decoded Safe slot
  against the public `safe()` getter rather than validating storage only
  against values obtained through the same storage helper.
- Decode modern `feeRates()` with its five-field ABI. Continue exposing
  management and performance fees, and implement deposit and withdrawal fee
  accessors from the entry and exit fields. Convert the basis-point values with
  the existing fee denominator and expose them through the project's `Percent`
  type. Do not fold the separate haircut rate into the asynchronous withdrawal
  fee: document it as a modern sync-redemption concept until the generic vault
  fee model has a dedicated representation.
- Update compatibility comments and selector checks that currently say support
  ends at v0.6. Keep the existing settlement selector compatibility assertion
  for v1.

### 4. Add focused unit coverage

Extend `tests/lagoon/test_lagoon_deposit_permission_unit.py` and related Lagoon
unit tests to cover:

- exact parsing of `v1.0.0` and rejection of a genuinely unknown version;
- v1 public-vault detection through the sentinel `isAllowed()` call;
- v1 account-level allowlist checks;
- decoding `AddressNotAllowed` for v1 request failures;
- modern five-field fee decoding, including non-zero entry and exit rates;
- version-to-ABI routing and modern-version-family membership; and
- modern role-slot decoding at an explicit block identifier.

Use mocked contract/storage responses only for branch and error-shape coverage;
the deployed contract behaviour belongs in the Anvil test below.

### 5. Add the Base fixed-block Anvil integration test

Create `tests/lagoon/test_lagoon_v1.py` following the shared-fork pattern from
`eth_defi/testing/anvil_fork_pool.py`:

- Define `LAGOON_V1_BASE_BLOCK = 51_649_628` and the production vault address.
  A test-specific block is intentional: the repository's canonical Base
  midnight block `49_030_926` predates this deployment.
- Mark the module with `pytest.mark.xdist_group("fork:base:51649628")` and skip
  when `JSON_RPC_BASE` is absent.
- Obtain Web3 through
  `anvil_fork_pool.get_web3(JSON_RPC_BASE, LAGOON_V1_BASE_BLOCK)`. Do not launch
  a per-file Anvil process. The tests are read-only, so snapshot/revert is not
  needed.
- Instantiate the vault with the fixed default block and assert exact values
  for version, name, symbol, asset, safe, silo, roles, fee rates, paused state,
  total assets/supply, and the zero-address allowlist result.
- Exercise `fetch_vault_info()`, `fetch_fee_data()`, async deposit/redemption
  capability selection, pending-request views, and scanner-facing vault
  construction. The regression assertion is that creating/scanning this v1
  vault no longer raises the production `NotImplementedError`.
- Assert addresses and fixed-block numeric values exactly where immutable at
  this block; do not replace them with weak non-zero assertions.

Also migrate `tests/lagoon/test_lagoon_version.py` from its private
`fork_network_anvil()` process to `anvil_fork_pool`, use the canonical fixed
Ethereum block, and remove the flaky retry marker if the pooled/cached test is
deterministic in repeated focused runs. The existing v0.6 test is read-only, so
it does not need snapshot/revert isolation. This keeps both modern Lagoon
versions on the required shared fork infrastructure. The pool key includes the
RPC URL, block number, and launch configuration, so the one-off Lagoon Base
block cannot collide with tests using `BASE_MIDNIGHT_BLOCK`.

### 6. Capture and verify stored RPC replies

- Run the new test once against the real configured Base provider with a fresh
  cache directory and allow the shared Anvil fixture to shut down cleanly so
  Foundry flushes its cache.
- Copy only the resulting block-specific file to
  `eth_defi/testing/rpc_cache_seed/base/51649628/storage.json`. Do not commit
  transient lock files or unrelated cached blocks. Update the seed README to
  document this intentional non-canonical-block exception: the canonical Base
  block predates Lagoon 1.0, while this exact block reproduces the production
  failure.
- Capture with the same Foundry version used in CI, inspect the seed for
  embedded provider credentials before committing, and reject any accidental
  `latest`-block entries.
- Re-seed an empty live cache from the committed directory and re-run the test
  while observing the fork proxy/cache diagnostics. Require no upstream state
  read for any covered contract call or storage slot. The repository still
  performs remote chain-identity and archive-availability bootstrap checks, so
  this is a stored-state replay test rather than an inaccurate claim that
  Anvil starts with no upstream connection at all.
- Run `tests/test_rpc_cache.py` so the committed seed layout, block identity,
  and cache helper behaviour are checked.
- Record the real-provider integration result for the eventual pull request as
  required by the repository's external-integration policy, including the
  command, date, pass/fail result, and redacted provider identity.

## Verification commands

Run focused tests with the repository environment and the required extended
timeout:

```shell
source .local-test.env && poetry run pytest tests/lagoon/test_lagoon_v1.py tests/lagoon/test_lagoon_version.py tests/lagoon/test_lagoon_deposit_permission_unit.py -q
source .local-test.env && poetry run pytest tests/test_rpc_cache.py -q
```

Run the existing vault inspection script against the Base vault after the
change and confirm it completes classification and Lagoon metadata reads at the
fixed block instead of failing during construction. Run Ruff formatting on the
changed Python files. Do not run the full test suite or build Sphinx docs.

## Acceptance criteria

- A scan record for the production Base vault at block `51_649_628` can be
  constructed without an unknown-version exception.
- Unknown future Lagoon versions still fail explicitly.
- The characterised v1 deployment exposes the modern access, silo, role, and
  five-field fee reads exercised by the adapter, while legacy through v0.5
  behaviour remains covered.
- Every state-dependent read used by the fixed-block test honours the selected
  block identifier.
- The v1 integration test uses `anvil_fork_pool`, a fixed block, a matching
  xdist group, and a committed Base RPC cache seed that replays every covered
  contract and storage read without an upstream state-read miss.
- Deployment APIs remain pinned to the known v0.5 artefacts.
- ABI provenance and the unverified-v1 compatibility boundary are documented.

## Implementation result

- Added the official v0.6 compatibility ABI, v1 version routing, and the
  fixed-block-observed modern access, ERC-7201 role, pending-Silo, and
  five-field fee reads.
- Added the fixed-block Base v1 Anvil characterisation test and committed
  replay seed at `base/51649628/storage.json`.
- Migrated the v0.6 version test to the shared fixed-block Anvil pool.
- Focused verification passed: 34 tests covering v1, v0.6, legacy Base
  behaviour, unit branches, and RPC cache seeding/replay.

## Risks and follow-ups

- Selector and call compatibility proves only the surface exercised at the
  fixed block; it does not prove that the unpublished v1 implementation has an
  identical full storage layout. Restrict direct storage reads to the slots
  confirmed by official v0.6 source and fixed-block values, and revisit them
  when Lagoon publishes v1 source.
- Lagoon v1 adds or retains functionality beyond the generic ERC-4626 adapter,
  including synchronous redemption haircut semantics, sanctions/blacklist
  controls, caps, and guardrails. These should receive separate API designs if
  consumers need them; they are not necessary to restore scanning.
- A single bad vault currently aborts the chain's parallel scan. Per-vault
  failure isolation would improve scanner resilience, but it is a separate
  cross-protocol change and should not obscure this compatibility fix.

## Kimi review

Kimi reviewed this plan on 2026-09-22 and found no blocker to implementation.
The review's actionable findings were incorporated as follows:

- pin the Solidity source separately from the ABI and cross-check the Safe
  storage slot against `safe()`;
- state the basis-point/`Percent` conversion requirement for modern fees;
- make the one-off Base cache seed exception, Foundry-version match,
  credential inspection, and absence of `latest` reads explicit; and
- distinguish complete replay of cached state reads from the repository's
  still-remote Anvil bootstrap checks.

The concern that two consecutive address fields might share one Solidity
storage slot was not adopted: two 20-byte addresses cannot fit in one 32-byte
slot. Inspection of `AnvilForkPool` also confirmed that its key includes the
fork block, so the Lagoon-specific Base block and the canonical Base block use
separate forks.

Kimi then reviewed the implementation on 2026-09-22. It found no medium- or
high-severity defects and independently verified the ABI, role layout, cache
block and version routing. Its two low-severity findings were addressed by
correcting the pending-Silo namespace to `hopper.storage.ERC7540` and keeping
the missing-policy-getter fallback restricted to v0.5. The review also noted
that the inherited fee aggregator used `latest`; Lagoon now overrides it to
read the fee tuple once at the adapter's selected block.
