How to read Solidity events fast with web3.py
---------------------------------------------

This is an example how to efficiently read all Uniswap pairs and their swaps in a blockchain.

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

To run:

.. code-block:: shell

    # Switch between INFO and DEBUG
    export LOG_LEVEL=INFO
    # Your Ethereum node RPC
    export JSON_RPC_URL="https://xxx@vitalik.tradingstrategy.ai"
    python scripts/read-uniswap-v2-pairs-and-swaps.py


.. literalinclude:: ../../../scripts/uniswap-v2-pairs-and-swaps.py
   :language: python