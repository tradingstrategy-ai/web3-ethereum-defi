# Accountable vault deposit manager plan

## Goal

Add production-quality deposit, asynchronous redemption and historical
settlement support for Accountable Capital vaults, using the merged Ember
deposit-manager work as the quality and integration baseline while preserving
Accountable's different ERC-7540 lifecycle.

The completed change must:

- construct, broadcast and analyse synchronous Accountable deposits;
- request, persist, reconstruct, settle and analyse asynchronous redemptions;
- support instant, queued and partially fulfilled redemption requests;
- discover historical redemption requests as restart-safe pending-flow hints;
- scan historical `RedeemClaimable` settlement events into the generic vault
  settlement DuckDB database;
- work through the existing GuardV0/SimpleVault and Lagoon Safe call paths;
- publish synchronous-deposit/asynchronous-redemption capability metadata only
  after the complete lifecycle has focused evidence;
- document the protocol-specific aggregation and partial-fill behaviour.

This plan is for implementation, tests and documentation. It does not send a
transaction to a live Accountable vault. All state-changing integration tests
must use an ephemeral Anvil fork.

## Starting point

The Accountable protocol is already integrated for identification, metadata,
fees and corrected NAV reads:

- `eth_defi/erc_4626/vault_protocol/accountable/vault.py`;
- `eth_defi/erc_4626/vault_protocol/accountable/offchain_metadata.py`;
- `eth_defi/abi/accountable/AccountableAsyncRedeemVault.json`;
- `tests/erc_4626/vault_protocol/test_accountable.py`;
- `docs/source/vaults/accountable/index.rst`.

The merged Ember feature at commit `a758afa03` is the baseline for adapter
coverage and scanner integration:

- `eth_defi/erc_4626/vault_protocol/ember/deposit_redeem.py`;
- `eth_defi/erc_4626/vault_protocol/ember/settlement.py`;
- `eth_defi/erc_4626/vault_protocol/ember/vault.py`;
- `eth_defi/erc_4626/settlement_scan.py`;
- the focused Ember manager, settlement, Guard and Lagoon tests.

Reuse common abstractions and scanner orchestration from Ember and Lagoon, but
do not copy Ember's operator-finalised/no-claim semantics. Accountable users
own the final `redeem()` claim transaction, and one request can need more than
one claim.

The initial integration target is the Monad sUSN Delta Neutral Yield Vault:

- chain id: `143`;
- vault: `0x58ba69b289De313E66A13B7D1F822Fc98b970554`;
- asset: Monad USDC, `0x754704Bc059F8C67012fEd69BC8A327a5aafb603`;
- strategy at plan-writing time:
  `0xd43F8443Bd91829b36153e33d3e631Bdd3b93844`;
- deployment block: `39_419_040`;
- minimum amount: `MIN_AMOUNT_WEI() == 10_000` at plan-writing time;
- contract source and ABI:
  `https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554#code`.

Monad does not provide archive-state guarantees. Fork lifecycle tests must use
the latest available state and derive expected share/asset values from the
pre-transaction contract state. Historical event tests may pin exact blocks,
logs, transaction hashes and timestamps because they do not require historical
`eth_call` state.

## Verified contract lifecycle

The verified `AccountableAsyncRedeemVault` source establishes the following
behaviour.

### Deposits

- `deposit(uint256,address)` and `deposit(uint256,address,address)` are
  synchronous.
- The vault emits the standard
  `Deposit(address indexed sender,address indexed owner,uint256 assets,uint256 shares)`
  event.
- `previewDeposit()` is supported.
- Deposits may still be rejected by current strategy caps or permission rules;
  static capability metadata is not a live fillability promise.

### Redemption request

- `requestRedeem(uint256 shares,address controller,address owner)` escrows
  shares in the vault and emits `RedeemRequest`.
- The packaged event calls its final value `assets`, but verified source passes
  the requested **share count** to that field. The adapter must name and handle
  it as raw shares.
- If the strategy can fulfil immediately, the request emits request id `0`,
  calls `_fulfillRedeemRequest()` in the same transaction and also emits
  `RedeemClaimable`.
