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
TVL and gross share price using batched historical Multicall reads.
Blue and Onyx factory detections retain a zero ERC-4626 deposit count because
their investor actions use protocol-specific events. Their feature flags bypass
the generic activity threshold so recurring scans continue both histories.

The historical Blue reader derives gross share value from GAV and share supply.
The future exact source is Enzyme's release-aware
`FundValueCalculatorRouter.calcNetShareValue()
<https://github.com/enzymefinance/protocol/blob/dev/contracts/persistent/fund-value-calculator/FundValueCalculatorRouter.sol>`__,
called through ``eth_call`` so fee settlement is simulated but not persisted.
It requires a third call per vault and sampled block. Until that is implemented,
the frontend fee-fill step will subtract the current exported fees to estimate
net value. Historical fee rates and performance-fee state can change, so this
is not an exact historical net share value.

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

For comparable scanner metrics, each currently discovered Base Onyx named value
asset is exported with a canonical Base ERC-20 denomination: ``USD`` with USDC,
``BTC`` with cbBTC, and ``EUR`` with EURC. This is a reporting convention for
the share price and TVL, not a claim that the corresponding token is accepted
by the current deposit handler: an USD-valued Onyx handler can instead accept
USDT or another ERC-20. Transaction integrations must inspect the active
handler rather than use this normalised denomination. A new, unsupported named
value asset fails before historical scanning instead of producing rows without
a denomination token.

The Onyx adapter supports discovery, metadata and historical accounting. It
does not implement deposits, redemptions, flow-event accounting or portfolio
composition, because these depend on the particular handler configuration.

The scanner records feature flags separately: ``enzyme_onyx_like`` marks
official Onyx SharesFactory deployments, while ``enzyme_blue_like`` is the
distinct label for the Blue VaultProxy/ComptrollerProxy model, including its
separate Base Dispatcher. The old
persisted ``enzyme_like`` feature value is retained as an Onyx compatibility
alias.

Every exported vault link points directly to that vehicle's Enzyme application
page using its canonical VaultProxy or Shares address and deployment network.
Protocol-level metadata and the links section below may still use the generic
discovery catalogue because they do not identify an individual vault.

Listing metadata
----------------

Enzyme share-token contracts provide a vault name, but not a manager-entered
strategy narrative. For Blue vaults, the authenticated `Enzyme GetVault API
<https://sdk.enzyme.finance/api/endpoints/vault/>`__ is the authoritative
offchain metadata source. It returns a manager-entered ``tagline`` for the
catalogue's short description, plus a ``description`` for the product-detail
view. The API documentation says it aggregates Enzyme-managed offchain sources
including vault descriptions, rather than merely reproducing onchain ERC-20
names.

The scanner does not call that API per vault during each chain scan. The
metadata migration gathers all existing Blue rows through a bounded,
authenticated batch and writes a versioned JSON cache under
``~/.tradingstrategy/cache/enzyme/vault-metadata.json``. Blue adapters read
that cache without credentials, so the scheduled scanner remains deterministic
and does not turn a temporary API failure into a database-wide description
rewrite.

The migration retains a successful API response with empty fields: this is
evidence that the manager has supplied no public copy, not a failed lookup.

Onyx supports manager-editable vault and collection taglines and descriptions
in its management application, but Enzyme does not document a public API for
this data. The scanner therefore does not scrape a gated interface or consume
an undocumented endpoint. Every refreshed Onyx row has an empty short
description and the exact long-description marker ``Description is not
publicly available``; a dedicated migration marker refreshes older generic
copy once. Blue leaves each description field empty when its official API
response has no manager text.

Deposit permission and availability
-----------------------------------

Current vault metadata exports the shared ``deposit_permission`` status. For
Blue, the direct adapter reads the active PolicyManager contracts and reports
``whitelisted`` when the reviewed ``ALLOWED_DEPOSIT_RECIPIENTS`` policy is
enabled. This policy limits the wallets that may invest. No such policy means
``permissionless``; this classification does not promise that a deposit will
succeed, because approvals and other fund policies still apply. Asset,
adapter, transfer-recipient, deposit-size and redemption policies do not turn
the deposit-permission enum into ``whitelisted``. The accompanying
``whitelist.notes`` value makes clear that this is an address-level
restriction and does not establish an offchain KYC process.

The classification is per vault, not per Enzyme product family. `Enzyme's
Blue and Onyx comparison <https://www.enzyme.finance/blue-vs-onyx>`__ describes
Blue access as public while also listing whitelisting and deposit control for
both products. In practice, Blue is permissionless unless its manager enables
the `Allowed Deposit Recipients policy
<https://docs.enzyme.finance/user-documentation/blue-enzyme-vaults/markdown/seeding>`__.
The official Blue ``GetVaultConfiguration`` API exposes the same enabled
policy for an independent current-state comparison.

