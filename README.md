# Mock smart contracts

A repository of common Ethereum smart contracts to be used in the unit test deployments. 
This was created for [Trading Stratetgy](https://tradingstrategy.ai), but can be used for any other 
projects as well.

As opposite to mainnet forking strategies, this project aims to explicit deployments and speed of test execution.
It grabs popular ABI files with their bytecode anbd compilation artifacts so that the contracts
are easily deployable on any Ethereum tester interface.

Smart contracts include 

* ERC-20 token
* [SushiSwap]https://github.com/sushiswap/sushiswap): router, factory, pool
* (More to come)

# Distribute precompiled ABI files

You can find Solidity ABI files containing bytecode in [abi]` folder.

# Building

Requires

* Node v14 
* npx 
* yarn
* GNU Make
* Unix shell

The sources of the smart contacts are available as 
 
The compiled Solidity contracts will be created in `dist` folder

To build:

```shell
git submodule update --recursive --init
make
```

[See SushiSwap continuous integration files for more information](https://github.com/sushiswap/sushiswap/blob/canary/.github/workflows/sushiswap.yml).

# Python support

The Python support is available as `smart_contract_test_fixtures` Python package.

To create an example environment:

```python

```

[For more information how to user Web3.py in testing, see Web3.py documentation](https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python).

# License 

MIT