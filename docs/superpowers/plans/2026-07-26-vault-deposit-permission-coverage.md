# Vault deposit-permission coverage plan

**Date:** 2026-07-26

**Status:** In progress. The common export shape and source-proven D2,
YieldNest, Gains and Ostium adapters are implemented. The remaining protocol
workstreams intentionally retain ``unknown`` until their deployed generation
and account-admission semantics have been verified.

## Goal

Expand reliable vault-wide deposit-permission reporting for YieldNest,
Accountable, Morpho, Euler, Upshift, Ember, D2 Finance, Plutus, Gains Network
and Ostium.

The public export must distinguish a verified absence of KYC/manual identity
approval (``permissionless``) from a verified KYC/manual identity requirement
(``whitelisted``). ``unknown`` remains correct when the deployed contract
generation has no canonical, cheap and vault-wide policy read. A paused vault,
zero deposit cap, an epoch window, open date, lock-up, token allowance, balance
requirement or the scanner account's current eligibility must never be used as
a substitute for this policy classification.

This is reporting coverage, not a commitment to implement every protocol's
deposit/redemption transaction lifecycle. Existing manager capabilities and
live ``deposit_closed_reason`` fields continue to describe separate concerns.

Morpho and Euler are an explicit operational-assumption exception: all vaults
detected by their V1/V2 and EVK/Earn adapters report ``whitelisted``. Their
structured ``whitelist.notes`` field must state ``No permissioned hook checks
were performed`` so consumers do not mistake the result for an audit of every
optional permissioned hook.

## Baseline

The public export generated on 2026-07-26 contains no non-unknown permission
classification for any protocol in this plan:

| Protocol | Exported vaults | Current permission result |
| --- | ---: | --- |
| Accountable | 18 | 18 unknown |
| D2 Finance | 47 | 47 have no deposit-manager object |
| Ember | 6 | 6 unknown |
| Euler | 358 | 358 unknown |
| Gains Network | 9 | 9 unknown |
| Morpho | 591 | 591 unknown |
| Ostium | 1 | 1 unknown |
| Plutus | 2 | 2 unknown |
| Upshift | 38 | 37 unknown; 1 has no deposit-manager object |
| YieldNest | 1 | 1 has no deposit-manager object |

The scanner already persists ``_deposit_permission`` independently of
``_deposit_manager``. However, the lifetime report currently adds it only to a
non-null ``deposit_manager`` mapping. That hides a known policy for protocols
such as D2 whose manager capability is intentionally absent or partial. This
export-shape defect must be fixed before protocol coverage is measured.

## Common export work

### 1. Make permission a first-class public field

- In ``eth_defi/research/vault_metrics.py``, normalise
  ``_deposit_permission`` once for every vault row, retaining the present
  validation and legacy default of ``unknown``.
- Emit the normalised value as top-level ``deposit_permission`` in every
  lifetime metrics row, irrespective of ``_deposit_manager``. Retain the
  nested ``deposit_manager.deposit_permission`` during a compatibility window
  so existing consumers do not break. The nested field exists only when the
  manager is non-null; do not synthesise a manager object to carry policy.
- For a stale sticky fallback, export top-level ``unknown`` unless the fallback
  record contains the policy's scan timestamp. A timestamped fallback may
  retain its last observed policy only with the existing stale annotation. Do
  not infer a positive policy from a stale manager capability.
- Extend ``VaultMetricsExport`` and the JSON-export tests so every record has
  the top-level key. Test a null manager plus a known policy, a legacy pickle
  with no private field, invalid persisted values, and a normal manager-backed
  record.
- Update the public JSON schema/documentation and downstream consumer examples
  to use ``deposit_permission`` as the authoritative field. Mark the nested
  copy as compatibility-only rather than removing it in this change.

### 2. Establish a repeatable policy-evidence contract

