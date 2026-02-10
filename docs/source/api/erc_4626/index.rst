.. _erc-4626:

ERC-4626 API
------------

This module contains ERC-4626 support for Python.

ERC-4626 is a standard to optimize and unify the technical parameters of yield-bearing vaults. It provides a standard API for tokenized yield-bearing vaults that represent shares of a single underlying ERC-20 token. ERC-4626 also outlines an optional extension for tokenized vaults utilizing ERC-20, offering basic functionality for depositing, withdrawing tokens and reading balances.

- Scan chains for all ERC-4626 vaults
- Read historical market data
- Deposit and redeem from vaults
- Specific protocol integrations like :py:mod:`eth_defi.lagoon.vault`, :py:mod:`eth_defi.ipor.vault`, :py:mod:`eth_defi.morpho.vault` subclass these base classes

Tutorials
=========

- :ref:`scan-erc_4626_vaults`

More info
=========

- https://ethereum.org/en/developers/docs/standards/tokens/erc-4626/
- https://docs.openzeppelin.com/contracts/5.x/erc4626

.. autosummary::
   :toctree: _autosummary_erc_4626
   :recursive:

   eth_defi.erc_4626.vault
   eth_defi.erc_4626.deposit_redeem
   eth_defi.erc_4626.flow
   eth_defi.erc_4626.analysis
   eth_defi.erc_4626.hypersync_discovery
   eth_defi.erc_4626.core
   eth_defi.erc_4626.classification
   eth_defi.erc_4626.scan
   eth_defi.erc_4626.estimate
   eth_defi.erc_4626.profit_and_loss
   eth_defi.erc_4626.discovery_base
   eth_defi.erc_4626.lead_scan_core
   eth_defi.erc_4626.rpc_discovery
   eth_defi.erc_4626.warmup

