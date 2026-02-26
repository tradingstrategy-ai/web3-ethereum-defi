# GuardV0 — on-chain trade validation for asset management

GuardV0 is a guard-pattern smart contract that validates every action an asset manager
performs on behalf of asset owners. It works with **any vault or multisignature wallet**
that can delegate call validation to an external contract, but is primarily designed for:

- **[Gnosis Safe](https://safe.global/) multisig wallets** — via the
  [TradingStrategyModuleV0](../safe-integration/src/TradingStrategyModuleV0.sol) Zodiac module
- **[Lagoon](https://lagoon.finance/) vaults** — ERC-7540 vaults backed by a Safe wallet

Even if the asset manager's private key is compromised, the guard ensures the attacker
cannot withdraw capital, trade into non-whitelisted tokens, or route swap output to
unauthorised addresses.

## Architecture

GuardV0 uses an **external library pattern** to stay within the
[EIP-170](https://eips.ethereum.org/EIPS/eip-170) 24 KB contract size limit.
Protocol-specific logic is extracted into Forge libraries that are called via
`DELEGATECALL` and use [diamond storage](https://eips.ethereum.org/EIPS/eip-2535)
for their whitelist state.

```
┌─────────────────────┐       DELEGATECALL       ┌──────────────────┐
│  GuardV0Base.sol    │ ◄─────────────────────── │  UniswapLib.sol  │
│  (main dispatcher)  │ ◄─────────────────────── │  CowSwapLib.sol  │
│                     │ ◄─────────────────────── │  VeloraLib.sol   │
│                     │ ◄─────────────────────── │  GmxLib.sol      │
│                     │ ◄─────────────────────── │  HypercoreVaultLi│
└─────────────────────┘                          └──────────────────┘
         ▲
         │ validateCall()
         │
┌────────┴────────────┐
│ TradingStrategy     │       Safe wallet
│ ModuleV0            │ ────► execTransactionFromModule()
│ (Zodiac module)     │
└─────────────────────┘
```

All libraries implement the [IGuardLib](./src/lib/IGuardLib.sol) deployment check interface.

## Supported protocols

The guard dispatcher validates calls to the following protocols:

| Protocol | Guard logic | Description |
|----------|-------------|-------------|
| **Uniswap V2** | [UniswapLib](./src/lib/UniswapLib.sol) | Swap path token validation, receiver checks |
| **Uniswap V3** | [UniswapLib](./src/lib/UniswapLib.sol) | `exactInput`, `exactOutput`, SwapRouter02 recipient validation |
| **Aave V3** | Built-in | `supply`, `withdraw` with asset and receiver checks |
| **ERC-4626** | Built-in | `deposit`, `withdraw`, `redeem` with receiver validation |
| **ERC-7540** | Built-in | `deposit`, `requestDeposit`, `requestRedeem` with receiver validation |
| **CowSwap** | [CowSwapLib](./src/lib/CowSwapLib.sol) | Presigned order creation with sender/token/receiver validation |
| **Velora (ParaSwap)** | [VeloraLib](./src/lib/VeloraLib.sol) | Atomic swaps with balance-envelope verification for opaque Augustus calldata |
| **GMX V2** | [GmxLib](./src/lib/GmxLib.sol) | Perpetuals multicall validation with market/router whitelisting |
| **Hypercore** | [HypercoreVaultLib](./src/lib/HypercoreVaultLib.sol) | HyperEVM native vault deposits, CoreWriter action validation |
| **ERC-20** | Built-in | `approve`, `transfer` to whitelisted addresses only |

Additional built-in support: `multicall` batching, Lagoon vault `settle`/`requestSettle`,
and general-purpose call-site whitelisting for any contract+selector pair.

## Security model

Every trade or action must pass through these checks:

1. **Sender validation** — only whitelisted asset managers can initiate calls
2. **Call-site whitelisting** — every (contract address, function selector) pair must be pre-approved
3. **Asset whitelisting** — tokens involved in trades must be on the allowed list (unless `anyAsset` mode)
4. **Receiver validation** — swap output, deposit shares, and withdrawal proceeds can only go to whitelisted addresses
5. **Protocol-specific validation** — each supported protocol has tailored checks (swap paths, order parameters, balance envelopes, etc.)

See the [contract size and optimisation notes](../../docs/README-contract-size.md) for details
on the library extraction pattern and compiler settings.

## Documentation

- **API reference**: [web3-ethereum-defi API documentation](https://web3-ethereum-defi.readthedocs.io/api/)
  - [Lagoon vault integration](https://web3-ethereum-defi.readthedocs.io/api/lagoon/index.html)
- **Tutorials**:
  - [Lagoon + CowSwap trading](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-cowswap.html)
  - [Lagoon + Velora (ParaSwap) trading](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-velora.html)
  - [Lagoon + GMX V2 perpetuals](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-gmx.html)
  - [Lagoon + Hyperliquid vault](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-hyperliquid.html)
  - [Enzyme vault deployment](https://web3-ethereum-defi.readthedocs.io/tutorials/enzyme-deploy.html)

## Tests

Integration tests use the [eth-defi](https://github.com/tradingstrategy-ai/web3-ethereum-defi)
Python test suite with Anvil mainnet forks. Run individual test modules with:

```shell
source .local-test.env && poetry run pytest tests/guard/<module> -v
```

### Python test suites

| Test module | Coverage |
|-------------|----------|
| [test_guard_simple_vault_uniswap_v2.py](../../tests/guard/test_guard_simple_vault_uniswap_v2.py) | Uniswap V2 swaps, access control, vault/guard basics |
| [test_guard_simple_vault_uniswap_v3.py](../../tests/guard/test_guard_simple_vault_uniswap_v3.py) | Uniswap V3 exactInput/exactOutput, malicious recipient detection |
| [test_guard_simple_vault_aave_v3.py](../../tests/guard/test_guard_simple_vault_aave_v3.py) | Aave V3 supply/withdraw guard validation |
| [test_guard_simple_vault_erc_4626.py](../../tests/guard/test_guard_simple_vault_erc_4626.py) | ERC-4626 deposit/withdraw, malicious receiver detection |
| [test_guard_simple_vault_one_delta.py](../../tests/guard/test_guard_simple_vault_one_delta.py) | 1delta leveraged trading guard validation |
| [test_guard_gmx_validation.py](../../tests/guard/test_guard_gmx_validation.py) | GMX V2 multicall validation, market/router whitelisting |
| [test_guard_simple_vault_hypercore.py](../../tests/guard/test_guard_simple_vault_hypercore.py) | Hypercore vault guard validation |
| [test_guard_hypercore_vault_lagoon.py](../../tests/guard/test_guard_hypercore_vault_lagoon.py) | Full Lagoon vault with Hypercore integration |

### Forge tests

```shell
cd contracts/guard && forge test -v
```

## Development

### Installing dependencies

```shell
forge soldeer install --config-location foundry
```

### Compiling

```shell
forge build
```

### Repackaging ABIs for Python

From the repository root:

```shell
make guard safe-integration
```

This regenerates ABI JSON files used by the Python automation layer.

### Deployment

Production deployments use the Python function
[`deploy_automated_lagoon_vault()`](https://web3-ethereum-defi.readthedocs.io/api/lagoon/index.html)
which handles the full deployment flow:

1. Deploy a Safe 1.4.1 multisig (or attach to an existing one)
2. Deploy the Lagoon vault contract
3. Deploy GuardV0, link all required protocol libraries (UniswapLib, CowSwapLib, VeloraLib, GmxLib, HypercoreVaultLib)
4. Deploy TradingStrategyModuleV0 and enable it as a Safe module
5. Whitelist routers, assets, and protocol-specific contracts
6. Optionally verify all contracts on Etherscan/Blockscout/Sourcify

See [`eth_defi.erc_4626.vault_protocol.lagoon.deployment`](../../eth_defi/erc_4626/vault_protocol/lagoon/deployment.py)
for the full source and [`LagoonDeploymentParameters`](../../eth_defi/erc_4626/vault_protocol/lagoon/deployment.py) for configuration options.

For manual single-contract deployment with Forge:

```shell
export DEPLOY_PRIVATE_KEY=
export JSON_RPC_POLYGON=
export POLYGONSCAN_API_KEY=
forge create \
  --rpc-url $JSON_RPC_POLYGON \
  --private-key $DEPLOY_PRIVATE_KEY \
  --etherscan-api-key $POLYGONSCAN_API_KEY \
  --verify \
  src/GuardV0.sol:GuardV0
```
