.. meta::
   :description: Chainlink Python tutorial for reading price

.. _chainlink-native-token:

Chainlink price feed reading
============================

Here is an example how to read any Chainlink price feed.

- `JSON-RPC API and node access needed <https://tradingstrategy.ai/glossary/json-rpc>`__

- `Find Chainlink feeds here <https://docs.chain.link/data-feeds/price-feeds/addresses?network=ethereum&page=1>`__

- Uses :py:func:`eth_defi.chainlink.token_price.get_token_price_with_chainlink`

Sample output:

.. code-block:: text

   The token price of is 306.34 BNB / USD

Further reading

- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

- See :py:mod:`eth_defi.chainlink` API documentation

.. literalinclude:: ../../../scripts/chainlink-token-price.py
   :language: python
