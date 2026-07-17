Circle USYC
===========

`Circle USYC <https://www.circle.com/usyc>`__ is the on-chain representation
of shares in the Hashnote International Short Duration Yield Fund Ltd. The fund
invests in short-term U.S. government securities and reverse repurchase
agreements. Circle International Bermuda Limited administers the token on the
fund's behalf.

USYC is a permissioned ERC-20 fund token rather than an ERC-4626 vault. The
adapter reads outstanding token supply and the official, Chainlink-compatible
USYC Oracle to calculate NAV and historical TVL. The product's `technical
documentation <https://usyc.docs.hashnote.com/>`__ says the oracle is updated
once per business day after subscriptions, redemptions and accrued interest
are reconciled.

Subscriptions and redemptions use USDC through Circle's entitlement-gated
Teller. Generic transaction support is therefore intentionally unavailable:
access is limited to eligible non-U.S. institutional investors and redemption
availability can depend on the product's instant-redemption capacity.

.. autosummary::
   :toctree: _autosummary_usyc
   :recursive:

   eth_defi.tokenised_fund.usyc.constants
   eth_defi.tokenised_fund.usyc.vault
   eth_defi.tokenised_fund.usyc.historical
