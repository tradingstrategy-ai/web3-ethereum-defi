Nara API
========

NaraUSD+ vault support is provided through the shared ERC-4626 classification API.
The reviewed NaraUSD+ Ethereum deployment is classified by its hardcoded address and
the :py:class:`eth_defi.erc_4626.vault_protocol.nara.vault.NaraVault` adapter exposes
its cooldown-based redemption lifecycle.

See :doc:`the Nara vault documentation </vaults/nara/index>` for product and contract
references.

.. autosummary::
   :toctree: _autosummary_nara
   :recursive:

   eth_defi.erc_4626.vault_protocol.nara.constants
   eth_defi.erc_4626.vault_protocol.nara.vault
   eth_defi.erc_4626.vault_protocol.nara.deposit_redeem
