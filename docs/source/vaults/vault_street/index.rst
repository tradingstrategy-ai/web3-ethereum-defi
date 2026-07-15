Vault Street
============

`Vault Street <https://vaultstreet.com/>`__ is an institutional platform from
Resolv. Its first product, `primeUSD
<https://docs.vaultstreet.com/overview/primeusd.md>`__, is a permissioned,
USDC-denominated yield token backed by a leveraged carry strategy using
tokenised investment-grade fixed-income collateral.

primeUSD is not an ERC-4626 vault. Its ERC-20 token contract provides supply,
while a separate Vault Street PriceStorage contract publishes NAV/share through
``getPrice()``. The adapter therefore directly subclasses ``VaultBase`` and
calculates TVL as ``totalSupply * getPrice``.

The adapter is read-only. Vault Street applies KYB/KYC allowlisting to deposits,
transfers and redemptions, so generic transaction support is intentionally not
provided.

.. autosummary::
   :toctree: _autosummary_vault_street
   :recursive:

   eth_defi.vault_street.constants
   eth_defi.vault_street.vault
   eth_defi.vault_street.historical
