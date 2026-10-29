Atoma API
---------

`Atoma <https://app.atoma.fi/>`__ integration.

Atoma is a delta-neutral USDC vault on Arbitrum. Depositors receive Atoma Vault
Share tokens, while the protocol allocates capital to perpetual DEX strategies
across venues such as Nado and Extended.

Atoma uses standard ERC-4626 deposits, but disables direct ERC-4626 withdrawals.
Redemptions use an epoch-based request-and-claim flow through
``requestWithdrawal(shares)`` and ``claimWithdrawal(epochId)``.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/atoma>`__
- `App <https://app.atoma.fi/>`__
- `Twitter <https://x.com/atoma_fi>`__
- `Proxy vault <https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c>`__
- `Verified implementation <https://arbiscan.io/address/0x9161be115867c347adfb4c88a80e759920de86dc#code>`__

.. autosummary::
   :toctree: _autosummary_atoma
   :recursive:

   eth_defi.erc_4626.vault_protocol.atoma.vault
   eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem

Execution and accounting
~~~~~~~~~~~~~~~~~~~~~~~~

The adapter supports Atoma Index and Atoma RWA on Arbitrum with implementation
``0x9161be115867c347adfb4c88a80e759920de86dc``. Every transaction builder checks
the proxy version. An unreviewed upgrade disables execution. The historical
interface remains available for receipt decoding.

Deposits require at least 100 native USDC and mint shares synchronously. The
receipt gives the actual shares after performance-fee accrual; the native
``deposit`` function has no minimum-shares slippage argument. Only newly minted
shares are locked until the receiver's deposit epoch ends. Approval is a
separate transaction to the manager's declared approval target. The receiver
must be the actual custody caller (EOA, SimpleVault or Safe).

A withdrawal request escrows shares and targets the following epoch. Its end
is only the earliest operator-settlement time. Passing time does not settle
requests. Settlement burns escrowed shares and reserves a liability; claiming
later transfers USDC to the caller. Deposits can be paused while requests and
claims remain enabled. There is no cancellation route.

Persist ``serialize_redemption_ticket()`` immediately after the request receipt.
``reconstruct_redemption_ticket()`` works offline. Ticket identity is chain,
vault, owner and settlement epoch; raw amounts are decimal strings. The mined
event determines the epoch even if inclusion crosses a boundary. Recover
broadcast transactions from their receipts before resubmitting. Indexed request
discovery and completed-claim lookup use Hypersync; discovery hints do not
establish current claim status.

Atoma aggregates requests for one owner and epoch. The adapter refuses a second
known request for the same epoch. If a concurrent or external request enlarges
the aggregate, use ``reconcile_redemption_ticket()`` with a fully caught-up
Hypersync client. Inspect every contribution, explicitly retain unattributed
external requests, then acknowledge all request identities and atomically
replace the component tickets with the aggregate. This library does not own
the consumer ledger and cannot perform that atomic write. Never credit the
aggregate payout separately to each component. Indexer lag defers recovery.

``claimable`` means settled, not liquid. ``can_finish_redeem()`` simulates the
exact owner claim; known insufficient-idle failures are temporarily unavailable.
Evidence mismatches, reconciliation requirements and unreviewed versions raise
``VaultFlowUnavailable``; polling consumers must handle that exception.
The status remains ``claimable`` when the epoch is settled, independently of
these execution restrictions.
Settled claims can subsequently suffer a haircut. Receipt analysis validates
custody, shares, epoch, emitter and denomination-token transfer. The event's
assets are already net of the 0.5% exit fee. ``AtomaFlowAnalysis`` reports the fee
separately and supports a verified zero payout. Do not deduct the fee again.

Track queued shares, settled liabilities and realised cash separately. Executor
support for aggregate tickets, loss realisation and restart reconciliation is
required before strategy activation. This integration does not implement
executor accounting or backtest settlement scheduling.

Guard configuration and validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``whitelistAtoma()`` instead of the generic ERC-4626 whitelist. Lagoon's
configuration, the existing-vault whitelist script and the deposit probe select
this automatically. It enables USDC approval/deposit plus caller-bound request
and claim selectors, registers shares as an asset, and emits ``ERC4626Approved``
for discovery. It does not enable share transfers, share approvals, ordinary
redemption or administrative calls.
The allowlist is additive: an existing broad whitelist must be removed or the
Guard/module replaced before relying on this restriction. Existing modules
need compatible bytecode and configuration.

``force_settle()`` is restricted to Anvil. It advances to the ticket's epoch end,
impersonates the actual operator, funds native gas and settles the whole epoch.
It does not alter protocol storage, NAV or vault liquidity. Other users' pending
requests in that epoch are settled too.

The real guarded lifecycle tests use the shared fork pool at block 519150162
for both deployments and both SimpleVault and Safe custody. The normal Arbitrum
fork block 487039644 predates this implementation and is used for historical
readers. Owner-wide progress diagnosis scans every possible epoch in bounded
Multicall batches; use ticket-specific status calls for routine polling.

See ``docs/README-atoma-execution.md`` in the repository for focused test commands
and the recorded integration evidence. No funded production round trip is
implied by fork tests.
