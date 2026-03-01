Lighter API
-----------

`Lighter <https://lighter.xyz/>`__ decentralised perpetuals exchange integration.

This module provides tools for extracting Lighter pool data
via public endpoints, mirroring the :doc:`Hyperliquid integration <../hyperliquid/index>`
and :doc:`GRVT integration <../grvt/index>`:

- Pool listing via the public ``/api/v1/publicPoolsMetadata`` endpoint
- Per-pool share price history from ``/api/v1/account``
- DuckDB storage for daily metrics
- Bridge into the ERC-4626 pipeline (VaultDatabase pickle + uncleaned Parquet)

No authentication is required -- all data comes from public endpoints.

For architecture details, API endpoint reference, DuckDB schema, and
fee model documentation, see
`README-lighter-vaults.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/lighter/README-lighter-vaults.md>`__.

.. autosummary::
   :toctree: _autosummary_lighter
   :recursive:

   eth_defi.lighter.vault
   eth_defi.lighter.daily_metrics
   eth_defi.lighter.vault_data_export
   eth_defi.lighter.session
   eth_defi.lighter.constants
