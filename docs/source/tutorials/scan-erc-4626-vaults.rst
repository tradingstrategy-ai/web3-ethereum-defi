.. meta::
   :description: Tutorial to find all ERC-4626 vaults onchain

.. _scan-erC_4626_vaults:

ERC-4626 scanning all vaults onchain
====================================

Here is an example how to find all ERC-4626 vaults on a particular blockchain
and export the information to a CSV file.

- Supports multiple EVM blockchains, like Ethereum, Base and Arbitrum

- `JSON-RPC API and node access needed <https://tradingstrategy.ai/glossary/json-rpc>`__,
  for an `archive node <https://ethereum.stackexchange.com/a/84200/620>`__.

- The script uses public `HyperSync server to speed up the scan <https://docs.envio.dev/docs/HyperSync/overview>`__.
  The server access is needed, but it is public.

- Extract vault data like type, name, token symbol, TVL,

- Gets vault TVL converted to USD

- Display results in a terminal

- Save raw data to a Parquet file

The script does the vault discovery in three phases:

1. Use :py:class:`eth_defi.erc_4626.hypersync_discovery.HypersyncVaultDiscover`
   to pull all deposit events that gives us a clue of the existence of ERC-4626 vaults
2. Use :py:func:`eth_defi.event_reader.multicall_batcher.read_multicall_chunked`
   to fire hundreds of ``eth_call`` RPC queries to the smart contracts to classify ERC-4626
   vault types and feature based on what Solidity functions they implement.
3. Use :py:class:`eth_defi.erc_4626.vault.ERC4626Vault` instance to query the remaining data from the vaults,
   like share and denomination token, which in turn query ERC-20 details from the chain using
   :py:func:`eth_defi.token.fetch_erc20_details`.

You must install the package with all extra dependencies (HyperSync) to use this script:

.. code-block:: shell

    pip install web3-ethereum-defi["hypersync"]

Then to run this script:

.. code-block:: shell

    # Get RPC server from ethereumnodes.com
    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-vaults.py

Output looks like (scroll right):

.. code-block:: shell
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



Further reading

- See :py:ref:`erc-4626` API documentation.
- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

.. literalinclude:: ../../../scripts/erc-4626/scan-vaults.py
   :language: python
