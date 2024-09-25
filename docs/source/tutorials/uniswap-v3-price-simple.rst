.. meta::
   :description: Read live price of Uniswap v3 pool
   :title: Uniswap v3 price read using Python

Uniswap v3 price (minimal)
--------------------------

This is a minimal example code for reading the live price of a single Uniswap v3 pool.

- This example runs on a free Polygon JSON-RPC node.

- It will print out live price for a chosen pool

- It will use a polling approach

To run:

.. code-block:: shell

    python scripts/uniswap-v3-price.py

Example output:

.. code-block:: text

    --------------------------------------------------------------------------------
    Uniswap pool details
    Chain 137
    Pool 0x45dda9cb7c25131df268515131f647d726f50608
    Token0 USDC
    Token1 WETH
    Fee 0.0005
    --------------------------------------------------------------------------------

    [2024-09-25T15:24:14.419151, block 62,262,906] Price USDC / WETH: 2626.504043965991386673686020
    [2024-09-25T15:24:20.332237, block 62,262,909] Price USDC / WETH: 2626.504043965991386673686020

.. literalinclude:: ../../../scripts/uniswap-v3-price.py
   :language: python
