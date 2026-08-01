Tokenised fund price scheduling
===============================

The recurring price-feed registry integrates reviewed tokenised-fund adapters
with the all-chain vault scanner. It selects already registered products by
their persisted protocol feature, writes address-scoped daily samples and
keeps each protocol's scheduler result separate. Every product has an
independent continuation and rewrite boundary so a missing-history bootstrap
cannot remove another product's rows.

Operator settings and bootstrap behaviour are documented in
``scripts/erc-4626/README-vault-scripts.md``.

.. automodule:: eth_defi.tokenised_fund.scan
   :members:
   :undoc-members:
