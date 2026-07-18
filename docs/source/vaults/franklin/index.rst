Franklin Templeton
==================

`Franklin Templeton Benji <https://digitalassets.franklintempleton.com/benji/>`__
is the manager's blockchain-integrated recordkeeping and transfer-agent
platform for tokenised fund shares. This integration recognises the official
Ethereum contracts for the Franklin OnChain U.S. Government Money Fund
(``BENJI``) and Franklin OnChain Institutional Liquidity Fund Ltd.
(``iBENJI``), as listed in the issuer's `contract registry
<https://digitalassets.franklintempleton.com/benji/benji-contracts/>`__.

The contracts are permissioned ERC-20 fund-share proxies, not ERC-4626 vaults.
Public subscriptions, redemptions and transfers are deliberately unavailable
through the adapter because they require investor approval and the issuer's
transfer-agent servicing flow. Stellar BENJI and other non-Ethereum Benji
deployments are outside this integration and have separate identifiers.

Historical prices
-----------------

The adapter samples ``totalSupply()`` and the issuer-maintained
``lastKnownPrice()`` at the same Ethereum archive block. ``lastKnownPrice`` is
decoded as base-18 USD per share, and TVL is calculated as share supply times
that reference price. The reference is administrator-maintained, so consumers
must validate current NAV and freshness against the issuer's fund materials
before relying on it for investment or operational decisions.

Fees and limitations
--------------------

The token contracts do not expose a general fund-fee schedule, so the adapter
does not manufacture management, performance, entry or exit fees. Fund terms,
eligibility and liquidity arrangements are product-specific. The integration
is read-only and does not assert that a holder may transfer, subscribe or
redeem shares.

.. autosummary::
   :toctree: _autosummary_franklin
   :recursive:

   eth_defi.tokenised_fund.franklin.constants
   eth_defi.tokenised_fund.franklin.vault
   eth_defi.tokenised_fund.franklin.historical
