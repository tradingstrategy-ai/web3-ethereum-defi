# GuardV0 — on-chain trade validation for asset management

GuardV0 is a guard-pattern smart contract that validates every action an asset manager
performs on behalf of asset owners. It works with **any vault or multisignature wallet**
that can delegate call validation to an external contract, but is primarily designed for:

- **[Gnosis Safe](https://safe.global/) multisig wallets** — via the
  [TradingStrategyModuleV0](../safe-integration/src/TradingStrategyModuleV0.sol) Zodiac module
- **[Lagoon](https://lagoon.finance/) vaults** — ERC-7540 vaults backed by a Safe wallet

With a deliberately narrow product configuration, the guard limits a compromised asset
manager to reviewed call sites, assets and receivers. It is not a guarantee that every
configuration or integrated protocol is safe; read the deployment restrictions below.

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
│                     │ ◄─────────────────────── │  LagoonLib.sol   │
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

### Compiler pipeline

Build GuardV0 and its libraries with the legacy Solidity compiler pipeline
(`via_ir = false`). With the default `optimizer_runs = 1` configuration,
the Yul IR pipeline produces larger deployed GuardV0 and
TradingStrategyModuleV0 dispatcher bytecode and reduces their EIP-170 margin.
An individual external library may be smaller under IR, but the deployable
top-level contracts determine the pipeline choice. Keep the Guard and Safe
integration configurations aligned, rebuild both with
`make guard safe-integration`, and check their deployed sizes after changing
compiler settings or guard rules. See the detailed
[contract-size notes](../../docs/README-contract-size.md).

## Supported protocols

The guard dispatcher validates calls to the following protocols:

| Protocol | Guard logic | Description |
|----------|-------------|-------------|
| **Uniswap V2** | [UniswapLib](./src/lib/UniswapLib.sol) | Development adapter; not approved for product use |
| **Uniswap V3** | [UniswapLib](./src/lib/UniswapLib.sol) | Development adapter; not approved for product use |
| **Aave V3** | Built-in | `supply`, `withdraw` with asset and receiver checks |
| **ERC-4626** | Built-in | `deposit`, `withdraw`, `redeem` with receiver and share-owner validation |
| **ERC-7540** | Built-in | Request and claim calls with controller/receiver and owner validation |
| **Ember** | Built-in | `redeemShares` payout-receiver validation |
| **Gains V1** | Built-in | `makeWithdrawRequest` share-owner and standard `redeem` receiver/owner validation |
| **Ostium V1.5** | Built-in | Request, claim, cancellation and reclaim call-site validation |
| **NaraUSD+** | Built-in | `cooldownShares` and `unstake(receiver)` validation |
| **Upshift** | Built-in | `deposit`, instant/queued redemption and claim receiver validation |
| **CowSwap** | [CowSwapLib](./src/lib/CowSwapLib.sol) | Development adapter; not approved for product use |
| **Velora (ParaSwap)** | [VeloraLib](./src/lib/VeloraLib.sol) | Development adapter; not approved for product use |
| **GMX V2** | [GmxLib](./src/lib/GmxLib.sol) | Perpetuals multicall validation with market/router whitelisting |
| **Hypercore** | [HypercoreVaultLib](./src/lib/HypercoreVaultLib.sol) | HyperEVM native vault deposits, CoreWriter action validation |
| **Lighter (Ethereum)** | [LighterLib](./src/lib/LighterLib.sol) | Ethereum `ZkLighter` USDC deposit/withdraw validation with receiver + asset-index checks; Robinhood custody is not supported ([docs](../../eth_defi/lighter/README-lighter-guard.md)) |
| **ERC-20** | Built-in | `approve`, `transfer` to whitelisted addresses only |

Additional built-in support: `multicall` batching, Lagoon vault `settle`/`requestSettle`,
and general-purpose call-site whitelisting for any contract+selector pair.

## Security model

Every trade or action must pass through these checks:

1. **Sender validation** — only whitelisted asset managers can initiate calls
2. **Call-site whitelisting** — every (contract address, function selector) pair must be pre-approved
3. **Asset whitelisting** — tokens involved in trades must be on the allowed list in every product deployment
4. **Receiver validation** — swap output, deposit shares, and withdrawal proceeds can only go to whitelisted addresses
5. **Protocol-specific validation** — each supported protocol has tailored checks (swap paths, order parameters, balance envelopes, etc.)

Use ``whitelistERC4626()`` for an ERC-4626-compatible vault and its established
extension surface, including Plutus Hedge's ``redeem(requestId, receiver)``
claim and request cancellation. Upshift is deliberately separate because it is multi-asset
and does not use the ERC-4626 deposit ABI: configure every accepted asset with
``whitelistUpshift(vault, asset, notes)``. Unknown selectors remain rejected.

### Product deployment restrictions

`anyAsset` is an escape hatch for development and live-network rehearsals. It remains
available on mainnet because the contract cannot distinguish a rehearsal from a product
deployment. Every product deployment must instead configure an explicit asset list and
leave `anyAsset` disabled.

With `anyAsset` enabled, an asset manager can call `approve()` on a dynamic token target.
The guard cannot validate that target or its call site, so it can only validate the spender.
That makes approvals unsuitable for a product asset manager.

`anyHypercoreVault` is a separate, narrower policy for HyperEVM strategies with
an open-ended Hypercore vault universe. It bypasses only the vault-address
allowlist for CoreWriter `vaultTransfer()` actions; CoreWriter action IDs,
CoreDepositWallet targets, receivers and ERC-20 approval checks remain enforced.
It does not enable `anyAsset`, whose separate risks still apply if governance
chooses to turn it on.

Uniswap V2/V3, CowSwap and Velora are present for development but are not enabled for
active product use. Their manager-selected minimum-output checks do not provide an
oracle-backed cumulative slippage limit. Before any product adoption, fortify each adapter
with a cumulative maximum-slippage policy comparable to the one historically used by
Enzyme.

CCTP `depositForBurn()` calls must set `destinationCaller` to `bytes32(0)`. Circle specifies
that a non-zero value exclusively authorises that address to call `receiveMessage()` on the
destination chain; rejecting it prevents an asset manager from stranding the Safe's burned
USDC behind an attacker-controlled caller. The mint recipient and destination domain remain
independently allowlisted. See Circle's [CCTP contract interface reference](https://developers.circle.com/cctp/references/contract-interfaces).
This validation does not cap the burn amount or CCTP `maxFee`; deployments requiring
monetary limits need an explicit policy for those values.

### Lagoon v0.5 asset-manager settlement safety

Lagoon deployments may set `LagoonConfig.max_settlement_amount` as a safety limit on
the gross underlying amount processed by an asset-manager settlement transaction.
The default is `None`, which preserves the legacy unlimited behaviour. When the
feature is enabled, `LagoonConfig.settlement_cooldown` also rate-limits non-zero
asset-manager settlements and defaults to 24 hours.

The complete amount-and-cooldown policy is identified by Guard internal version 3 and
`TradingStrategyModuleV0` ABI version `v0.5`.

Stock Lagoon v0.5 does not expose one public value covering the gross underlying
movement of both deposit and redemption queues, and it always settles a snapshotted
queue in full. `GuardV0Base` therefore asks `LagoonLib` for an opaque snapshot of the
relevant underlying balances immediately before the Safe call and routes it back to
the library after execution:

```text
deposit assets = Silo balance before - Silo balance after
redeem assets  = vault balance after - vault balance before
gross amount   = deposit assets + redeem assets
```

The complete transaction reverts when `gross amount > maxSettlementAmount`, rolling
back Lagoon accounting and all token transfers. This is a reject policy, not partial
settlement. Governance may recover an oversized queue with a direct Safe transaction.
Direct Safe transactions intentionally bypass module policy.

A per-call amount limit would still let an asset manager submit several individually
valid non-zero settlements to drain the vault. After every successful capped settlement
with non-zero gross movement, `LagoonLib` records the block timestamp and rejects
another non-zero asset-manager settlement until the configured positive cooldown has
elapsed. Because the gross amount is only known after Lagoon executes, this check runs
in atomic post-call validation: rejection rolls back the Safe and Lagoon transaction.
Empty settlements do not start or extend the cooldown and remain callable while it is
active. Rejected or reverted settlements also leave cooldown state unchanged. The
onchain and Python API default is 86,400 seconds (24 hours).

`TradingStrategyModuleV0` only carries a generic post-call validation context around
Safe execution. Validator selection is a hardcoded `GuardV0Base` enum and dispatcher;
there is no governance-configurable plugin or arbitrary validator address. Future
vault integrations can add another reviewed validator kind without adding
protocol-specific execution code to the module.

Re-calling `whitelistLagoonWithSettlementLimit()` updates an existing cap and applies
the 24-hour cooldown default. `whitelistLagoonWithSettlementLimitAndCooldown()` accepts
an explicit positive cooldown. Calling the backwards-compatible `whitelistLagoon()`
resets the vault to unlimited mode. The cap is stored in raw underlying-token units
onchain; Python deployment configuration accepts a human-readable `Decimal` and
performs the conversion.

The balance-envelope guarantee assumes a conventional non-rebasing token without
transfer fees. It does not validate the `_newTotalAssets` settlement argument, which is
the proposed Lagoon NAV rather than a transfer amount.

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

## Reading back guard configuration

The Python module
[`config_event_scanner`](../../eth_defi/erc_4626/vault_protocol/lagoon/config_event_scanner.py)
decodes the full cross-chain guard configuration by scanning on-chain events
emitted during deployment. It follows CCTP destination chains automatically
to build a multichain picture.

Diagnostic script:

```shell
source .local-test.env
export CHAIN_ID=42161
export SAFE_ADDRESS=0x...
poetry run python scripts/lagoon/read-guard-config.py
```

See [`scripts/lagoon/read-guard-config.py`](../../scripts/lagoon/read-guard-config.py) for details.

**Note:** When adding new guard configuration events (in GuardV0Base or any linked library),
update `GUARD_CONFIG_EVENT_NAMES` and `_build_chain_config()` in `config_event_scanner.py`.

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
| [test_guard_simple_vault_csigma.py](../../tests/guard/test_guard_simple_vault_csigma.py) | cSigma mainnet-fork approval/deposit/redeem lifecycle, including receiver and owner validation |
| [test_guard_simple_vault_forty_acres.py](../../tests/guard/test_guard_simple_vault_forty_acres.py) | 40acres Aerodrome USDC approval/deposit/redeem lifecycle with non-governance asset manager and receiver/owner rejection |
| [test_guard_simple_vault_lagoon.py](../../tests/guard/test_guard_simple_vault_lagoon.py) | Lagoon ERC-7540 request, real-liquidity settlement and claim on a Base fork |
| [test_guard_async_mock_settlement.py](../../tests/guard/test_guard_async_mock_settlement.py) | Protocol-shaped settlement mocks, event/payout parsing, and YieldNest's explicit mock-only liquidity override before guarded redemption |
| [test_guard_simple_vault_one_delta.py](../../tests/guard/test_guard_simple_vault_one_delta.py) | 1delta leveraged trading guard validation |
| [test_guard_gmx_validation.py](../../tests/guard/test_guard_gmx_validation.py) | GMX V2 multicall validation, market/router whitelisting |
| [test_guard_simple_vault_hypercore.py](../../tests/guard/test_guard_simple_vault_hypercore.py) | Hypercore vault guard validation |
| [test_guard_hypercore_vault_lagoon.py](../../tests/guard/test_guard_hypercore_vault_lagoon.py) | Full Lagoon vault with Hypercore integration |
| [test_lagoon_config_event_scanner.py](../../tests/lagoon/test_lagoon_config_event_scanner.py) | Multichain guard config event scanning and decoding |

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
3. Deploy and link the required protocol libraries (UniswapLib, CowSwapLib, VeloraLib, GmxLib, HypercoreVaultLib, LagoonLib)
4. Deploy the GuardV0-based TradingStrategyModuleV0 and enable it as a Safe module
5. Whitelist routers, assets, and protocol-specific contracts
6. Optionally verify all contracts on Etherscan/Blockscout/Sourcify

See [`eth_defi.erc_4626.vault_protocol.lagoon.deployment`](../../eth_defi/erc_4626/vault_protocol/lagoon/deployment.py)
for the full source and [`LagoonConfig`](../../eth_defi/erc_4626/vault_protocol/lagoon/deployment.py) for configuration options.

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
