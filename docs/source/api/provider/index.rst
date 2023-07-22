JSON-RPC API provider integrations
----------------------------------

This submodule offers functionality to connect and enhance robustness of various EVM JSON-RPC APIs.

- Support for test and mainnet fork backends like :py:mod:`eth_defi.provider.anvil` and :py:mod:`eth_defi.provider.ganache`

- `Malicious Extractable Value (MEV) <https://tradingstrategy.ai/glossary/mev>__` mitigations

- Using multiple JSON-APRC providers and fallback providers

.. autosummary::
   :toctree: _autosummary_provider
   :recursive:

   eth_defi.provider.mev_blocker
   eth_defi.provider.fallback_provider
   eth_defi.provider.anvil
   eth_defi.provider.ganache
   eth_defi.provider.named

