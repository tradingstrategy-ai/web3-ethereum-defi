Lagoon Finance API
------------------

`Lagoon Finance <https://lagoon.finance/>`__ vault protocol integration.

Lagoon is a non-custodial asset management protocol built on ERC-4626.
This module provides tools for interacting with Lagoon vaults, including:

- Vault deployment and configuration
- Deposits and redemptions
- CoW Swap integration for vault trading
- Offchain metadata fetching

.. autosummary::
   :toctree: _autosummary_lagoon
   :recursive:

   eth_defi.lagoon.vault
   eth_defi.lagoon.deployment
   eth_defi.lagoon.deposit_redeem
   eth_defi.lagoon.cowswap
   eth_defi.lagoon.config
   eth_defi.lagoon.analysis
   eth_defi.lagoon.beacon_proxy
   eth_defi.lagoon.lagoon_compatibility
   eth_defi.lagoon.testing
