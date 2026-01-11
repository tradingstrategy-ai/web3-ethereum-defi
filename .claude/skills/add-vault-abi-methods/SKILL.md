---
name: add-vault-abi-methods
description: Expose vault smart contract ABI methods in the vault class
---

# Add vault ABI methods

This is a skill description to add smart contract 

## Inputs

1. Vault protocol name
2. Smart contract address and a blockchain as a blockchain explorer link
3. What methods to cover

## Vault classes

Vault protocol classes can be found in `eth_defi/erc_4626/vault_protocol` folder. Each vault protocol has its own class. There are 40+ protocols. Some protocol share classes and extend other protocols.

## Smart contracts

Each vault class wraps a JSON-RPC calls to an Ethereum smart contract. These smart contracts are described by ABI files in `eth_defi/abi` folder.

# Step 1) Identify the proxy class

Identify the proxy class used for the vault protocol.

- Identify the proxy class under `eth_defi/erc_4626/vault_protocol` folder.
- The protocol might not have a proxy class. In this case report this and abort.

# Step 2) Identify ABI file

Identify ABI file we should use for the task.

- Check folder `eth_defi/abi` for already downloded ABI files
- Check if the ABI files we are similar. If they are not, summarise the differences and ask the user how to proceed, then stop.

# Step 3) Add a ABI file loading to the vault protocol proxy class

See `YearnV3Vault.vault_contract` property as an example.

# Step 4) Find methods relevant for the task

- Read ABI file 
- Read protocol documentation
- Identify relevant smart contract methods
- 

# Step 5) Create accessor methods

See `IPORVault.get_performance_fee()` method as an example.

- How to call the smart contract 
- How to interpret the result

# Step 6) Write test case

See `test_ipor_fee` test case as an example.

- Write the test using Anvil mainnet fork based method
- Use the latest block number for the named chain, use `get-block-number` skill if needed for the test case
- Run it
- Fix potential issues

# Step 7) Check regression

See we did not break anything by accident.

Run all vault tests with the command:

```shell
source .local-test.env && poetry run pytest -n auto -k vault_protocol
```

# Step 8) Report results to back

- Findings on how the smart contract functions operate and how the results are translated
- Created files
- Created accessor methods