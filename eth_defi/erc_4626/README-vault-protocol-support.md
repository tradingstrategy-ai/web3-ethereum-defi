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

## Strategy tags

VaultBase adapters expose ``VaultBase.get_strategy_tags()`` for investment
strategy classification. ``None`` means that strategy information is missing;
it is distinct from an empty set, which means the vault was reviewed but no
available tag applied. Native perpetual DEX exports do not use ``VaultBase``;
ApeX, Hyperliquid, GRVT, Hibachi, and Lighter persist their default and
address-specific tags in their native protocol ``tags.py`` modules. The
perpetual-futures default applies to products that actually trade perpetuals;
a documented RWA or fund exception must opt out of that default rather than
inherit a misleading tag. All mappings are maintained with the
``categorise-vault-strategy`` skill. Use ``StrategyTag.unknown`` only when
research establishes that the strategy itself is unknown; otherwise leave an
unclassified address unmapped so the missing-information result is retained.

When adding a protocol or expanding an existing detector, run
``categorise-vault-strategy`` for **every newly added and every newly covered
vault**, not only the example contract. Use the vault's published strategy
description and context, preserve source/date decision comments above each
address mapping, and add focused no-RPC coverage. Tokenised funds keep their
mapping under ``eth_defi/tokenised_fund/{slug}/tags.py``; native perpetual DEX
exports keep theirs under ``eth_defi/{slug}/tags.py``. Aave, Euler and Morpho
adapters add the generic ``StrategyTag.lending`` tag automatically, while
their address-level mappings remain additive.

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

### Minimum amounts

``VaultBase`` exposes optional, block-aware minimum accessors so a caller can
size a deposit and a later redemption from one adapter contract. Deposit
minimums are denomination-token amounts and redemption minimums are vault-share
amounts. The API exposes one decimal accessor for each direction:
``fetch_minimum_deposit()`` and ``fetch_minimum_redemption()``. A manager
converts a known value with the denomination or share token only when it needs
an exact raw-unit comparison or transaction input.

``None`` means only that the adapter has no source-proven minimum getter. It
does not prove that the deployed protocol accepts every positive amount. An
adapter must not infer a minimum by broadcasting a deliberately failing
transaction, cache a latest-block value, or use a capacity getter as a
minimum. Accountable exposes its verified vault scalar in both relevant unit
contexts, while Ember exposes its ``minWithdrawableShares()`` redemption
threshold. Managers consume these accessors for their existing typed
``below_minimum`` preflights.

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

### Closed-deposit Guard validation

When a manager raises a typed ``deposit_closed`` or ``deposit_paused``
preflight result, a simulation consumer may use
``create_deposit_request_for_guard_validation(owner, raw_amount)`` on an Anvil
fork. The standard ERC-4626 manager supports this only after its authoritative
global closure reader returns ``maxDeposit(address(0)) == 0``; it does not
accept a non-zero per-account capacity shortfall. Protocol managers may support
additional closure signals, such as Yearn's global shutdown/deposit-limit
state, D2's funding phase and cSigma's ``Pausable`` state. Every supported
manager returns normal deposit calldata with temporary availability checks
omitted while preserving protocol account admission.
The method rejects every non-Anvil provider with
``UnsupportedVaultSimulation(unsupported_reason="anvil_provider_required")``.
Pass the request and the original ``VaultFlowUnavailable`` to
``validate_closed_deposit_request_with_guard()``. It submits each returned call
to ``GuardV0.validateCall()`` and returns closure context plus the independently
validated target, calldata and selector evidence. The closure must identify the
same vault and owner as the validation request. Never broadcast this request to
the closed vault.

This path requires an authoritative, typed manager closure result. For the
standard ERC-4626 manager that evidence is its existing meaningful global
``maxDeposit(address(0)) == 0`` reader; adapters whose zero result is merely
owner-specific capacity must override that reader. Add a protocol-specific
closure reader and fixed-block test before exposing this validation mode for
another vault family.

This is deliberately not an approval test. Do not construct an ERC-20 approval
or try to establish approval-before-deposit ordering in this validation mode:
``validateCall()`` assesses every policy-relevant call independently, and the
normal live simulation remains responsible for approvals, call ordering,
receipts and balance deltas.

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