- Add a short contributor-facing section beside
  ``VaultBase.is_whitelisted_deposit()`` defining the required evidence:
  verified implementation source or application ABI, a vault-wide view, and a
  deterministic fixed-block assertion. ``is_account_whitelisted(address)`` is
  required only where the protocol exposes a meaningful account-admission
  predicate.
- Keep each protocol override in its own adapter. Do not introduce a generic
  ``maxDeposit()`` heuristic: zero can mean paused, capped, epoch-gated or
  empty, none of which means the vault is permissioned.
- Extend ``tests/vault/test_deposit_permissions.py`` with a small matrix of
  fake adapters proving that policy classification is independent from manager
  capability, a pause, a zero cap, a closed epoch and ``maxDeposit() == 0``.
- For every external ACL ABI added or changed, commit the verified interface
  under ``eth_defi/abi/<protocol>/`` and record its canonical source in that
  directory's README. Do not use inline ABI fragments except for one or two
  functions.

## Protocol workstreams

### 3. Accountable

The existing Accountable ABI exposes ``permissionLevel()``, ``allowed(address)``
and ``allowedMany(address[])`` but the adapter reports no policy.

- Obtain the verified implementation source for each detected Accountable
  generation and map every ``PermissionLevel`` enum value to a public-policy
  or restricted-policy result. Do not assume enum ordinal values from the ABI;
  record the verified mapping in code comments and tests.
- Implement ``AccountableVault.is_whitelisted_deposit()`` from
  ``permissionLevel()`` and ``is_account_whitelisted(address)`` from
  ``allowed(address)``. If a generation does not expose both semantics, raise
  ``NotImplementedError`` so the scanner exports ``unknown``.
- Add fixed-block fork tests for one public and one restricted Accountable
  vault. Assert the global classification and a known allowed/disallowed
  address without sending transactions. Monad cases must use current-state or
  state-relative assertions only.

### 4. D2 Finance

``VaultV1Whitelisted`` exposes a historical ``onlyWhitelisted`` modifier plus
``whitelisted(address)``, ``whitelistAsset()`` and ``whitelistBalance()``. The
names describe D2 asset eligibility, not KYC or manual identity approval.

- Implement ``D2Vault.is_whitelisted_deposit()`` as ``False``. The public
  schema treats D2's asset threshold, open dates, epoch timing and lock-ups as
  eligibility/availability conditions, not KYC.
- Do not implement ``is_account_whitelisted(address)`` for D2: the exposed
  mapping and token threshold do not answer whether an account completed KYC.
- Add fixed-block Arbitrum coverage that proves D2's timing state is separate
  from the permissionless classification.
- Add ``D2Vault.get_deposit_manager_capability()`` only if its static lifecycle
  is actually supported; the new top-level export field must make D2 policy
  visible even if the capability remains absent.

### 5. YieldNest

The current adapter supports the ynRWAx synchronous deposit receipt but has no
policy accessor and its trimmed ABI only proves standard ERC-4626 methods.

- Inspect each deployed YieldNest implementation, beginning with ynRWAx, for
  KYC, sanctions, allow-list, transfer restriction or router-mediated
  admission contracts. Record proxy implementation addresses and canonical
  source links in ``eth_defi/abi/yieldnest/README.md``.
- If an implementation has a global mode plus an account predicate, add the
  smallest verified ABI and implement both policy methods. If deposits are
  unconditionally public, implement only ``is_whitelisted_deposit() -> False``
  with source and fixed-block proof. Leave unknown for variants whose policy
  cannot be identified safely.
- Add one fixed-block Ethereum test for every supported YieldNest generation,
  including a caller-specific assertion whenever the policy is restricted.

### 6. Morpho

Morpho V1 and V2 represent distinct contract architectures. Morpho market or
allocator whitelists must not be confused with user deposit admission.

- Review verified V1 MetaMorpho and V2 adapter implementations used by the
  scanner, including any guardian/owner configuration that can introduce
  depositor-level gating. Catalogue their stable policy views and generation
  boundaries.
