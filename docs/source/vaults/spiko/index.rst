Spiko
=====

`Spiko <https://www.spiko.io/>`__ issues tokenised shares in regulated
money-market funds. This integration tracks the Ethereum USTBL token, a share
in Spiko's U.S. Treasury-bill money-market fund. USTBL is a permissioned ERC-20
share, not an ERC-4626 vault.

Spiko's verified `Oracle contract source
<https://github.com/spiko-tech/contracts/blob/main/contracts/oracle/Oracle.sol>`__
implements the Chainlink ``AggregatorV3Interface`` and publishes NAV/share.
The adapter reads that NAV together with the USTBL ERC-20 supply to derive an
estimated total fund NAV. The historical reader repeats the same two reads at
each sampled block, beginning at the official Oracle deployment.

Eligibility and dealing
-----------------------

USTBL is not a generic public-deposit product. Spiko's `smart-contract
documentation <https://tech.spiko.io/posts/spiko-smart-contracts/>`__ describes
permissioned token transfers, issuer-operated minting and a daily redemption
workflow. The adapter therefore intentionally exposes no public deposit,
redemption or generic flow manager; it is limited to safe read-only discovery,
NAV/share and historical analytics.

Fees and curator attribution
-----------------------------

Spiko reports a 0.25% annual management fee for its Treasury-bill funds; the
published NAV/share is net of this fee. The USTBL contract is attributed to
Spiko as an address-scoped, protocol-operated curator because the issuer runs
the token permissioning, NAV publishing and redemption servicing.

.. autosummary::
   :toctree: _autosummary_spiko
   :recursive:

   eth_defi.tokenised_fund.spiko.vault
   eth_defi.tokenised_fund.spiko.historical
   eth_defi.tokenised_fund.spiko.constants
