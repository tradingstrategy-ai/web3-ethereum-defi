# Guard and vault (prototype)

This is a simple implementation of a guard smart contract and a vault smart contract

- [GuardV0](./src/GuardV0.sol) can check whether an asset manager is allowed to do an action on behalf of the asset owners 
- [SimpleVaultV0](./src/SimpleVaultV0.sol) is an example vault implementation with two roles
  - Owner (who can withdraw assets)
  - Asset manager (who can decide on trades)

This code is prototype code for Trading Strategy Protocol Minimal Viable Product version
and not indented for wider distribution.

## Guard activities

Guard will check for activities asset manager perform, all of them which need to be whitelisted by the owner: 
- Any smart contract call (contract address, selector)
- Whitelisted token (asset manager cannot trade into an unsupported token)
- Withdrawal (transfer) of assets - assets can be only withdraw back to the owner
- Uniswap v2 router swaps (approval + swap path)

Guard can be used independently from the vault implementation.
It can be used with any asset management protocol e.g. Enzyme.

## Simple vault

- The vault has a guard, an asset manager and an owner
- Initially the vault is configured to allow withdrawals to the owner
- Enabling asset manager allows perform trades
- Each token needs to be separately whitelisted
- Each router needs to be separately whitelisted

Simple vault can be used as a layer of protection for cases where the hot wallet private key
of the asset manager is compromised (asset manager can only perform legit trades, not withdraw any assets).

## Supported protocols

- Uniswap v2 compatibles
- Uniswap v3 compatibles
- Aave v3 compatibles (coming)
- 1delta (coming)

## Development

Compiling

```shell
foundry build
```