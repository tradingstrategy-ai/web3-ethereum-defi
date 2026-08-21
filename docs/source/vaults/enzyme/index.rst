Enzyme vaults
=============

`Enzyme <https://enzyme.finance/>`__ provides two distinct EVM vault
architectures: Enzyme Blue and Enzyme Onyx. Both issue ERC-20 investor shares,
but neither should be assumed to implement ERC-4626.

Enzyme blue
-----------

`Enzyme Blue <https://docs.enzyme.finance/enzyme-blue-protocol>`__ is the
established Enzyme architecture. A fund consists of a ``VaultProxy`` share
token and a paired ``ComptrollerProxy`` that controls fund accounting,
assets and investor actions. The existing :mod:`eth_defi.enzyme.vault` helpers
support this two-contract model for reading and managing funds. The scanner
discovers every reviewed Blue vault on Ethereum, Polygon, Base and Arbitrum from the
persistent Dispatcher ``VaultProxyDeployed`` event. It records the canonical
VaultProxy together with its ComptrollerProxy accessor, reads current GAV,
share supply and fees through a direct VaultBase adapter, and backfills GAV,
TVL and gross share price using one historical Multicall batch per chain.

Enzyme onyx
-----------

`Enzyme Onyx <https://docs.enzyme.finance/onyx-protocol>`__ is a separate,
modular architecture. The current scanner support targets Onyx SharesFactory
deployments on Base, where a vault's standalone Shares token represents an
investor's interest in the vehicle.

The Shares contract is an ERC-20 token rather than an ERC-4626 vault. The
adapter therefore reads it directly through :class:`eth_defi.vault.base.VaultBase`.
The scanner discovers Base Onyx vaults from the official SharesFactory's
``ProxyDeployed`` events and reads their stored share price and share supply.
It calculates total value as ``share price × share supply`` in the declared
value asset. A value asset can be a named unit, such as USD, rather than an
ERC-20 token, so this value is not necessarily a US-dollar valuation.

For comparable scanner metrics, Base Onyx Shares with the named ``USD`` value
asset are exported with Base USDC as their denomination token. This is a
reporting convention for the USD-valued share price and TVL, not a claim that
USDC is the asset accepted by the current deposit handler: an Onyx handler can
instead accept USDT or another ERC-20. Transaction integrations must inspect
the active handler rather than use this normalised denomination.

The Onyx adapter supports discovery, metadata and historical accounting. It
does not implement deposits, redemptions, flow-event accounting or portfolio
composition, because these depend on the particular handler configuration.

The scanner records feature flags separately: ``enzyme_onyx_like`` marks
official Onyx SharesFactory deployments, while ``enzyme_blue_like`` is the
distinct label for the Blue VaultProxy/ComptrollerProxy model, including its
separate Base Dispatcher. The old
persisted ``enzyme_like`` feature value is retained as an Onyx compatibility
alias.

Deposit permission and availability
-----------------------------------

Current vault metadata exports the shared ``deposit_permission`` status. For
Blue, the direct adapter reads the active PolicyManager contracts and reports
``whitelisted`` when the reviewed ``ALLOWED_DEPOSIT_RECIPIENTS`` policy is
enabled. This policy limits the wallets that may invest. No such policy means
``permissionless``; this classification does not promise that a deposit will
succeed, because approvals and other fund policies still apply. The
accompanying ``whitelist.notes`` value makes clear that this is an
address-level restriction and does not establish an offchain KYC process.

Onyx Shares does not enumerate its active deposit handlers. Because a handler
can impose a depositor allowlist or a queue-controller restriction, the direct
adapter exports ``unknown`` rather than guessing whether deposits are
permissioned. Establishing this requires a separate, chain-level HyperSync
handler index.

Neither architecture exports historical ``deposits_open`` or
``redemption_open`` values. Blue policies and Onyx handlers are mutable and
can depend on the particular investor and action, so past GAV, share price and
supply samples cannot demonstrate universal availability. These historical
columns intentionally remain null rather than reporting a misleading state.

Fees
----

The Onyx adapter reads current fees selected per vehicle;
it reads active management and performance fee trackers, plus the current
standard FeeHandler entrance and exit rates, during a current metadata scan.
Management and performance rates are annual fractions; entrance and exit rates
reduce the investor's issued or redeemed shares. The standard FeeHandler has no
separate global protocol-fee setting, so the exported Onyx management fee is
already the full user-facing recurring charge and does not double-count a
platform charge.
Historical fee rates are not yet exported, because the fee handler or tracker
can change over a vault's lifetime; that backfill needs component-change,
``RateSet``, ``EntranceFeeSet`` and ``ExitFeeSet`` event handling.

Blue has a different fee model. Its fund-level FeeManager configuration can be
combined with an additional protocol fee. The `protocol-access mechanism
<https://specs.enzyme.finance/topics/protocol-fee>`__ can settle that charge
either by minting shares to the ProtocolFeeReserve or by paying MLN. The
current export's management fee is the user-facing sum of the manager and
ProtocolFeeTracker rates. It also publishes the protocol rate separately as a
breakdown; consumers must not add it to management a second time. Blue
management, performance, entrance, exit and protocol rates are current reads
only.
Historical Blue fee configuration remains TODO because releases, FeeManager
plugins and protocol-fee trackers can change at migration boundaries.

Links
-----

- `Discover Onyx Base vaults <https://app.enzyme.finance/discover/vaults?network=base>`__
- `Base contract deployments <https://docs.enzyme.finance/onyx-protocol/contract-addresses>`__
- `Onyx security information <https://docs.enzyme.finance/onyx-protocol/security>`__
- `Onyx GitHub <https://github.com/enzymefinance/protocol-onyx>`__
- `Blue GitHub <https://github.com/enzymefinance/protocol>`__
