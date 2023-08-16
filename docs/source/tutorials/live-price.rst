

Uniswap v2 live price with web3.py
==================================

Below is an example script that displays the real-time price of Uniswap v2 compatible trading pair
in a terminal.

- Display latest price

- Time-weighted average price (TWAP)

- Update the price for every new block in BNB Smart chain

- Abort the application with CTRL+C

Uniswap v2 compatible DEXes include

- PancakeSwap

- TradeJoe

The example displays BNB/BUSD price from PancakeSwap.

Sample output:

.. code-block:: text

    Block 19,937,848 at 2022-07-28 06:16:16 current price:269.3162 WBNB/BUSD TWAP:269.3539 WBNB/BUSD
        Oracle data updates: Counter({'created': 6, 'discarded': 1, 'reorgs': 0}), trades in TWAP buffer:144, oldest:2022-07-28 06:11:16, newest:2022-07-28 06:16:13

`For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

.. literalinclude:: ../../../scripts/uniswap-v2-swaps-live.py
   :language: python
