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
   to fire hundreds of ``eth_call`` queries to the smart contracts to classify ERC-4626
   vault types and feature based on what Solidity functions they implement.
3. Use :py:class:`eth_defi.erc_4626.vault.Vault` instance to query the remaining data from the vaults,
   like share and denomination token, which in turn query ERC-20 details from the chain using
   :py:func:`eth_defi.token.fetch_erc20_details`.

You must install the package with all extra dependencies (HyperSync) to use this script:

.. code-block:: shell

    pip install web3-ethereum-defi["hypersync"]

Then to run this script:

.. code-block:: shell

    # Get RPC server from
    export JSON_RPC_BASE=...
    python scripts/erc-4626/scan-vaults.py

Output looks like:

.. code-block:: shell

                             Symbol                                 Name          Denomination                         NAV                      Shares       Type   First seen
    Address
    0xF65aC7                poUSDbC  POPT-V1 USDbC LP on WETH/USDbC 5bps                 USDbC                   61.245761                   60.837299  IPORVault  2023-Nov-02
    0x381ef6                   RT-4                        RightsToken-4                  USDC                           0                           0  IPORVault  2023-Nov-10
    0x37f716              BRT2 vAMM                BRT2: vAMM WETH-USDbc       vAMM-WETH/USDbC            3.13166808944E-7            3.12658763624E-7  IPORVault  2023-Nov-21
    0x49AF8C              BRT2 vAMM               BRT2: vAMM WETH-wstETH      vAMM-WETH/wstETH        0.000385266755770396        0.000384772504231782  IPORVault  2023-Nov-21
    0x83B2D9              BRT2 vAMM                BRT2: vAMM AERO-USDbc       vAMM-AERO/USDbC        0.000015669978149107        0.000015619722583097  IPORVault  2023-Nov-21
    0x5A83d5                 poWETH  POPT-V1 WETH LP on BALD/WETH 100bps                  WETH        0.005539972612005919        0.005528567486068551  IPORVault  2023-Nov-17


Further reading

- `For any questions please join to Discord chat <https://tradingstrategy.ai/community>`__.

- See :py:mod:`eth_defi.erc_4626` API documentation

.. literalinclude:: ../../../scripts/erc-4626/scan-vaults.py
   :language: python
