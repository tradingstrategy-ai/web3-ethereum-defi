.. meta::
   :description: ApeX Omni native vault public API reader

.. _apex_api:

ApeX
====

The ApeX integration reads public native-vault metadata and actual-timestamp
NAV and TVL history into DuckDB. The all-chain scanner can also export the
data into the shared vault metadata and price pipeline, including a direct
ApeX vault link for each exported vault. The shared raw price file stores
timestamps at millisecond precision. Replaying a ranking observation updates
the row for that vault and millisecond. If two source observations for that vault
map to the same stored millisecond, the ApeX merge rejects the ambiguous key.

See the `ApeX public API documentation
<https://api-docs.pro.apex.exchange/>`__ for the platform API.
The package architecture and operating model are described in
`README-apex.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/eth_defi/apex/README-apex.md>`__.

.. autosummary::
   :toctree: _autosummary
   :recursive:

   eth_defi.apex.config
   eth_defi.apex.constants
   eth_defi.apex.session
   eth_defi.apex.vault
   eth_defi.apex.metrics
   eth_defi.apex.vault_data_export
