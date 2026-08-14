Arcus API
=========

Arcus pToken vaults are detected on Robinhood Chain through the reviewed
``bridgeVault()`` selector and read with the generic ERC-4626 interface. The
address-scoped overlay supplies reviewed BTC and HOOD product copy. The public
Arcus market catalogue is deliberately not used: exchange-market data is not
pToken accounting data. See :doc:`the Arcus vault documentation
</vaults/arcus/index>` for product and contract references.

.. autosummary::
   :toctree: _autosummary_arcus
   :recursive:

   eth_defi.erc_4626.vault_protocol.arcus.constants
   eth_defi.erc_4626.vault_protocol.arcus.offchain_data
   eth_defi.erc_4626.vault_protocol.arcus.vault
