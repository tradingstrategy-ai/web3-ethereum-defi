Spiko API
=========

Read-only Spiko USTBL tokenised-fund support. The adapter combines the USTBL
share supply with Spiko's verified, Chainlink-compatible Oracle NAV/share
value. Permissioned subscription, transfer and redemption lifecycle operations
are deliberately not exposed as public vault operations.

.. autosummary::
   :toctree: _autosummary_spiko
   :recursive:

   eth_defi.tokenised_fund.spiko.vault
   eth_defi.tokenised_fund.spiko.historical
   eth_defi.tokenised_fund.spiko.constants
