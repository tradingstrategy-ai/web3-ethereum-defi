Pancakeswap reading swaps real-time
-----------------------------------

This is a minimal example code for showing live swaps happening
on PancakeSwap and all other `Binance Uniswap v2 compatible DEXes <https://tradingstrategy.ai/glossary/fork>`__.

- Reads the swap stream of PancakeSwap pools

- Displays human readable price of swaps

- *This event reader is not robust* as it does not deal with minor blockchain reorganisations.
  See other examples in the Tutorials section how to handle this.

- Uses public BNB Chain JSON-RPC endpoint by default

To run:

.. code-block:: shell

    python scripts/pancakeswap-live-swaps-minimal.py

Example output:

.. code-block:: text

    Swap block:30,238,558 tx:0x826a7b87f2155f90a2ce41a8c9eebc58532d36e2e707181e6471c0c29573a5ab sell price:0.00099721 Welle/USDT
    Swap block:30,238,558 tx:0x5cf346e19501bcf8aa428409d016390528e840c29a7758a4ba10f795c60bb18a buy price:12.20856495 RWT/USDT
    Swap block:30,238,558 tx:0x54e6edccaf39a753b732e2e9c09fa5220b373c1b5116016f0fb5b2796d1a3af5 sell price:240.90248007 WBNB/USDT
    Swap block:30,238,559 tx:0xc0bb97210da2fda1348f00e9daec9dbdd23c1dac50e6b44296c8fe4810a861fb buy price:0.06722527 TEA/USDT
    Swap block:30,238,559 tx:0x6df61a50ca1073a9698929873718b98cc2330c33b8225ddc5cd6cb66aac7fd63 buy price:0.07435398 TEA/USDT
    Swap block:30,238,559 tx:0x0b2f2bd819bcc19bbdabe7fb799e057c46ec6c50328dcbd7928a503046ef7da2 sell price:0.07993185 TEA/USDT
    Swap block:30,238,559 tx:0x0b2f2bd819bcc19bbdabe7fb799e057c46ec6c50328dcbd7928a503046ef7da2 sell price:0.07805091 TEA/USDT

.. literalinclude:: ../../../scripts/pancakeswap-live-swaps-minimal.py
   :language: python