#### Immediate-liquidity mock overrides

Some synchronous vaults expose a standard guarded ``redeem`` selector but a
live fork cannot provide the required immediate buffer. In that case, model the
admission gate — never a production shortcut — in a dedicated mock. The test
must first prove the normal manager preflight refuses the unavailable capacity,
then explicitly call ``force_settle(None, mock=mock,
ignore_liquidity=True)`` and parse the mock configuration event before building
the guarded redemption. Finally parse the protocol/ERC-4626 withdrawal event
and assert the received raw asset amount.

``MockYieldNestVault`` is the reference: it starts with
``maxRedeem(owner) == 0`` and emits ``LiquidityOverrideSet(true)`` only when
the test asks for the override. It retains the deposited assets; the flag
changes the mock's immediate-redemption admission gate, not token accounting.
This is deliberately distinct from an asynchronous settlement mock: there is
no ticket or keeper call to simulate.

#### Async settlement and events

An asynchronous mock must model the request, the non-asset-manager settlement
boundary and the later claim separately. A test must never execute an
operator/keeper settlement through ``SimpleVaultV0.performCall()`` merely to
make the lifecycle complete: GuardV0 is deliberately configured only for
manager-emitted calls. Instead, call the mock settlement function directly as
the corresponding protocol actor, record its events, then submit the
manager-owned claim through the guard.

The current guard mocks use the deployed protocol's event shape wherever its
ABI exposes one:

| Protocol flow | Mock settlement action | Required settlement or terminal event |
| --- | --- | --- |
| Accountable/ERC-7540 | ``fulfillRedeemRequest(requestId)`` | ``RedeemClaimable`` |
| Plutus Hedge | ``fulfillRedeem(requestId)`` | ``RedeemFulfilled`` followed by guarded ERC-4626 ``Withdraw`` on claim |
| Ember | ``processWithdrawalRequests(numRequests)`` | ``RequestProcessed`` plus the receiver's denomination-token balance delta; it pays directly and has no user claim |
| Gains V1 | ``forceNewEpoch()`` | The published V1 ABI has no dedicated settlement event; assert the epoch transition and terminal guarded ``Withdraw`` |
| Ostium V1.5 | ``tryNewSettlement()`` | ``AsyncDepositWithdrawExecuted`` followed by ``WithdrawClaimedV2`` |
| NaraUSD+ | Advance the mock chain clock through the cooldown | Nara's published ABI has no cooldown-settlement event; assert the terminal ERC-20 ``Transfer`` emitted by ``unstake`` |

Every settlement test must parse the settlement/terminal event and assert the
raw asset or share amount it reports, then independently assert the receiving
token balance. This prevents a state-only mock from falsely proving a payout.

``force_settle(ticket, mock=mock_contract)`` is available only for focused
local Anvil mock tests of Accountable/ERC-7540, Plutus, Ember, Gains and
Ostium V1.5. The
``mock`` parameter is never a production override: it executes the mock's
operator/keeper method and reports the resulting transaction hash. Production
fork simulation continues to use the protocol-specific driver, or raises its
published typed unsupported reason. Ember returns terminal status ``none``
after processing because it pays the receiver rather than making a ticket
claimable.

#### Liquidity-bypass simulations

``force_settle(..., ignore_liquidity=False)`` defaults to a real-liquidity
simulation for generic and non-Lagoon drivers. Lagoon defaults to synthetic
Safe provisioning because ``settleDeposit`` always processes the whole shared
redemption queue, including the simulated redemption and unrelated requests. A
claimable synthetic result therefore proves settlement mechanics only. A caller
requiring real Lagoon Safe liquidity must pass
``ignore_liquidity=False`` explicitly. ``ignore_liquidity=True`` is an
Anvil-only test fixture, not a production capability and not a
request-construction override. It is allowed only for a manager with a tested
driver and must set
``VaultForcedSettlementResult.liquidity_constraints_ignored``. A non-zero
``synthetic_assets_injected_raw`` additionally records the exact raw assets
written into a fork.

The current opt-in drivers are deliberately narrow:

