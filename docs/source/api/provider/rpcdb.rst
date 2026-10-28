JSON-RPC request accounting
---------------------------

The :py:mod:`eth_defi.provider.rpcdb` module provides physical JSON-RPC request
counters and append-only `DuckDB persistence
<https://duckdb.org/docs/stable/clients/python/overview>`__.

.. automodule:: eth_defi.provider.rpcdb
   :members:
   :undoc-members:

Scanner operation detail and maintenance
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``vault_rpc_api_calls`` and ``vault_rpc_api_errors`` retain their legacy
positional schemas. ``vault_rpc_operation_calls`` stores disjoint operation
counts and zero-call outcome records. Its requests are already included in the
legacy totals. The reserved reset marker uses chain ``0``, phase
``counter_reset``, method/provider ``none`` and zero calls/items; exclude it
from scan denominators.

:py:class:`~eth_defi.provider.rpcdb.RPCOperationRecorder` binds a fixed operation
label to an existing locked accumulator. Worker-owned providers may attach this
view while sharing counters; recorder bindings and provider sessions must remain
private to each worker. Isolated Multicall operations use an ``_greylist`` suffix,
including failed attempts and provider verification. These operation counts
partition the physical totals and must not be added to them again.

The :py:mod:`eth_defi.provider.rpc_counter_maintenance` utility checkpoints,
verifies and privately backs up all accounting tables before an optional
transactional reset. Stable reset IDs and in-database receipts protect newer
calls during recovery. The caller must hold the scanner pipeline writer lock
and arrange idle accounting writers. Neither historical data nor reader state
is reset. :py:mod:`eth_defi.provider.rpc_counter_comparison` compares explicit
complete UTC date windows in read-only snapshots.

Production commands and the baseline/follow-up procedure are documented in
`README-vault-scripts <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/erc-4626/README-vault-scripts.md>`__.
