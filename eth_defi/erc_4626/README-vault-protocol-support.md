# Vault protocol deposit and redemption support

This document defines the minimum integration contract for a vault protocol
that exposes deposits or redemptions through ``eth_defi``. It applies to
plain ERC-4626 vaults and to protocol-specific manager flows. Constructing a
Python transaction, or succeeding with a direct EOA call, is not sufficient:
the complete advertised lifecycle must execute through the deployed
``SimpleVaultV0`` and ``GuardV0`` contracts.

The guarded cSigma integration in
``tests/guard/test_guard_simple_vault_csigma.py`` is the reference for a
synchronous lifecycle. It executes the manager-selected approval, deposit and
redemption calls through ``SimpleVaultV0.performCall()`` on a fixed Ethereum
mainnet Anvil fork.

## Deposit manager

Implement a protocol-specific ``VaultDepositManager`` when the generic
ERC-4626 flow does not exactly describe the protocol. The manager owns the
full public lifecycle, not merely its first transaction:

- Build every protocol call in ``create_deposit_request()`` and
  ``create_redemption_request()``. This includes request, claim,
  cancellation, reclaim and settlement calls where the protocol needs them.
  Expose the required ERC-20 approval spender through
  ``get_deposit_approval_target()``.
- Use the manager's declared approval destination from
  ``get_deposit_approval_target()``. Tests must not assume that the vault
  itself is always the token spender.
- Parse and analyse every broadcast transaction so callers receive tickets,
  share counts and denomination-token amounts from the actual protocol flow.
- Publish capability metadata only for the directions and lifecycle phases that
  are implemented and tested. A deposit-only manager must not advertise
  redemption support.
- Call ``check_deposit_whitelist(owner)`` before constructing a deposit
  request. Add protocol-specific preflights for capacity, pauses, cooldowns or
  queue state where an unsafe request can be identified before broadcast.

For cSigma, ``CsigmaDepositManager`` derives from the synchronous
``ERC4626DepositManager`` but overrides the immediate redemption-capacity
preflight. A cSigma redemption remains a standard ``redeem`` call; it is not
an ERC-7540 request/claim flow. When the withdrawal manager has queue debt or
the reserve cannot fill the request, the manager raises
``VaultFlowUnavailable`` instead of broadcasting a transaction that reverts
``WithdrawalPending()``.

## Whitelisting

Whitelisting has two separate responsibilities:

1. Protocol admission is the deposit manager's preflight. The shared
   ``check_deposit_whitelist(owner)`` raises ``WhitelistingRequired`` only when
   a queryable protocol policy proves that the owner is excluded. It must not
   guess from unavailable state; a protocol-specific manager may add a stricter
   fail-closed policy when that is safe.
2. Guard admission controls what a delegated asset manager can execute. The
   guard owner must allow the vault call sites, the denomination and share
   tokens, the manager's actual approval spender and the vault/Safe as an
   allowed receiver. The receiver is also the permitted share owner for the
   standard ERC-4626 flow.

The cSigma reference initialises ``SimpleVaultV0`` before whitelisting the
pool with ``GuardV0.whitelistERC4626()``. It then confirms that the manager's
approval destination is allowed, not just that the pool address happened to be
allowed on this deployment.

## GuardV0 updates

Every function emitted by a supported manager must have an explicit GuardV0
call-site and argument-validation path. Do not add a generic vault-selector or
multicall bypass to accommodate a protocol.

When adding a new flow:

- Add the exact selector and full signature to ``GuardV0Base.sol``.
- Decode every value-bearing argument that controls assets, shares, approvals,
  receivers, controllers or owners, and require it to be authorised by the
  appropriate allowlist.
- Register only the selectors, tokens and approval destinations required by
  the protocol's whitelist method. Retain the compatibility
  ``whitelistERC4626()`` wrapper only where its broader selector surface is
  intentionally required.
- Add an accepted manager-generated call and adversarial mutations for every
  sensitive address. The guard must reject an unknown selector, an unapproved
  approval spender and every unapproved recipient/controller/owner before the
  target contract executes.
- Rebuild the copied Guard ABI artefacts with ``make guard`` whenever Solidity
  source changes.

cSigma uses the standard ERC-4626 ``deposit(uint256,address)`` and
``redeem(uint256,address,address)`` forms. GuardV0 therefore requires the
deposit-share receiver, redemption receiver and redemption share owner to be
allowed. The owner check prevents a delegated manager from redeeming shares
held by an unrelated account that previously approved the vault.

