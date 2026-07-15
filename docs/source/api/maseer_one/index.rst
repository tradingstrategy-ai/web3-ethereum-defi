Maseer One API
==============

Maseer One tokenised asset support.

Maseer One is read through :class:`eth_defi.vault.base.VaultBase`, not through
ERC-4626. The adapter combines ERC-20 token supply, ``gem()`` denomination
metadata and ``navprice()`` to build historical share-price and TVL rows.

.. autosummary::
   :toctree: _autosummary_maseer_one
   :recursive:

   eth_defi.maseer_one.vault
   eth_defi.maseer_one.historical
   eth_defi.maseer_one.constants
