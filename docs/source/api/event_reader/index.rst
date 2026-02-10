Solidity event reader
---------------------

Solidity event reader is a high-performance, parallerised, optimised, event reader for EVM blockchains.

It works with GoEthereum, Erigon, free and commercial Ethereum JSON-RPC service providers.

The event reader supports chain reorganisation detection and can handle blockchain rewinds
where the blocks at the chain tip change.

Multiple options are offered to save the block data (headers, events), so that
any historical event scan that takes long time be interrupted and successfully
resumed from the data on disk.

.. autosummary::
   :toctree: _autosummary_event_reader
   :recursive:

   eth_defi.event_reader.multithread
   eth_defi.event_reader.multicall_batcher
   eth_defi.event_reader.reader
   eth_defi.event_reader.logresult
   eth_defi.event_reader.filter
   eth_defi.event_reader.progress_update
   eth_defi.event_reader.conversion
   eth_defi.event_reader.fast_json_rpc
   eth_defi.event_reader.block_header
   eth_defi.event_reader.block_time
   eth_defi.event_reader.multicall_timestamp
   eth_defi.event_reader.block_data_store
   eth_defi.event_reader.reorganisation_monitor
   eth_defi.event_reader.parquet_block_data_store
   eth_defi.event_reader.csv_block_data_store
   eth_defi.event_reader.web3factory
   eth_defi.event_reader.web3worker
   eth_defi.event_reader.state
   eth_defi.event_reader.json_state
   eth_defi.event_reader.lazy_timestamp_reader
   eth_defi.event_reader.timestamp_cache
