Lagoon Finance API
------------------

`Lagoon Finance <https://lagoon.finance/>`__ vault protocol integration.

Lagoon is a non-custodial asset management protocol built on ERC-4626.
This module provides tools for interacting with Lagoon vaults, including:

- Vault deployment and configuration
- Deposits and redemptions
- CoW Swap integration for vault trading
- GMX perpetuals integration for vault trading
- Lighter account activation and perpetuals integration
- Offchain metadata fetching

Tutorials
~~~~~~~~~

- :ref:`lagoon-cowswap` - Trading via CowSwap from a Lagoon vault
- :ref:`lagoon-gmx` - Trading GMX perpetuals from a Lagoon vault
- :ref:`lagoon-velora` - Velora integration
- :ref:`lagoon-hyperliquid` - Deploying on HyperEVM with Hypercore deposits
- ``scripts/lagoon/lagoon-lighter-example.py`` - Deploying a Lagoon vault with a Lighter API key
- ``scripts/lagoon/lagoon-lighter-trade-example.py`` - Trading an ETH perpetual with the deployed API key

.. autosummary::
   :toctree: _autosummary_lagoon
   :recursive:

   eth_defi.lagoon.vault
   eth_defi.lagoon.deployment
   eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem
   eth_defi.erc_4626.vault_protocol.lagoon.funding
   eth_defi.lagoon.cowswap
   eth_defi.lagoon.config
   eth_defi.lagoon.analysis
   eth_defi.lagoon.beacon_proxy
   eth_defi.lagoon.lagoon_compatibility
   eth_defi.lagoon.testing