- Otherwise `_push()` assigns a positive queue request id. Further requests by
  the same controller while queued reuse that id and add shares to the same
  queue item.
- The adapter must use the self-controller form (`controller == owner`) and
  refuse to create a second request while that controller has either pending or
  claimable shares. This prevents adapter-created tickets from becoming
  ambiguous even though direct external contract calls can aggregate them.

### Strategy settlement and user claim

- The strategy may fulfil all or part of a queued request through
  `fulfillRedeemRequest()`, `processUpToShares()` or
  `processUpToRequestId()`.
- `_fulfillRedeemRequest()` converts the fulfilled shares with the current
  `sharePrice()`, reserves denomination liquidity, decreases pending shares,
  increases claimable shares and emits `RedeemClaimable`.
- Partial fulfilment is explicitly supported. A controller can have non-zero
  pending and claimable shares at the same time.
- `pendingRedeemRequest(uint256,address)` ignores the request-id argument and
  returns controller-aggregate pending shares.
- `claimableRedeemRequest(uint256,address)` also ignores the request-id
  argument and returns controller-aggregate `maxRedeem(controller)` shares.
- `redeem(uint256 shares,address receiver,address controller)` claims any
  currently claimable subset, burns escrowed shares, transfers denomination
  assets and emits the standard `Withdraw` event.
- The redeem price was fixed during `RedeemClaimable`; the later `Withdraw` is
  a user claim, not a new settlement.
- `previewRedeem()` and `previewWithdraw()` deliberately revert for this async
  vault. Estimates must use `convertToAssets()`/the Accountable share price and
  must not generate expected-revert warning noise.
- `cancelRedeemRequest()` can return only the still-pending portion. Supporting
  voluntary cancellation is outside this first feature; do not misclassify
  ordinary pending requests as `reclaimable`.

These facts require a repeated-claim lifecycle:

1. request shares;
2. if claimable shares are non-zero, claim exactly the current claimable
   amount;
3. analyse and account for that `Withdraw` transaction;
4. query status again;
5. repeat while claimable, or wait while pending;
6. finish only when both aggregate pending and claimable shares are zero.

Do not bind `redeem(ticket.raw_shares, ...)` unconditionally. That reverts on a
partial fulfilment and is the main correctness difference from the existing
Lagoon manager.

## Feature-parity definition

Parity with Ember means equivalent adapter and integration coverage, not an
identical protocol state machine.

| Capability | Ember | Accountable target |
| --- | --- | --- |
| Deposit | Synchronous custom event | Synchronous standard `Deposit` |
| Redemption request | `redeemShares()` | `requestRedeem()` |
| Settlement | Operator pays receiver | Strategy makes shares claimable |
| User finish call | None | `redeem(current_claimable, receiver, controller)` |
| Partial settlement | No | Yes; repeated claims required |
| Request identity | Globally monotonic sequence | Queue id, with `0` for instant and reuse while controller remains queued |
| Current status | Pending/none plus terminal log | Claimable first, then pending, then none |
| Historical request discovery | `RequestRedeemed` | `RedeemRequest` |
| Historical settlement marker | Successful `RequestProcessed` | `RedeemClaimable` |
| Guarded path | Deposit/request/operator payout | Deposit/request/user claims |
| Public capability | Sync/async | Sync/async |

## Adapter design

### Protocol-specific request types

Create
`eth_defi/erc_4626/vault_protocol/accountable/deposit_redeem.py` with:

- `AccountableRedemptionTicket(RedemptionTicket)` as
  `dataclass(slots=True)`, storing the queue `request_id`, request block number
  and naive UTC request block timestamp. `get_request_id()` returns the id.
- `AccountableRedemptionRequest(RedemptionRequest)`, whose parser reads exactly
  one `RedeemRequest` from the final receipt and validates the emitting vault,
  controller, owner and raw requested share count before constructing the
  ticket.
- `AccountableDepositManager(ERC4626DepositManager)` to reuse standard deposit
  analysis and common estimation types while overriding the Accountable
  redemption state machine.

The event parser must explicitly translate the ABI field named `assets` into
`raw_shares`. Pin the full ABI topic in a test:

- `RedeemRequest`:
  `0x1fdc681a13d8c5da54e301c7ce6542dcde4581e4725043fdab2db12ddc574506`;