Onyx Shares does not enumerate its active deposit handlers. The Enzyme
migration therefore collects ``DepositHandlerAdded`` and
``DepositHandlerRemoved`` alongside factory events in the same chain-level
Hypersync stream. It then inspects all active handlers at the fixed metadata
block using one batched Multicall read. A ``SyncDepositHandler`` is
``whitelisted`` when ``getDepositorAllowlist()`` is non-zero; an
``ERC7540LikeDepositQueue`` is ``whitelisted`` when
``getDepositRestriction()`` selects its internal or external controller
allowlist. ``SharesMintHandler`` is also permissioned because only the owner or
an admin can select recipients for subscriptions settled offchain.

The migration persists that active handler set on each Onyx discovery lead.
Later all-chain scanner cycles include the same add/remove topics in their
normal incremental Hypersync stream and repeat only the batched current-state
handler read. They therefore refresh current permission without rescanning
historical blocks or falling back to per-vault event or RPC calls.

Issuance hooks can run arbitrary policies, including Chainlink ACE. A handler
with no built-in allowlist is therefore public only when its relevant pre- and
post-issuance hooks are also absent. The aggregation rule is: a proven public
active route makes the vault ``permissionless``; all active routes requiring
prior account approval make it ``whitelisted``; and a vault with no active
handler is permissionless for identity policy but has no route that can
currently accept deposits. An
unrecognised active handler or opaque hook remains ``unknown``. Share-transfer
validators are deliberately excluded because they validate secondary
transfers, not minting. Likewise, an administrator's ability to choose which
queued requests to settle is an operating condition rather than an address
whitelist. The
`Onyx Shares and components documentation
<https://docs.enzyme.finance/onyx-protocol/architecture/shares-and-components>`__
defines deposit handlers as the accounts authorised to perform deposit actions,
while the `subscription control documentation
<https://docs.enzyme.finance/onyx-user-documentation/enzyme-vault/subscription/control>`__
describes the optional wallet allowlist. Neither is a substitute for reading
each vault's current handler configuration.

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
combined with an additional protocol fee. Enzyme's `Protocol Fees
<https://docs.enzyme.finance/user-documentation/blue-general-info/protocol-fees>`__
documentation defines this as a fee on Assets Under Technology, settled by
share inflation or MLN payment, rather than a high-water-mark performance fee.
The current export therefore includes the ProtocolFeeTracker rate in the
user-facing management fee. It also retains the protocol rate separately, so
consumers can calculate the manager-only rate as ``Mgmt fee - Protocol fee``.
Blue management, performance, entrance and exit rates are current reads only.
Historical Blue fee configuration remains TODO because releases, FeeManager
plugins and protocol-fee trackers can change at migration boundaries.

Permission audit report
-----------------------

``scripts/enzyme/report-whitelist.py`` reads the persisted metadata used by
the website and groups all Enzyme rows by chain, Blue/Onyx architecture and
``whitelisted``/``permissionless``/``unknown`` status. It does not mutate the
database. Set ``DETAILS=true`` to print every vault. To compare Blue rows with
Enzyme's authenticated `vault configuration API
<https://sdk.enzyme.finance/api/endpoints/vault/>`__, set
``CHECK_ENZYME_API=true`` and ``ENZYME_API_TOKEN``; ``MAX_WORKERS`` controls
the bounded threaded API reads. Onyx is excluded from this API comparison
because its authoritative permission state belongs to the active handler set.
The Enzyme migration refreshes a healthy Blue or Onyx row whose persisted
permission is missing or ``unknown``. The Onyx-specific migration marker also
forces one refresh of previously labelled rows when handler-classification
semantics change. An inconclusive custom handler is not retried repeatedly at
the same checkpoint, but a later completed run evaluates it at the newer fixed
block.

.. code-block:: shell

    poetry run python scripts/enzyme/report-whitelist.py

    CHECK_ENZYME_API=true ENZYME_API_TOKEN="$ENZYME_API_TOKEN" \
        poetry run python scripts/enzyme/report-whitelist.py

    ENZYME_SCAN_PRICES=false \
        poetry run python scripts/enzyme/backfill-history.py

Current metadata migration
--------------------------

``scripts/enzyme/migrate-current-metadata.py`` is the safe production entry
point for applying cached optional Blue descriptions,
the explicit Onyx unavailable marker, direct address-specific links and current
Blue and Onyx permission data to existing Enzyme rows. It reuses the
targeted factory discovery and durable metadata batching while forcibly
disabling historical price and cleaned-Parquet writes. Factory and Onyx handler
events use the same per-chain Hypersync stream, and all active Onyx handlers
use one current-state Multicall pass on Base. Successfully migrated rows become
their own resume markers, so an interrupted rerun skips them without a blanket
metadata refresh or duplicate RPC calls. The metadata-only checkpoint is
separate from the historical-price checkpoint, and the command holds the
shared scanner writer lock while the metadata pickle is loaded and replaced.
Blue permission reads cover Sulu and the deprecated Encore and Phoenix policy
managers and whitelist identifiers used by the Enzyme website. Current NAV and
fee fields remain blank when a deprecated vault can no longer execute its old
release calls; name, symbol, denomination and the architecture-specific
description policy remain mandatory. Blue API fields are optional, whereas
Onyx must have an empty short description and the explicit unavailable
long-description marker.

