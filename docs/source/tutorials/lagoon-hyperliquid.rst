.. meta::
   :description: Tutorial for deploying Lagoon vaults on HyperEVM with Hypercore vault deposits

.. _lagoon-hyperliquid:

Lagoon vault on HyperEVM with Hypercore deposits
=================================================

Here is a Python example how to deploy a `Lagoon <https://tradingstrategy.ai/glossary/lagoon>`__
`vault <https://tradingstrategy.ai/glossary/vault>`__
on `HyperEVM <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm>`__
and deposit `USDC <https://tradingstrategy.ai/glossary/usdc>`__ into a
`Hypercore vault <https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults>`__.
This is a low-level code example that shows every step in the
`smart contract <https://tradingstrategy.ai/glossary/smart-contract>`__ deployment and
deposit/withdrawal process.

The script will deploy the vault, the guard smart contract, deposit and withdraw from the deployed Lagoon vault to native Hypercore vaults:

1. Deploy a Lagoon vault with Hypercore guard integration
2. Whitelist the target Hypercore vault and CoreWriter contracts
3. Fund the Safe with USDC (deposit amount + activation overhead)
4. Activate the Safe's HyperCore account via ``depositFor``
5. Bridge USDC from HyperEVM to HyperCore spot (phase 1)
6. Wait for EVM escrow to clear
7. Move USDC from spot to perp and deposit into vault (phase 2)
8. Verify the deposit landed on HyperCore
9. Withdraw from the vault (if ``ACTION=both`` or ``ACTION=withdraw``)
10. Print a summary of all transactions and gas costs

Prerequisites
-------------

You need:

- A HyperEVM wallet funded with HYPE (gas) and USDC (see amounts below)
- Environment variables configured (see below)

Required funds
--------------

Your wallet must have the following minimum balances on HyperEVM:

**HYPE** (~0.1 HYPE recommended)
    Used for `gas fees <https://tradingstrategy.ai/glossary/gas-fee>`__. The tutorial performs multiple transactions:

    - Vault deployment (big blocks): ~0.02–0.05 HYPE
    - Guard configuration: ~0.005 HYPE
    - USDC transfer to Safe: ~0.001 HYPE
    - Account activation: ~0.001 HYPE
    - Deposit phases: ~0.002 HYPE
    - Withdrawal: ~0.002 HYPE

    Total gas costs vary with network congestion. Having 0.1 HYPE provides
    a comfortable buffer. Big block transactions take ~1 minute to confirm.

**USDC** (~$7 minimum for deposit)
    Broken down as:

    - $5 USDC for the vault deposit (minimum Hypercore vault deposit)
    - $2 USDC for HyperCore account activation ($1 creation fee + $1 reaches spot)

    You can modify ``USDC_AMOUNT`` to deposit larger amounts.

Account funding for HyperEVM testnet
-------------------------------------

.. tip::

    We recommend using **HyperEVM mainnet** for testing, because testnet EVM
    bridging does not seem to work, and factory contracts are not available on the testnet.


Funding a HyperEVM testnet account requires several steps because there is no
direct faucet:

1. Create a new private key and set ``HYPERCORE_WRITER_TEST_PRIVATE_KEY``
2. Move ~$2 worth of ETH on Arbitrum to that address
3. Move ~$5 worth of USDC on Arbitrum to that address
4. Sign in to `app.hyperliquid.xyz <https://app.hyperliquid.xyz>`__ with the new account
5. Deposit $5 USDC (minimum)
6. Now you have an account on Hyperliquid mainnet
7. Visit `app.hyperliquid-testnet.xyz/drip <https://app.hyperliquid-testnet.xyz/drip>`__ and claim
8. Now you have 1,000 USDC on the Hypercore testnet
9. Buy 1 HYPE with the mock USDC (set max `slippage <https://tradingstrategy.ai/glossary/slippage>`__ to 99%, testnet orderbook is illiquid)
10. Visit `Testnet portfolio <https://app.hyperliquid-testnet.xyz/portfolio>`__ — click EVM <-> CORE
11. Move 100 USDC to HyperEVM testnet
12. Move 0.01 HYPE to HyperEVM testnet
13. Check HyperEVM testnet balance on EVM <-> CORE dialog
    (there is no working HyperEVM testnet explorer)

Environment variables
---------------------

Refer to the script content for the full list of environment variables. 

Running the script
------------------

Simulate on Anvil fork (no real funds needed):

.. code-block:: shell

    # Simulate — deploys mock contracts on Anvil fork
    python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Explicit simulate flag
    SIMULATE=true python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

Mainnet deployment (recommended for testing):