| Protocol | What the option changes | Evidence it does not provide |
| --- | --- | --- |
| Lagoon | By default, tops up a short Safe on an Anvil fork before a settlement round. ``ignore_liquidity=False`` fails before settlement. | That the live Safe can pay queued redemptions. |
| Ember | By default, tops up the vault and configured operator on an Anvil fork before queue processing. On a later insufficient-balance revert, it may increase those balances again. ``ignore_liquidity=False`` conservatively rejects a shortfall in either possible source. | That the live vault or operator can pay the FIFO queue. |
| YieldNest | Switches a dedicated ``MockYieldNestVault`` immediate ``maxRedeem`` gate on before the guarded standard ERC-4626 redeem call. | That a live YieldNest buffer exists, or that the maturity-aware queue is implemented. |

cSigma, Morpho, IPOR and Forty Acres also have liquidity or capacity
preflights, but none has a safe settlement action that this option could
represent. Their drivers continue to refuse unavailable capacity rather than
silently suppressing the preflight. The asynchronous operator mocks (Gains,
Ostium, Plutus, Ember, Accountable and Upshift) already contain the assets for
their requested payout. Their local settlement boundaries are not a
liquidity-bypass problem. The Ember fork driver is separate because deployed
queue processing can require unobservable funding outside the selected request.

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

The version-controlled
``eth_defi/data/deposit-status/vault-deposit-status.json`` artefact records
the generated deposit-probe result, not a substitute for a focused guarded
lifecycle certificate. Follow
``eth_defi/data/deposit-status/README-deposit-status.md`` when deliberately
refreshing that probe; do not hand-edit a row from a fork test. In particular,
the probe cannot certify Gains' later epoch settlement and claim, and a vault
that is absent from probe selection, such as the cSigma reference pool, is
certified by its version-controlled guarded test instead.

Historical rows created before the distinct governance/asset-manager probe fix
prove that a manager call completed through ``SimpleVaultV0``; they do *not*
prove GuardV0 validation, because the governance sender intentionally bypasses
the guard. Only a guarded test or a refreshed probe which uses a distinct asset
manager is GuardV0 evidence. Each successful probe row must include its Anvil
fork block. The artefact is historical compatibility evidence, not a promise
that a live vault remains open, liquid, unpaused or permissionless; production
callers still need a current-state preflight and must handle a live revert.

Morpho V1 and V2 intentionally have no synchronous public capability metadata
until a guarded full lifecycle is fork-proven. At Arbitrum block ``483532847``,
Gauntlet USDC Core (``0x7e97fa6893871a2751b5fe961978dccb2c201e65``) minted
``9625543470030157637`` shares for the guarded 10-USDC deposit, but its
owner-specific ``maxRedeem`` permitted only ``9625542507475810634`` shares.
The standard ``Withdraw`` event and analyser both reported that lower amount;
the ``962554347003`` remaining shares were real residual capacity, not an
event-decoding error. Steakhouse Prime USDC
(``0x250cf7c82bac7cb6cf899b6052979d4b5ba1f9ca`` at block ``483531638``)
exhibited the same ``maxRedeem`` epsilon clamp. Its direct full redemption can
succeed at that state, but another V1 vault reverts a full request, so a generic
manager must remain conservative rather than bypassing ``maxRedeem``. Morpho
V2 Steakhouse High Yield Turbo
(``0xbeefff13dd098de415e07f033dae65205b31a894`` at block ``420581609``)
accepted a deposit but returned ``maxRedeem == 0`` and reverted a full
redemption with custom selector ``0xe65b7a77`` without emitting ``Withdraw``.
The generic manager remains available for explicit caller-controlled use; its
absence from the certified capability list prevents public metadata from
claiming immediate, complete redemption.

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

40acres Aerodrome USDC has the same standard ERC-4626 call shapes, but has its
own guarded Base-fork evidence in
``tests/guard/test_guard_simple_vault_forty_acres.py``. The test uses a
non-governance asset manager to execute the manager-selected approval,
``deposit(uint256,address)`` and ``redeem(uint256,address,address)`` calls,
then rejects substituted deposit receiver, redemption receiver and redemption
share owner. This certification applies only to Aerodrome's generic manager;
Pharaoh's address-scoped direct-underlying capacity preflight remains separate.
When an Anvil-only vault test encounters Pharaoh's
``redemption_capacity_limited`` result, its manager may temporarily increase
the vault's denomination-token balance, re-check the unchanged real
``redeem`` call, and report the smallest successful injection as
``redemption_capacity_increased``. This is diagnostic state only: production
execution continues to refuse unavailable capacity. A terminal simulated
success must retain the before/after capacity, requested assets and shares,
and injected raw denomination amount; a balance mutation without the real
redemption succeeding is not evidence of success.

