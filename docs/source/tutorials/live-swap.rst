Uniswap v2 reading real-time swaps and new pairs
------------------------------------------------

This is an example code for showing live swaps happening
on Uniswap v2 compatible examples. In this example
we use QuickSwap (Polygon) because Polygon provides
good free RPC nodes.

- This example runs on free Polygon JSON-RPC nodes,
  you do not need any self-hosted or commercial node service providers.

- This is an modified example of `read-uniswap-v2-pairs-and-swaps.py` to gracefully handle  chain reorganisations, thus the code is suitable for live event reading. It should also support low quality JSON-RPC nodes that may give different replies between API requests.

- It will print out live trade events for Uniswap v2 compatible exchange.

- This will also show how to track block headers on disk,
  so that next start up is faster.

- This is a dummy example just showing how to build the live loop,
  because how stores are constructed it is not good for processing
  actual data.

- Because pair and token details are dynamically fetched
  when a swap for a pair is encountered for the first time,
  the startup is a bit slow as the pair details cache
  is warming up.


To run for Polygon (and QuickSwap):

.. code-block:: shell

    # Need for nice output
    pip install coloredlogs

    # Switch between INFO and DEBUG
    export LOG_LEVEL=INFO
    # Your Ethereum node RPC
    export JSON_RPC_URL="https://polygon-rpc.com"
    python scripts/read-uniswap-v2-pairs-and-swaps-live.py

.. literalinclude:: ../../../scripts/uniswap-v2-swaps-live.py
   :language: python
