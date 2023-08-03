.. meta::
   :description: Reading historical Uniswap v2 compatible DEX data

Uniswap v2 historical swaps and pairs event reading
---------------------------------------------------

This is an example of how to efficiently read all Uniswap pairs and their swaps in a blockchain,
using :py:mod:`eth_defi.event_reader` module.

Overview:

- Stateful: Can resume operation after CTRL+C or crash

- Outputs two append only CSV files, `/tmp/uni-v2-pairs.csv` and `/tmp/uni-v2-swaps.csv`

- Iterates through all the events using `read_events()` generator

- Events can be pair creation or swap events

- For pair creation events, we perform additional token lookups using Web3 connection

- Demonstrates how to hand tune event decoding

- The first PairCreated event is at Ethereum mainnet block is 10000835

- The first swap event is at Ethereum mainnet block 10_008_566, 0x932cb88306450d481a0e43365a3ed832625b68f036e9887684ef6da594891366

- Uniswap v2 deployment transaction https://bloxy.info/tx/0xc31d7e7e85cab1d38ce1b8ac17e821ccd47dbde00f9d57f2bd8613bff9428396

.. note ::

    This reader uses a single thread. For speedups, see the
    `concurrent reader version of the script on Github <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/uniswap-v2-pairs-and-swaps-concurrent.py>`__.

To run:

.. code-block:: shell

    # Switch between INFO and DEBUG
    export LOG_LEVEL=INFO
    # Your Ethereum node RPC
    export JSON_RPC_URL="https://xxx@mynode.example.com"
    python scripts/read-uniswap-v2-pairs-and-swaps.py


.. literalinclude:: ../../../scripts/uniswap-v2-pairs-and-swaps.py
   :language: python
