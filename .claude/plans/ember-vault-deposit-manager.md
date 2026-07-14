# Ember vault deposit manager plan

## Goal

Add a production-quality `VaultDepositManager` for Ember EVM vaults, with the
same practical coverage expected from the Lagoon ERC-7540 and Ostium V1.5
adapters:

- construct, broadcast and analyse deposits and redemptions;
- persist and reconstruct asynchronous redemption tickets;
- report request state after a process restart;
- discover pending redemption requests from historical events;
- collect historical successful Ember settlements into the generic settlement
  database used by the vault price pipeline;
- work through `GuardV0`, `SimpleVaultV0` and a Lagoon Safe;
- publish the mixed synchronous/asynchronous capability introduced by
  [PR #1269](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1269);
- prove the complete lifecycle on a reproducible Anvil mainnet fork.

This plan is for implementation, tests and documentation. It does not send a
transaction to a live Ember vault.

## Starting point and verified facts

PR #1269 is the baseline. It added fail-closed deposit-manager capability
metadata, guarded fork probes and the packaged status artefact. The Ember
follow-up must not advertise support until its complete lifecycle has focused
evidence.

The current Ember adapter only provides metadata and an Ember ABI:

- `eth_defi/erc_4626/vault_protocol/ember/vault.py`
- `eth_defi/erc_4626/vault_protocol/ember/offchain_metadata.py`
- `eth_defi/abi/ember/EmberVault.json`
- `tests/erc_4626/vault_protocol/test_ember.py`

The initial integration target is the Ethereum Crosschain USD Vault:

- Vault: `0xf3190A3ECC109F88e7947b849b281918c798A0C4`
- Asset: Ethereum USDC, `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`
- Reproducible fork block: `24_496_689`
- Contract version at the fork: `v1.1.1`
- Operator at the fork: `0x116046991e3F0B0967723073a87820eF5edB29f2`
- Pause flags at the fork: all false
- Pending queue length at the fork: zero
- `minWithdrawableShares()` at the fork: `100_000`

The proxy is upgradeable. At block `25_529_542` it reports `v2.3.1`, while the
same deposit, request, account-state and processing interfaces remain present.
Tests must therefore pin their block and assert the version they exercise.

The historical request and processing transactions below both executed against
`v1.1.1`, as verified by calling `version()` at blocks `24_286_355` and
`24_290_495`. Their first Ember log topics exactly match the packaged ABI. The
three lifecycle topics used by the adapter are:

- `VaultDeposit(address,address,address,uint256,uint256,uint256,uint256,uint256)`:
  `0x2f319f08ae3fcb401c4325ba57ae57a5d38a443aca074042d7b0329c69dec991`;
- `RequestRedeemed(address,address,address,uint256,uint256,uint256,uint256,uint256)`:
  `0xa860c7ba918bd53ab101f8fa1e1e8cee055aedf31b1d9c5b12401a91d79b17bd`;
- `RequestProcessed(address,address,address,uint256,uint256,uint256,uint256,bool,bool,uint256,uint256,uint256,uint256)`:
  `0x14239ade46d853ae1a98641c2a237d05a11e24ff2678eb6bf0e409953779a057`.

The topics are derived from the full packaged ABI signatures, not shortened
ERC-4626-shaped guesses. The upstream `v2.3.1` source at commit
[`4297da1`](https://github.com/ember-protocol/Ember-Vaults-EVM/blob/4297da1399f905fa9d890eb5ec1e716f1079617f/contracts/EmberVault.sol)
retains the same event fields. Add assertions for these topics and the pinned
`v1.1.1` version so a future proxy upgrade or ABI refresh cannot silently
invalidate historical decoding. Before adding another deployed Ember version
to the supported set, compare all three lifecycle event signatures and field
meanings against that implementation.

Upstream source also fixes two ticket identity rules:

- `_getChainTimestampMs()` returns `block.timestamp * 1000`, so the
  `RequestRedeemed.timestamp` value and the containing block timestamp describe
  the same instant at different resolutions. Persist the naive UTC block
  timestamp as the canonical ticket value and validate that the decoded event
  timestamp divided by 1,000 equals the receipt block timestamp.
- withdrawal requests use the proxy-storage `sequenceNumber`, incremented
  before constructing each request. It is initialised once, never decremented
  or reassigned, and survives queue deletion and UUPS upgrades. Treat it as
  globally monotonic and non-reused; practical reuse would require a `uint256`
  wrap. Completion queries still begin at the ticket's request block as a
  defence-in-depth bound.

The following lifecycle was executed successfully on an ephemeral Anvil fork
at block `24_496_689`:

1. Fund a fresh account from the configured Ethereum USDC whale.
2. Deposit exactly 100 USDC with `deposit(uint256,address)`.
3. Receive exactly `97.218907` Ember shares.
4. Approve the Ember vault to transfer those shares.
5. Call `redeemShares(uint256,address)` and parse request sequence `145`.
6. Observe `getAccountState(owner) == (97_218_907, [145], [])`.
7. Impersonate the Ember operator and call `processWithdrawalRequests(1)`.
8. Parse a successful `RequestProcessed` event for request `145`.
9. Observe `getAccountState(owner) == (0, [], [])`, zero remaining shares and
   `99.999999` USDC returned.

Upstream source confirms the key semantic difference from Lagoon and Ostium:

- deposits are synchronous and emit `VaultDeposit`;
- standard ERC-4626 `withdraw()` and `redeem()` revert with `UseRedeemShares`;
- `redeemShares()` escrows shares and emits `RequestRedeemed`;
- the operator later calls `processWithdrawalRequests()`;
- successful processing burns shares and transfers assets directly to the
  requested receiver in the operator transaction;
- there is no depositor-owned claim transaction after processing;
- cancelled or skipped requests return shares and emit `RequestProcessed` with
  the terminal flags set;
- upstream [`_processRequest()`](https://github.com/ember-protocol/Ember-Vaults-EVM/blob/4297da1399f905fa9d890eb5ec1e716f1079617f/contracts/EmberVault.sol#L2059-L2175)
  is atomic per queued request: it either returns
  all escrowed shares for a skipped/cancelled request or burns all requested
  shares and transfers the full calculated withdrawal amount, then removes the
  request and emits one `RequestProcessed`; it does not partially fill or
  requeue the request.

The implementation must model this as synchronous deposit plus asynchronous,
operator-finalised redemption. It must not pretend that Ember has a Lagoon- or
Ostium-style user claim call.

## Feature-parity definition

Parity means matching the useful adapter surface and evidence, not copying a
protocol lifecycle that Ember does not have.

| Capability | Lagoon | Ostium V1.5 | Ember target |
| --- | --- | --- | --- |
| Deposit flow | Request, settle, claim | Request, settle, claim | Synchronous deposit |
| Redemption flow | Request, settle, claim | Request, settle, claim | Request, operator transfer |
| Request event parsing | Yes | Yes | `RequestRedeemed` |
| Ticket persistence | Yes | Yes | Request sequence plus request block data |
| Current request state | Pending/claimable/none | Pending/claimable/reclaimable/none | Pending/none; terminal event supplies outcome |
| Completion analysis | Claim transaction | Claim transaction | Operator `RequestProcessed` transaction |
| Historical request discovery | Hypersync | Hypersync | Hypersync |
| Historical settlement timeline | `SettleDeposit`/`SettleRedeem` in DuckDB | Not yet generic | Successful `RequestProcessed` in the same DuckDB |
| Guarded asset-manager path | Yes | Yes | Deposit and `redeemShares` |
| Lagoon Safe integration | Native | Covered for Ostium | Required |
| Capability metadata | Async/async | Async/async | Sync/async |

## Adapter design

### Protocol-specific request types

Create `eth_defi/erc_4626/vault_protocol/ember/deposit_redeem.py` with:

- `EmberRedemptionTicket(RedemptionTicket)` as `dataclass(slots=True)`, storing
  `request_sequence_number`, request block number and naive UTC request block
  timestamp. `get_request_id()` returns the sequence number.
- `EmberRedemptionRequest(RedemptionRequest)`, whose parser reads exactly one
  `RequestRedeemed` event from the final transaction receipt and verifies the
  vault, owner, receiver, raw share count and millisecond event timestamp
  against the containing block before constructing the ticket.
- `EmberDepositManager(ERC4626DepositManager)` to reuse the standard
  synchronous deposit construction and ERC-4626 conversion helpers while
  overriding every Ember-specific lifecycle operation.

`create_redemption_request()` must:

- accept either decimal shares or raw shares, but not an ambiguous or zero
  amount;
- default `to` to `owner` while allowing a distinct non-zero receiver;
- verify the owner has enough shares when `check_enough_token=True`;
- return two bound calls in order: share-token `approve(vault, shares)` and
  `redeemShares(shares, receiver)`;
- rely on `RedemptionRequest.broadcast()` to execute both calls from the owner
  and parse the final receipt.

Including approval in the request prevents callers from silently omitting the
non-standard self-allowance Ember requires. It also makes the direct and guarded
flows use the same request object.

### Lifecycle methods

Implement and document the full manager contract:

- `has_synchronous_deposit()` returns true.
- `has_synchronous_redemption()` returns false.
- `is_deposit_in_progress()` returns false.
- `is_redemption_in_progress(owner)` reads the owner-specific
  `getAccountState(owner)` tuple and checks its first, ABI-named
  `totalPendingWithdrawalShares` return value. Pin that accessor in a test
  against the same tuple's `pendingWithdrawalRequestSequenceNumbers`, so it
  cannot be confused with vault-global pending shares.
- `can_create_deposit_request(owner)` reports whether deposits are currently
  available using the Ember protocol/vault state. Because this API accepts no
  amount, it is not a fillability guarantee: the vault enforces the applicable
  per-transaction and remaining-cap limits when `deposit()` is broadcast.
- `can_create_redemption_request(owner)` checks the withdrawal pause state,
  owner share balance and `minWithdrawableShares()`.
- `estimate_deposit()` and `estimate_redeem()` use the vault's
  `convertToShares()` and `convertToAssets()` at the supplied block.
- `estimate_redemption_delay()` exposes the Ember vault's documented estimated
  lock-up, but clearly labels it as an operator service estimate rather than an
  on-chain processing gate.
- `get_redemption_delay_over(address)` returns `None` because there is no
  deterministic on-chain deadline.
- `can_finish_redeem()` returns false because the user never owns a finish
  action.
- `finish_redemption()` returns `None`, with the shared return annotation widened
  to `ContractFunction | None` and documentation explaining operator-finalised
  protocols. It must never bind `processWithdrawalRequests()` for a depositor.

Before widening the shared return type, repeat a repository-wide audit of
`finish_redemption()` and `can_finish_redeem()` call sites. The current tree has
only concrete Lagoon/Gains tests and the manual Ostium script, all of which use
claim-capable managers; there is no generic in-repository production caller.
Preserve their behaviour and add focused coverage for the new contract:
callers must check `can_finish_redeem()` or the request status before attempting
to build a transaction, and must accept `None` for Ember without calling
`.transact()` or `.build_transaction()`. Keep the trade-executor audit and
no-claim handling as the explicit downstream hand-off below.

The lifecycle method names which perform reads (`get_redemption_request_status`,
`estimate_*` and `can_create_*`) are inherited API overrides and therefore keep
their established names. Any new standalone helper which performs a network
read must use the `fetch_` prefix. `get_ember_settlement_events_by_topic()` is
pure ABI mapping and does not perform a network read.

Keep the existing `AsyncVaultRequestStatus` values. For Ember,
`get_redemption_request_status(ticket)` returns `pending` while the exact
sequence number remains in the owner's pending sequence list and `none` after
the request is consumed. For Ember, `none` means only that the request is no
longer pending; it is never evidence of successful settlement on its own.
Callers must perform completion lookup and analyse the matching terminal event
before changing money or accounting state. The event determines whether the
terminal outcome was a successful transfer, cancellation or skip.

### Completion lookup and analysis

Add a generic optional hook to `VaultDepositManager`:

```python
def fetch_completed_redemption_tx_hash(
    self,
    ticket: RedemptionTicket,
) -> HexBytes | None:
    return None
```

The Ember override fetches `RequestProcessed` logs from the persisted request
block through the current block, restricts the query to the vault address and
event topic, and locates the terminal event by exact
`requestSequenceNumber`. After locating it, verify its owner, receiver and
shares against the ticket; raise on any mismatch instead of filtering the event
out and returning a false `None`. Use bounded log queries and do not scan
unrelated events. The globally monotonic request sequence makes one terminal
match the only valid result; raise on multiple conflicting matches instead of
choosing silently because they indicate corrupted input, an ABI mismatch or a
violated contract invariant.
Returning `None` means that no processing event has been observed yet; it does
not turn a non-pending request into a success. Treat this inconsistent state as
unresolved until the bounded lookup range and chain state have been checked.

`analyse_deposit()` parses the Ember `VaultDeposit` event from the supplied
transaction receipt and returns the actual depositor, receiver, deposited USDC,
minted shares, block number and naive UTC timestamp. It must work when the
outer transaction target is a guarded SimpleVault or Lagoon module because the
Ember event is still present in the receipt.

`analyse_redemption()` parses the exact matching `RequestProcessed` event:

- return `DepositRedeemEventAnalysis` with `shares` and `withdrawAmount` when
  both `skipped` and `cancelled` are false;
- return `DepositRedeemEventFailure` with a descriptive reason for a skipped or
  cancelled terminal request;
- reject a receipt whose owner, receiver, shares or request sequence does not
  match the supplied ticket.

Do not infer the final amount from `convertToAssets()`: Ember applies the rate
and any withdrawal fees at processing time, so `withdrawAmount` in the event is
the executed value.

### Ticket persistence and historical discovery

Override redemption ticket serialisation and reconstruction so JSON round trips
preserve:

- base vault, owner, receiver, raw shares and request transaction hash;
- `request_sequence_number`;
- request block number;
- naive UTC request block timestamp.

Implement `fetch_vault_flow_events()` for `RequestRedeemed` using the shared
Hypersync helpers in `eth_defi/vault/flow_events.py`. Decode the indexed owner
and receiver plus non-indexed shares, millisecond event timestamp and sequence
number. Use the Hypersync block timestamp, converted to naive UTC, as the
canonical ticket timestamp; assert the event timestamp divided by 1,000 equals
it rather than persisting two subtly different representations. Yield a
`PendingVaultFlow` with:

- direction `redeem` and status `pending`;
- `request_id` equal to Ember's sequence number;
- raw shares and no raw assets;
- owner as controller;
- ticket data accepted by `reconstruct_redemption_ticket()`.

The event-derived `pending` value is a discovery hint, not current-state proof:
historical results can include requests that were processed after the requested
event range. Every consumer must reconstruct the ticket and call
`get_redemption_request_status()` plus terminal-event lookup before acting. Add
a regression assertion using processed request `29` to demonstrate this
discovery-then-revalidation behaviour.

Deposits are synchronous and must not be emitted as pending deposit flows.

Use the known request at block `24_286_355` for the historical integration
test:

- owner/receiver: `0x74588dD3661781bfa0B497C613ad861B3Dae6F32`;
- request sequence: `29`;
- raw shares: `30_000_000`;
- canonical request block timestamp: `2026-01-21 23:09:23` naive UTC;
- event timestamp: `1_769_036_963_000` milliseconds;
- request transaction:
  `0x18165ec393dbba57b6bd1802925abce160ee15d78caf389725bbd7c73ea14dca`.

Its successful processing event is at block `24_290_495`, transaction
`0x9ad0c9fe93adcbffb158da6d4b8694059afea77b24c8a09deb8ae3ebba15ae79`,
with `withdrawAmount=30_663_930` and `requestSequenceNumber=29`.

### Historical settlement collection

Create `eth_defi/erc_4626/vault_protocol/ember/settlement.py`, following
`eth_defi/erc_4626/vault_protocol/lagoon/settlement.py`, with:

- `EMBER_PROTOCOL_NAME = "Ember"`;
- `fetch_ember_settlements()` for an inclusive block range;
- `get_ember_settlement_events_by_topic()` mapping only the full
  `RequestProcessed` topic to its contract event class;
- `build_ember_settlement_rows_from_logs()` returning generic
  `VaultSettlement` rows.

Use `fetch_vault_settlement_logs()` so Hypersync remains the preferred reader
when configured and chunked `eth_getLogs` remains the fallback. Do not treat
`VaultDeposit`, `RequestRedeemed` or unrelated operator/valuation events as
settlements.

Decode every `RequestProcessed` log before conversion. Store a settlement
marker only when both `skipped` and `cancelled` are false. Skipped and cancelled
requests are terminal lifecycle evidence for `analyse_redemption()`, but no
assets were paid out and they must not annotate a price row as a successful
settlement. The packaged event ABI has no separate payout-failure flag; these
two booleans are the complete terminal non-success discriminator.

Ember can process several requests in one operator transaction and emits one
`RequestProcessed` per request. The generic settlement database models a
settlement transaction and timestamp rather than per-user payloads, so collapse
multiple successful logs with the same transaction hash to one
`VaultSettlement(event_name="RequestProcessed")`. Keep different transactions
in the same block as separate rows. The per-request sequence, shares, receiver
and success/failure outcome remain available through the completion lookup and
receipt analysis paths; do not overload `event_name` with a sequence number.

Wire Ember into `eth_defi/erc_4626/settlement_scan.py`:

- add `ERC4626Feature.ember_like` to `SUPPORTED_SETTLEMENT_FEATURES`;
- include `EmberVault` in `PreparedSettlementVault`;
- prepare Ember topic metadata in `_prepare_settlement_vault()`;
- route Ember logs to `build_ember_settlement_rows_from_logs()`;
- update scanner documentation that currently lists only Lagoon and D2.

This makes normal all-chain scans and forced backfills share the same batched,
per-chain Hypersync/RPC read, incremental scan watermark and idempotent DuckDB
upsert used by Lagoon. Advance Ember's scan watermark to the full successfully
scanned range end regardless of the number of fetched logs or surviving
settlement rows. This includes both a genuinely empty log range and a non-empty
range whose `RequestProcessed` logs are all filtered out as skipped/cancelled.
Only adapter preparation, log fetching or row-building failure may withhold the
watermark; sparse or unsuccessful request history must not be rescanned
indefinitely.

Use the known successful settlement above as the live fixture:

- block: `24_290_495`;
- block timestamp: `2026-01-22 13:02:59` naive UTC;
- block hash:
  `0xb9cd1320438f956457ae081802fc404fa3d07c670bce61c7b60efa043f91209c`;
- transaction:
  `0x9ad0c9fe93adcbffb158da6d4b8694059afea77b24c8a09deb8ae3ebba15ae79`;
- event name: `RequestProcessed`.

## Vault capability and public metadata

Update `EmberVault` to return `EmberDepositManager` from
`get_deposit_manager()` and declare:

```python
VaultDepositManagerCapability(
    can_deposit=True,
    can_redeem=True,
    deposit_flow="synchronous",
    redemption_flow="asynchronous",
)
```

Add this only after the direct, historical and guarded tests pass. This is a
static statement that the adapter implements both directions; live pause,
allow-list, cap, liquidity and operator timing remain current-state concerns.

Run the PR #1269 probe for the Ember protocol on Ethereum and review the
packaged status artefact. The probe should exercise the guarded synchronous
deposit and record asynchronous redemption as not exercised by the generic
probe. The dedicated Ember tests provide the redemption evidence.

## Guard and Lagoon support

The existing synchronous deposit path already validates its receiver:
`SEL_DEPOSIT` dispatches to `_validate_ERC4626Deposit()`, which decodes
`(uint256,address)` and requires `isAllowedReceiver(receiver)`. Preserve this
validation while adding Ember support, and lock it down with an explicit
negative test; registering Ember must not weaken the existing deposit rule.

`whitelistERC4626()` already calls `allowApprovalDestination(vault)` and
whitelists the vault share token's `approve()` call site. This permits Ember's
required share-token `approve(vault, shares)` even though the token and spender
are the same vault address. Keep this behaviour and assert that approval step
separately in the guarded fork test before calling `redeemShares()`.

Add the selector for `redeemShares(uint256,address)` to
`contracts/guard/src/GuardV0Base.sol` and register it in the existing
`whitelistERC4626()` path so current deployment and whitelisting scripts remain
compatible.

Add a dedicated payload validator which decodes the receiver and requires
`isAllowedReceiver(receiver)`. A malicious asset manager must not be able to
send the eventual Ember payout to an arbitrary address.

This registration is intentionally selector-global within GuardV0: any address
registered through `whitelistERC4626()` which receives
`redeemShares(uint256,address)` is interpreted with Ember's receiver position.
That matches the guard's existing selector-based dispatch model but broadens
the shared ERC-4626 call surface. Add a protocol-neutral guard regression test
using a non-Ember ERC-4626 mock/call site: an unapproved second argument must be
rejected before target execution and an approved receiver must pass guard
validation. This makes the global assumption explicit and prevents a future
contract with the same selector but different argument semantics from being
silently treated as safe.

Do not whitelist `processWithdrawalRequests(uint256)`. It is an Ember operator
action, not an asset-manager action. Tests impersonate the live operator only
to simulate external settlement on an ephemeral fork.

Rebuild Guard and Safe integration artefacts with the repository compiler
commands (`make guard safe-integration`); never edit generated ABI JSON files
directly. Confirm the contract-size report remains within the deployment limit.

The guarded flow is:

1. Safe approves USDC to Ember through the module.
2. Safe calls Ember `deposit()` through the module and receives Ember shares.
3. Safe approves its Ember shares to the Ember vault through the module.
4. Safe calls `redeemShares(shares, safe)` through the module.
5. The external Ember operator processes the queue directly.
6. USDC is transferred to the Safe in the operator transaction.

## Test plan

### Direct manager fork test

Add `tests/erc_4626/vault_protocol/test_ember_deposit_redeem.py`, using
`JSON_RPC_ETHEREUM`, fork block `24_496_689`, the configured Ethereum USDC
whale and the fork-time Ember operator.

Cover in one full lifecycle:

- autodetection returns `EmberVault` and `EmberDepositManager`;
- capability is synchronous deposit plus asynchronous redemption;
- deposit and redemption estimates have fixed expected values;
- 100 USDC deposit produces exactly `97_218_907` raw shares
  (`Decimal("97.218907")`);
- `VaultDeposit` analysis reports the exact executed values;
- the redemption request contains approval then `redeemShares`;
- parsing yields request sequence `145` and the exact raw shares;
- ticket serialisation survives a JSON round trip;
- status changes from pending to none after operator processing;
- completion lookup returns the operator transaction;
- completion lookup returns `None` while an exact request is not yet processed;
- completion lookup raises if mocked or decoded log input contains more than
  one matching terminal event;
- completion lookup raises if the sequence matches but the decoded owner,
  receiver or share amount disagrees with the ticket;
- `RequestProcessed` analysis reports exactly `97_218_907` raw shares and
  `99_999_999` raw USDC (`Decimal("99.999999")`);
- owner account state and share balance are zero after processing.

Add focused negative coverage for a mismatched request sequence, a redemption
below the fork's exact `minWithdrawableShares()` value of `100_000`, and a
terminal `RequestProcessed` event with skipped/cancelled flags. Confirm the
minimum-size check rejects the request before constructing or broadcasting its
approval and redemption calls. Use decoded fixtures or small contract-call
mocks where a live fork transition would be unnecessarily expensive.

Assert raw token integers and `Decimal` values exactly. If any API boundary
produces a float, use `pytest.approx()` rather than bare float equality.

### Historical flow integration test

Extend `tests/vault/test_pending_vault_flow_events.py` with the known Ethereum
block and event values above. Assert the decoded direction, owner, receiver,
request id, raw shares and reconstructed `EmberRedemptionTicket` fields. Assert
the canonical ticket timestamp is exactly `2026-01-21 23:09:23` naive UTC and
that the decoded event timestamp is the same instant in milliseconds, matching
the direct-receipt parser's timestamp rule.

### Historical settlement tests

Add `tests/erc_4626/vault_protocol/test_ember_settlement.py`, mirroring
`tests/lagoon/test_lagoon_settlement.py`:

- offline conversion retains one successful `RequestProcessed` transaction;
- skipped-only and cancelled-only logs produce no settlement rows;
- a mixed operator transaction containing successful and skipped/cancelled
  requests produces one successful transaction marker;
- multiple successful `RequestProcessed` logs in one transaction collapse to
  one marker;
- different processing transactions in the same block remain separate rows;
- protocol, event name, vault, chain, transaction, block hash and naive UTC
  timestamp are exact.

Add a live Hypersync test over exactly 1,000 inclusive blocks centred on block
`24_290_495`. Assert the known transaction produces one `RequestProcessed` row
with the exact timestamp and block hash listed above. The test must use the
full ABI-derived topic and remain guarded by the same RPC, Hypersync and
`pytest.importorskip("hypersync")` conditions as Lagoon's live test.

Extend `tests/erc_4626/test_settlement_scan.py` so Ember is selected by
`ERC4626Feature.ember_like`, prepared with only the `RequestProcessed` topic,
routed through the Ember row builder in a mixed Lagoon/D2/Ember chain batch,
and advances its scan watermark after an empty successful read. Add a distinct
case where the fetched range contains only skipped/cancelled Ember logs: it
must write zero settlement rows but still advance to the requested range end.
Retain the existing behaviour that a failed Ember vault does not advance its
watermark.

### Guard test

Add `tests/guard/test_guard_simple_vault_ember.py` on the same fixed fork:

- deploy `SimpleVaultV0` and whitelist the Ember vault;
- fund it with exactly 100 USDC and record its starting balances;
- use the manager-generated calls to execute USDC approval and `deposit()` via
  GuardV0/`performCall`, then parse `VaultDeposit` from the guarded outer
  receipt and assert exactly `97_218_907` shares were minted to SimpleVault;
- use the same manager request object to execute share approval and
  `redeemShares(97_218_907, simple_vault)` via GuardV0/`performCall`, parse the
  guarded receipt into an `EmberRedemptionTicket`, and assert request `145` is
  pending with the exact account state;
- assert `can_finish_redeem()` is false and `finish_redemption()` is `None`, so
  the guarded asset manager never attempts a claim transaction;
- impersonate the external Ember operator only after the guarded request and
  call `processWithdrawalRequests(1)` directly, outside GuardV0;
- recover that operator transaction through completion lookup and pass it to
  `analyse_redemption()`;
- assert the successful event burns exactly `97_218_907` shares, transfers
  exactly `99_999_999` raw USDC to SimpleVault, leaves zero Ember shares and
  zero pending withdrawal shares, and changes SimpleVault's USDC balance by the
  analysed executed amount;
- prove an unapproved receiver is rejected separately for both synchronous
  `deposit()` and asynchronous `redeemShares()`;
- prove the asset manager cannot call `processWithdrawalRequests()`;
- add the protocol-neutral non-Ember selector test described above so the
  global validator behaviour is covered independently of Ember execution.

This guarded deposit-request-process-analysis sequence is a required complete
redemption-cycle integration test, not merely selector or request-construction
coverage.

### Lagoon integration test

Add `tests/lagoon/test_lagoon_ember.py`, following
`tests/gains/test_ostium_v15_lagoon.py`:

- deploy a Lagoon Safe with the Ember vault in `erc_4626_vaults`;
- fund and settle the Lagoon vault;
- use the Ember manager calls through the trading strategy module;
- parse and persist the redemption ticket;
- impersonate the external Ember operator to process one request;
- analyse the operator transaction and verify the Safe receives USDC and holds
  no redeemed Ember shares.

Use absolute expected values at the fixed block, not merely greater-than-zero
assertions. Keep tests as fixture and test functions, without pytest classes or
stdout output.

### Capability and regression tests

Extend the focused deposit-probe and scan-feature tests to assert Ember's mixed
flow capability serialises to public JSON. Retain fail-closed behaviour for all
other uncertified `ERC4626Vault` subclasses.

Add a focused optional-finish API regression test and repeat the caller audit
with `rg` during implementation:

- Ember always reports false and returns `None` without exposing the
  operator-only processing function;
- a protocol-neutral caller branches on capability/status and does not invoke
  transaction methods on `None`;
- every in-repository `finish_redemption()` caller is confirmed to be statically
  tied to a claim-capable manager or updated to handle the optional return.

Run only the focused tests, always with the required environment wrapper and
extended timeout:

```shell
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_ember.py tests/erc_4626/vault_protocol/test_ember_deposit_redeem.py -q
source .local-test.env && poetry run pytest tests/vault/test_pending_vault_flow_events.py -k ember -q
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_ember_settlement.py tests/erc_4626/test_settlement_scan.py -k ember -q
source .local-test.env && poetry run pytest tests/guard/test_guard_simple_vault_ember.py -q
source .local-test.env && poetry run pytest tests/lagoon/test_lagoon_ember.py -q
source .local-test.env && poetry run pytest tests/erc_4626/test_deposit_probe.py tests/erc_4626/test_scan_features.py -q
```

## Documentation and release notes

Update:

- `docs/source/vaults/ember/index.rst` to include the new deposit-manager API
  module, settlement reader and request/operator-transfer redemption lifecycle;
- `docs/source/api/erc_4626/index.rst` with the Ember `deposit_redeem` and
  `settlement` module stubs alongside the existing Lagoon settlement reader;
- module and class docstrings with links to Ember's canonical contracts and
  withdrawal documentation;
- `CHANGELOG.md` with a dated feature entry when the implementation PR is
  opened.

Document that the estimated withdrawal period is not a smart-contract time
lock and that live callers must handle pause, validation, liquidity and operator
timing changes.

## Downstream integration hand-off

The `web3-ethereum-defi` adapter can fully represent and analyse Ember, but the
trade-executor settlement retry currently assumes every asynchronous
redemption ends in a user claim transaction. Before enabling Ember vault trades
there, make a companion change which:

- on `AsyncVaultRequestStatus.none`, always calls
  `fetch_completed_redemption_tx_hash(ticket)` and analyses the recovered
  `RequestProcessed` event before any balance or accounting transition; `none`
  alone must never be recorded as successful settlement;
- keeps the request unresolved and raises an operator-visible error if the
  terminal lookup returns `None`, rather than falling through to the legacy
  generic `Withdraw`-event recovery scan and guessing success;
- accepts `finish_redemption(ticket) is None` for an already operator-finalised
  request;
- records the recovered operator processing transaction and passes it to
  `analyse_redemption()` without signing another transaction;
- marks `DepositRedeemEventFailure` terminal outcomes failed instead of leaving
  them pending forever;
- includes restart/idempotency tests for success and skipped/cancelled Ember
  processing.

Do not advertise trade-executor routing support for Ember until this companion
path is merged. This hand-off does not block publishing the lower-level Python
adapter capability from this repository once its own evidence is complete.

## Acceptance criteria

The work is complete when:

- `EmberVault.get_deposit_manager()` returns the protocol-specific manager;
- public metadata reports `synchronous` deposit and `asynchronous` redemption;
- all request construction, parsing, serialisation, status, completion lookup
  and event analysis paths have focused tests;
- direct and historical tickets use the same validated naive UTC block
  timestamp, and completion lookup relies on the verified monotonic request id
  before post-validating owner, receiver and shares;
- the optional `finish_redemption()` contract is audited across repository
  callers and Ember's no-finish path has focused coverage;
- the fixed-fork direct cycle reproduces the exact balances above;
- historical Hypersync decoding reconstructs a usable ticket;
- historical settlement collection stores successful `RequestProcessed`
  transaction markers, excludes skipped/cancelled requests, handles batched
  processing without duplicate transaction markers and participates in the
  incremental all-chain scanner;
- the GuardV0/SimpleVault test completes the full deposit, redemption request,
  external operator processing, completion lookup and analysis cycle with exact
  raw balances, without allowing an arbitrary receiver or operator-only call;
- the selector-global `redeemShares` receiver rule has a protocol-neutral
  non-Ember guard regression test;
- the Lagoon Safe path completes with the same no-claim settlement semantics;
- Guard/Safe artefacts are compiler-generated and contract size is acceptable;
- the PR #1269 status artefact records a successful guarded Ember deposit;
- focused formatting, tests and documentation builds pass;
- the PR description calls out the downstream no-claim settlement hand-off.
