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

The adapter includes manual product metadata and notes for reviewed DSToken
funds, including BlackRock BUIDL and BUIDL-I, Apollo ACRED, VanEck VBILL, the
Securitize Tokenized AAA CLO Fund (STAC), Blockchain Capital BCAP, Mantle Index
Four and the `ARK Venture Fund <https://www.ark-funds.com/funds/arkvx>`__
(ARKVX). These products share the contract framework but have distinct
investment strategies and NAV arrangements.

DSTokens are not ERC-4626: their fund NAV is not exposed through an ERC-4626
conversion method. The adapter reads ERC-20 ``totalSupply()`` and prices each
product with one reviewed NAV source:

- **Fixed estimate** – BUIDL and BUIDL-I target a USD 1 share value, so the
  adapter uses an explicitly labelled one-USD estimate.
- **RedStone push feed** – ACRED, VBILL, STAC, HLSCOPE, BCAP and MI4 read a
  reviewed RedStone fundamental-value feed at each archive block.
- **Subscription settlements** – ARKVX is sold through an ERC-7540-style
  ``AsyncFundVault`` with no onchain NAV oracle. Its NAV is rebuilt from the
  vault's ``DepositGenerationFulfilled`` events, removing the 2% subscription
  fee that the settler includes in each settlement price.

Products without a reviewed NAV source are registered as leads without price
history. Public subscriptions and redemptions are intentionally unsupported.

.. autosummary::
   :toctree: _autosummary_securitize
   :recursive:

   eth_defi.tokenised_fund.securitize.vault
   eth_defi.tokenised_fund.securitize.historical
   eth_defi.tokenised_fund.securitize.description
   eth_defi.tokenised_fund.securitize.redstone
   eth_defi.tokenised_fund.securitize.settlement
