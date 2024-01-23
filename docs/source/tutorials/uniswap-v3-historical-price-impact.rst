.. meta::
   :description: Python example to calculate slippage and price impact

.. _slippage and price impact:

Uniswap v3 historical price estimation
--------------------------------------

Below is an example Python script that analyses the historical Uniswap v3 trade.
It does some calculations around `slippage <https://tradingstrategy.ai/glossary/slippage>`__ and `price impact <https://tradingstrategy.ai/glossary/price-impact>`__
analysis.

- We know the block number when the trade decision was made
  (time and block number at the time when the price impact was estimated)

- We know the block number when the trade was actually executed

- We use a Polygon JSON-RPC archive node to check Uniswap WMATIC-USDC
  pool state at both blocks and compare the results

- Slippage is assumed execution price vs. realised execution price

- We also double check some other numbers like `TVL <https://tradingstrategy.ai/glossary/total-value-locked>`__
  and `mid price <https://tradingstrategy.ai/glossary/mid-price>`__ of the underlying Uniswap v3 pool

- See :py:mod:`eth_defi.uniswap_v3.price` module for more information

.. literalinclude:: ../../../scripts/slippage-and-price-impact.py
   :language: python
