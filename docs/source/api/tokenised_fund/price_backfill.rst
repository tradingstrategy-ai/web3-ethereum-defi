Recurring price backfill API
============================

The manual price-backfill entrypoint uses the same address-scoped historical
scan as the recurring all-chain scheduler. It is intended for already
registered products and does not change vault metadata or reader state.
Operator settings and targeted repair examples are documented in
``scripts/erc-4626/README-vault-scripts.md``.

.. automodule:: eth_defi.tokenised_fund.price_backfill
   :members:
   :undoc-members:
