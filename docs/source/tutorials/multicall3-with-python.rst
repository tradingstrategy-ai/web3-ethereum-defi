.. meta::
   :description: How to use Multicall3 with Python

.. _multicall3-tutorial:

Multicall3: Doing fast chain data reading with Web3.py
======================================================

Here is an example how to read data using Multicall3 with Python and Web3.py.

- Multicall offers a way to speed up reading smart contract data (``eth_call`` RPC) by
  packing multiple calls to one smart contract call.

- This example reads Uniswap v3 pool prices on Base. We could read any data,
  this is just an convenient example.

- The API is designed for high-performance reading, using streaming and Python iterators

- We use :py:mod:`eth_defi.reader.multicall_batcher` module to interact with `Multicall3 <https://www.multicall3.com/>`__
  smart contract.

- We offer two kind of ways to construct the Multicall payload
    - ``contract_instance`` using Web3 `Contract class <https://web3py.readthedocs.io/en/stable/web3.contract.html>`__
    - ``abi_encode`` by manually constructing signatures using `eth_abi package <https://eth-abi.readthedocs.io/en/stable/encoding.html>`__

- For this particular example, we get a high call failure rate because some Uniswap v3 pools do not have liquidity

To run this script:

.. code-block:: shell

    # Your personal RPC node is needed because Multicall3 is too heavy for free nodes
    export JSON_RPC_BASE=<https:// url here>
    python scripts/erc-4626/scan-prices.py

Output looks like (scroll right):

.. code-block:: plain
                      
    2025-04-21 23:05:45 eth_defi.provider.multi_provider             Configuring MultiProviderWeb3. Call providers: ['lb.drpc.org'], transact providers -
    2025-04-21 23:05:46 eth_defi.event_reader.multicall_batcher      About to perform 200 multicalls
    /Users/moo/Library/Caches/pypoetry/virtualenvs/web3-ethereum-defi-YE4GM4ox-py3.11/lib/python3.11/site-packages/joblib/parallel.py:1362: UserWarning: The backend class 'SequentialBackend' does not support timeout. You have set 'timeout=1800' in Parallel but the 'timeout' parameter will not be used.
      warnings.warn(
    2025-04-21 23:05:46 eth_defi.provider.multi_provider             Configuring MultiProviderWeb3. Call providers: ['lb.drpc.org'], transact providers -
    2025-04-21 23:05:47 eth_defi.event_reader.multicall_batcher      Initialising multiprocess multicall handler, process 26410, thread <_MainThread(MainThread, started 8833780800)>, provider fallbacks lb.drpc.org
    2025-04-21 23:05:47 eth_defi.event_reader.multicall_batcher      Performing multicall, 40 calls included, 0 calls excluded, block is 29,238,295, example filtered out block number is -
    2025-04-21 23:05:47 eth_defi.event_reader.multicall_batcher      Multicall result fetch and handling took 0:00:00.371320, output was 8836 bytes
    Pool 1: FloCo/USDC@30 BPS: price 0.6738888777574890389964124380 FloCo / USDC, at block 29238295
    Pool 2: CYBER/USDC@30 BPS: price 1.219927460747768984228220915 CYBER / USDC, at block 29238295
    Pool 3: DAI/USDC@5 BPS: price 1.000142060626520113525564953 DAI / USDC, at block 29238295
    Pool 4: WETH/USDC@1 BPS: price 1574.548671094156898938435818 WETH / USDC, at block 29238295
    Pool 5: axlUSDC/USDC@30 BPS: price 1.004994824276654975226877582 axlUSDC / USDC, at block 29238295
    Pool 6: SOFI/USDC@100 BPS: call failed, debug details:
    Address: 0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a
    Data: 0xcdca1753000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000f4240000000000000000000000000000000000000000000000000000000000000002b833589fcd6edb6e08f4c7c32d4f71b54bda02913002710703d57164ca270b0b330a87fd159cfef1490c0a5000000000000000000000000000000000000000000
    Pool 7: WETH/USDC@100 BPS: price 1596.524319246668635727865053 WETH / USDC, at block 29238295
    Pool 8: WETH/USDC@30 BPS: price 1581.694867268408399870570714 WETH / USDC, at block 29238295

Further API documentation

- :py:mod:`eth_defi.event_reader.multicall_batcher` - Multicall3 batcher
- :py:func:`eth_defi.event_reader.multicall_batcher.read_multicall_chunked` - multiprocess reader
- :py:class:`eth_defi.event_reader.multicall_batcher.EncodedCall` - one packed call
- :py:class:`eth_defi.event_reader.multicall_batcher.EncodedCallResult` - one packed result


.. literalinclude:: ../../../scripts/base/multicall3-example.py
   :language: python
