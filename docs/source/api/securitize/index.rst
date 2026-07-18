Securitize API
==============

Securitize DS Protocol tokenised-security support. The adapter implements
:class:`eth_defi.vault.base.VaultBase` for ERC-20-compatible Securitize tokens
rather than ERC-4626 vaults. Product-specific metadata and manual vault notes
are maintained separately from the shared adapter.

.. autosummary::
   :toctree: _autosummary_securitize
   :recursive:

   eth_defi.tokenised_fund.securitize.vault
   eth_defi.tokenised_fund.securitize.historical
   eth_defi.tokenised_fund.securitize.description
   eth_defi.tokenised_fund.securitize.redstone
   eth_defi.tokenised_fund.securitize.chronicle
   eth_defi.tokenised_fund.securitize.backfill
