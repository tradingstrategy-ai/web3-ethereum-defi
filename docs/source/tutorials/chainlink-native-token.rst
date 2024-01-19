.. meta::
   :description: Chainlink Python tutorial for reading price

Read blockchain native token price using Chainlink
==================================================

Here is an example how to read the blockchain native asset price in USD.

- `JSON-RPC API and node access needed <https://tradingstrategy.ai/glossary/json-rpc>`__

- Supports multiple blockchains including Ethereum, Avalanche, Polygon, BNB Smart Chain

- Latest (current) price and token symbol

- Using Chainlink oracles

- Uses :py:func:`eth_defi.chainlink.token_price.get_native_token_price_with_chainlink`

Sample output:

.. code-block:: text

   The chain native token price of is 2465.16569563 ETH / USD

`For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

.. literalinclude:: ../../../scripts/native-token-price.py
   :language: python
