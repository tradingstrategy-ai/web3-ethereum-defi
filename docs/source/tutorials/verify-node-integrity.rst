JSON-RPC node integrity verifier for EVM blockchains
----------------------------------------------------

This is an example script how to verify the integrity of your JSON-RPC full node.

The script will check for the full block range 1 - current block that your node will reply with proper

- Block data

- Transaction

- Smart contract code

- Transaction receipts

- Logs (Solidity events)

Prerequisites
~~~~~~~~~~~~~

To use the script first

- Understand basics of Python programming

- Install `web3-ethereum-defi <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`__ package

Usage
~~~~~

The script  fetches of random blocks and recipies to see the node contains all the data,
up to the latest block. Uses parallel workers to speed up the checks.

The script contains heurestics whether or not block comes from a "good" full node
with all transaction receipts intact, not pruned. There are also other various failure
modes like RPC nodes just failing to return core data (e.g. polygon-rpc.com).

Here are some usage examples for UNIX shell.

First set up your JSON-RPC connection URL (with any secret tokens:

.. code-block:: shell

    export JSON_RPC_URL=https://polygon-rpc.com/

Run a check for 100 randomly selected blocks:

.. code-block:: shell

    CHECK_COUNT=100 python scripts/verify-node-integrity.py

Run a check for 100 randomly selected blocks from the last 10,000 blocks of the chain:

.. code-block:: shell

    START_BLOCK=-10000 CHECK_COUNT=100 python scripts/verify-node-integrity.py

Run in a single-thread example, good for debugging):

.. code-block:: shell

    MAX_WORKERS=1 python scripts/verify-node-integrity.py

The script will go through all randomly blocks in the order print its progress int the console:

.. code-block:: text

    Block 26,613,761 ok - has logs
    Block 26,525,210 ok - has logs
    Block 26,618,551 ok - has logs
    Block 26,629,338 ok - has logs

In the end the script prints out all failed :

.. code-block:: text

    Finished, found 0 uncertain/failed blocks out of 1,000 with the failure rate of 0.0%

In the case of errors you will see:

.. code-block:: text

    Finished, found 52 uncertain/failed blocks out of 100 with the failure rate of 52.0%
    Double check uncertain blocks manually and with a block explorer:
        Block 10,472,300 - could not fetch transaction data for transaction 0xdd090fdde0f32d5c1beb27bcbf08220e3976c59c7f9bceb586b4841d9a4acd0e
        Block 10,857,915 - could not fetch transaction receipt for transaction 0xf77fc0d5053738b688022e8ab3c7cc4335f0467e22042f3cc0ec85a10a0e42a3
        Block 11,984,710 - could not fetch transaction data for transaction 0x

The verifier script source code (also on `Github <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/scripts>`__):

.. literalinclude:: ../../../scripts/verify-node-integrity.py
   :language: python
