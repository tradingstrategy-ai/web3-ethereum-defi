Sygnum API
==========

Read-only support for Sygnum Desygnate FILQ tokenised fund shares. The adapter
tracks the permissioned ERC-20 supply and deliberately reports NAV as
unavailable until a public, verified historical price route is available.

.. autosummary::
   :toctree: _autosummary_sygnum
   :recursive:

   eth_defi.tokenised_fund.sygnum.vault
   eth_defi.tokenised_fund.sygnum.historical
   eth_defi.tokenised_fund.sygnum.constants
   eth_defi.tokenised_fund.sygnum.backfill
