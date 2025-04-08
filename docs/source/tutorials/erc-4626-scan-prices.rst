.. meta::
   :description: Tutorial to get historical ERC-4626 returns

.. _scan-erc_4626_prices:

ERC-4626: scanning vaults' historical price and performance
===========================================================

Here is an example how to get the ERC-4626 vault historical data.

- Get share price, fees which then can be used to calculate the historical
  performance of the vaults.

- Based on earlier :ref:`scan-erc_4626_vaults`__ tutorial which discovers all vaults for us
  adds the ability to get historical prices and performance of the vaults.

- Supports multiple EVM blockchains, like Ethereum, Base and Arbitrum

- `JSON-RPC API and node access needed <https://tradingstrategy.ai/glossary/json-rpc>`__,
  for an `archive node <https://ethereum.stackexchange.com/a/84200/620>`__.

- See *ERC-4626: examine vault historical performance* tutorial how to read
  the data from the Parquet file.

The script does the price scan in multiple phases:

1. First get the available vaults from the earlier script in `vault-db.pickle` file
2. Prepare vault metadata based on the given chain and Web3 connection
3. Set up multicall batcher to read the vault state in every historical block
4. Read all metrics of all vaults 
5. Update a Parquet file for records for a specific chain

Then to run this script:

.. code-block:: shell

    # Get RPC server from ethereumnodes.com
    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-prices.py

Output looks like (scroll right):

.. code-block:: text
                                                                        Symbol                                               Name          Denomination  ...         Protocol                   Shares   First seen
      Scanning vault historical prices on chain 999: Hyperliquid
      Chain Hyperliquid has 12 vaults in the vault detection database
      After filtering vaults for non-interesting entries, we have 6 vaults left
      Loading token metadata for 6 addresses using 8 workers:   0%|                                                                                                                                                                      | 0/1 [00:00<?, ?it/s]
      Preparing historical multicalls for 6 readers using 12 workers: 100%|████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████| 6/6 [00:02<00:00,  2.92 readers/s]
      Reading historical vault price data for chain 999 with 12 workers, blocks 68,843 - 2,206,919: 3it [00:02,  1.15it/s, Active vaults=2, Last block at=2025-03-11 01:12:36]                                                                                 
      Token cache size is 802,816
      Scan complete
      {'chain_id': 999,
      'chunks_done': 1,
      'existing': True,
      'existing_row_count': 119592,
      'file_size': 1164518,
      'output_fname': PosixPath('/Users/moo/.tradingstrategy/vaults/vault-prices.parquet'),
      'rows_deleted': 0,
      'rows_written': 15}

Further reading

- See :py:ref:`erc-4626` API documentation.
- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

.. literalinclude:: ../../../scripts/erc-4626/scan-prices.py
   :language: python
