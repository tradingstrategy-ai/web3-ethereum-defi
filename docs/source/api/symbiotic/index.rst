Symbiotic API
=============

The Symbiotic integration identifies Core V2 vaults through their
``withdrawalQueue()`` accessor. :py:class:`eth_defi.erc_4626.vault_protocol.symbiotic.vault.SymbioticVault`
reads vault fee configuration, retrieves curator-submitted metadata, and links
a vault to the official application.

See :doc:`the Symbiotic vault documentation </vaults/symbiotic/index>` for
product information and contract references.

.. autosummary::
   :toctree: _autosummary_symbiotic
   :recursive:

   eth_defi.erc_4626.vault_protocol.symbiotic.vault
   eth_defi.erc_4626.vault_protocol.symbiotic.offchain_metadata
