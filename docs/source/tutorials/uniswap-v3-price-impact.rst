.. meta::
   :description: Tutorial to estimate Uniswap v3 price impact for a swap
   :title: Uniswap v3 price impact using Python

Uniswap v3 price impact estimation
----------------------------------

This is a minimal example code for estimating Uniswap v3 price impact.

- This example runs on a free Polygon JSON-RPC node.

- It will print out live price for a chosen pool

- It will estimate the price impact for the given pool, for the given swap buy amount

- In this example we are buying WETH with $1,000,000.50 USDC cash in hand

- See :py:mod:`eth_defi.uniswap_v3` for Uniswap v3 API documentation

.. note::

    `Price impact <https://tradingstrategy.ai/glossary/price-impact>`__ and `slippage <https://tradingstrategy.ai/glossary/slippage>`__ are two different things.

To run:

.. code-block:: shell

    python scripts/uniswap-v3-price-impact.py

Example output:

.. code-block:: text

    --------------------------------------------------------------------------------
    Uniswap pool details
    Chain 137
    Pool 0x45dda9cb7c25131df268515131f647d726f50608
    Token0 USDC
    Token1 WETH
    Base token WETH
    Quote token USDC
    Fee (BPS) 5
    --------------------------------------------------------------------------------
    Block: 62,632,744
    Swap size: 1,000,000.50 USDC
    Pool base token TVL: 739.37 WETH
    Pool quote token TVL: 558,088.84 USDC
    Mid price WETH / USDC: 2,423.61
    Quoted amount to received: 354.87 WETH
    Quoted price (no execution slippage): 2,817.91 USDC
    Price impact: 16.27%

.. literalinclude:: ../../../scripts/uniswap-v3-price-impact.py
   :language: python
