Buoy
----

`Buoy <https://buoy.finance/>`__ offers USDC-denominated trading vaults on
HyperEVM. Each vault is an ERC-20 share token and has a corresponding
HyperCore trading account. Leaders trade spot assets or perpetual markets;
returns depend on the individual strategy.

Read-only integration
~~~~~~~~~~~~~~~~~~~~~

The adapter uses registered factory identities and verified clone
implementations. Economic values, token metadata, fees, supply and admission
rules come from onchain state. Optional descriptions and images come from the
public API through ``offchain_metadata.py`` with expiring disk/process caches.
API failure does not make a registered vault unofficial or replace its prices.

The :doc:`API modules </api/buoy/index>` expose published accounting only.
Although some familiar ERC-4626 views are present, user requests follow a
bespoke Router lifecycle. No deposit/redemption manager or guarded transaction
support is advertised by this integration.

Buoy currently uses a $500 threshold for price cleaning, indexing, reader
traction and rankings. It is a small protocol focused on prop trading, so
these thresholds are kept low for now. NAV freshness checks still apply.

Fee coverage
~~~~~~~~~~~~

The `fee schedule <https://docs.buoy.finance/for-depositors/fees>`__ separates
performance fees from the fixed HYPE charge for submitting a deposit or
withdrawal request. The request charge funds pricing and settlement, is paid
by the transacting wallet in addition to gas, and is not refunded on cancellation.
Its configured amounts are available from Router ``nativeDepositFee()`` and
``nativeWithdrawFee()``; the documentation currently quotes 0.01 HYPE each.

This charge is irrelevant to our read-only indexing and ranking use case:
we submit no deposit or withdrawal requests. It remains a real cost for a
transacting depositor and is not included in published share-price returns.
We document it separately instead of treating it as a percentage charge or
subtracting an assumed transaction cost from vault returns.

Vault JSON includes management, performance, deposit and withdrawal percentage
fees, the fee mode, the internalisation flag and label, and complete
``gross_fees`` / ``net_fees`` objects. Management and percentage entry/exit
fees are zero. Performance fees are read onchain and use
``internalised_minting``: fee shares already dilute the published share price.
For v2/v3, ``gross_fees.protocol`` is the onchain referral share multiplied by
the performance rate, on the same new-profit basis; it is already included
in ``gross_fees.performance``, not an additional charge. Pre-v2 has no split.
``net_fees`` contains zero additional percentage deductions, so performance
fees are not charged twice. Raw protocol configuration read failures remain
unavailable rather than being replaced with zeroes.

Valuation provenance
~~~~~~~~~~~~~~~~~~~~

The protocol fulfiller calculates NAV offchain and publishes it during
settlement or explicit fee crystallisation. There is no periodic NAV heartbeat.
``totalAssets`` already subtracts locked withdrawal obligations. The canonical
``pricePerShare`` view uses an 18-decimal scale; shares and USDC use six decimals.
See the `contract integration reference <https://docs.buoy.finance/developers/smart-contract-integration>`__.

``timestamp`` is the actual chain read time; ``nav_timestamp`` is the original
NAV publication time. Both survive historical export and resampling. A new
poll or forward-filled row does not refresh NAV. Stale published accounting
remains visible in detail exports with its age, while current rankings and
freshness-dependent performance fields exclude it. Unsupported Router flow
accounting remains null, not a claimed zero inflow or outflow.

Historical prices reproduce published accounting, not intraperiod market
values or independently reconstructed HyperCore equity. Flat periods are not
evidence that the trading strategy had no volatility. Administrative upgrades,
pauses and reliance on the fulfiller's NAV are covered in the
`trust disclosures <https://docs.buoy.finance/protocol-deep-dive/security-and-trust>`__.

Validation and migration
~~~~~~~~~~~~~~~~~~~~~~~~

Load the private environment, then run focused checks::

    source .local-test.env
    poetry run pytest tests/erc_4626/vault_protocol/test_buoy.py

Migration ``101-buoy-read-only.py`` freezes 60 reviewed creation boundaries.
It refreshes metadata and backfills only required early historical intervals,
using the existing numbered migration runner and price-backfill helper.
It never resets reader state or imports API prices. Historical ranges end at
an existing first price or the saved HyperEVM scanner resume block; they are
not capped at the earlier research block. Eligibility is checked at each
historical endpoint. Preview uses a temporary descriptive API cache.
Stateless Buoy reads include the final block of a partial hourly interval, so
a first publication near the handoff is retained. Published empty vaults may
retain complete zero-supply accounting without claiming a share price; an
unavailable read or unpriced live supply still fails the backfill check. For a preview, an optional
``MIGRATION_BACKFILL_BLOCKS`` bounds the development scan; apply refuses this
partial-history override.

Run after merge: follow the repository's numbered migration runbook. Inspect
production ``docker-compose.yml`` and stop ``vault-scanner-looped`` before both
preview and apply. Load ``~/vault-scanner/vault-rpc.env``, retain the mounted
persistent data and dense chain timestamp cache, and use::

    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run

The runner must acquire the lifetime guard before the pipeline writer lock.
Review the preview and required price-write postconditions, then perform the
normal scanner export/publication and verify the individual-vault JSON fields.
Neither merging this code nor starting the scanner applies the migration.

Validation snapshot (2026-11-14)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The public API and configured HyperEVM RPC integration tests passed locally.
The final focused regression run passed 276 tests. Claude CLI Opus 5.5 approved
the completed implementation with no blocking findings.
A downloaded production raw Parquet copy (21,797,043 rows) passed the nullable
schema migration check, preserving every original column. This manual check
can be repeated without modifying production::

    source .local-test.env
    BUOY_PRODUCTION_PARQUET=/path/to/downloaded-copy.parquet poetry run pytest tests/erc_4626/vault_protocol/test_buoy.py -k production_copy

An isolated preview using the shared backfill helper, the production Parquet
copy and a copy of the dense timestamp cache scanned Delta Neutral blocks
43,420,087–43,440,087 (exclusive end). It produced two historical rows, including
one priced observation, with no deleted rows and no scheduled reader-state
changes. The full 60-vault production migration has not been applied. Vaults
which have never published a NAV remain catalogue-only and have no price
backfill target until a real investment observation exists.
