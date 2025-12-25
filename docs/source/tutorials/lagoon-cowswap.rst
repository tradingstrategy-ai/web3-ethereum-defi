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

Although this integration is written Lagoon vaults in mind, it works with any Gnosis Safe based wallet or product,
as it is using a standard Zodiac module for building the presigned trade transaction and checking the trade against given whitelist.

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

    Connected to Arbitrum (chain ID: 42161), last block is 397,188,070
    Hot wallet address: 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6, ETH balance: 0.006796688648525337 ETH, current nonce is 1035
    Current gas price estimate:
    {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    Out CowSwap quote data is:
    {'Buy': 'USDC.e',
     'Price': '3445.182645618117434978435882',
     'Sell': 'WETH',
     'expiration': '2025-11-05T22:01:55.488688398Z',
     'from': '0xdcc6d3a3c006bb4a10b448b1ee750966395622c6',
     'id': 60680954,
     'quote': {'appData': '0x0000000000000000000000000000000000000000000000000000000000000000',
               'buyAmount': '1140100',
               'buyToken': '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8',
               'buyTokenBalance': 'erc20',
               'feeAmount': '2407482000000',
               'kind': 'sell',
               'partiallyFillable': False,
               'receiver': None,
               'sellAmount': '330925851333333',
               'sellToken': '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
               'sellTokenBalance': 'erc20',
               'signingScheme': 'presign',
               'validTo': 1762381315},
     'verified': True}
    Target price is 3445.182646 WETH/USDC.e
    We set the max slippage goal to 0.855075 USDC.e for 0.000333 WETH with max slippage of 25.0%
    Broadcasting tx #1: a4968318b3d381c1736e25f9d6bf5f9aea74c594bec7faf68487e88a934325d7, calling deposit() with account nonce 1035
    After wrapping our WETH balance is 0.000333333333333333 WETH
    22:52:28 eth_defi.lagoon.deployment                   Beginning Lagoon vault deployment, legacy mode: False, ABI is lagoon/v0.5.0/Vault.json
    22:52:28 eth_defi.safe.deployment                     Deploying safe.
    Initial cosigner list: ['0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6']
    Initial threshold: 1
    22:52:30 eth_defi.safe.deployment                     Safe deployed at 0x9022F4e75EF11b97aD74A3B609A163ca44ce4460
    22:52:30 eth_defi.lagoon.deployment                   Deployed new Safe: 0x9022F4e75EF11b97aD74A3B609A163ca44ce4460
    22:52:30 eth_defi.lagoon.deployment                   Between contracts deployment delay: Sleeping 15.0 for new nonce to propagade
    22:52:45 eth_defi.lagoon.deployment                   Deploying Lagoon vault on chain 42161, deployer is <eth_account.signers.local.LocalAccount object at 0x1049a3a40>, legacy is False
    22:52:45 eth_defi.lagoon.deployment                   Wrapped native token is: 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
    22:52:45 eth_defi.lagoon.deployment                   Transacting with OptinBeaconFactory contract 0xb1ee4f77a1691696a737ab9852e389cf4cb1f1f5.createVaultProxy() with args ['0x0000000000000000000000000000000000000000', '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460', 259200, ['0x82aF49447D8a07e3bd95BD0d56f35241523fBab1', 'https://github.com/tradingstrategy-ai/web3-ethereum-defi', 'TradingStrategy.ai', '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460', '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460', '0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6', '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460', '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460', 200, 2000, False, 86400], b'\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01']
    22:52:47 eth_defi.lagoon.deployment                   Between contracts deployment delay: Sleeping 15.0 for new nonce to propagade
    22:53:02 eth_defi.lagoon.deployment                   Deploying TradingStrategyModuleV0
    22:53:02 eth_defi.foundry.forge                       Doing Etherscan verification with 9 retries
    22:53:02 eth_defi.foundry.forge                       Deploying a contract with forge. Working directory /Users/moo/code/trade-executor/deps/web3-ethereum-defi/contracts/safe-integration, forge command: /Users/moo/.foundry/bin/forge create --broadcast --rpc-url https://lb.drpc.org/ogrpc?network=arbitrum&dkey=AiWA4TvYpkijvapnvFlyx_UuJsZmMjkR8JUBzoXPVSjK --nonce 1038 --etherscan-api-key QZ29ISZDE6UEC9JZ9BUUIJCHYFB71ZISB6 --verify --retries 9 --delay 20 src/TradingStrategyModuleV0.sol:TradingStrategyModuleV0 --constructor-args 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 0x9022F4e75EF11b97aD74A3B609A163ca44ce4460
    22:53:43 eth_defi.safe.execute                        Using gas estimate: {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    22:53:44 eth_defi.lagoon.deployment                   Between contracts deployment delay: Sleeping 15.0 for new nonce to propagade
    22:53:59 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1040
    22:53:59 eth_defi.lagoon.deployment                   Setting up TradingStrategyModuleV0 guard: 0x326C6318e855aE23A3eC7eFe595FC51fF348F221
    22:53:59 eth_defi.lagoon.deployment                   Whitelisting trade-executor as sender
    22:53:59 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1040
    22:54:01 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:54:03 eth_defi.lagoon.deployment                   Whitelist Safe as trade receiver
    22:54:03 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1041
    22:54:05 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:54:07 eth_defi.lagoon.deployment                   Not whitelisted: Uniswap v2
    22:54:07 eth_defi.lagoon.deployment                   Not whitelisted: Uniswap v3
    22:54:07 eth_defi.lagoon.deployment                   Not whitelisted: Aave v3
    22:54:07 eth_defi.lagoon.deployment                   Not whitelisted: Orderly vault
    22:54:07 eth_defi.lagoon.deployment                   Not whitelisted: any ERC-4626 vaults
    22:54:07 eth_defi.lagoon.deployment                   Processing assets chunk #1, size 4
    22:54:07 eth_defi.lagoon.deployment                   Whitelisting #1 token <USD Coin (Arb1) (USDC.e) at 0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8, 6 decimals, on chain 42161>:
    22:54:07 eth_defi.lagoon.deployment                   Whitelisting #2 token <USD Coin (USDC) at 0xaf88d065e77c8cC2239327C5EDb3A432268e5831, 6 decimals, on chain 42161>:
    22:54:07 eth_defi.lagoon.deployment                   Whitelisting #3 token <USD₮0 (USD₮0) at 0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9, 6 decimals, on chain 42161>:
    22:54:07 eth_defi.lagoon.deployment                   Whitelisting #4 token <Wrapped Ether (WETH) at 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1, 18 decimals, on chain 42161>:
    22:54:08 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1042
    22:54:09 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:54:11 eth_defi.lagoon.deployment                   Enforce vault tx readback lag on mainnet, sleeping 10 seconds
    22:54:31 eth_defi.lagoon.deployment                   Total 4 assets whitelisted
    22:54:31 eth_defi.lagoon.deployment                   Whitelisting CowSwap: 0x9008D19f58AAbD9eD0D60971565AA8510560ab41
    22:54:31 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1043
    22:54:33 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:54:35 eth_defi.lagoon.deployment                   Using only whitelisted assets
    22:54:35 eth_defi.lagoon.deployment                   Whitelist vault settlement
    22:54:35 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1044
    22:54:37 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:54:39 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1045
    22:54:40 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:54:43 eth_defi.safe.execute                        Using gas estimate: {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    22:54:44 eth_defi.lagoon.deployment                   Gnosis GS206 sync issue sleep 20.0 seconds
    22:55:04 eth_defi.safe.deployment                     Updating Safe owner list: ['0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6'] with threshold 1
    22:55:05 eth_defi.safe.deployment                     Deployer: already exist on Safe cosigner
    22:55:05 eth_defi.safe.deployment                     Changing signing threshold to: 1
    22:55:05 eth_defi.safe.execute                        Using gas estimate: {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    22:55:06 eth_defi.safe.deployment                     Owners updated
    Deployed Lagoon vault at 0x3A7f3938F183d496252F892A463c8E4765cB3E96 with Cowswap integration
    Key                            Label
    Deployer                       0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    Safe                           0x9022F4e75EF11b97aD74A3B609A163ca44ce4460
    Vault                          0x3A7f3938F183d496252F892A463c8E4765cB3E96
    Beacon proxy factory           0xb1ee4f77a1691696a737ab9852e389cf4cb1f1f5
    Trading strategy module        0x326C6318e855aE23A3eC7eFe595FC51fF348F221
    Asset manager                  0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    Underlying token               0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
    Underlying symbol              WETH
    Share token                    0x3A7f3938F183d496252F892A463c8E4765cB3E96
    Share token symbol             TradingStrategy.ai
    Multisig owners                0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    Block number                   397,188,836
    Performance fee                20.0 %
    Management fee                 2.0 %
    ABI                            lagoon/v0.5.0/Vault.json
    Gas used                       0.00008509901999999999419078966500507021919474937021732330322265625

    Broadcasting tx #2: 1cf28ed3d180e62a28c122699bcbabc61f5e74aee5008ac34a651f02892620d8, calling approve() with account nonce 1048
    Broadcasting tx #3: d1f4de20728201cbf4ecd2aa8542385a11311ec4ffbae3d7bdd9a07ef00e8e07, calling requestDeposit() with account nonce 1049
    Broadcasting tx #4: 488102d6a60f7b798d7ae468b52aeda5747addd2e38e255a41b8f889c5423f8e, calling updateNewTotalAssets() with account nonce 1050
    Broadcasting tx #5: 63016c8dda2489273fad68851d5a69aaef7c67d7dc167b28b804a2a2d7751abf, calling performCall() with account nonce 1051
    Broadcasting tx #6: 0f2cf655bb198169a798d01fbe119eaa79d87ee6fb0bad4cbf34f07c0765a0a6, calling performCall() with account nonce 1052
    Broadcasting tx #7: 6da35fd18c95c4ca6d67efa3d9e591e24e24f19ecd37904695121a3284090676, calling swapAndValidateCowSwap() with account nonce 1053
    Our CoW Swap presigned order is:
    {'appData': '0x0000000000000000000000000000000000000000000000000000000000000000',
     'buyAmount': 855075,
     'buyToken': '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
     'buyTokenBalance': 'erc20',
     'feeAmount': 0,
     'from': '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460',
     'kind': 'sell',
     'partiallyFillable': False,
     'receiver': '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460',
     'sellAmount': 333333333333333,
     'sellToken': '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
     'sellTokenBalance': 'erc20',
     'tx_hash': '6da35fd18c95c4ca6d67efa3d9e591e24e24f19ecd37904695121a3284090676',
     'uid': '0x1dfeb94fdeb93a798d8119d7157296d64c6342fee4fbef745ce3ab6dc5002d169022f4e75ef11b97ad74a3b609a163ca44ce4460690bcc73',
     'validTo': 1762380915}
    View the order at CoW Swap explorer https://explorer.cow.fi/arb1/search/0x1dfeb94fdeb93a798d8119d7157296d64c6342fee4fbef745ce3ab6dc5002d169022f4e75ef11b97ad74a3b609a163ca44ce4460690bcc73
    22:55:16 eth_defi.cow.order                           Posting CowSwap order to https://api.cow.fi/arbitrum_one/api/v1/orders: {'appData': '0x0000000000000000000000000000000000000000000000000000000000000000',
     'buyAmount': '855075',
     'buyToken': '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
     'buyTokenBalance': 'erc20',
     'feeAmount': '0',
     'from': '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460',
     'kind': 'sell',
     'partiallyFillable': False,
     'receiver': '0x9022F4e75EF11b97aD74A3B609A163ca44ce4460',
     'sellAmount': '333333333333333',
     'sellToken': '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
     'sellTokenBalance': 'erc20',
     'signature': '0x',
     'signingScheme': 'presign',
     'tx_hash': '6da35fd18c95c4ca6d67efa3d9e591e24e24f19ecd37904695121a3284090676',
     'uid': '0x1dfeb94fdeb93a798d8119d7157296d64c6342fee4fbef745ce3ab6dc5002d169022f4e75ef11b97ad74a3b609a163ca44ce4460690bcc73',
     'validTo': 1762380915}
    22:55:18 eth_defi.cow.order                           Received posted order UID from Cow backend: 0x1dfeb94fdeb93a798d8119d7157296d64c6342fee4fbef745ce3ab6dc5002d169022f4e75ef11b97ad74a3b609a163ca44ce4460690bcc73
    22:55:18 eth_defi.cow.status                          Fetching order data https://api.cow.fi/arbitrum_one/api/v1/orders/0x1dfeb94fdeb93a798d8119d7157296d64c6342fee4fbef745ce3ab6dc5002d169022f4e75ef11b97ad74a3b609a163ca44ce4460690bcc73/status, timeout is 0:10:00
    22:55:18 eth_defi.cow.status                          CowSwap order 0x1dfeb94fdeb93a798d8119d7157296d64c6342fee4fbef745ce3ab6dc5002d169022f4e75ef11b97ad74a3b609a163ca44ce4460690bcc73 completed with status scheduled in 0:00:00.160873
    Cowswap order completed, order UID: 1dfeb94fdeb93a798d8119d7157296d64c6342fee4fbef745ce3ab6dc5002d169022f4e75ef11b97ad74a3b609a163ca44ce4460690bcc73, status: scheduled
    Order failed - not sure why:
    {'type': 'scheduled'}
    (web3-ethereum-defi-py3.12) ➜  web3-ethereum-defi git:(master) ✗
    (web3-ethereum-defi-py3.12) ➜  web3-ethereum-defi git:(master) ✗ python scripts/lagoon/lagoon-cowswap-example.py
    Connected to Arbitrum (chain ID: 42161), last block is 397,189,131
    Hot wallet address: 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6, ETH balance: 0.006369202215192004 ETH, current nonce is 1054
    Current gas price estimate:
    {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    Out CowSwap quote data is:
    {'Buy': 'USDC.e',
     'Price': '3443.526320294457096467519041',
     'Sell': 'WETH',
     'expiration': '2025-11-05T22:06:21.246012377Z',
     'from': '0xdcc6d3a3c006bb4a10b448b1ee750966395622c6',
     'id': 60681262,
     'quote': {'appData': '0x0000000000000000000000000000000000000000000000000000000000000000',
               'buyAmount': '1139553',
               'buyToken': '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8',
               'buyTokenBalance': 'erc20',
               'feeAmount': '2407156500000',
               'kind': 'sell',
               'partiallyFillable': False,
               'receiver': None,
               'sellAmount': '330926176833333',
               'sellToken': '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
               'sellTokenBalance': 'erc20',
               'signingScheme': 'presign',
               'validTo': 1762381581},
     'verified': True}
    Target price is 3443.526320 WETH/USDC.e
    We set the max slippage goal to 0.854665 USDC.e for 0.000333 WETH with max slippage of 25.0%
    Broadcasting tx #1: 951b86b26b4ffc33509d3736ba38b9864f3e2a0dc2d83e6bece3e0ab6e894697, calling deposit() with account nonce 1054
    After wrapping our WETH balance is 0.000333333333333333 WETH
    22:56:55 eth_defi.lagoon.deployment                   Beginning Lagoon vault deployment, legacy mode: False, ABI is lagoon/v0.5.0/Vault.json
    22:56:55 eth_defi.safe.deployment                     Deploying safe.
    Initial cosigner list: ['0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6']
    Initial threshold: 1
    22:56:57 eth_defi.safe.deployment                     Safe deployed at 0x468c672F46F23590439673B7642990959af1eD0c
    22:56:57 eth_defi.lagoon.deployment                   Deployed new Safe: 0x468c672F46F23590439673B7642990959af1eD0c
    22:56:57 eth_defi.lagoon.deployment                   Between contracts deployment delay: Sleeping 15.0 for new nonce to propagade
    22:57:12 eth_defi.lagoon.deployment                   Deploying Lagoon vault on chain 42161, deployer is <eth_account.signers.local.LocalAccount object at 0x108617980>, legacy is False
    22:57:12 eth_defi.lagoon.deployment                   Wrapped native token is: 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
    22:57:12 eth_defi.lagoon.deployment                   Transacting with OptinBeaconFactory contract 0xb1ee4f77a1691696a737ab9852e389cf4cb1f1f5.createVaultProxy() with args ['0x0000000000000000000000000000000000000000', '0x468c672F46F23590439673B7642990959af1eD0c', 259200, ['0x82aF49447D8a07e3bd95BD0d56f35241523fBab1', 'https://github.com/tradingstrategy-ai/web3-ethereum-defi', 'TradingStrategy.ai', '0x468c672F46F23590439673B7642990959af1eD0c', '0x468c672F46F23590439673B7642990959af1eD0c', '0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6', '0x468c672F46F23590439673B7642990959af1eD0c', '0x468c672F46F23590439673B7642990959af1eD0c', 200, 2000, False, 86400], b'\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01']
    22:57:13 eth_defi.lagoon.deployment                   Between contracts deployment delay: Sleeping 15.0 for new nonce to propagade
    22:57:28 eth_defi.lagoon.deployment                   Deploying TradingStrategyModuleV0
    22:57:29 eth_defi.foundry.forge                       Doing Etherscan verification with 9 retries
    22:57:29 eth_defi.foundry.forge                       Deploying a contract with forge. Working directory /Users/moo/code/trade-executor/deps/web3-ethereum-defi/contracts/safe-integration, forge command: /Users/moo/.foundry/bin/forge create --broadcast --rpc-url https://lb.drpc.org/ogrpc?network=arbitrum&dkey=AiWA4TvYpkijvapnvFlyx_UuJsZmMjkR8JUBzoXPVSjK --nonce 1057 --etherscan-api-key QZ29ISZDE6UEC9JZ9BUUIJCHYFB71ZISB6 --verify --retries 9 --delay 20 src/TradingStrategyModuleV0.sol:TradingStrategyModuleV0 --constructor-args 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 0x468c672F46F23590439673B7642990959af1eD0c
    22:58:25 eth_defi.safe.execute                        Using gas estimate: {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    22:58:26 eth_defi.lagoon.deployment                   Between contracts deployment delay: Sleeping 15.0 for new nonce to propagade
    22:58:41 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1059
    22:58:41 eth_defi.lagoon.deployment                   Setting up TradingStrategyModuleV0 guard: 0x7A2a02F8Fc40a568A4f7983Cb37C838f7874f3E7
    22:58:41 eth_defi.lagoon.deployment                   Whitelisting trade-executor as sender
    22:58:42 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1059
    22:58:43 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:58:45 eth_defi.lagoon.deployment                   Whitelist Safe as trade receiver
    22:58:45 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1060
    22:58:47 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:58:49 eth_defi.lagoon.deployment                   Not whitelisted: Uniswap v2
    22:58:49 eth_defi.lagoon.deployment                   Not whitelisted: Uniswap v3
    22:58:49 eth_defi.lagoon.deployment                   Not whitelisted: Aave v3
    22:58:49 eth_defi.lagoon.deployment                   Not whitelisted: Orderly vault
    22:58:49 eth_defi.lagoon.deployment                   Not whitelisted: any ERC-4626 vaults
    22:58:49 eth_defi.lagoon.deployment                   Processing assets chunk #1, size 4
    22:58:49 eth_defi.lagoon.deployment                   Whitelisting #1 token <USD Coin (Arb1) (USDC.e) at 0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8, 6 decimals, on chain 42161>:
    22:58:50 eth_defi.lagoon.deployment                   Whitelisting #2 token <USD Coin (USDC) at 0xaf88d065e77c8cC2239327C5EDb3A432268e5831, 6 decimals, on chain 42161>:
    22:58:50 eth_defi.lagoon.deployment                   Whitelisting #3 token <USD₮0 (USD₮0) at 0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9, 6 decimals, on chain 42161>:
    22:58:50 eth_defi.lagoon.deployment                   Whitelisting #4 token <Wrapped Ether (WETH) at 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1, 18 decimals, on chain 42161>:
    22:58:50 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1061
    22:58:52 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:58:54 eth_defi.lagoon.deployment                   Enforce vault tx readback lag on mainnet, sleeping 10 seconds
    22:59:14 eth_defi.lagoon.deployment                   Total 4 assets whitelisted
    22:59:14 eth_defi.lagoon.deployment                   Whitelisting CowSwap: 0x9008D19f58AAbD9eD0D60971565AA8510560ab41
    22:59:14 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1062
    22:59:15 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:59:18 eth_defi.lagoon.deployment                   Using only whitelisted assets
    22:59:18 eth_defi.lagoon.deployment                   Whitelist vault settlement
    22:59:18 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1063
    22:59:19 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:59:21 eth_defi.hotwallet                           Synced nonce for 0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6 to 1064
    22:59:23 eth_defi.lagoon.deployment                   Sleeping for 2 seconds to wait for nonce to propagate
    22:59:26 eth_defi.safe.execute                        Using gas estimate: {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    22:59:27 eth_defi.lagoon.deployment                   Gnosis GS206 sync issue sleep 20.0 seconds
    22:59:47 eth_defi.safe.deployment                     Updating Safe owner list: ['0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6'] with threshold 1
    22:59:48 eth_defi.safe.deployment                     Deployer: already exist on Safe cosigner
    22:59:48 eth_defi.safe.deployment                     Changing signing threshold to: 1
    22:59:48 eth_defi.safe.execute                        Using gas estimate: {'Base Fee': '0.01G (10,000,000)',
     'Max fee per gas': '0.02G (20,000,000)',
     'Max priority fee per gas': '0.00G (0)'}
    22:59:49 eth_defi.safe.deployment                     Owners updated
    Deployed Lagoon vault at 0xBB5Dd4697F240E1826A833D02fB1fD2B1d25f371 with Cowswap integration
    Key                            Label
    Deployer                       0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    Safe                           0x468c672F46F23590439673B7642990959af1eD0c
    Vault                          0xBB5Dd4697F240E1826A833D02fB1fD2B1d25f371
    Beacon proxy factory           0xb1ee4f77a1691696a737ab9852e389cf4cb1f1f5
    Trading strategy module        0x7A2a02F8Fc40a568A4f7983Cb37C838f7874f3E7
    Asset manager                  0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    Underlying token               0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
    Underlying symbol              WETH
    Share token                    0xBB5Dd4697F240E1826A833D02fB1fD2B1d25f371
    Share token symbol             TradingStrategy.ai
    Multisig owners                0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6
    Block number                   397,189,960
    Performance fee                20.0 %
    Management fee                 2.0 %
    ABI                            lagoon/v0.5.0/Vault.json
    Gas used                       0.000084546800000000001703952257070540099448408000171184539794921875

    Broadcasting tx #2: 2917955733e6b54497f92230cde69775d23b03c8f8b4a9bc5e3f999b7f21bb26, calling approve() with account nonce 1067
    Broadcasting tx #3: cf11de129d958f6c457a7779e8cfeb4cd208a839faf5245637ad807435e2ecaf, calling requestDeposit() with account nonce 1068
    Broadcasting tx #4: c9cc848bb4f781f6c0b870819cee9cd73bd1108d1eb0f1b548870ef8db26c3cc, calling updateNewTotalAssets() with account nonce 1069
    Broadcasting tx #5: 37cff964085311265fea0c101126843c230608fd160dbd01a67b13d5517320fe, calling performCall() with account nonce 1070
    Broadcasting tx #6: 5df5864b18add44b085e62af574a2f7603758907ef29b796d01497cfa859986d, calling performCall() with account nonce 1071
    Broadcasting tx #7: a9015a2dd89c245d5fb3da19e5146e59a493e1470ec4e19edd734750121b49d9, calling swapAndValidateCowSwap() with account nonce 1072
    Our CoW Swap presigned order is:
    {'appData': '0x0000000000000000000000000000000000000000000000000000000000000000',
     'buyAmount': 854664,
     'buyToken': '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
     'buyTokenBalance': 'erc20',
     'feeAmount': 0,
     'from': '0x468c672F46F23590439673B7642990959af1eD0c',
     'kind': 'sell',
     'partiallyFillable': False,
     'receiver': '0x468c672F46F23590439673B7642990959af1eD0c',
     'sellAmount': 333333333333333,
     'sellToken': '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
     'sellTokenBalance': 'erc20',
     'tx_hash': 'a9015a2dd89c245d5fb3da19e5146e59a493e1470ec4e19edd734750121b49d9',
     'uid': '0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f',
     'validTo': 1762381199}
    View the order at CoW Swap explorer https://explorer.cow.fi/arb1/search/0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f
    23:00:00 eth_defi.cow.order                           Posting CowSwap order to https://api.cow.fi/arbitrum_one/api/v1/orders: {'appData': '0x0000000000000000000000000000000000000000000000000000000000000000',
     'buyAmount': '854664',
     'buyToken': '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',
     'buyTokenBalance': 'erc20',
     'feeAmount': '0',
     'from': '0x468c672F46F23590439673B7642990959af1eD0c',
     'kind': 'sell',
     'partiallyFillable': False,
     'receiver': '0x468c672F46F23590439673B7642990959af1eD0c',
     'sellAmount': '333333333333333',
     'sellToken': '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
     'sellTokenBalance': 'erc20',
     'signature': '0x',
     'signingScheme': 'presign',
     'tx_hash': 'a9015a2dd89c245d5fb3da19e5146e59a493e1470ec4e19edd734750121b49d9',
     'uid': '0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f',
     'validTo': 1762381199}
    23:00:02 eth_defi.cow.order                           Received posted order UID from Cow backend: 0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f
    23:00:02 eth_defi.cow.status                          Fetching order data https://api.cow.fi/arbitrum_one/api/v1/orders/0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f/status, timeout is 0:10:00
    23:00:02 eth_defi.cow.status                          Waiting for CowSwap to complete cycle 1, order scheduled is 0:00:00.332169,, passed UID is 0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f, sleeping 10.0...
    23:00:12 eth_defi.cow.status                          Waiting for CowSwap to complete cycle 2, order active is 0:00:10.457994,, passed UID is 0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f, sleeping 10.0...
    23:00:22 eth_defi.cow.status                          CowSwap order 0xa736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f completed with status traded in 0:00:20.609588
    Cowswap order completed, order UID: a736afd811caa90aa4eb6e541b1bf8852f7f97568ff59c1bb9e2bef202531c3b468c672f46f23590439673b7642990959af1ed0c690bcd8f, status: traded
    Moooooo 🐮
    Order final result:
    'traded'
    All ok, check the vault at https://routescan.io/0xBB5Dd4697F240E1826A833D02fB1fD2B1d25f371


.. literalinclude:: ../../../scripts/lagoon/lagoon-cowswap-example.py
   :language: python