- `RedeemClaimable`:
  `0x4dd5187225a2ae5f5ea35ca7b1732180f848cc4b6f7dce34b4c5e9f384d77dec`.

`create_deposit_request()` must retain standard ERC-4626 behaviour while
tightening the inherited API boundary in the same style as Ember:

- accept exactly one of decimal amount or raw amount;
- require a positive amount;
- default `to` to `owner`, but allow a distinct non-zero receiver;
- call `deposit(uint256,address)` with that receiver;
- preserve current max-deposit and token-balance checks.

`create_redemption_request()` must:

- accept exactly one of decimal shares or raw shares and reject zero;
- default final claim receiver `to` to `owner` and reject the zero address;
- use owner as both ERC-7540 controller and share owner;
- reject a new request while either aggregate pending or aggregate claimable
  shares are non-zero for the owner;
- enforce `MIN_AMOUNT_WEI()` and the current share balance when requested;
- bind one `requestRedeem(raw_shares, owner, owner)` call;
- not add an ERC-20 approval call: Accountable shares are the vault token and
  `requestRedeem()` internally escrows them from the authorised owner.

### Lifecycle methods

Implement and document the complete manager contract:

- `has_synchronous_deposit()` returns true.
- `has_synchronous_redemption()` returns false.
- `is_deposit_in_progress()` returns false.
- `is_redemption_in_progress(owner)` returns true when either aggregate
  pending or aggregate claimable shares are non-zero.
- `can_create_deposit_request(owner)` checks that the current `maxDeposit` is
  positive. The actual request still handles current-state reverts.
- `can_create_redemption_request(owner)` requires no active pending/claimable
  balance and at least `MIN_AMOUNT_WEI()` shares. It is guidance, not a promise
  that the strategy hook will accept the next transaction.
- `estimate_deposit()` uses `previewDeposit()`/`convertToShares()`.
- `estimate_redeem()` uses `convertToAssets()` directly because Accountable's
  `previewRedeem()` intentionally reverts.
- `estimate_redemption_delay()` returns zero only to represent that there is no
  enforced minimum time lock; document that queue timing is unknown and
  strategy/liquidity-dependent.
- `get_redemption_delay_over()` returns `None` because there is no deterministic
  deadline.
- `can_finish_redeem(ticket)` checks aggregate claimable shares are non-zero.
- `finish_redemption(ticket)` reads the current claimable share amount and
  binds `redeem(claimable_shares, ticket.to, ticket.owner)`. Raise a clear
  state error if it is called with zero claimable shares instead of binding a
  guaranteed revert.
- `get_redemption_request_status(ticket)` checks claimable first, pending
  second, then returns none. This ordering is mandatory because both can be
  non-zero during partial fulfilment.
- `can_finish_deposit()` and `finish_deposit()` retain the synchronous ERC-4626
  behaviour.
- `analyse_deposit()` may reuse standard ERC-4626 `Deposit` analysis.
- `analyse_redemption()` must decode the actual standard `Withdraw` log from
  the claim transaction and report that claim's shares and assets. It must not
  require the claim share count to equal the ticket's original request because
  partial claims are valid.

All state reads above are established inherited API names. Any new standalone
network helper must use the `fetch_` prefix.

### Ticket persistence

Override redemption ticket serialisation and reconstruction so process restarts
preserve:

- base vault, owner/controller, receiver, raw requested shares and transaction
  hash;
- Accountable request id;
- request block number;
- naive UTC request block timestamp.

Raw integer quantities must remain strings in JSON-compatible storage. Test an
actual `json.dumps()`/`json.loads()` round trip, not only a direct dict round
trip.

Because Accountable getters are controller-aggregate, a reconstructed ticket is
usable only while the adapter's no-overlapping-request invariant holds. State
this in the class documentation and fail closed when request construction sees
existing pending or claimable state.

### Historical pending-flow discovery

Implement `fetch_vault_flow_events()` using the shared Hypersync event helper
and the ABI-derived `RedeemRequest` topic.

Decode:

- controller from indexed topic 1;
- owner from indexed topic 2;
- request id from indexed topic 3;
- sender and raw shares (the ABI field named `assets`) from event data.