- Implement policy methods separately in ``vault_v1.py`` and ``vault_v2.py``.
  Return ``permissionless`` only for a source-proven public-deposit generation;
  return ``whitelisted`` only for a demonstrated account admission mode; keep
  unrecognised proxies unknown.
- Do not reuse Morpho API ``not_whitelisted`` risk warnings: they concern
  market or allocator configuration, not an investor's eligibility to deposit.
- Add fixed-block tests for one V1 and one V2 public vault plus any discovered
  restricted generation. Include an assertion that a market-whitelist warning
  does not alter the vault deposit-policy result.

### 7. Euler and Euler Earn

Euler EVK and Euler Earn must be evaluated independently because a vault owner
or a cap manager does not by itself imply user-level deposit permission.

- Inspect the verified implementation and factory/deployer configuration for
  both ``EulerVault`` and ``EulerEarnVault``. Identify any explicit user
  allow-list, KYC gate or access-manager integration and its vault-wide mode
  getter.
- Implement the policy override at the narrowest relevant class level. Use a
  public classification only where a deployed implementation proves all users
  can invoke the deposit route; leave bespoke curator wrappers unknown.
- Cover Ethereum/Base/other actively exported implementations with fixed-block
  tests selected by bytecode or generation, not by vault name. Ensure cap,
  pause and temporary liquidity conditions do not change the policy result.

### 8. Upshift

Upshift's existing ``assetsWhitelistAddress()`` governs accepted denomination
assets. It is not evidence that depositors themselves are allow-listed.

- Audit both TokenizedAccount and multi-asset implementations, including their
  factory/router path, for an investor ACL separate from the asset whitelist.
- Implement ``permissionless`` only if the verified implementation's deposit
  entry point is public and no external user gate is configured. Otherwise add
  the exact policy and account reads for the relevant generation; retain
  unknown where the route is offchain or proxy-specific.
- Add unit tests proving that changing the asset whitelist does not change the
  reported deposit policy. Add fixed-block tests for a standard and multi-asset
  vault, with any restricted sample discovered during the audit.

### 9. Ember

Ember's ``pauseStatus`` and operational roles regulate availability and
operations, but do not by themselves establish investor admission policy.

- Inspect the verified Ember implementation, offchain onboarding route and
  any linked access-control contracts for depositor-specific restrictions.
- Add a policy override only after locating a global mode that distinguishes
  public and restricted deposits. Classify an implementation as permissionless
  only with source-backed evidence that the deposit path has no caller gate.
- Add fixed-block Ethereum tests for the selected implementation. Test pause
  independently to prevent a paused public vault being classified as
  whitelisted.

### 10. Plutus

Plutus uses manually opened and closed deposit windows, and classification
already recognises protocol roles. The plan must distinguish that timing from
an investor whitelist.

- Retrieve the verified implementation ABI/source for each supported Plutus
  hedge-token generation and determine whether any role, registry or external
  contract controls depositor membership.
- Implement a source-backed policy predicate and account predicate where
  available. If the observed contract only exposes an administrator-controlled
  open/closed window, report it as permissionless and continue to expose the
  window through the existing closed-reason fields.
- Add Arbitrum fixed-block tests that demonstrate a public account can deposit
  during an open window and that closing the window does not change the policy
  result.

### 11. Gains Network and Ostium

Gains and Ostium share some historical contract surfaces but have independent
deployment generations. Ostium V1 and V1.5 must not share an unverified policy
assumption.

- Review the verified Gains gToken, Ostium V1 and Ostium V1.5 deposit/request
  paths, registry dependencies and any externally configured allow-lists.
- Implement separate overrides for ``GainsVault`` and ``OstiumVault``. For
  Ostium, dispatch by the existing detected version and return unknown for
  unsupported/legacy generations until their public or restricted policy is
  proven.
- For a public route, classify only the caller policy as permissionless;
  epoch settlement, supply caps, locked deposits and a disabled direct
  ``deposit()`` function remain availability/lifecycle facts.
