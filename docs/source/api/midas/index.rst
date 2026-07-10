Midas API
=========

Midas tokenised product support.

Midas products are read through :class:`eth_defi.vault.base.VaultBase`, not
through ERC-4626. The adapter combines ERC-20 mToken supply with Midas
``getDataInBase18()`` NAV/share feeds to build historical share price rows.

.. autosummary::
   :toctree: _autosummary_midas
   :recursive:

   eth_defi.midas.vault
   eth_defi.midas.historical
   eth_defi.midas.constants
   eth_defi.midas.registry
