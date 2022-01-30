# Mock smart contracts for writing Ethereum test suites

A repository of common Ethereum smart contracts to be used in the unit test deployments. 
This was created for [Trading Stratetgy](https://tradingstrategy.ai), but can be used for any other 
projects as well.

As opposite to mainnet forking strategies, this project aims to explicit deployments and speed of test execution.
It grabs popular ABI files with their bytecode and compilation artifacts so that the contracts
are easily deployable on any Ethereum tester interface.

Smart contracts include 

* ERC-20 token
* [SushiSwap](https://github.com/sushiswap/sushiswap): router, factory, pool
* (More to come)

# Precompiled ABI file distribution

You can find Solidity ABI files containing bytecode in (abi)[] folder.

These files are good to go with any framework:
* Web3.js
* Ethers.js
* Hardhat
* Truffle
* Web3j

Just download and embed with your project.

# Building

This step will extract compiled smart contract from Sushiswap repository. 

## Requires

* Node v14 
* npx 
* yarn
* GNU Make
* Unix shell

## Make

To build:

```shell
git submodule update --recursive --init
make
```

[See SushiSwap continuous integration files for more information](https://github.com/sushiswap/sushiswap/blob/canary/.github/workflows/sushiswap.yml).

# Python support

The Python support is available as `smart_contract_test_fixtures` Python package.
The package depends only on [web3.py](github.com/ethereum/web3.py) and not others, like Brownie.

## Features

* Documented functions
* Full type hinting support

## Token example

To use the package to deploy a simple ERC-20 token in [pytest](https://docs.pytest.org/) testing: 

```python

```

[For more information how to user Web3.py in testing, see Web3.py documentation](https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python).

## Use in your Python project

Add `smart_contract_test_fixtures` as a development dependency:

Poetry:

```shell
poetry add -D smart_contract_test_fixtures
```

# Discord

[Join Discord for any questions](https://tradingstrategy.ai/community).

# License 

MIT