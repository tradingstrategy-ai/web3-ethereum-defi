GRVT API
--------

`GRVT <https://grvt.io/>`__ (Gravity Markets) decentralised perpetuals exchange integration.

This module provides tools for extracting GRVT vault/strategy data
via public endpoints, mirroring the :doc:`Hyperliquid integration <../hyperliquid/index>`:

- Vault discovery via the public `GraphQL API <https://edge.grvt.io/query>`__
  (includes per-vault management and performance fees)
- Live vault data (TVL, share price, APR, risk metrics, share price history)
  from the public `market data API <https://market-data.grvt.io>`__
- DuckDB storage for daily metrics and point-in-time snapshots
- Bridge into the ERC-4626 pipeline (VaultDatabase pickle + uncleaned Parquet)

No authentication is required -- all data comes from public endpoints.

For architecture details, API endpoint reference, DuckDB schema, and
fee model documentation, see
`README-grvt-vaults.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/grvt/README-grvt-vaults.md>`__.

.. autosummary::
   :toctree: _autosummary_grvt
   :recursive:

   eth_defi.grvt.vault
   eth_defi.grvt.vault_scanner
   eth_defi.grvt.daily_metrics
   eth_defi.grvt.vault_data_export
   eth_defi.grvt.session
   eth_defi.grvt.constants
