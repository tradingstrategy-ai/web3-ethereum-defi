.. meta::
   :description: Tutorial for Lagoon vaults and CowSwap trade automation

.. _lagoon-cowswap:

Lagoon and CowSwap integration
==============================

Here is a Python example how to automated trades from a Lagoon vault using CowSwap.

- You need 0.005 ETH on Arbitrum to run this manual test script.
- This script deploys a new Lagoon vault.
- The deployed vault has `TradingStrategyModuleV0 <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/safe-integration>`__
  configured for allowing automated whitelisted trades by an asset manager. This is a Zodiac module which extends the underlying
  Gnosis Safe functionality used as the core of Lagoon vaults.
- In this example, the deployer account, asset manager and Gnosis co-signers are all the same account for simplicity.
- After deploying the vault, the script deposits assets into the vault.
- The deposit must be settled to the vault per `ERC-7540 deposit and settlement cycle <https://tradingstrategy.ai/glossary/erc-7540>`__.
- When the vault the deposit in sitting in the Safe, we then swap the deposited assets to another token using CowSwap.

Then to run this script:

.. code-block:: shell

    # Your Arbitrum node
    export JSON_RPC_ARBITRUM=...
    # Private key with ETH loaded in
    # See https://ethereum.stackexchange.com/a/125699/620
    export PRIVATE_KEY=...
    # We need EtherScan API to verify the contracts on Etherscan
    export ETHERSCAN_API_KEY=...

    # Run the script
    python scripts/lagoon/lagoon-cowswap-example.py

Output looks like:

.. code-block:: none

    TODO

.. literalinclude:: ../../../scripts/lagoon/lagoon-cowswap-example.py
   :language: python
