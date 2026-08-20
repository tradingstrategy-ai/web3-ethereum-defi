Pallas API
----------

`Pallas <https://app.pallas.fund/>`__ read-only integration for its reviewed
HyperEVM vault deployments. The adapter reads ERC-4626 metadata, assets and
vault-specific management and performance fees. Pallas uses custom asynchronous
request and claim signatures, so no generic deposit manager is exposed.

.. autosummary::
   :toctree: _autosummary_pallas
   :recursive:

   eth_defi.erc_4626.vault_protocol.pallas.constants
   eth_defi.erc_4626.vault_protocol.pallas.vault
