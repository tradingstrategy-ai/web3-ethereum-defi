JSON-RPC provider API
---------------------

This submodule offers functionality to connect to and improve the resilience of EVM JSON-RPC API providers.

- See :ref:`multi rpc` for a tutorial

- Support for test and mainnet fork backends like :py:mod:`eth_defi.provider.anvil` and :py:mod:`eth_defi.provider.ganache`

- `Malicious Extractable Value (MEV) <https://tradingstrategy.ai/glossary/mev>`__ mitigations
  in :py:mod:`eth_defi.provider.mev_blocker`

- Using multiple JSON-RPC providers and fallback providers in :py:mod:`eth_defi.provider.fallback`

- Classifying why an upstream call failed (out of credits, rate limited, timeout)
  in :py:mod:`eth_defi.provider.rpc_failure`

.. autosummary::
   :toctree: _autosummary_provider
   :recursive:

   eth_defi.provider.multi_provider
   eth_defi.provider.mev_blocker
   eth_defi.provider.fallback
   eth_defi.provider.receipt
   eth_defi.provider.broken_provider
   eth_defi.provider.ankr
   eth_defi.provider.llamanodes
   eth_defi.provider.anvil
   eth_defi.provider.ganache
   eth_defi.provider.named
   eth_defi.provider.env
   eth_defi.provider.log_block_range
   eth_defi.provider.quicknode
   eth_defi.provider.rpc_proxy
   eth_defi.provider.rpc_monitoring_adapter
   eth_defi.provider.rpc_failure
   eth_defi.provider.rpcdb
   eth_defi.provider.tenderly

Selective retries for optional contract calls
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An optional Solidity method probe can use
:py:meth:`eth_defi.event_reader.multicall_batcher.EncodedCall.call` with
``ignore_error=True``. This does not swallow its exception: it disables retries
for expected contract reverts. Pass
``retry_exceptions={requests.exceptions.ReadTimeout}`` to retry only a
transient request timeout and let the fallback provider switch endpoint. The
call's ``attempts`` and ``retry_sleep`` bound these selected fallback retries.