.. code-block:: shell

    # Set your private key (must have HYPE + USDC on HyperEVM)
    export HYPERCORE_WRITER_TEST_PRIVATE_KEY="0x..."
    export JSON_RPC_HYPERLIQUID="https://rpc.hyperliquid.xyz/evm"

    # Deploy vault and deposit 5 USDC into HLP vault
    NETWORK=mainnet ACTION=deposit python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

Testnet deployment:

.. code-block:: shell

    export HYPERCORE_WRITER_TEST_PRIVATE_KEY="0x..."

    # Deploy vault and deposit 5 USDC (testnet)
    NETWORK=testnet ACTION=deposit python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py

    # Next day: withdraw from the same vault after lock-up expires
    NETWORK=testnet ACTION=withdraw USDC_AMOUNT=5 LAGOON_VAULT=0x... TRADING_STRATEGY_MODULE=0x... python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py


Troubleshooting
---------------

If a deposit lands on HyperEVM but the vault position is missing, use the
``check-hypercore-user.py`` diagnostic script to inspect the Safe's HyperCore state:

.. code-block:: shell

    ADDRESS=<safe_address> NETWORK=mainnet python scripts/hyperliquid/check-hypercore-user.py

This shows the Safe's spot balances, EVM escrows, perp account and vault positions
on HyperCore, helping diagnose where the USDC ended up.

API documentation
-----------------

- CoreWriter actions: :py:mod:`eth_defi.hyperliquid.core_writer`
- EVM escrow management: :py:mod:`eth_defi.hyperliquid.evm_escrow`
- Hyperliquid API: :py:mod:`eth_defi.hyperliquid.api`
- Session management: :py:mod:`eth_defi.hyperliquid.session`
- Big block helpers: :py:mod:`eth_defi.hyperliquid.block`
- LagoonVault: :py:mod:`eth_defi.erc_4626.vault_protocol.lagoon.vault`
- Vault deployment: :py:func:`eth_defi.erc_4626.vault_protocol.lagoon.deployment.deploy_automated_lagoon_vault`
- Guard contract: `GuardV0Base.sol <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/guard/src/GuardV0Base.sol>`__

Source code
-----------

.. literalinclude:: ../../../scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py
   :language: python

Example output
--------------

