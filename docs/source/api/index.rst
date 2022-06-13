API documentation
=================

This is the API documentation for Python `web3-ethereum-defi` package.
See `the project README for an overview <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`_.

Core API
--------

.. autosummary::
   :toctree: _autosummary
   :recursive:

   eth_defi.token
   eth_defi.balances
   eth_defi.abi
   eth_defi.deploy
   eth_defi.event
   eth_defi.gas
   eth_defi.confirmation
   eth_defi.revert_reason
   eth_defi.hotwallet
   eth_defi.ganache
   eth_defi.middleware
   eth_defi.tx
   eth_defi.utils

Uniswap v2 API
--------------

.. autosummary::
   :toctree: _autosummary_uniswap_v2
   :recursive:

   eth_defi.uniswap_v2.deployment
   eth_defi.uniswap_v2.fees
   eth_defi.uniswap_v2.analysis
   eth_defi.uniswap_v2.utils
   eth_defi.uniswap_v2.swap
   eth_defi.uniswap_v2.liquidity

Uniswap v3 API
--------------

.. autosummary::
   :toctree: _autosummary_uniswap_v3
   :recursive:

   eth_defi.uniswap_v3.deployment
   eth_defi.uniswap_v3.constants
   eth_defi.uniswap_v3.utils
   eth_defi.uniswap_v3.liquidity
   eth_defi.uniswap_v3.events
   eth_defi.uniswap_v3.price

Solidity event and log reader
-----------------------------

.. autosummary::
   :toctree: _autosummary_block_reader
   :recursive:

   eth_defi.event_reader.reader
   eth_defi.event_reader.logresult
   eth_defi.event_reader.conversion
   eth_defi.event_reader.fast_json_rpc


Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