ERC-7540 uses related but distinct signatures. Its request calls pass a
controller and owner rather than an ERC-4626 payout receiver. Treat those
arguments according to their actual ABI semantics and cover them with
dedicated tests; do not rely solely on cSigma's synchronous ERC-4626 coverage.

## Protocol discovery and ABI provenance

Lifecycle support starts only after the repository can identify the deployment
and bind a reviewed interface to it. Add protocol detection/classification
coverage that proves the vault wrapper initialises and returns the expected
metadata. The detection tests in ``tests/erc_4626/vault_protocol/`` are a
necessary companion to the guarded lifecycle tests; they deliberately cover
wrapper construction rather than deposits or redemptions.

Before adding or loading an ABI, follow ``eth_defi/abi/README.md``. Store a
verified protocol ABI under ``eth_defi/abi/<protocol>/`` and record its
canonical source. Resolve proxies to their implementation before deciding
which interface or custom errors a manager needs. Small inline ABI fragments
are appropriate only for a one- or two-function external interface; they are
not a replacement for a protocol ABI.

Add a vault classifier, a protocol-specific vault subclass or a specialised
manager only when its address/type detection is deterministic. Register public
capability metadata in ``get_deposit_manager_capability()`` after the relevant
lifecycle direction has the evidence described below.

## Necessary integration tests

### Anvil fork based tests

Add a focused mainnet-fork test for each publicly advertised lifecycle. Follow
the shared Anvil-fork pattern in ``eth_defi/testing/anvil_fork_pool.py``:

- Select a fixed block with the required protocol state, use
  ``AnvilForkPool`` and an ``xdist_group`` derived from that chain and block.
- Use ``evm_snapshot_revert`` to isolate every mutating test.
- Deploy ``SimpleVaultV0``, initialise it, obtain its deployed ``GuardV0`` and
  configure the protocol whitelist.
- Fund the simple vault, then execute the ERC-20 approval for the manager's
  declared spender and every manager-generated deposit, redemption and claim
  call through ``performCall()``. Do not replace this with a direct EOA call
  or a static ``validateCall()`` assertion.
- Assert the target-side state transition and analysed flow result: token
  balances, shares, tickets and final redemption proceeds.

The cSigma test uses Ethereum block ``21_900_000`` because the selected pool
can immediately redeem its complete test position there. A newer canonical
block has withdrawal-manager queue debt, so it would test the capacity refusal
rather than the successful complete lifecycle.

Use the chain's canonical ``*_MIDNIGHT_BLOCK`` and matching
``fork:<chain>:midnight`` xdist group by default. A protocol-specific block is
an exception that must document the state invariant it needs, as cSigma does.
This preserves the shared Anvil process and committed RPC-cache seed for the
normal case.

### Mock contracts

Live forks cannot reliably expose every queue, cancellation or failure state.
For each distinct manager call surface, add a small stateful Solidity mock
under ``contracts/guard/src/testing/Mock.sol`` and Guard-focused Foundry tests.
The mock must expose only the manager-emitted functions, record relevant
arguments, and enforce enough token/share accounting to prove that the guarded
call reached the target.

Use one mock per unique selector and ABI shape, not one duplicate per protocol.
Parameterise manager-specific tests over the shared mock where the emitted
calls are byte-for-byte identical. Cover both the accepted lifecycle and all
security-relevant address mutations, including approval spender, receiver,
controller and owner substitutions.

### force_settle support

``force_settle(ticket)`` is part of the integration promise for an advertised
asynchronous manager. It must advance the protocol-specific ticket on Anvil
and return observable before/after status plus any settlement transaction
hashes. If the protocol cannot be safely advanced on an Anvil fork, publish
that limitation in ``VaultDepositManagerCapability`` rather than claiming a
complete tested lifecycle.

Synchronous managers use the inherited no-op: call ``force_settle(None)`` and
assert that settlement is not required. The cSigma reference does this after
its deposit and before its immediate redemption; it has no onchain request
ticket to settle.

Gains V1 is the asynchronous reference. Its guarded fork test creates
``makeWithdrawRequest(uint256,address)`` through ``performCall()``, advances
the required permissionless epochs with the test driver, and calls
``force_settle(ticket)`` before the guarded ``redeem`` claim. Assert the
ticket's pending and claimable states, every settlement transaction hash and
the final asset balance.

