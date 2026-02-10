JSON-RPC provider API
---------------------

This submodule offers functionality to connect and enhance robustness of various EVM JSON-RPC API providers..

- See :ref:`multi rpc` for a tutorial

- Support for test and mainnet fork backends like :py:mod:`eth_defi.provider.anvil` and :py:mod:`eth_defi.provider.ganache`

- `Malicious Extractable Value (MEV) <https://tradingstrategy.ai/glossary/mev>`__ mitigations
  in :py:mod:`eth_defi.provider.mev_blocker`

- Using multiple JSON-PRC providers and fallback providers in :py:mod:`eth_defi.provider.fallback`

.. autosummary::
   :toctree: _autosummary_provider
   :recursive:

   eth_defi.provider.multi_provider
   eth_defi.provider.mev_blocker
   eth_defi.provider.fallback
   eth_defi.provider.broken_provider
   eth_defi.provider.ankr
   eth_defi.provider.llamanodes
   eth_defi.provider.anvil
   eth_defi.provider.ganache
   eth_defi.provider.named
   eth_defi.provider.env
   eth_defi.provider.log_block_range
   eth_defi.provider.quicknode
   eth_defi.provider.rpc_monitoring_adapter
   eth_defi.provider.tenderly

