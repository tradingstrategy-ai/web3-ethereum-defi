Nest
====

`Nest <https://www.nest.credit/>`__ is real-world-asset vault
infrastructure built by the team behind Plume. Its products provide routes
for stablecoin deposits and redemptions into tokenised yield strategies. A
product can have several chain and denomination routes, while its share token
remains separate from the
chain-specific ``NestVault`` entrypoint.

NestVault combines ERC-4626 accounting with ERC-7540 asynchronous redemptions
and ERC-7575 separate-share-token support. The adapter detects Nest's published
deployments using the chain-restricted ``operatorRegistry()`` view and exposes
their aggregate queued-share balance through ``totalPendingShares()``. A
redemption request may need compliance checks and a later claim, so the
integration deliberately does not advertise a generic deposit manager.

The adapter joins Nest's public `contract catalogue
<https://api.nest.credit/v1/vaults?status=all>`__ with its public CMS for
product names, strategy descriptions, yield-source and risk explanations,
holder counts, live statistics, separate reported and target APYs, and the
estimated redemption duration. A CMS yield estimate may be unpublished or
stale and is never treated as realised performance. The
first-party snapshot is stored with scanner metadata; its content remains
advisory, while contract state is authoritative for a transaction. Active Arc
routes link to their product page in `Nest's current vault application
<https://app.nest.credit/vaults>`__; other routes link to its catalogue.

The scanner uses a reviewed product manager name where Nest identifies one,
or the first published yield-source partner as an indicative
``_manager_name`` when it does not. A yield-source partner can be an issuer
or asset provider. `Nest's official site <https://nest.credit/>`__ names
Nest DAO LLC as the primary curator of Nest vaults. Exported Nest rows display
the curator as Nest DAO. Product managers such as BlackOpal, M11 Credit and
Plume remain separate manager labels. The maintained strategy
tags cover the EVM entrypoints of eight reviewed products.

As of 2026-10-06, Nest's catalogue publishes routes on Ethereum, BNB Chain, Monad,
Worldchain, Robinhood Chain, Arc, Base, Plasma, Avalanche, Plume and Morph.
The Nest classification probe covers these chains except Morph, which has no
active catalogue route. Envio HyperSync has Worldchain and Plume endpoints;
their onchain verification also requires ``JSON_RPC_WORLDCHAIN`` and
``JSON_RPC_PLUME`` respectively. The migration selects every active chain by
default and requires its ``JSON_RPC_*`` setting; ``NETWORKS`` can select a
subset for a focused repair.
Published catalogue start blocks are historical lower bounds and may precede
the deposit entrypoint's actual deployment. The shared historical reader also
honours Multicall3's deployment boundary, including Plume block 39,679.
The catalogue also lists Solana mints for nine active products as of
2026-10-06. This EVM scanner does not collect those deployments. Coverage
counts refer to EVM entrypoints; Nest's product TVL can include positions
beyond a single entrypoint's share supply.
The recurring all-chain scanner includes Arc, Worldchain and Plume by default
when their ``JSON_RPC_*`` variables are configured. It also scans their prices
when the global ``SCAN_PRICES`` switch is enabled.
``scripts/nest/migrate-vaults.py`` seeds or refreshes active routes on
selected chains, and refreshes CMS descriptions, managers and curator fields
on existing Nest rows on those chains. After metadata, the same command fills
the per-chain HyperSync timestamp cache and backfills hourly raw and cleaned
price histories for the selected Nest entrypoints. The cleaned public history
includes only recognised stablecoin denominations; other routes retain raw
observations. The migration keeps unrelated price rows, scheduled reader
state and discovery cursors. Run the default dry run
before ``DRY_RUN=false``; the applied run holds the shared scanner writer lock
and backs up metadata before writing it. For an isolated run, set
``VAULT_DB_PATH``, ``UNCLEANED_PRICE_DATABASE``,
``CLEANED_PRICE_DATABASE`` and ``TIMESTAMP_CACHE_DIR`` together. Set
``NEST_SCAN_PRICES=false`` only for a deliberate metadata repair. See
``scripts/erc-4626/README-vault-scripts.md`` for the migration commands.
Routine Nest scans retain every successful NAV sample too. The migration does
not seed scheduled scan progress: a first routine scan without saved reader
state can reread this history. Resume interrupted migrations with ``NETWORKS``
set to the remaining chains; there is no separate migration completion cursor.
Imported Nest routes bypass the generic deposit-count activity shortcut so
their historical lower bounds are not clamped to a recent 14-day window.
Stateful polling remains adaptive: small or inactive routes can be sampled
daily or weekly even though every successful sample is retained.

``scripts/nest/list-vaults.py`` tabulates every active catalogue route with
its exported name, chain, curator, TVL, one-month and all-time CAGR. Names are
checked against local scanner metadata; chain and curator are checked against
the catalogue route and reviewed Nest DAO identity. It shows Nest's
product-level TVL and reported SEC 30-day yield in separate columns: these
figures repeat across chain routes and must not be confused with the export's
price-derived figures. Exported TVL and CAGR can also repeat across deposit
routes that share a product and should not be summed across those routes. The
table includes the price observation date, share price and history start date.
All-time CAGR describes the available collected history, which may be shorter
than the vault lifetime. The report prefers net CAGR when
available, matching the export's category metric. The report labels gross
fallbacks explicitly. Nest's fee decoder is not implemented, so these gross
metrics do not establish returns after all fees. Newly imported routes cannot have a one-month export
CAGR until enough historical price data exists. Nest's application displays
rolling NAV yield using simple annualisation, while its API also offers
composition yield and a SEC month-end window. These fields should be compared
with the export only after accounting for their formula and observation dates.

The representative nOPAL USDC route is a verified
`Avalanche NestVault <https://snowtrace.io/address/0xd258029cf5a177e3306e09fbea63424543a505c0#code>`__.

.. autosummary::
   :toctree: _autosummary_nest
   :recursive:

   eth_defi.erc_4626.vault_protocol.nest.vault
   eth_defi.erc_4626.vault_protocol.nest.offchain_metadata
   eth_defi.erc_4626.vault_protocol.nest.tags
