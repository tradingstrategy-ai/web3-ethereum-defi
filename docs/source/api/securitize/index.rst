Securitize API
==============

Securitize DS Protocol tokenised-security support. The adapter implements
:class:`eth_defi.vault.base.VaultBase` for ERC-20-compatible DSTokens rather
than ERC-4626 vaults. Product-specific metadata and manual vault notes are
maintained separately from the shared DSToken adapter.

.. autosummary::
   :toctree: _autosummary_securitize
   :recursive:

   eth_defi.securitize.vault
   eth_defi.securitize.historical
   eth_defi.securitize.description
