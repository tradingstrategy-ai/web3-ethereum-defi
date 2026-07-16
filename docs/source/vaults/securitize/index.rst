Securitize
==========

`Securitize <https://securitize.io/>`__ is a tokenisation platform for real-world
assets, serving asset managers, Web3 firms and DAOs, advisers and investors. Its
Digital Securities Protocol (DS Protocol) is a permissioned framework for issuing
and administering tokenised securities. The framework's ``DSToken`` is
ERC-20-compatible, while its registry, trust and compliance services enforce
investor eligibility and transfer rules.

This library recognises DSTokens by their ``COMPLIANCE_SERVICE()`` ABI method.
Lead discovery uses the DSToken ``Issue`` event on every supported EVM chain,
then verifies candidates through that probe.
This avoids an Ethereum-specific allow-list and prevents generic ERC-20 transfers
from becoming vault leads. ``Issue`` identifies token issuance, not necessarily
a cash subscription.

The initial product adapter is BlackRock USD Institutional Digital Liquidity Fund
(BUIDL) on Ethereum. BUIDL is not ERC-4626: it is a permissioned DSToken proxy,
and its fund NAV is not exposed through an ERC-4626 conversion method. The adapter
uses ERC-20 ``totalSupply()`` and explicitly labels BUIDL's one-USD share-price
estimate. Public subscriptions and redemptions are intentionally unsupported.

.. autosummary::
   :toctree: _autosummary_securitize
   :recursive:

   eth_defi.securitize.vault
   eth_defi.securitize.historical