Gains V1 adds the standard approval and deposit rows, plus
``makeWithdrawRequest(uint256,address owner)`` and the eventual standard
``redeem`` claim. Its guarded Arbitrum test settles the concrete ticket over
three epochs and rejects a request against an unwhitelisted share owner.

Nara adds the standard approval and deposit rows, followed by
``cooldownShares(uint256)`` and ``unstake(address)``. Its guarded Ethereum test
advances the seven-day Anvil cooldown to a fixed timestamp, completes the
claim and rejects an unwhitelisted ``unstake`` receiver. Plutus Hedge supplies
the live ERC-7540 negative cases for ``requestRedeem`` controller and owner;
the generic mock suite also covers ``requestWithdraw`` and deposit-claim
controller/owner validation. Its protocol mock additionally executes the
operator fulfilment boundary followed by guarded ``redeem(requestId, receiver)``,
parses the emitted ``Withdraw`` event and checks the received denomination
amount. The GuardV0 selector must therefore be configured for both the
three-argument ERC-4626 ``redeem`` and Plutus' two-argument asynchronous claim.

Accountable's mock lifecycle exercises its production ABI's
``requestRedeem`` event parser, external settlement, and guarded claim; its
``RedeemClaimable`` mock event must retain Accountable's indexed
``controller, requestId`` order. Ember, Gains V1 and Ostium V1.5 likewise have
manager-level mock tests which call ``force_settle(ticket, mock=...)`` and
decode their protocol-shaped settlement event before checking the terminal
payout. Upshift adds two guarded redemption surfaces:
``instantRedeem(uint256,address)`` and the queued
``requestRedeem(uint256,address)`` / ``claim(uint256,uint256,uint256,address)``
pair. Its date-batch ``processAllClaimsByDate`` function is an operator action,
not a GuardV0 asset-manager permission; test it only through an explicitly
supplied mock settlement driver.

### PR 1582 protocol audit

The vault-result comment on `trade-executor PR 1582 <https://github.com/tradingstrategy-ai/trade-executor/pull/1582#issuecomment-5087987663>`__ is a useful
operational-state snapshot, not a statement that an adapter or GuardV0 lacks a
flow. A vault may be paused, closed, full, unwhitelisted or lack liquid assets
at the scan block. The following matrix records the separate protocol-level
GuardV0 simulation evidence for every protocol named in that comment.

| Protocol family | Deposit and redemption simulation evidence | Important limitation |
| --- | --- | --- |
| cSigma, D2, Forty Acres, Gains, IPOR Fusion, Euler, Yearn, standard ERC-4626 families | Non-governance guarded fork lifecycle with approval, deposit and redemption | Individual live vaults can still be paused, capped or unwhitelisted. |
| Lagoon Finance | Non-governance guarded Base-fork request, real settlement and claim in both directions | A selected vault may require whitelisting. |
| Accountable, Ember, Plutus Hedge, Ostium V1.5, Upshift | Guarded request/claim simulation with stateful protocol mocks; settlement event and raw payout are parsed | Operator settlement is intentionally not granted to the asset manager. Some production forks cannot be advanced safely. |
| Morpho V1/V2 | Guarded standard deposit and bounded ``maxRedeem`` path remain callable | The tested V1 states intentionally retain the protocol's max-redeem epsilon; the V2 state had zero redeem capacity. Do not advertise a complete immediate redemption. |
| YieldNest | Guarded standard deposit and redemption-refusal preflight, plus a dedicated mock-only ``ignore_liquidity`` guarded redemption | Tested ``ynRWAx`` states have zero ``maxRedeem`` and no buffer, including after maturity; it remains deposit-only until a real redemption lifecycle is evidenced. |

Accordingly, an outcome such as ``simulation unsupported async`` in the
operational report is a useful request to run the appropriate mock or
protocol-specific driver. It must not be re-labelled as a completed live-fork
redemption when settlement authority or liquidity is absent.
