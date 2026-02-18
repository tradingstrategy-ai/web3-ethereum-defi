.. _multithread-reader:

Solidity event high-speed multithread reading
=============================================

Preface
-------

This is an example for high-speed EVM blockchain event reading in Python.
The example shows How to read Solidity events, or use eth_getLogs RPC, to maximize output.

- We use Web3.py `Contract` class and its `Event` subclass

- The example uses a thread pool reader with 16 parallel reading threads (JSON-RPC API requets)

- The example uses optimised `orjson` library for decoding JSON

- We do ABI decoding by hand to optimise speed - to avoid wasting time to decode data
  we do not read, or will discard

- This example needs you to have a JSON-RPC Polygon full node. A free JSON-RPC node endpoint is not sufficient,
  as they do not store history. Try e.g. QuickNode or nodes from `ethereumnodes.com <https://ethereumnodes.com/>`__.

- For the best performance, always run `local nodes at the same server or close your server where you run your application <https://twitter.com/moo9000/status/1370323189486784513>`__

About the code
--------------

- In this example we read `PrimitiveAdded` events from Enzyme Protocol's Polygon deployment.
  We limit the reading to 25,000,000 - 26,000,000 blocks range, so that the example script finished fast (under 1 minute).

- We set up :py:class:`eth_defi.event_reader.multithread.MultithreadEventReader` instance

- We display API call count and rates at the end using :py:func:`eth_defi.chain.install_api_call_counter_middleware`

- We use :py:class:`eth_defi.token.TokenDetails` class for human-friendly ERC-20 output, like name and symbol

Running the code
----------------

The script is shipped with `eth_defi` package. To run in UNIX shell from master checkout :

.. code-block:: shell

    export JSON_RPC_POLYGON_FULL_NODE=https://...

    # Read blocks 25,000,000 - 26,000,000 around when Enzyme was deployment on Polygon
    START_BLOCK=25000000 END_BLOCK=26000000 python scripts/multithread-reader.py


After run you will see output like:

.. code-block:: none

    Scanning blocks 25,999,700 - 25,999,800, done 100.0%
    Scanning blocks 25,999,800 - 25,999,900, done 100.0%
    Scanning blocks 25,999,900 - 26,000,000, done 100.0%
    Scanning blocks 26,000,000 - 26,000,100, done 100.0%
    INFO:futureproof.executors:21 task(s) completed in the last 2.01 seconds
    INFO:futureproof.executors:Shutting down monitor...
    Found 64
       Token RAI: Chainlink aggregator is set to 0x7f45273fD7C644714825345670414Ea649b50b16
       Token amGHST: Chainlink aggregator is set to 0xe638249AF9642CdA55A92245525268482eE4C67b
       Token SUSHI: Chainlink aggregator is set to 0x17414Eb5159A082e8d41D243C1601c2944401431
    We did 10,001 JSON-RPC API requests, avg 179.68 requests/second, as the run took 0:00:55.658691

Example code
------------

.. literalinclude:: ../../../scripts/multithread-reader.py
   :language: python
