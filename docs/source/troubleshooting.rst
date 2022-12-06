Troubleshooting
===============

In this document there are troubleshooting
instructions for common errors.

Limits exceeded
---------------

You get the following reply from a BNB Chain node:

.. code-block:: text

    { "jsonrpc": "2.0", "id": 1, "error": { "code": -32005, "message": "limit exceeded" } }

This is `documented in BNB Chain issue tracker <https://github.com/bnb-chain/bsc/issues/1215>`_.

The eth_getLogs api has been turned off in the public RPCs. This api allow users to view events that occurred on the blockchain. That's why you are getting the "limit exceeded" error.

Use private BNB Chain node to run your code.



