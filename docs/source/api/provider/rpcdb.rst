JSON-RPC request accounting
---------------------------

The :py:mod:`eth_defi.provider.rpcdb` module provides reusable physical
JSON-RPC request counters and append-only DuckDB persistence. Its ``phase`` and
``items_scanned`` values are caller-defined, so the same API can account for a
vault scan, block index, transaction batch, or diagnostic job.

For example, a block indexer can use the same fixed `DuckDB database
<https://duckdb.org/docs/stable/clients/python/overview>`__ with its own phase
and item meaning:

.. code-block:: python

   from eth_defi.compat import native_datetime_utc_now
   from eth_defi.provider.rpcdb import (
       RPCRequestStats,
       RPCUsageDatabase,
       resolve_rpc_tracking_database_path,
   )

   stats = RPCRequestStats()
   stats.record_call("rpc.example.com", "eth_getBlockByNumber", count=100)

   with RPCUsageDatabase(resolve_rpc_tracking_database_path()) as database:
       database.record_scan(
           chain=1,
           phase="block_index",
           cycle_started=native_datetime_utc_now().date(),
           cycle_number=database.allocate_cycle(),
           stats=stats,
           items_scanned=100,  # Blocks indexed, not vaults
       )

.. automodule:: eth_defi.provider.rpcdb
   :members:
   :undoc-members:
