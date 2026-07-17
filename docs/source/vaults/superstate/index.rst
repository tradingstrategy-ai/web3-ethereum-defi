Superstate
==========

`Superstate <https://superstate.com/>`__ issues tokenised fund products and
provides the on-chain infrastructure used to administer and settle them. The
first tracked product is USTB, tokenised shares in the Invesco Short Duration
US Government Securities Fund.

USTB is not an ERC-4626 vault. It is a six-decimal, permissioned ERC-20 fund
share. The adapter reads token supply and Superstate's documented continuous
NAV/share endpoint at historical archive blocks. The resulting value is an
issuer-published NAV estimate, not an exchange price or a guarantee that USDC
redemption liquidity is currently available.

Public transaction flows are intentionally unavailable. Superstate documents
eligibility checks for holders and issuer-managed subscription, transfer and
redemption processes; this integration does not certify these operations until
an end-to-end lifecycle has been tested against the relevant systems.

Links
-----

- `USTB fund documentation <https://docs.superstate.com/superstate-funds/ustb>`__
- `Superstate smart-contract registry <https://docs.superstate.com/welcome-to-superstate/smart-contracts>`__
- `USTB redemption documentation <https://docs.superstate.com/ustb/redeeming-ustb>`__
- :doc:`Superstate API documentation </api/superstate/index>`

API
---

.. autosummary::
   :toctree: _autosummary_superstate
   :recursive:

   eth_defi.tokenised_fund.superstate.constants
   eth_defi.tokenised_fund.superstate.vault
   eth_defi.tokenised_fund.superstate.historical
