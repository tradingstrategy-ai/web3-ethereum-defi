Sygnum
======

`Sygnum <https://www.sygnum.com/>`__ provides the Desygnate tokenisation and
settlement platform used for `FILQ <https://www.sygnum.com/filq/>`__, Fidelity
International's USD Digital Liquidity Fund. FILQ-A is an Ethereum,
permissioned ERC-20 share class rather than an ERC-4626 vault.

The adapter supports only the reviewed hardcoded FILQ share classes. It reads
ERC-20 supply, but does not advertise subscriptions, transfers or redemptions:
Sygnum's permission manager requires approved wallets and issuer-controlled
settlement. The historical reader emits supply-only rows. Although the token
publishes class-specific price-feed metadata, no public, verified generic
historical NAV interface was callable at integration time, so the adapter does
not infer a price or TVL.

.. autosummary::
   :toctree: _autosummary_sygnum
   :recursive:

   eth_defi.tokenised_fund.sygnum.vault
   eth_defi.tokenised_fund.sygnum.historical
   eth_defi.tokenised_fund.sygnum.constants
