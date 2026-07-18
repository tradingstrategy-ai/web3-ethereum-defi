WisdomTree
==========

`WisdomTree Connect <https://www.wisdomtreeconnect.com/>`__ provides eligible,
permissioned investors with access to WisdomTree-issued tokenised fund shares.
The initial adapter covers the Ethereum WTGXX token, representing shares in the
WisdomTree Treasury Money Market Digital Fund. The fund seeks current income,
capital preservation and liquidity while maintaining a stable one-dollar NAV.

WTGXX is not an ERC-4626 vault. Its Ethereum token is a compliance-controlled,
revocable ERC-20 record of shares, and WisdomTree's transfer agent remains the
official ownership record. Wallets must be approved before transfers,
subscriptions or redemptions; this integration is deliberately read-only and
does not expose a partial public transaction flow.

Supply is read from the token and NAV/history from WisdomTree's documented
`DataSpan NAV API <https://docs.wisdomtreeconnect.com/dataspan/nav>`__. The API
requires an operator-provided ``WISDOMTREE_DATASPAN_API_KEY``. The WTGXX fund
page publishes the product terms, including the 0.25% expense ratio, supported
token addresses and risk disclosures.

.. autosummary::
   :toctree: _autosummary_wisdomtree
   :recursive:

   eth_defi.tokenised_fund.wisdomtree.vault
   eth_defi.tokenised_fund.wisdomtree.historical
   eth_defi.tokenised_fund.wisdomtree.nav
   eth_defi.tokenised_fund.wisdomtree.constants
