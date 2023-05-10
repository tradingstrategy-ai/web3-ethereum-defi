Uniswap v2 reading swaps real-time (minimal)
--------------------------------------------

This is a minimal example code for showing live swaps happening
on Uniswap v2 compatible examples.

- This example runs on a free Polygon JSON-RPC node.

- It will print out live trade events for *all* Uniswap v2 compatible exchanges.
  This includes QuickSwap, Sushi and couple of others.
  `See the full DEX list here <https://tradingstrategy.ai/trading-view/polygon>`__.

- It demonstrates the chain reorganisation detection and event reader API.

- See :doc:`the more complete example <./live-swap>`

To run:

.. code-block:: shell

    python scripts/uniswap-v2-swaps-live-minimal.py

Example output:

.. code-block:: text

    Latest block is 42,548,252
    Latest block is 42,548,253
    Swap at block 42,548,254 tx: 0x91716939df48be38beba8148970d20c65242a76b708285fd93e809f6b92b9b9f
    Latest block is 42,548,254
    Swap at block 42,548,255 tx: 0x8601aa1472fe5b0f67edb71b63afab584b35c762610a53b048527c8c043d6f80
    Swap at block 42,548,255 tx: 0x447fe7d38ad5cfddb635aa8e8d7f00dc2c820f59ab0c99d19e73006164ed4272
    Swap at block 42,548,259 tx: 0xf243a4526e07a91362ca2f893bd031bb1d9fa4e5ec5f5737e7358f21a512089d
    Swap at block 42,548,259 tx: 0x256bbe281d5760564255135cb6cb69fb42ca90256ba039bbb6491685c28d2ea5
    Latest block is 42,548,259
    Swap at block 42,548,260 tx: 0xe092085d06aa6bfa282509a571e69118e6d13d69ec49b21267b815709cf177c6
    Latest block is 42,548,260

.. literalinclude:: ../../../scripts/uniswap-v2-swaps-live-minimal.py
   :language: python
