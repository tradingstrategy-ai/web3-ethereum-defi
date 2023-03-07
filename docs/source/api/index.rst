.. meta::
   :description: Uniswap and Aave Python APIs

API documentation
=================

This is the API documentation for Python `web3-ethereum-defi` package.
See `the project README for an overview <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`_.


Core API
--------

.. autosummary::
   :toctree: _autosummary
   :recursive:

   eth_defi.chain
   eth_defi.token
   eth_defi.balances
   eth_defi.abi
   eth_defi.deploy
   eth_defi.event
   eth_defi.gas
   eth_defi.confirmation
   eth_defi.revert_reason
   eth_defi.hotwallet
   eth_defi.anvil
   eth_defi.ganache
   eth_defi.middleware
   eth_defi.tx
   eth_defi.trace
   eth_defi.utils

Uniswap v2 API
--------------

.. autosummary::
   :toctree: _autosummary_uniswap_v2
   :recursive:

   eth_defi.uniswap_v2.deployment
   eth_defi.uniswap_v2.pair
   eth_defi.uniswap_v2.fees
   eth_defi.uniswap_v2.analysis
   eth_defi.uniswap_v2.utils
   eth_defi.uniswap_v2.swap
   eth_defi.uniswap_v2.liquidity
   eth_defi.uniswap_v2.oracle
   eth_defi.uniswap_v2.token_tax

Uniswap v3 API
--------------

.. autosummary::
   :toctree: _autosummary_uniswap_v3
   :recursive:

   eth_defi.uniswap_v3.deployment
   eth_defi.uniswap_v3.constants
   eth_defi.uniswap_v3.utils
   eth_defi.uniswap_v3.liquidity
   eth_defi.uniswap_v3.analysis
   eth_defi.uniswap_v3.events
   eth_defi.uniswap_v3.price
   eth_defi.uniswap_v3.pool
   eth_defi.uniswap_v3.swap
   eth_defi.uniswap_v2.oracle


Aave v3 API
-----------

.. autosummary::
   :toctree: _autosummary_aave_v3
   :recursive:

   eth_defi.aave_v3.balances
   eth_defi.aave_v3.constants
   eth_defi.aave_v3.events
   eth_defi.aave_v3.rates


Price oracle
------------

.. autosummary::
   :toctree: _autosummary_price_oracle
   :recursive:

   eth_defi.price_oracle.oracle

Data research and science
-------------------------

.. autosummary::
   :toctree: _autosummary_research
   :recursive:

   eth_defi.research.candle


Solidity event and log reader
-----------------------------

.. autosummary::
   :toctree: _autosummary_block_reader
   :recursive:

   eth_defi.event_reader.reader
   eth_defi.event_reader.logresult
   eth_defi.event_reader.conversion
   eth_defi.event_reader.fast_json_rpc
   eth_defi.event_reader.block_header
   eth_defi.event_reader.block_time
   eth_defi.event_reader.block_data_store
   eth_defi.event_reader.reorganisation_monitor
   eth_defi.event_reader.parquet_block_data_store
   eth_defi.event_reader.csv_block_data_store
   eth_defi.event_reader.json_state
   eth_defi.event_reader.web3factory
   eth_defi.event_reader.web3worker
   eth_defi.event_reader.state

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
