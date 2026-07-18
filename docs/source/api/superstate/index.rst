Superstate API
==============

`Superstate <https://superstate.com/>`__ tokenised-fund integration for
permissioned fund-share tokens. The current USTB adapter is read-only: it
combines ERC-20 outstanding supply with the issuer's documented continuous
NAV/share endpoint. It deliberately does not provide public subscription,
transfer or redemption execution because these actions require Superstate
eligibility and settlement controls.

The historical reader uses archive-block calls to ``totalSupply()`` and the
documented ``getChainlinkPrice()`` endpoint. A token response marked stale or
invalid produces an explicit historical error instead of an estimated price.

.. autosummary::
   :toctree: _autosummary_superstate
   :recursive:

   eth_defi.tokenised_fund.superstate.constants
   eth_defi.tokenised_fund.superstate.vault
   eth_defi.tokenised_fund.superstate.historical
   eth_defi.tokenised_fund.superstate.backfill
