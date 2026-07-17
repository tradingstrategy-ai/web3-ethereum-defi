WisdomTree API
==============

Read-only support for WisdomTree tokenised fund shares. The adapter represents
permissioned ERC-20 shares through :class:`eth_defi.vault.base.VaultBase` and
uses the issuer's documented NAV API rather than treating the token as an
ERC-4626 vault.

.. autosummary::
   :toctree: _autosummary_wisdomtree
   :recursive:

   eth_defi.tokenised_fund.wisdomtree.vault
   eth_defi.tokenised_fund.wisdomtree.historical
   eth_defi.tokenised_fund.wisdomtree.nav
   eth_defi.tokenised_fund.wisdomtree.constants