Emit only redemption flows. Deposits are synchronous and must never appear as
pending deposit flows. Construct the same `AccountableRedemptionTicket` shape
used by direct receipt parsing, and serialise it into `ticket_data`.

Treat discovered request events as historical hints. In particular, request id
`0` may already have become claimable in the request transaction, and positive
queue ids may now be partially claimed, fully claimed or cancelled. Consumers
must reconstruct the ticket and call `get_redemption_request_status()` before
acting.

Use this exact queued request as the historical fixture:

- vault: `0x58ba69b289De313E66A13B7D1F822Fc98b970554`;
- block: `84_665_686`;
- timestamp: `2026-06-30 10:50:22` naive UTC;
- block hash:
  `0xfa553809516a5d34b18daacb846c09f5e6b3fea6fda6388fe13c36568ae4ed06`;
- transaction:
  `0xc3eb98d689c6a91288231feee38048f757843ac52a8a99dc6672478323de620b`;
- controller/owner:
  `0x87C13aab721a29118d618ca954d662a8A69E68cC`;
- request id: `159`;
- raw requested shares: `490_000_000`.

## Historical settlement collection

Create `eth_defi/erc_4626/vault_protocol/accountable/settlement.py`, following
the Ember and Lagoon readers, with:

- `ACCOUNTABLE_PROTOCOL_NAME = "Accountable"`;
- `fetch_accountable_settlements()` for an inclusive block range;
- `get_accountable_settlement_events_by_topic()` mapping only
  `RedeemClaimable`;
- `build_accountable_settlement_rows_from_logs()` returning generic
  `VaultSettlement` rows.

`RedeemClaimable` is the settlement marker because the strategy converts shares
at the current price and reserves assets in `_fulfillRedeemRequest()`. Do not
store `RedeemRequest`, `Withdraw`, `CancelRedeemRequest`, `LockAssets`,
`ReleaseAssets` or ordinary deposits as settlement rows.

A strategy transaction can fulfil several controllers and emits one
`RedeemClaimable` per controller. The generic settlement database key is
transaction-level for a given event name, so collapse multiple Accountable
logs in the same transaction to one
`VaultSettlement(event_name="RedeemClaimable")`. Retain distinct transactions
in the same block. Request ids and per-controller quantities remain available
from the underlying logs and manager lifecycle; do not encode them into
`event_name`.

`VaultSettlement` is deliberately a transaction/timestamp marker and has no
asset or share quantity columns. The collapsed row therefore stores neither a
representative quantity nor a sum; no value-accounting information is silently
assigned to it. Add a schema-level assertion that the row contains only the
generic marker fields, while separate decoded-log assertions prove all four
fixture quantities remain readable before collapse.

Use `fetch_vault_settlement_logs()` so Hypersync remains preferred when
configured and the recursively chunked JSON-RPC reader handles Monad provider
range limits. Preserve naive UTC timestamps and idempotent database writes.

Use this known batched queued settlement as the live fixture:

- block: `85_323_091`;
- timestamp: `2026-07-03 11:54:16` naive UTC;
- block hash:
  `0x35b76b59b35d1504849f6244cff68a67e7e11d0095621b8dbd7be768ac198dc6`;
- transaction:
  `0x1df1ce4350e22db66f59424327c1078759f5b62915bb71bba85a78db33bc40ae`;
- four `RedeemClaimable` logs for request ids `156`, `157`, `158` and `159`;
- request `159` settled `490_000_000` shares for `512_914_643` raw USDC.

The row builder must collapse these four logs to one settlement transaction.
Also cover the instant path with request id `0`, where `RedeemRequest` and
`RedeemClaimable` share a transaction; it is still a valid settlement marker.

Wire Accountable into `eth_defi/erc_4626/settlement_scan.py`:

- add `ERC4626Feature.accountable_like` to
  `SUPPORTED_SETTLEMENT_FEATURES`;
- include `AccountableVault` in `PreparedSettlementVault`;
- prepare the Accountable topic map in `_prepare_settlement_vault()`;
- route logs through `build_accountable_settlement_rows_from_logs()`;
- update scanner module/function documentation listing supported readers.

