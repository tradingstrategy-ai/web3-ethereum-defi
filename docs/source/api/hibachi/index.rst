Hibachi API
-----------

`Hibachi <https://hibachi.xyz/>`__ stablecoin-native FX / crypto perpetuals exchange integration.

This module provides tools for extracting Hibachi vault data
via public endpoints, mirroring the :doc:`GRVT integration <../grvt/index>`:

- Vault metadata via the public `data API <https://data-api.hibachi.xyz/vault/info>`__
- Daily share price and TVL history via the `performance endpoint <https://data-api.hibachi.xyz/vault/performance>`__
- DuckDB storage for daily metrics and point-in-time snapshots
- Bridge into the ERC-4626 pipeline (VaultDatabase pickle + uncleaned Parquet)

No authentication is required -- all data comes from public endpoints.

For architecture details, API endpoint reference, and DuckDB schema, see
`README-hibachi-vaults.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/hibachi/README-hibachi-vaults.md>`__
and `eth_defi/hibachi/README.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/hibachi/README.md>`__.

.. autosummary::
   :toctree: _autosummary_hibachi
   :recursive:

   eth_defi.hibachi.vault
   eth_defi.hibachi.daily_metrics
   eth_defi.hibachi.vault_data_export
   eth_defi.hibachi.session
   eth_defi.hibachi.constants
