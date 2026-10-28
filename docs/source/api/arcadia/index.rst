Arcadia API
-----------

`Arcadia Finance <https://arcadia.finance/>`__ lending tranche metadata.

The adapter reads descriptive fields from the official pool API and generates
short and long lending explanations when the provider supplies only a label.
Generated copy uses known token, chain and tranche identities and remains
available during API outages.

.. autosummary::
   :toctree: _autosummary_arcadia
   :recursive:

   eth_defi.erc_4626.vault_protocol.arcadia.vault
   eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata
   eth_defi.erc_4626.vault_protocol.arcadia.description