## Preflight and failure tests

Exercise every preflight that prevents an avoidable broadcast. A test must
assert the typed exception, its stable reason/direction when present, and that
no transaction was sent to Anvil:

- an excluded, queryable whitelist member raises ``WhitelistingRequired``;
- a paused vault, closed epoch, minimum/maximum deposit rule or insufficient
  immediate liquidity raises the manager's documented unavailable result;
- a queue or capacity gate, such as cSigma's ``WithdrawalPending()`` state,
  raises ``VaultFlowUnavailable`` before a redemption is broadcast;
- an unknown or unavailable protocol state follows the manager's documented
  conservative policy instead of being silently treated as permissionless or
  liquid.

Keep these adapter/preflight tests separate from GuardV0 negative tests. The
former prove that a legitimate owner is not sent into a predictable protocol
revert; the latter prove that a compromised asset manager cannot redirect funds
or broaden its permission.

## Evidence and certification

``VaultDepositManagerCapability`` describes implemented public behaviour. It
does not by itself certify that a direction completed through GuardV0 on a
particular fork. Use these terms consistently:

| State | Meaning |
| --- | --- |
| Implemented | The manager and its capability metadata describe the direction and its request/claim interface. |
| Guarded deposit evidence | ``SimpleVaultV0`` completed the deposit through GuardV0 on an Anvil fork. |
| Fully lifecycle-certified | The guarded fork test completed every supported phase, including synchronous redemption or asynchronous request, settlement and claim. |
| Settlement limitation | The asynchronous manager is implemented, but its ticket cannot be safely advanced on Anvil; publish ``supports_anvil_settlement=False`` and its reason. |

Record release evidence in the version-controlled
``eth_defi/data/deposit-status/vault-deposit-status.json`` artefact. Follow
``eth_defi/data/deposit-status/README-deposit-status.md`` when refreshing it.
Each current successful row must include its Anvil fork block. The artefact is
historical compatibility evidence, not a promise that a live vault remains
open, liquid, unpaused or permissionless; production callers still need a
current-state preflight and must handle a live revert.

## Coverage matrix

Maintain a small manager-to-Guard test matrix as part of every protocol change.
It may live in the protocol README, the guard README or a focused test module,
but it must name every manager-emitted call surface and its evidence.

| Manager call or state | Guard configuration | Positive proof | Negative proof | Settlement proof |
| --- | --- | --- | --- | --- |
| ERC-20 approval | Approval destination | Approval executed through ``performCall()`` | Unapproved spender and non-emitted ERC-20 approval variants rejected | Not applicable |
| Deposit/request | Exact selector, asset and receiver/controller | Shares or request state changed | Unapproved receiver/controller and unknown selector rejected | Synchronous completion or ticket status |
| Redeem/withdraw/claim | Exact selector, receiver/controller and owner | Shares burned and assets returned, or claim state changed | Unapproved receiver/controller/owner rejected | Synchronous completion or ``force_settle(ticket)`` then claim |
| Batched/protocol-specific call | Exact protocol call sites and every nested selector | Target-side protocol state changed | ``multicall`` and every unregistered nested selector rejected | Protocol-specific driver or published limitation |

For cSigma, the matrix currently contains the manager-selected USDT approval,
``deposit(uint256,address)`` and ``redeem(uint256,address,address)``. Its
guarded fork test proves the successful lifecycle and rejects substituted
deposit receiver, redemption receiver and redemption share owner. ERC-7540,
Nara, Upshift and other non-standard surfaces need their own rows; they cannot
inherit cSigma certification merely because they share an ERC-4626 base class.

Gains V1 adds the standard approval and deposit rows, plus
``makeWithdrawRequest(uint256,address)`` and the eventual standard ``redeem``
claim. Its guarded Arbitrum test settles the concrete ticket over three epochs
and rejects a request whose receiver is not whitelisted.

Nara adds the standard approval and deposit rows, followed by
``cooldownShares(uint256)`` and ``unstake(address)``. Its guarded Ethereum test
advances the seven-day Anvil cooldown to a fixed timestamp, completes the
claim and rejects an unwhitelisted ``unstake`` receiver. Plutus Hedge supplies
the live ERC-7540 negative cases for ``requestRedeem`` controller and owner;
the generic mock suite also covers ``requestWithdraw`` and deposit-claim
controller/owner validation.
