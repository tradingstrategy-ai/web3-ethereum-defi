Wildcat API
===========

Read-only support for `Wildcat <https://wildcat.finance/>`__ version 2
lending markets through a dedicated :class:`eth_defi.vault.base.VaultBase`
adapter. Markets are discovered from the ``WildcatArchController`` registry
and priced from their ``scaleFactor``.

.. autosummary::
   :toctree: _autosummary_wildcat
   :recursive:

   eth_defi.erc_4626.vault_protocol.wildcat.constants
   eth_defi.erc_4626.vault_protocol.wildcat.discovery
   eth_defi.erc_4626.vault_protocol.wildcat.state
   eth_defi.erc_4626.vault_protocol.wildcat.vault
   eth_defi.erc_4626.vault_protocol.wildcat.historical
   eth_defi.erc_4626.vault_protocol.wildcat.offchain_metadata
   eth_defi.erc_4626.vault_protocol.wildcat.tags