- Add fixed-block Arbitrum tests for Gains, Ostium V1 and Ostium V1.5, asserting
  the classification before and during an unavailable epoch/settlement window.

## Migration

No manual data-migration script is needed or appropriate. The new public
``deposit_permission`` field is additive, and legacy metadata safely exports as
``unknown`` until it has a fresh value.

After deploying the scanner and export changes, run a complete vault metadata
scan and then regenerate/publish the top-vault JSON. The scanner must derive
each value from the live protocol adapter and persist it as
``_deposit_permission`` and ``_whitelist_notes``. Do not patch, rewrite or
backfill the production pickle with guessed values: this would turn unverified
legacy data into a misleading policy claim.

## Implementation status

Implemented in this change:

- All public records carry the authoritative top-level
  ``deposit_permission`` field and structured ``whitelist`` object; the nested
  manager copy is compatibility-only.
- Sticky fallback records always report ``unknown`` rather than replaying a
  historical policy claim.
- D2 reports ``permissionless``: its contract-named whitelist, token balance,
  open dates and lock-ups are not KYC or manual identity approval.
- The verified YieldNest ynRWAx deposit implementation reports
  ``permissionless`` when it is not paused.
- Gains gToken and Ostium V1/V1.5 public deposit/request routes report
  ``permissionless``.
- Morpho V1/V2 and Euler EVK/Earn report ``whitelisted`` under the requested
  operating assumption. Their ``whitelist.notes`` value is ``No permissioned
  hook checks were performed``.

Still intentionally ``unknown`` pending the source/generation audit described
above: Accountable, Upshift, Ember and Plutus. In particular, Accountable's
ABI does not name its ``PermissionLevel`` enum members. Morpho V2 and Euler
can support externally configurable gates or hooks, which is why their public
classification carries the explicit caveat rather than claiming a completed
permissioned-hook audit. Classifying the remaining protocols from an ABI
ordinal, cap, pause or scanner account observation would be unsafe.

## Delivery sequence

1. Land the top-level export field, compatibility tests and documentation as a
   standalone change. This makes policy coverage observable even for adapters
   without a deposit manager.
2. Land Accountable after its enum and account-check semantics have been
   verified. D2 is permissionless because its contract-named eligibility checks
   are not KYC or manual identity approval.
3. Complete YieldNest, Upshift, Ember and Plutus reconnaissance, then land only
   the adapters whose exact generation semantics are proven.
4. Complete Morpho and Euler by implementation generation; keep each protocol's
   V1/V2 or EVK/Earn changes independently reviewable.
5. Complete Gains and each Ostium version separately, followed by the focused
   export coverage tests.
6. Deploy the scanner image, run a complete vault metadata scan, then publish a
   fresh top-vault JSON. Do not mutate old pickles to fabricate a policy: the
   scanner must write every new value from a live contract read.

## Verification and acceptance criteria

- Use the shared fixed-block Anvil fork pattern documented in
  ``eth_defi/testing/anvil_fork_pool.py`` for every source-proven policy
  override. Use current-state/state-relative assertions for Monad. The explicit
  Morpho/Euler operating assumption instead needs a unit test for both the
  reported status and its caveat.
- Run focused tests only, always with ``source .local-test.env`` and the
  required extended timeout. At minimum, run the new unit export tests,
  ``tests/vault/test_deposit_permissions.py`` and each touched protocol module.
- Add a JSON-export regression that verifies a known policy survives when
  ``deposit_manager`` is ``None``.
- Before deployment, generate a local report grouped by protocol and policy,
  and compare it with the current baseline in this document.
- After the full production rescan and republish, query
  ``top_vaults_by_chain.json`` and assert that every vault whose adapter landed
  a source-proven policy override exposes a top-level non-unknown
  ``deposit_permission``. The report must list all remaining unknowns by
  implementation generation and a concrete reason; it must not silently label
  them permissionless merely to meet a coverage target.
