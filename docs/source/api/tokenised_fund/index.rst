Tokenised fund backfill API
===========================

The generic dispatcher runs protocol-owned metadata and history backfills.
Select integrations with the comma-separated ``PROTOCOLS`` environment
variable; an empty selection runs every registered tokenised-fund protocol.

.. autosummary::
   :toctree: _autosummary_tokenised_fund
   :recursive:

   eth_defi.tokenised_fund.backfill