Advance the Accountable scan watermark to the successfully scanned range end
even when no settlement logs were found. Withhold it on adapter preparation,
log fetch, row conversion or persistence failure, matching the existing
scanner guarantees.

## Vault wiring and capability

Update `AccountableVault` to:

- return `AccountableDepositManager` from `get_deposit_manager()` using a
  `TYPE_CHECKING` import plus local runtime import to avoid cycles;
- return
  `VaultDepositManagerCapability(can_deposit=True, can_redeem=True,
  deposit_flow="synchronous", redemption_flow="asynchronous")`;
- expand its lifecycle documentation with instant, queued, partial-fill and
  repeated-claim semantics.

Add capability only after direct, historical, guarded and scanner tests pass.
It advertises implemented adapter directions, not current caps, permissions,
liquidity, queue delay or strategy availability.

Run the existing guarded deposit probe for an Accountable Monad vault if the
probe supports chain 143. If it cannot exercise async redemption, record that
limitation in the status artefact and use the dedicated lifecycle tests as the
redemption evidence.

## Guard and Lagoon integration

No GuardV0 Solidity change is expected. The merged guard already registers and
validates all Accountable selectors through `whitelistERC4626()`:

- `deposit(uint256,address)` validates the share receiver;
- `requestRedeem(uint256,address,address)` validates `controller`, the first
  address argument and second ABI parameter (not `owner`, the second address
  argument/third ABI parameter);
- `redeem(uint256,address,address)` validates the denomination-token receiver.

Do not whitelist Accountable strategy-only methods such as
`fulfillRedeemRequest`, `processUpToShares` or `processUpToRequestId` for the
asset manager. The fork test may impersonate the deployed strategy only to
simulate external settlement after a guarded request.

Add explicit negative tests for an unapproved deposit receiver, request
controller and claim receiver. The request test must put the unapproved address
specifically in `controller`/ABI parameter 2 while leaving `owner` as the Safe,
so it proves the money-controlling argument is checked. Assert strategy-only
calls remain unavailable.
If these tests expose a selector-validation gap, make the smallest
protocol-neutral Guard change, rebuild Guard and Safe artefacts with
`make guard safe-integration`, and never edit ABI JSON directly. Otherwise do
not rebuild or churn generated artefacts.

The guarded flow is:

1. SimpleVault/Safe approves USDC to Accountable.
2. It calls synchronous `deposit()` and receives shares.
3. It calls `requestRedeem(shares, safe, safe)`; Accountable internally escrows
   the shares, so no share-token approval is needed.
4. The external strategy makes some or all shares claimable.
5. The Safe repeatedly calls manager-generated `redeem()` claims until status
   becomes none.

Add the same flow through a Lagoon Safe on Monad if the existing Lagoon deploy
fixtures support chain 143. Keep strategy impersonation outside the trading
strategy module.

## Test plan

### Direct manager fork tests

Add
`tests/erc_4626/vault_protocol/test_accountable_deposit_redeem.py`, guarded by
`JSON_RPC_MONAD`, and fork the latest Monad block because archive state is not
available.

Use the sUSN vault, an unlocked current USDC holder and the fork-time strategy.
All amounts and addresses must be read or asserted before mutation so a latest
fork cannot silently switch to an incompatible deployment.

Cover:

- autodetection returns `AccountableVault` and the protocol manager;
- capability reports sync deposit plus async redemption;
- exactly 100 USDC is approved and deposited;
- `previewDeposit()` before the transaction matches the exact `Deposit` event
  and minted raw share balance;
- deposit analysis reports exact event amounts;
- request construction contains one `requestRedeem` call and no share approval;
- parsing validates controller, owner, raw shares and request id;
- ticket JSON round-trip preserves request block data;
- request id `0` instant fulfilment is handled when the current strategy takes
  that path;
- a queued path is settled by the impersonated strategy when current fork state
  takes that path;
- status checks claimable before pending;
- `finish_redemption()` uses the current claimable amount rather than original
  ticket shares;
- each `Withdraw` analysis reports exact claimed raw shares/assets;
- after each partial claim, status is queried again and remains pending or
  claimable as appropriate;
