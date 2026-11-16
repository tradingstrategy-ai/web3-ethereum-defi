.. meta::
   :description: Neutral Trade read-only partner API inspection

.. _neutral_trade_api:

Neutral Trade
=============

The Neutral Trade client inspects directory metadata and bounded canonical
share-price and daily-snapshot samples through the partner REST API. It
preserves source amount strings and excludes WSOL before per-vault reads.

A partner API key is required for directory and historical reads. The public
aggregator feed is available without credentials. Persistent collection resumes
canonical pages in a private DuckDB archive and
exports approved, continuous native USDC intervals through the shared pipeline.

See the `Neutral Trade API guide
<https://docs.neutral.trade/for-distribution-partners/api-and-sdk-and-idl>`__
and the `architecture and manual verification README
<https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/neutral_trade/README-delta-neutral.md>`__
for commands, credential handling, sampling limits and accounting caveats.

.. autosummary::
   :toctree: _autosummary
   :recursive:

   eth_defi.neutral_trade.api

Collection and backfill
-----------------------

The same page collector discovers leads, resumes first backfills and refreshes
current observations. A single owner commits each page with its cursor in a
private DuckDB archive; HTTP workers never write to that database. Scheduled
runs yield after their page/time budget and resume on the next cycle.

The native exporter publishes approved Solana USDC intervals only. EVM records
are retained in the private archive, and WSOL is excluded before history reads.
Legacy daily means and unreviewed PPS discontinuities do not become investment
returns. Native fees are externalised, with explicitly all-zero schedules
classified as feeless. Net-return fields estimate published base fees; individual high-water
marks, wallet discounts and historical fee changes are not reconstructed.
Neutral Trade has High technical risk due to centralised counterparty, custody
and asset-freeze exposure despite its transparency dashboards.

.. autosummary::
   :toctree: _autosummary
   :recursive:

   eth_defi.neutral_trade.constants
   eth_defi.neutral_trade.metrics
   eth_defi.neutral_trade.reporting
   eth_defi.neutral_trade.vault_data_export
   eth_defi.neutral_trade.performance
   eth_defi.neutral_trade.vault_metadata
   eth_defi.neutral_trade.tags

Curators and descriptions
-------------------------

The REST directory provides manager wallets, not human-readable curator names
or editorial descriptions. Reviewed address-scoped metadata from official
strategy pages supplements all thirteen native USDC accounts. The common JSON
export includes individual vault summaries and full descriptions, curator
names and profiles, and the strategy-documentation provenance. Published
aliases remain aliases. Unknown accounts retain empty fields; strategy names
and shared manager wallets never establish curator identity.

Migration 068 refreshes cached metadata without changing source histories,
prices, visibility or EVM records. See the architecture README for the guarded
preview/apply commands. Strategy classifications use the same maintained
address mapping during collection and migrations.
