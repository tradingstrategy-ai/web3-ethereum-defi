.. meta::
   :description: Monitor Pancakeswap live trades with Python

PancakeSwap follow live trades programmatically
-----------------------------------------------

This is a minimal example code for showing live swaps happening
on PancakeSwap and all other `Uniswap v2 compatible DEXes <https://tradingstrategy.ai/glossary/fork>`__
on `Binance Smart Chain <https://tradingstrategy.ai/glossary/bnb-chain>`__.

- The example scripts reads the swap stream of PancakeSwap pools

- Displays human readable price of swaps

- **The example event reading method is not robust** as it does not deal with minor blockchain reorganisations.
  See other examples in the Tutorials section how to handle this.

- Uses public BNB Chain JSON-RPC endpoint by default

- The public JSON-RPC endpoint is not very performant,
  so you should get your own JSON-RPC node for production usage

To run:

.. code-block:: shell

    python scripts/pancakeswap-live-swaps-minimal.py

Example output:

.. code-block:: text

    Swap at block:30,239,875 buy price:2.64193807 LEE/USDT in tx 0x0b51a9f0a0f50c493111c29e670a419f400daba03e63b9360c52dcf6a3b16c20
    Swap at block:30,239,875 sell price:236.64745674 WBNB/USDT in tx 0x13b65557c777ec26a52dd2e025ac22f8382c62262af8838dc7ebd13b2711765d
    Swap at block:30,239,875 buy price:0.00000039 JGB/USDT in tx 0x13b65557c777ec26a52dd2e025ac22f8382c62262af8838dc7ebd13b2711765d
    Swap at block:30,239,875 sell price:0.00000043 JGB/USDT in tx 0x8b54bc21666463b533a42264c5b5cf9090d91e9bbdbc9d24b1cc757a128cf67b
    Swap at block:30,239,876 buy price:70.20751754 L3/USDT in tx 0xe9dc7b06729f563de7bc7c4412586c54a3e8f774aace636320bff06583f77b33
    Swap at block:30,239,876 buy price:70.20771432 L3/USDT in tx 0xec507565a1d8e330b717f64b63a1d1f75ca5fed14aeca76cc27981da528d515e

.. literalinclude:: ../../../scripts/pancakeswap-live-swaps-minimal.py
   :language: python
