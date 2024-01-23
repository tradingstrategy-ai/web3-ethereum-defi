.. meta::
   :description: Tutorial to get all Enzyme vaults on-chain

.. _enzyme-read-vaults:

Enzyme reading all vaults on a blockchain
=========================================

Here is an example how to read all Enzyme vaults on a particular blockchain
and export the information to a CSV file.

- `JSON-RPC API and node access needed <https://tradingstrategy.ai/glossary/json-rpc>`__,
  based purely on-chain data and no external services are needed

- Supports multiple blockchains including Ethereum and Polygon

- Extract vault data like name, token symbol, TVL

- Gets vault TVL converted to USD

Further reading

- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

- See :py:mod:`eth_defi.enzyme` API documentation

.. literalinclude:: ../../../scripts/enzyme/fetch-all-vaults.py
   :language: python
