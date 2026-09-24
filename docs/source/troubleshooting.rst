Troubleshooting
===============

In this document there are troubleshooting
instructions for common errors.

Sequential transaction broadcasts
---------------------------------

``wait_and_broadcast_multiple_nodes()`` broadcasts transactions with sequential
nonces one at a time. Before broadcasting the following nonce, it polls each
configured read provider with ``eth_getTransactionByHash`` for the preceding
transaction. This avoids a fixed inter-transaction sleep and proceeds as soon
as every provider can return the transaction.

``inter_node_delay`` remains the maximum visibility wait for compatibility. If
it expires, the batch proceeds when at least one read provider can see the
transaction, so one failed or lagging read provider does not block execution.
If no provider responds, the gate logs a warning and normal confirmation and
rebroadcast handling continues. Transaction visibility is not receipt
confirmation; receipt confirmation still happens after the complete batch is
broadcast.

Limits exceeded
---------------

You get the following reply from a BNB Chain node:

.. code-block:: none

    { "jsonrpc": "2.0", "id": 1, "error": { "code": -32005, "message": "limit exceeded" } }

This is `documented in BNB Chain issue tracker <https://github.com/bnb-chain/bsc/issues/1215>`_.

The eth_getLogs api has been turned off in the public BNB Chain RPCs.
Use private BNB Chain node to run your code.

Testing BNB Chain eth_getLogs RPC call manually
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can do the following:

.. code-block:: shell

        export JSON_RPC_BINANCE="https://bsc-mainnet.nodereal.io/v1/64a9df0874fb4a93b9d0a3849de012d3"

        curl \
            --location \
            --header 'Content-Type: application/json' \
            --request POST $JSON_RPC_BINANCE \
            --data-raw '{"jsonrpc": "2.0", "method": "eth_getLogs", "params": [{"topics": [["0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"]], "fromBlock": "0xd59f80", "toBlock": "0xd59fe3", "address": "0x58F876857a02D6762E0101bb5C46A8c1ED44Dc16"}], "id": 10}'

See also `bnb-chain-get-logs-test.py` script.