- the sum of claim transactions equals the request's fulfilled shares, with no
  double claim and final status none once pending and claimable are both zero.

Because Monad is the documented exception to fixed archive-fork assertions,
derive exact expected raw results from `previewDeposit`, `convertToAssets` and
decoded events at the latest fork. Do not weaken event/accounting assertions to
mere greater-than-zero checks.

Add focused mocked or decoded-log tests for deterministic edge cases:

- ambiguous amount arguments and zero values;
- below-`MIN_AMOUNT_WEI` request;
- insufficient share balance;
- overlapping request rejected when pending is non-zero;
- overlapping request rejected when claimable is non-zero;
- malformed/multiple `RedeemRequest` events;
- receipt controller, owner or share mismatch;
- simultaneous pending and claimable maps to `claimable`;
- partial fulfilment binds only current claimable shares;
- zero claimable shares cannot bind a finish call;
- analysis accepts a claim smaller than original ticket shares;
- an authorised external cancellation/reduction which takes aggregate pending
  to zero is not mistaken for a fully claimed request merely because status is
  now none.

Also test the claim time-of-check/time-of-use boundary. `finish_redemption()`
can only bind the claimable amount observed at construction time; concurrent
authorised controller/operator activity can consume it before broadcast. A
revert must leave the ticket unresolved, trigger a fresh status/claimable read
and rebuild the call. Do not retry the stale bound transaction indefinitely.

### Historical request test

Extend `tests/vault/test_pending_vault_flow_events.py` with the exact request
`159` fixture above, using Monad Hypersync and `JSON_RPC_MONAD`.

Assert direction, controller, owner, request id, raw shares, transaction, block,
naive UTC timestamp and reconstructed ticket fields exactly. Add an offline
decoder unit test if Hypersync availability would otherwise leave the ABI field
named `assets` untested as shares.

### Historical settlement tests

Add
`tests/erc_4626/vault_protocol/test_accountable_settlement.py` covering:

- the four known `RedeemClaimable` logs collapse to one transaction marker;
- protocol, event name, vault, chain, transaction, block hash and timestamp are
  exact;
- different settlement transactions in one block remain distinct;
- an instant request-id-zero `RedeemClaimable` is retained;
- unrelated Accountable events are not requested by the topic mapper;
- the one-block live RPC reader returns the known settlement;
- the equivalent narrow Hypersync read returns the same row when configured.

Extend `tests/erc_4626/test_settlement_scan.py` so Accountable:

- is selected by `ERC4626Feature.accountable_like`;
- is prepared with only the `RedeemClaimable` topic;
- routes through its row builder in a mixed protocol chain batch;
- advances its watermark after an empty successful scan;
- does not advance after preparation, decode or persistence failure;
- writes one row, not four, for the known batched settlement transaction.

### Guard and Lagoon tests

Add `tests/guard/test_guard_simple_vault_accountable.py` for the complete
deposit/request/strategy-settle/repeated-claim path. Use exact decoded event and
balance deltas at the fork, and prove arbitrary receivers/controllers and
strategy-only calls are rejected.

Add `tests/lagoon/test_lagoon_accountable.py` when the existing Monad Lagoon
fixture can deploy reliably. Exercise manager-generated calls through the
trading strategy module and ensure every claim returns USDC to the Safe. If the
repository's Lagoon fixture cannot run on Monad, document the concrete fixture
gap and retain the Guard/SimpleVault end-to-end evidence rather than faking an
Ethereum deployment.

### Capability regressions

Extend the focused deposit-probe and scan-feature tests to assert Accountable's
public manager metadata. Preserve fail-closed behaviour for uncertified vault
classes.

Audit all repository callers of `finish_redemption()` and
`get_redemption_request_status()` for repeated-claim handling. The lower-level
manager can return a sequence of successful claim transactions for one ticket;
any downstream caller that assumes one claim completes the original request
must be updated in its own repository before Accountable trading is enabled.
Historical tickets and tickets sharing a controller with direct, non-adapter
activity cannot be reconciled by request id because the getters are aggregate.
Treat the single-active-request invariant as a prerequisite for exact
per-ticket completion, not as a property the chain enforces.

## Documentation and release notes

Update:

