.. meta::
   :description: Tutorial for Lagoon vaults and Velora trade automation

.. _lagoon-velora:

Lagoon and Velora integration
=============================

Here is a Python example how to automate trades from a Lagoon vault using Velora (formerly ParaSwap).

Overview
--------

- You need 0.005 ETH on Arbitrum to run this manual test script.
- This script deploys a new Lagoon vault with Velora integration enabled.
- The deployed vault has `TradingStrategyModuleV0 <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/safe-integration>`__
  configured for allowing automated whitelisted trades by an asset manager.
- Unlike CowSwap which uses an offchain order book and presigning, Velora executes swaps atomically
  in a single transaction.

Velora vs CowSwap
-----------------

Key differences:

- **Atomic execution**: Velora swaps execute in a single transaction (no offchain order book)
- **Simpler flow**: No presigning or order polling required - just approve, quote, and swap
- **Market API**: Uses the Market API (not Delta API) for Safe multisig compatibility

Swap flow
---------

1. Fetch quote from Velora API (GET /prices)
2. Build swap transaction from Velora API (POST /transactions/:network)
3. Approve TokenTransferProxy via vault's performCall()
4. Execute swap via swapAndValidateVelora() on TradingStrategyModuleV0

Running the example
-------------------

.. code-block:: shell

    # Your Arbitrum node
    export JSON_RPC_ARBITRUM=...
    # Private key with ETH loaded in
    # See https://ethereum.stackexchange.com/a/125699/620
    export PRIVATE_KEY_SWAP_TEST=...
    # We need Etherscan API to verify the contracts on Etherscan
    export ETHERSCAN_API_KEY=...

    # Run the script
    python scripts/lagoon/lagoon-velora-example.py

Example code
------------

.. literalinclude:: ../../../scripts/lagoon/lagoon-velora-example.py
   :language: python
