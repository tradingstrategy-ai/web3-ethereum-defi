Inverse Finance API
-------------------

`Inverse Finance <https://www.inverse.finance/>`__ vault integration.

Inverse Finance is a decentralised lending protocol built around the DOLA stablecoin
and the `FiRM <https://www.inverse.finance/firm>`__ fixed-rate lending market. The protocol
allows users to borrow DOLA at a fixed rate using the DOLA Borrowing Right (DBR) token.

The sDOLA vault is an ERC-4626 compliant savings vault where users stake DOLA and earn yield
derived from FiRM lending revenues. Yield is generated through an automated xy=k auction
mechanism: the vault accumulates DBR rewards from the DolaSavings contract, and anyone can
purchase the accrued DBR for DOLA via the ``buyDBR()`` function. Revenue from these purchases
flows back into the vault, increasing the share price.

Links
~~~~~

- `Homepage <https://www.inverse.finance/>`__
- `App <https://www.inverse.finance/firm>`__
- `Documentation <https://docs.inverse.finance/>`__
- `GitHub <https://github.com/InverseFinance/dola-savings>`__
- `Twitter <https://x.com/InverseFinance>`__
- `DefiLlama <https://defillama.com/protocol/inverse-finance>`__
- `Audits <https://www.inverse.finance/audits>`__
- `Contract <https://etherscan.io/address/0xb45ad160634c528Cc3D2926d9807104FA3157305>`__

.. autosummary::
   :toctree: _autosummary_inverse_finance
   :recursive:

   eth_defi.erc_4626.vault_protocol.inverse_finance.vault
