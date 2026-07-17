wstGBP API
==========

wstGBP tokenised asset support.

wstGBP is read through :class:`eth_defi.vault.base.VaultBase`, not through
ERC-4626. The adapter combines ERC-20 token supply, ``gem()`` denomination
metadata and ``navprice()`` to build historical share-price and TVL rows.

.. autosummary::
   :toctree: _autosummary_wstgbp
   :recursive:

   eth_defi.wstgbp.vault
   eth_defi.wstgbp.historical
   eth_defi.wstgbp.constants
