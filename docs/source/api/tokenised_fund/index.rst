Tokenised fund backfill API
===========================

The generic dispatcher runs protocol-owned metadata and history backfills.
Select integrations with the comma-separated ``PROTOCOLS`` environment
variable; an empty selection runs every registered tokenised-fund protocol.

.. toctree::
   :maxdepth: 1

   scan
   price_backfill

.. autosummary::
   :toctree: _autosummary_tokenised_fund
   :recursive:

   eth_defi.tokenised_fund.vault
   eth_defi.tokenised_fund.backfill
