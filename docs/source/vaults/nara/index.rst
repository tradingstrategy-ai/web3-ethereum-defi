Nara
====

`Nara <https://www.nara.io/>`__ provides NaraUSD, a synthetic digital dollar,
and NaraUSD+, its yield-accruing staking product. Nara says its products are
backed by short-term payment-financing assets from cross-border transactions.

NaraUSD+ accepts NaraUSD deposits and mints a proportional share of the product.
The share value changes as the product accrues returns. Redemptions follow a
holder-initiated cooldown and later claim flow; the live Ethereum contract currently
uses a seven-day cooldown. Direct minting and redemption of NaraUSD are restricted to
authorised KYC/KYB-completed users, while NaraUSD may otherwise trade on secondary
markets.

Nara's `audit page <https://docs.nara.io/resources/code-audits>`__ says that public
audit reports are still outstanding. The integration therefore leaves technical risk
and fee mode unclassified. The NaraUSD+ contract is available on
`Etherscan <https://etherscan.io/address/0x1aa23CDFC941f6b54251C72012A9Bfa4bF5394D6>`__.

.. autosummary::
   :toctree: _autosummary_nara
   :recursive:

   eth_defi.erc_4626.vault_protocol.nara.constants
   eth_defi.erc_4626.vault_protocol.nara.vault
   eth_defi.erc_4626.vault_protocol.nara.deposit_redeem
