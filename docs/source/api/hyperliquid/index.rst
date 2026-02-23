Hyperliquid API
---------------

Hyperliquid decentralised perpetuals exchange integration.

This module provides tools for interacting with Hyperliquid, including:

- Vault data extraction and analysis
- Position history reconstruction from trade fills
- DataFrame-based position analytics
- HyperEVM smart contract deployment with dual-block architecture
- CoreWriter actions for Hypercore vault deposits and withdrawals

Tutorials
~~~~~~~~~

- :ref:`lagoon-hyperliquid` - Deploying a Lagoon vault on HyperEVM with Hypercore deposits

.. autosummary::
   :toctree: _autosummary_hyperliquid
   :recursive:

   eth_defi.hyperliquid.vault
   eth_defi.hyperliquid.position
   eth_defi.hyperliquid.position_analysis
   eth_defi.hyperliquid.session
   eth_defi.hyperliquid.combined_analysis
   eth_defi.hyperliquid.deposit
   eth_defi.hyperliquid.vault_scanner
   eth_defi.hyperliquid.constants
   eth_defi.hyperliquid.daily_metrics
   eth_defi.hyperliquid.vault_data_export
   eth_defi.hyperliquid.api
   eth_defi.hyperliquid.core_writer
   eth_defi.hyperliquid.evm_escrow
   eth_defi.hyperliquid.block
