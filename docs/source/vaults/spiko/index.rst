Spiko
=====

`Spiko <https://www.spiko.io/>`__ issues tokenised shares in regulated
money-market funds. This integration tracks the Ethereum USTBL token, a USD
share in Spiko's U.S. Treasury-bill money-market fund, and Arbitrum EUTBL, an
EUR share in Spiko's Eurozone Treasury-bill money-market fund. Both are
permissioned ERC-20 shares, not ERC-4626 vaults.

Spiko's verified `Oracle contract source
<https://github.com/spiko-tech/contracts/blob/main/contracts/oracle/Oracle.sol>`__
implements the Chainlink ``AggregatorV3Interface`` and publishes NAV/share.
The adapter reads that NAV together with ERC-20 supply to derive an estimated
total fund NAV. USTBL is already USD-denominated. EUTBL's issuer NAV is in EUR,
so the adapter combines it with Chainlink's Arbitrum EUR/USD feed before
exporting USD-normalised share price and TVL; the source EUR denomination
remains present in metadata and the top-vault export's
``source_denomination`` field. The historical reader repeats these reads at
each sampled block, beginning at the official Oracle deployment.

Eligibility and dealing
-----------------------

Spiko products are not generic public-deposit products. Spiko's `smart-contract
documentation <https://tech.spiko.io/posts/spiko-smart-contracts/>`__ describes
permissioned token transfers, issuer-operated minting and a daily redemption
workflow. The adapter therefore intentionally exposes no public deposit,
redemption or generic flow manager; it is limited to safe read-only discovery,
NAV/share and historical analytics.

Fees and curator attribution
-----------------------------

Spiko reports a 0.25% annual management fee for its Treasury-bill funds; the
published NAV/share is net of this fee. The USTBL and EUTBL contracts are
attributed to Spiko as address-scoped, protocol-operated curator products
because the issuer runs the token permissioning, NAV publishing and redemption
servicing.

.. autosummary::
   :toctree: _autosummary_spiko
   :recursive:

   eth_defi.tokenised_fund.spiko.vault
   eth_defi.tokenised_fund.spiko.historical
   eth_defi.tokenised_fund.spiko.constants
