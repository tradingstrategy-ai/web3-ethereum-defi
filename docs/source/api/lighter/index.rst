Lighter API
-----------

`Lighter <https://lighter.xyz/>`__ decentralised perpetuals exchange integration.

This package contains two Lighter integration surfaces.

The public vault-data surface extracts Lighter pool data via public endpoints,
mirroring the :doc:`Hyperliquid integration <../hyperliquid/index>` and
:doc:`GRVT integration <../grvt/index>`:

- Pool listing via the public ``/api/v1/publicPoolsMetadata`` endpoint
- Per-pool share price history from ``/api/v1/account``
- DuckDB storage for daily metrics
- Bridge into the ERC-4626 pipeline (VaultDatabase pickle + uncleaned Parquet)

No authentication is required for this vault-data path -- all data comes from
public endpoints.

The guarded Lagoon/Safe surface supports the manual mainnet custody lifecycle:

- Guard whitelisting for Lighter ``deposit`` / ``withdraw`` /
  ``withdrawPendingBalance`` L1 calls
- Safe-owned ``changePubKey`` API-key registration
- Lagoon-vault helpers for USDC deposits, secure Lighter withdrawals and
  pending-balance claims
- Account valuation via public Lighter account NAV fields
- Small manual ETH perpetual round trips through the optional Lighter SDK

Tutorials
~~~~~~~~~

- :doc:`Lighter: benchmark pools </tutorials/lighter-vault-benchmark>` - Benchmark Lighter pool performance, equity curves, and rolling returns

For architecture details, API endpoint reference, DuckDB schema, and
fee model documentation, see
`README-lighter-vaults.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/lighter/README-lighter-vaults.md>`__.

.. autosummary::
   :toctree: _autosummary_lighter
   :recursive:

   eth_defi.lighter.vault
   eth_defi.lighter.valuation
   eth_defi.lighter.daily_metrics
   eth_defi.lighter.vault_data_export
   eth_defi.lighter.session
   eth_defi.lighter.constants
   eth_defi.lighter.api
   eth_defi.lighter.deployment
   eth_defi.lighter.lagoon
   eth_defi.lighter.pubkey
   eth_defi.lighter.testing

Guard integration
~~~~~~~~~~~~~~~~~~

For depositing into and withdrawing from Lighter through an asset-managed
Gnosis Safe governed by ``GuardV0`` / ``TradingStrategyModuleV0``, see
:py:mod:`eth_defi.lighter.deployment` and the
`README-lighter-guard.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/lighter/README-lighter-guard.md>`__
architecture and security notes.
