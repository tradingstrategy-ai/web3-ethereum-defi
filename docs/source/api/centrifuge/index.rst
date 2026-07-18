Centrifuge API
==============

Read-only support for Centrifuge LiquidityPool vaults and reviewed direct
permissioned Tranche share tokens. The Tranche adapter intentionally does not
expose deposits, redemptions, NAV or TVL without a separately integrated pool
valuation and dealing route.

.. autosummary::
   :toctree: _autosummary_centrifuge
   :recursive:

   eth_defi.tokenised_fund.centrifuge.constants
   eth_defi.tokenised_fund.centrifuge.vault
   eth_defi.tokenised_fund.centrifuge.historical
   eth_defi.tokenised_fund.centrifuge.backfill
