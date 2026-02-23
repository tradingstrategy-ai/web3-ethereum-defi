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
