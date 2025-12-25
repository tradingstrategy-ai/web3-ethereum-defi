# Web3-Eth-Defi integration contracts

This Foundry project contains various contracts for integration and testing other protocols.
It's unlikely you want to use any of these contracts directly.
 
[See Web3-Ethereum-Defi project for full documentation](https://web3-ethereum-defi.readthedocs.io/).

## Solidity version

- We are stuck 0.6.12 because of Enzyme dependency of AdapterBase
- We have flattened out a local copy of AdapterBase to the source tree,
  because the original Enzyme hardhat compilation toolchain is too difficult to install and run in many environments,
  like Docker

## Compile

```shell
# Proceed with our own project
cd contracts/in-house
forge build
```

When Foundry has internal issues

```shell
RUST_LOG=forge,foundry_evm,backend forge build --force
```

## Deploying USDC payment forwarder

**Note**: Now this step is automated by `trade-executor enzyme-deploy-vault command`.

The repository contains an example contract for USDC payment relay using EIP-3009 approve() free
and gasless transactions.

- Uses EIP-3009 `receiveWithAuthorization()` (can be easily modified for `transferWithAuthorization()`)

- First deploy your Enzyme vault

- Then deploy the USDC payment relay

```shell
# Address of deployed vault comptroller contract for the vault contract
#
# You can get this from vault.getAccessor() call
#
export VAULT_COMPTROLLER=

# Deployer account
export PRIVATE_KEY=

# USDC on Polygon
export USDC_TOKEN=0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174

# Used node
export JSON_RPC_POLYGON=

# EtherScan API key
export POLYGONSCAN_API_KEY=

# Wrapped as a shells script, as Bash will check that all variables are set 
scripts/deploy-usdc-payment-forwarder.sh

export CONTRACT_ADDRESS=...

# Manually verify because Forge automatic non-flattened verify fails
scripts/manual-verify-usdc-payment-forwarder.sh
```

For more information see [unit tests](../../tests/enzyme/test_enzyme_usdc_payment_forwarder.py).

[See information regarding forge and the issue of verifying this contract](https://github.com/foundry-rs/foundry/issues/5003).

## More information

- [Deploying with Forge](https://book.getfoundry.sh/forge/deploying)