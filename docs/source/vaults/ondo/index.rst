Ondo
====

`Ondo <https://ondo.finance/>`__ issues tokenised investment products for
eligible investors. This integration covers the Ethereum deployments of USDY,
an accumulating U.S. dollar yield token, and OUSG, a tokenised short-term U.S.
government-securities fund share.

Neither token is an ERC-4626 vault. They are permissioned ERC-20 share tokens
with separate issuer-managed subscription and redemption contracts. The adapter
therefore deliberately does not expose a public deposit manager. It calculates
historical USD TVL from ERC-20 supply and the issuer's official 18-decimal
on-chain NAV oracle: USDY's redemption-price oracle and OUSG's unified
``OndoOracle``.

USDY's price accumulates yield, whereas OUSG publishes its fund NAV per share
at the end of business days. Eligibility, KYC/onboarding, transfer controls,
limits and fees remain product-specific. Consult the `official address
registry <https://docs.ondo.finance/addresses>`__, the `USDY documentation
<https://docs.ondo.finance/general-access-products/usdy/basics>`__ and the
`OUSG overview <https://docs.ondo.finance/qualified-access-products/ousg/overview>`__
before interacting with issuer contracts.

.. autosummary::
   :toctree: _autosummary_ondo
   :recursive:

   eth_defi.tokenised_fund.ondo.vault
   eth_defi.tokenised_fund.ondo.historical
   eth_defi.tokenised_fund.ondo.constants
