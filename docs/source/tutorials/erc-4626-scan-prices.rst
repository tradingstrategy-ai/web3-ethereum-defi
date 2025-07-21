.. meta::
   :description: How to scan ERC-4626 vault data on EVM blockchains.

.. _scan-erc_4626_prices:

ERC-4626: scanning vault data
=============================

Here is an example how to read the ERC-4626 vault historical data. We need this data in order to run other notebooks here,
which then analyse the performance of the vaults. The tutorial shows how to create a local
vault databases with list of vaults across all chains. It is based on open source pipeline in ``web3-ethereum-defi`` repository.

You need

- Expert Python knowledge to work with complex Python projects
- JSON-RPC archive nodes for various chains, e.g. from `dRPC <https://drpc.io/>`__
- `Hypersync account <https://docs.envio.dev/docs/HyperSync/hypersync-supported-networks>`__
- UNIX or Windows Subsystem for Linux (WSL) environment
- Preferably ``screen`` or ``tmux`` to or similar utility to run long running processes in the background on servers
- Some hours of patience

This is a three step scripted process:

- For each chain
    - Discover ERC-4626 vaults the chain
    - Scan their historical prices
- And afterwards
    - :ref:`Clean price data for all vaults across all chains <wrangle vault>`

.. note ::

    Pipeline open source code must be updated to accommodate new chains, with chain ids, names and such.

Scanning vaults for a single chain
----------------------------------

Discovering vaults
~~~~~~~~~~~~~~~~~~

To scan a single chain first we need to discover the vaults on the chain. This is done by ``scan-vaults.py`` script.

.. code-block:: shell

    # Point to HTTPS RPC server for your chain
    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-vaults.py

This script will create file ``~/.tradingstrategy/vaults/vault-db.pickle`` with the vaults found on the chain,
plus all other vaults across other chains we have scanned so far.

The console output looks like:

.. code-block:: none
                                                                   Symbol                                               Name          Denomination  ...         Protocol                   Shares   First seen
    Address                                                                                                                                         ...
    0x665a1B7F87a938a8ac560EfDB37Dd7f3567Ec263             f50BALD/50WETH                                 FARM_50BALD/50WETH         50BALD/50WETH  ...  Harvest Finance                    0.489  2023-Aug-04
    0xDfA7578B8187F5DD1D0CB9D86b6dD33625895cf2      fBPT-50STABAL3-50WETH                          FARM_BPT-50STABAL3-50WETH  BPT-50STABAL3-50WETH  ...  Harvest Finance                    0.000  2023-Aug-04
    0x488FE5Dd5f4D7587AD9c65A69457925711bEF773               fBPT-stabal3                                   FARM_BPT-stabal3           BPT-stabal3  ...  Harvest Finance                    1.822  2023-Aug-04
    0x50c6c553d083232F1994175332Ff5508b81Aeb96                  fBSWAP-LP                                      FARM_BSWAP-LP              BSWAP-LP  ...  Harvest Finance                    8.341  2023-Aug-11
    0xAb0a63eE480E02Ad0d9210ffFae0CFC3Ced7c98B                  fBSWAP-LP                                      FARM_BSWAP-LP              BSWAP-LP  ...  Harvest Finance                    0.000  2023-Aug-11
    0x8f61C658c5960962e6D108F0f63133F248F6d721                  fBSWAP-LP                                      FARM_BSWAP-LP              BSWAP-LP  ...  Harvest Finance                    0.000  2023-Aug-11
    0x99f5b2039768100d2Ef2484d52E1ca3889649b4D                  fBSWAP-LP                                      FARM_BSWAP-LP              BSWAP-LP  ...  Harvest Finance                    0.000  2023-Aug-11
    0x284a022dA3c08e54825347e081fD490cF5A15284                      xBASO                                              xBASO                  BASO  ...   <generic 4626>                   95.226  2023-Aug-20
    0xF0FfC7cd3C15EF94C7c5CAE3F39d53206170Fc01                      xBASO                                              xBASO                  BASO  ...   <generic 4626>                  3145766  2023-Aug-20
    0x127dc157aF74858b36bcca07D5A02ef27Cd442d0                  fBSWAP-LP                                      FARM_BSWAP-LP              BSWAP-LP  ...  Harvest Finance                   75.134  2023-Aug-23
    0x4C8d67201DCED0A8E44F59d419Cb74665b4cdE55                fcbETH/WETH                                    FARM_cbETH/WETH            cbETH/WETH  ...  Harvest Finance                    0.000  2023-Aug-23
    0x16Df1C008D1a2aCF511ea7A2e6eF06dC54Cd9f14          fBPT-USDbC-axlUSD                              FARM_BPT-USDbC-axlUSD      BPT-USDbC-axlUSD  ...  Harvest Finance                    1.704  2023-Aug-23


Scanning historical prices
~~~~~~~~~~~~~~~~~~~~~~~~~~~

After discovering the vaults on a chain, we scan their historical performance.
This is done by ``scan-prices.py`` script. It will read the vaults from the database file created by the previous step.
then use JSON-RPC archive nodes polling to extract historical prices and parameters like performance fees.

Scan process is stateful
- It can resume, you can rerun the script and it will rescan from where the scan ended last time
- Using the state, we filter out vaults that are not interesting, e.g. vaults that become
  dead after certain point of time, to keep the amount of JSON-RPC calls lower

The default scan is set to 1h interval.

This will write

- ``~/tradingstrategy/vaults/vault-prices-1h.parquet`` file with the historical prices
- ``~/tradingstrategy/vaults/vault-reader-state-1h.parquet`` to store the latest block scanned for each vault

.. code-block:: shell

    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-prices.py

Output looks like:

.. code-block:: none
                      
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

Cleaning data
~~~~~~~~~~~~~

The raw vault data contains a lot of abnormalities like almost infinite profits,
broken smart contracts, missing names and so on.

- Cleaning only supports stablecoin-nominated vaults, i.e. vaults that have denomination token in stablecoin.
  Cleaning process currently discards the data for other denonimations. If you need to access e.g.
  ETH-nominated vaults, you need to clean the data yourselfs
- Denormalise vault data to a single Parquet/Dataframe that can be handled without ``vault-db.pickle`` file,
  in any programming environment
- We calculate 1h returns for each vault
- We calculate rolling returns and such performance metrics


The script will
- Read ``~/tradingstrategy/vaults/vault-prices-1h.parquet``
- Write ``~/tradingstrategy/cleaned-vaults/vault-prices-1h.parquet``

.. code-block:: shell

   python scripts/erc-4626/clean-prices.py

Scanning all chains
-------------------

There is`scan-vaults-all-chains.sh <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/erc-4626/scan-vaults-all-chains.sh>`__
shell script to scan vaults across multiple chains.

You need to feed it multiple RPC endpoints like:

.. code-block::

    export JSON_RPC_ETHEREUM=...
    export JSON_RPC_BASE=...
    SCAN_PRICES=true scripts/erc-4626/scan-vaults-all-chains.sh

Further reading
~~~~~~~~~~~~~~~

- See :py:ref:`erc-4626` API documentation.
- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

