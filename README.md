[![Automated test suite](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/actions/workflows/tests.yml/badge.svg)](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/actions/workflows/tests.yml)

# Mock smart contracts for writing Ethereum test suites

This package contains common Ethereum smart contracts to be used in automated test suites. 
This was created for [Trading Strategy](https://tradingstrategy.ai), but can be used for any other 
projects as well.

As opposite to mainnet forking strategies, this project aims to explicit deployments and speed of test execution.
It grabs popular ABI files with their bytecode and compilation artifacts so that the contracts
are easily deployable on any Ethereum tester interface.

Smart contracts include 

* ERC-20 token
* [SushiSwap](https://github.com/sushiswap/sushiswap): router, factory, pool
* (More to come)

# Precompiled ABI file distribution

This package primarly supports Python, Web3.p3 and Brownie developers.
For other programming languages and frameworks,
you can [find precompiled Solidity smart contracts in abi folder](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/tree/master/smart_contracts_for_testing/abi).

These files are good to go with any framework:
* Web3.js
* Ethers.js
* Hardhat
* Truffle
* Web3j

Each JSON file has `abi` and `bytecode` keys you need to deploy a contract.

Just download and embed in your project. All compiled source code files are under MIT license.

# Python examples

The Python support is available as `smart_contract_test_fixtures` Python package.
The package depends only on [web3.py](github.com/ethereum/web3.py) and not others, like Brownie.

## Features

* Documented functions
* Full type hinting support

## ERC-20 token example

To use the package to deploy a simple ERC-20 token in [pytest](https://docs.pytest.org/) testing: 

```python
import pytest
from web3 import Web3, EthereumTesterProvider

from smart_contracts_for_testing.token import create_token


@pytest.fixture
def tester_provider():
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider):
    return tester_provider.ethereum_tester


@pytest.fixture
def web3(tester_provider):
    return Web3(tester_provider)


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account."""
    return web3.eth.accounts[0]


@pytest.fixture()
def user_1(web3) -> str:
    """User account."""
    return web3.eth.accounts[1]


@pytest.fixture()
def user_2(web3) -> str:
    """User account."""
    return web3.eth.accounts[2]


def test_deploy_token(web3: Web3, deployer: str):
    """Deploy mock ERC-20."""
    token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18)
    assert token.functions.name().call() == "Hentai books token"
    assert token.functions.symbol().call() == "HENTAI"
    assert token.functions.totalSupply().call() == 100_000 * 10**18
    assert token.functions.decimals().call() == 18


def test_tranfer_tokens_between_users(web3: Web3, deployer: str, user_1: str, user_2: str):
    """Transfer tokens between users."""
    token = create_token(web3, deployer, "Telos EVM rocks", "TELOS", 100_000 * 10**18)

    # Move 10 tokens from deployer to user1
    token.functions.transfer(user_1, 10 * 10**18).transact({"from": deployer})
    assert token.functions.balanceOf(user_1).call() == 10 * 10**18

    # Move 10 tokens from deployer to user1
    token.functions.transfer(user_2, 6 * 10**18).transact({"from": user_1})
    assert token.functions.balanceOf(user_1).call() == 4 * 10**18
    assert token.functions.balanceOf(user_2).call() == 6 * 10**18
```

[See full example](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/blob/master/tests/test_token.py).

[For more information how to user Web3.py in testing, see Web3.py documentation](https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python).

## Use in your Python project

Add `smart_contract_test_fixtures` as a development dependency:

Using [Poetry](https://python-poetry.org/):

```shell
poetry add -D smart_contract_test_fixtures
```

# Development

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

# Version history

[See change log](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/blob/master/CHANGELOG.md).

# Discord

[Join Discord for any questions](https://tradingstrategy.ai/community).

# License 

MIT