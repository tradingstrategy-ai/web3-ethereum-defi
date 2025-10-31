.. meta::
   :description: Tutorial for Lagoon vaults and CowSwap trade automation

.. lagoon-cowswap:

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
    export JSON_RPC_ARBITRUM=...
    # We need EtherScan API to verify the contracts on Etherscan
    export ETHERSCAN_API_KEY=...
    python scripts/lagoon/lagoon-cowswap-example.py

Output looks like:

.. code-block:: none

    Created provider lb.drpc.org, using request args {'headers': {'Content-Type': 'application/json', 'User-Agent': "web3.py/6.14.0/<class 'web3.providers.rpc.HTTPProvider'>"}, 'timeout': (3.0, 30.0)}, headers {'Content-Type': 'application/json', 'User-Agent': "web3.py/6.14.0/<class 'web3.providers.rpc.HTTPProvider'>"}
    Created provider base-mainnet.g.alchemy.com, using request args {'headers': {'Content-Type': 'application/json', 'User-Agent': "web3.py/6.14.0/<class 'web3.providers.rpc.HTTPProvider'>"}, 'timeout': (3.0, 30.0)}, headers {'Content-Type': 'application/json', 'User-Agent': "web3.py/6.14.0/<class 'web3.providers.rpc.HTTPProvider'>"}
    Configuring MultiProviderWeb3. Call providers: ['lb.drpc.org', 'base-mainnet.g.alchemy.com'], transact providers -
    Using JSON RPC provider fallbacks lb.drpc.org, base-mainnet.g.alchemy.com for chain Base
    Forking Base with Anvil
    Attempting to allocate port 27388 to Anvil
    Multi RPC detected, using Anvil at the first RPC endpoint https://lb.drpc.org/ogrpc?network=base&dkey=AiWA4TvYpkijvapnvFlyx_UuJsZmMjkR8JUBzoXPVSjK
    Launching anvil: anvil --port 27388 --fork-url https://lb.drpc.org/ogrpc?network=base&dkey=AiWA4TvYpkijvapnvFlyx_UuJsZmMjkR8JUBzoXPVSjK --hardfork cancun --code-size-limit 99999
    anvil forked network 8453, the current block is 31,815,357, Anvil JSON-RPC is http://localhost:27388
    Making request with data: <class 'web3.providers.rpc.HTTPProvider'> {'headers': {'Content-Type': 'application/json', 'User-Agent': "web3.py/6.14.0/<class 'web3.providers.rpc.HTTPProvider'>"}, 'timeout': 3.0}
    Created provider localhost:27388, using request args {'headers': {'Content-Type': 'application/json', 'User-Agent': "web3.py/6.14.0/<class 'web3.providers.rpc.HTTPProvider'>"}, 'timeout': (10.0, 60.0)}, headers {'Content-Type': 'application/json', 'User-Agent': "web3.py/6.14.0/<class 'web3.providers.rpc.HTTPProvider'>"}
    Configuring MultiProviderWeb3. Call providers: ['localhost:27388'], transact providers -
    Synced nonce for 0x1A76D579415532C527485FC83FDBc954F9b67cE6 to 0
    Creating a simulated wallet 0x1A76D579415532C527485FC83FDBc954F9b67cE6 with USDC and ETH funding for testing
    Will not retry, method eth_call, as not a retryable exception <class 'ValueError'>: {'code': 3, 'message': 'execution reverted: custom error 0x1425ea42', 'data': '0x1425ea42'}
    Using vault Autopilot USDC Base (0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4)
    Gas balance: 11.0 ETH
    USDC balance: 15598593.712583
    Depositing 10.00 USDC to vault 0x0d877dc7c8fa3ad980dfdb18b48ec9f8768359c4
    Depositing...
    Depositing to vault 0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4, amount 10.00, from 0x1A76D579415532C527485FC83FDBc954F9b67cE6
    Broadcasting transaction approve(): 0x03d57ddb1cc2984a137565c1597227cc147844ba09bc220189e0fc4fdd591a01
    Broadcasting transaction deposit(): 0xc297da0c345a41b2586229cba3fefb1c37a4663cd266279aa3f8beb51cfc99e9
    We received 9.775728 bAutopilot_USDC
    Redeeming, simulated waiting for 1800 seconds
    Redeeming from vault 0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4, amount 9.77572796, from 0x1A76D579415532C527485FC83FDBc954F9b67cE6
    Broadcasting transaction approve(): 0x0c83593eec26c4c1dea4a8f4b7ceeb742d5be4bdd5f351d5d07e324590127672
    Broadcasting transaction redeem(): 0x35973ecdf96ad79393a365cd6550ee9ccf6025af696b79e3bc1b95055fe38355
    Deposit value: 10.00 USDC
    Redeem value: 9.999998 USDC
    Share count: 9.77572796 bAutopilot_USDC
    Slippage: -0.0000%
    All done

.. literalinclude:: ../../../scripts/lagoon/lagoon-cowswap-example.py
   :language: python