.. code-block:: shell

    DRY_RUN=true poetry run python scripts/enzyme/migrate-current-metadata.py
    MAX_WORKERS=8 poetry run python scripts/enzyme/migrate-current-metadata.py

Offchain description migration
------------------------------

``scripts/enzyme/migrate-offchain-metadata.py`` fetches Enzyme Blue vault
taglines and descriptions above its recorded accounting-unit NAV threshold from
Enzyme's authenticated ``GetVault`` API, warms the durable adapter cache and
updates the public metadata pickle.
It is deliberately a separate migration: it has no JSON-RPC or Hypersync work,
does not change historical price data, and does not attempt unsupported Onyx
UI scraping. It starts in dry-run mode and writes neither cache nor database
if any API response fails. A real run creates a timestamped backup of the
metadata pickle before modifying it.

While collecting, a real run writes the successful API replies to
``enzyme-offchain-metadata-state.json`` next to the metadata pickle. This
minimal migration-only checkpoint resumes after an interruption without
repeating completed API reads. It is deleted only after the cache and metadata
pickle have both been updated successfully; set ``ENZYME_METADATA_STATE_PATH``
to override its location.

To conserve Enzyme API quota, only Blue rows with a recorded NAV greater than
1,000 in a reviewed USD-pegged accounting unit, 1 in an ETH-equivalent unit,
or 0.1 in a BTC-equivalent unit are collected. The migration does not convert
unsupported denominations through an inferred exchange rate. It also clears
the exact retired generated Blue fallback fields locally for every Blue row;
this does not make an API request and ensures older rows do not retain invented
copy.
After a complete cache/database update, later runs reuse the cache without a
token. Set ``ENZYME_METADATA_REFRESH=true`` with a token to fetch all eligible
Blue rows again.

Create an API token in the `Enzyme application
<https://app.enzyme.finance/account/api-tokens>`__, then run:

.. code-block:: shell

    source .local-test.env
    ENZYME_BLUE_API_TOKEN="$ENZYME_BLUE_API_TOKEN" \
        poetry run python scripts/enzyme/migrate-offchain-metadata.py

    ENZYME_BLUE_API_TOKEN="$ENZYME_BLUE_API_TOKEN" DRY_RUN=false MAX_WORKERS=1 \
        poetry run python scripts/enzyme/migrate-offchain-metadata.py

For production, first stop ``vault-scanner-looped`` and run this command in
the one-shot container described above, so the cache and metadata database are
the same mounted persistent state used by the scanner. Restart the looped
scanner after the migration completes.

For production maintenance, first inspect ``docker-compose.yml`` and stop the
looped scanner. Run the migration through the one-shot service so it uses the
mounted production state, then restart the looped service:

.. code-block:: shell

    source ~/vault-scanner/vault-rpc.env
    cd ~/vault-scanner/web3-ethereum-defi
    docker compose stop vault-scanner-looped
    docker compose run --rm --entrypoint /bin/bash \
        -e ENZYME_BLUE_API_TOKEN -e MAX_WORKERS=1 vault-scanner-oneshot \
        -c 'poetry run python scripts/enzyme/migrate-offchain-metadata.py'
    docker compose start vault-scanner-looped

Vault export
------------

``scripts/enzyme/export-vaults.py`` prints the local Enzyme database as a
Markdown table, including its stored short-description column. It is read-only
and never contacts Enzyme, JSON-RPC or Hypersync. Its
``Total value (accounting unit)`` column
is the vault's accounting-unit NAV, not USD-normalised TVL, so rows with
different denominations must not be ranked together. To review the twenty
largest rows among selected USD-pegged accounting units, run:

.. code-block:: shell

    VALUE_UNITS=USDC,DAI,USDT SORT_BY_TOTAL_VALUE=true LIMIT=20 \
        poetry run python scripts/enzyme/export-vaults.py

``VALUE_UNITS`` is a case-insensitive comma-separated filter,
``SORT_BY_TOTAL_VALUE=true`` sorts the selected records descending by their
reported NAV, and ``LIMIT`` caps the output. The command does not infer a
live USD exchange rate or guarantee that a pegged asset is worth one dollar.

Links
-----

- `Discover Onyx Base vaults <https://app.enzyme.finance/discover/vaults?network=base>`__
- `Base contract deployments <https://docs.enzyme.finance/onyx-protocol/contract-addresses>`__
- `Onyx security information <https://docs.enzyme.finance/onyx-protocol/security>`__
- `Onyx GitHub <https://github.com/enzymefinance/protocol-onyx>`__
- `Blue GitHub <https://github.com/enzymefinance/protocol>`__
