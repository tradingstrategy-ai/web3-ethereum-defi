.. meta::
   :description: Tutorial for Lagoon vaults and GMX perpetuals trading

.. _lagoon-gmx:

Lagoon and GMX perpetuals integration
=====================================

Here is a Python example how to trade GMX V2 perpetuals from a Lagoon vault.

- You need ~0.01 ETH and ~$50 USDC on Arbitrum to run this tutorial script.
- This script deploys a new Lagoon vault with GMX integration enabled.
- The deployed vault has `TradingStrategyModuleV0 <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/safe-integration>`__
  configured for allowing automated whitelisted trades by an asset manager. This is a Zodiac module which extends the underlying
  Gnosis Safe functionality used as the core of Lagoon vaults.
- In this example, the deployer account, asset manager and Gnosis co-signers are all the same account for simplicity.
- After deploying the vault, the script deposits USDC collateral into the vault.
- The deposit must be settled to the vault per `ERC-7540 deposit and settlement cycle <https://tradingstrategy.ai/glossary/erc-7540>`__.
- When the vault has USDC in the Safe, we open a leveraged ETH long position via GMX.
- The script then closes the position and withdraws collateral.

Architecture overview
---------------------

The Lagoon vault uses a Gnosis Safe multisig to hold assets securely.
Trading is performed through the TradingStrategyModuleV0, which wraps
all transactions via ``performCall()``. This allows the asset manager's
hot wallet to execute trades while the Safe retains custody of funds.

::

    Asset Manager (Hot Wallet)
        │
        ▼
    TradingStrategyModuleV0.performCall()
        │
        ▼
    Gnosis Safe (Holds assets)
        │
        ▼
    GMX ExchangeRouter.multicall([sendWnt, sendTokens, createOrder])
        │
        ▼
    GMX Keeper (Executes order on-chain)

The Guard contract validates all GMX calls to ensure:

- Funds can only be sent to the GMX OrderVault (not arbitrary addresses)
- Order receivers are whitelisted (Safe address only)
- Only approved markets and collateral tokens can be used

For security details, see the `README-GMX-Lagoon.md <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/README-GMX-Lagoon.md>`__ file.

Prerequisites
-------------

You need:

- An Arbitrum wallet funded with at least 0.01 ETH for gas fees
- Some USDC on Arbitrum for trading collateral (~$50-100 recommended)
- ``JSON_RPC_ARBITRUM`` environment variable pointing to an Arbitrum RPC
- ``PRIVATE_KEY_SWAP_TEST`` environment variable with your wallet private key
- ``ETHERSCAN_API_KEY`` for contract verification (optional but recommended)

Running the script
------------------

.. code-block:: shell

    # Your Arbitrum node
    export JSON_RPC_ARBITRUM=...
    # Private key with ETH and USDC loaded in
    # See https://ethereum.stackexchange.com/a/125699/620
    export PRIVATE_KEY_SWAP_TEST=...
    # We need EtherScan API to verify the contracts on Etherscan
    export ETHERSCAN_API_KEY=...

    # Run the script
    python scripts/lagoon/lagoon-gmx-example.py

The script will:

1. Deploy a Lagoon vault with GMX integration
2. Whitelist GMX contracts and the ETH/USDC market
3. Deposit USDC collateral into the vault
4. Approve tokens for GMX SyntheticsRouter
5. Open a leveraged ETH long position
6. Wait for GMX keeper execution
7. Close the position
8. Withdraw collateral from the vault
9. Print a summary of all transactions and costs

API documentation
-----------------

- GMX CCXT adapter: :py:mod:`eth_defi.gmx.ccxt`
- LagoonWallet: :py:mod:`eth_defi.gmx.lagoon.wallet`
- LagoonVault: :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.vault`
- Guard contract: `GuardV0Base.sol <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/guard/src/GuardV0Base.sol>`__

Source code
-----------

.. literalinclude:: ../../../scripts/lagoon/lagoon-gmx-example.py
   :language: python
