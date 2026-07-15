Vault Street API
================

Vault Street primeUSD support.

The permissioned primeUSD token is read through
:class:`eth_defi.vault.base.VaultBase`, not ERC-4626. The adapter calculates
USDC NAV as ERC-20 ``totalSupply()`` multiplied by the Vault Street
``PriceStorage.getPrice()`` oracle value.

.. autosummary::
   :toctree: _autosummary_vault_street
   :recursive:

   eth_defi.vault_street.constants
   eth_defi.vault_street.vault
   eth_defi.vault_street.historical
