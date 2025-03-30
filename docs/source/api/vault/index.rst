Vault framework
---------------

A generic high-level Python framework to integrate different vault providers.

- Provide an abstract base class and toolkit to interact with vault providers from Python applications

- The main use case is automated trading with vault-managed capital

- For more details see :py:class:`eth_defi.vault.base.VaultBase`

- See also ERC-4626 specific implementation :py:mod:`eth_defi.vault.erc_4626`.

.. autosummary::
   :toctree: _autosummary_velvet
   :recursive:

   eth_defi.vault.base
   eth_defi.vault.valuation
   eth_defi.vault.historical
   eth_defi.vault.lower_case_dict
   eth_defi.vault.mass_buyer