- `docs/source/vaults/accountable/index.rst` with deposit, request, partial
  fulfilment, repeated claim and settlement-marker semantics;
- `docs/source/api/erc_4626/index.rst` with the Accountable
  `deposit_redeem` and `settlement` modules;
- module/class/method docstrings with the verified Monadscan source link;
- `CHANGELOG.md` with a dated feature entry when the implementation PR is
  opened.

Document prominently that request-id getters are controller-aggregate despite
accepting a request-id argument, request id zero is the instant path, positive
ids can aggregate subsequent controller requests, and adapter callers must not
open overlapping requests.

## Verification commands

Before pytest, copy `.local-test.env` from the main checkout when missing and
never edit it. Use the parent repository Poetry environment for this worktree.
Run only focused tests with the required environment wrapper and a three-minute
timeout per command:

```shell
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/erc_4626/vault_protocol/test_accountable.py tests/erc_4626/vault_protocol/test_accountable_deposit_redeem.py -q
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/vault/test_pending_vault_flow_events.py -k accountable -q
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/erc_4626/vault_protocol/test_accountable_settlement.py tests/erc_4626/test_settlement_scan.py -k accountable -q
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/guard/test_guard_simple_vault_accountable.py -q
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/lagoon/test_lagoon_accountable.py -q
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/erc_4626/test_deposit_probe.py tests/erc_4626/test_scan_features.py -q
```

Format changed Python files with `poetry run ruff format` and build the relevant
Sphinx documentation with `source .local-test.env && make build-docs`. If a
Guard Solidity change proves necessary, also run the focused Guard tests,
`make guard safe-integration` and the contract-size check used by the Ember PR.

## Downstream integration hand-off

The web3-ethereum-defi adapter will expose one request ticket with zero or more
claim transactions. Before enabling Accountable trading in trade-executor,
update its settlement retry/accounting path to:

- query status before every action;
- claim the manager-provided current claimable amount;
- if a bound claim reverts because authorised concurrent activity changed the
  claimable amount, leave accounting unchanged, re-read state and rebuild the
  claim rather than replaying stale calldata;
- analyse and persist every claim transaction idempotently;
- accumulate claimed shares and denomination assets across partial fills;
- keep the trade unresolved while any pending or claimable shares remain;
- tolerate a pending-to-claimable-to-pending sequence;
- mark completion only when both values are zero and accumulated accounting
  matches the fulfilled request;
- reject or serialise a second redemption for the same controller while the
  first ticket remains active;
- treat status none with fewer total claimed shares than requested as
  cancellation/foreign activity requiring explicit reconciliation, never as a
  successful full redemption;
- reject exact per-ticket accounting when direct non-adapter requests share the
  controller, because aggregate getters cannot disambiguate them;
- include restart tests between partial claims.

Do not advertise end-to-end trade-executor routing for Accountable until this
companion behaviour is merged. This does not block publishing the lower-level
adapter capability once this repository's own lifecycle evidence passes.

## Acceptance criteria

The implementation is complete when:

- `AccountableVault.get_deposit_manager()` returns the protocol manager;
- public metadata reports synchronous deposit and asynchronous redemption;
- direct deposits and standard `Deposit` analysis are covered;
- request parsing, ticket persistence and historical discovery use the
  verified `RedeemRequest` shape and correctly interpret its `assets` field as
  shares;
- instant request id zero and positive queued ids are both covered;
- claimable status wins over pending when both are non-zero;
- partial fulfilments generate repeated claims of only the current claimable
  amount, and each standard `Withdraw` is analysed exactly;
- overlapping adapter-created requests are rejected to preserve ticket
  identity;
- historical `RedeemClaimable` events participate in the generic incremental
  settlement scanner;
- batched settlement logs collapse to one row per transaction without losing
  distinct transactions in the same block;
- empty successful scans advance their watermark and failures do not;
- GuardV0/SimpleVault completes the full lifecycle without arbitrary receiver,
  controller or strategy-call access;
- Lagoon Safe coverage passes on Monad or its real fixture limitation is
  documented without substituting a fake protocol deployment;
- focused formatting, tests and documentation build pass;
- the PR description calls out partial-claim downstream integration work.