.. code-block:: none

    Live mainnet mode (RPC: xxx)
    Created provider edge.goldsky.com, using request args {'headers': {'Content-Type': 'application/json', 'User-Agent': 'web3.py/7.14.1/web3.providers.rpc.rpc.HTTPProvider'}, 'timeout': (3, 500.0)}, headers {'Content-Type': 'application/json', 'User-Agent': 'web3.py/7.14.1/web3.providers.rpc.rpc.HTTPProvider'}
    Created provider hyperliquid-mainnet.g.alchemy.com, using request args {'headers': {'Content-Type': 'application/json', 'User-Agent': 'web3.py/7.14.1/web3.providers.rpc.rpc.HTTPProvider'}, 'timeout': (3, 500.0)}, headers {'Content-Type': 'application/json', 'User-Agent': 'web3.py/7.14.1/web3.providers.rpc.rpc.HTTPProvider'}
    Created provider lb.drpc.org, using request args {'headers': {'Content-Type': 'application/json', 'User-Agent': 'web3.py/7.14.1/web3.providers.rpc.rpc.HTTPProvider'}, 'timeout': (3, 500.0)}, headers {'Content-Type': 'application/json', 'User-Agent': 'web3.py/7.14.1/web3.providers.rpc.rpc.HTTPProvider'}
    Configuring MultiProviderWeb3. Call providers: ['edge.goldsky.com', 'hyperliquid-mainnet.g.alchemy.com', 'lb.drpc.org'], transact providers -
    Connected to chain 999, block 28053777
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 214
    Deployer: 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c
    Deployer balances: 1.8148 HYPE, 10.90 USDC
    Deploying Lagoon vault...
    Beginning Lagoon vault deployment, legacy mode: False, ABI is lagoon/v0.5.0/Vault.json
    Deploying Safe with deterministic address (CREATE2).
    Initial cosigner list: ['0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c']
    Initial threshold: 1
    Salt nonce: 762
    Expected deterministic Safe address: 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A
    Sleeping for 10 seconds for Safe deployment state to propagate
    Safe deployed at deterministic address 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A
    Deployed new Safe: 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A
    Between contracts deployment delay: Sleeping 8.0 for new nonce to propagade
    Deploying Lagoon vault on chain 999, deployer is <eth_account.signers.local.LocalAccount object at 0x129c2e360>, legacy is False
    Wrapped native token is: 0x5555555555555555555555555555555555555555
    Transacting with OptinBeaconFactory contract 0x90beB507A1BA7D64633540cbce615B574224CD84.createVaultProxy() with args ['0x0000000000000000000000000000000000000000', '0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A', 259200, ['0xb88339CB7199b77E23DB6E890353E22632Ba630f', 'HyperEVM Hypercore Manual Test', 'TEST', '0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A', '0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A', '0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c', '0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A', '0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A', 200, 2000, False, 86400], '0x0101010101010101010101010101010101010101010101010101010101010101']
    Between contracts deployment delay: Sleeping 8.0 for new nonce to propagade
    Deploying TradingStrategyModuleV0
    CowSwapLib not needed, linking with zero address
    GmxLib not needed, linking with zero address
    Deploying HypercoreVaultLib for HyperEVM chain 999
    Setting big blocks enabled for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c on mainnet
    Big blocks API response: {'status': 'ok', 'response': {'type': 'default'}}
    Setting big blocks disabled for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c on mainnet
    Big blocks API response: {'status': 'ok', 'response': {'type': 'default'}}
    Deployed HypercoreVaultLib at 0x2078aFf0dD0362B139722aB48C8C09d818530a24 for HyperEVM
    Setting big blocks enabled for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c on mainnet
    Big blocks API response: {'status': 'ok', 'response': {'type': 'default'}}
    Deploying TradingStrategyModuleV0 with libraries {'CowSwapLib': '0x0000000000000000000000000000000000000000', 'GmxLib': '0x0000000000000000000000000000000000000000', 'HypercoreVaultLib': '0x2078aFf0dD0362B139722aB48C8C09d818530a24'} and gas 10000000
    Only 1 RPC provider configured: edge.goldsky.com, cannot switch, sleeping and hoping the issue resolves itself
    Encountered JSON-RPC retryable error {'code': -32603, 'message': 'upstream responded emptyish: null'}
    When calling RPC method: eth_getTransactionReceipt('0x4fc8697f032540ff8b73c888b2c6fa926fa8af7e83aa7b1a46fbc03664105466',)
    Headers are: {'content-encoding': 'gzip',
    'content-type': 'application/json',
    'date': 'Mon, 23 Feb 2026 12:04:30 GMT',
    'endpoint_uri': 'edge.goldsky.com',
    'fly-request-id': '01KJ565TWY3R7PVE3B920K3R38-cdg',
    'headers-track-id': '53-8811291200',
    'method': 'eth_getTransactionReceipt',
    'server': 'Fly/84caf4a9 (2026-02-18)',
    'status_code': 200,
    'traceparent': '00-1150cf49be6c9db344e0f75522f33ec8-2a7ab7831e3c6afe-00',
    'transfer-encoding': 'chunked',
    'via': '1.1 fly.io',
    'x-erpc-commit': '',
    'x-erpc-machine': 'd8d14e6c00e938',
    'x-erpc-region': 'cdg',
    'x-erpc-version': ''}
    Retrying in 5.000000 seconds, retry #1 / 6
    Setting big blocks disabled for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c on mainnet
    Big blocks API response: {'status': 'ok', 'response': {'type': 'default'}}
    Enabling TradingStrategyModuleV0 on Safe multisig
    Using gas estimate: {'Base Fee': '0.12G (119,099,107)',
    'Max fee per gas': '0.52G (515,645,999)',
    'Max priority fee per gas': '0.22G (222,200,000)'}
    Between contracts deployment delay: Sleeping 8.0 for new nonce to propagade
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 219
    Setting up TradingStrategyModuleV0 guard: 0x6150c316cA5bce03948d62eBa1f23a5892AE53b4
    Whitelisting trade-executor as sender
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 219
    Sleeping for 2 seconds to wait for nonce to propagate
    Whitelist Safe as trade receiver
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 220
    Sleeping for 2 seconds to wait for nonce to propagate
    Not whitelisted: Uniswap v2
    Not whitelisted: Uniswap v3
    Not whitelisted: Aave v3
    Not whitelisted: Orderly vault
    Not whitelisted: any ERC-4626 vaults
    Not whitelisting specific ERC-20 tokens
    Not whitelisted: GMX
    Not whitelisted: CCTP
    Whitelisting Hypercore: CoreWriter=0x3333333333333333333333333333333333333333, CoreDepositWallet=0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24
    Whitelisting Hypercore vault #1: 0xdfc24b077bc1425ad1dea75bcb6f8158e10df303
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 221
    Sleeping for 2 seconds to wait for nonce to propagate
    Hypercore whitelisting complete: 1 vault(s)
    Using only whitelisted assets
    Whitelist vault settlement
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 222
    Sleeping for 2 seconds to wait for nonce to propagate
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 223
    Sleeping for 2 seconds to wait for nonce to propagate
    Using gas estimate: {'Base Fee': '0.15G (154,978,757)',
    'Max fee per gas': '0.60G (596,016,415)',
    'Max priority fee per gas': '0.22G (222,200,000)'}
    Gnosis GS206 sync issue sleep 20.0 seconds
    Updating Safe owner list: ['0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c'] with threshold 1
    Deployer: already exist on Safe cosigner
    Changing signing threshold to: 1
    Using gas estimate: {'Base Fee': '0.10G (100,000,000)',
    'Max fee per gas': '1.16G (1,161,617,466)',
    'Max priority fee per gas': '0.84G (837,158,452)'}
    Owners updated
    Vault:  0xd7A7768268a4010AF413f6c85D4901a79eFddd56
    Safe:   0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A
    Module: 0x6150c316cA5bce03948d62eBa1f23a5892AE53b4
    Deployment gas cost: 0.005827 HYPE
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 226
    Transferring 7.0 USDC from deployer to Safe 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A (5 deposit + 2 activation)
    USDC transfer to Safe complete: tx 7dac5a171267dfa026c006715bf2bf57f5f01e8c31ecbf766883c2979df37d30
    Safe USDC balance: 7
    Account 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A coreUserExists on HyperCore: False
    Safe 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A not activated on HyperCore, activating...
    Account 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A coreUserExists on HyperCore: False
    User 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A: 0 spot balance(s), 0 EVM escrow(s)
    Activating account 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A on HyperCore via depositFor (2000000 raw USDC)
    Lagoon: Wrapping call to TradingStrategyModuleV0  v0.1.4. Target: 0xb88339CB7199b77E23DB6E890353E22632Ba630f, function: approve (0x095ea7b3), args: ['0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24', '2000000'], payload is 68 bytes
    Activation: USDC approve tx 959286ff0b38dcdf2f638ab08205e04b2a52d3935e0b536f6f9b6597a43d977f
    Nonce sync failed, read onchain nonce 227 that is older than our current nonce: 228. This may happen if you have not broadcasted the last transaction yet or if the node fallbacks edge.goldsky.com, hyperliquid-mainnet.g.alchemy.com, lb.drpc.org is crappy.
    Lagoon: Wrapping call to TradingStrategyModuleV0  v0.1.4. Target: 0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24, function: depositFor (0xc23c545a), args: ['0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A', '2000000', '4294967295'], payload is 100 bytes
    Activation: depositFor tx adf4efb46f7aff9705bb15b8c5edd2cb24e77e226f898b9a38250d063047f979
    Account 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A coreUserExists on HyperCore: True
    Account 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A successfully activated on HyperCore
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 229
    Phase 1: bridging 5 USDC to HyperCore spot...
    Phase 1 tx: a3839c988b59e5f39fd3de0c4be252abaae2765180937e596d02798888554557 (gas: 125901)
    Waiting for EVM escrow to clear...
    User 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A: 1 spot balance(s), 0 EVM escrow(s)
    EVM escrow cleared for 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A after 1 poll(s)
    Phase 2: transferUsdClass + vaultTransfer...
    Synced nonce for 0x4E6B7f7aFB2E23Bf9355c10e4454f73E6E6F3D9c to 230

    Deposit results:
    -----------  ----------------------------------------------------------------
    Phase 2 tx   5b7cd08c93e3037ae44b13cc5da1b470d0de04842f3566d6b95b413634c21cba
    Gas used     145955
    Block        28053991
    USDC amount  5
    Vault        0xdfc24b077bc1425ad1dea75bcb6f8158e10df303
    Mode         two_phase
    -----------  ----------------------------------------------------------------
    Waiting 10s for CoreWriter actions to settle on HyperCore...
    User 0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A has 1 vault position(s)

    Hypercore vault balances (Safe):
    Vault                                         Equity (USDC)  Locked until (UTC)
    ------------------------------------------  ---------------  --------------------------
    0xdfc24b077bc1425ad1dea75bcb6f8158e10df303                5  2026-02-27T12:06:22.064000

    Summary:
    ------------------  ------------------------------------------
    Network             mainnet
    Vault               0xd7A7768268a4010AF413f6c85D4901a79eFddd56
    Safe                0x5B7cf48FC9d2bE82DED36582dCC5da9c0950c96A
    Module              0x6150c316cA5bce03948d62eBa1f23a5892AE53b4
    Chain ID            999
    Action              deposit
    USDC amount         5
    Final USDC balance  0.00
    HYPE spent (gas)    0.006326
    Simulate            no
    ------------------  ------------------------------------------

