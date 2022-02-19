[![Automated test suite](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/actions/workflows/tests.yml/badge.svg)](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/actions/workflows/tests.yml)

[![Documentation Status](https://readthedocs.org/projects/smart-contracts-for-testing/badge/?version=latest)](https://smart-contracts-for-testing.readthedocs.io/en/latest/?badge=latest)

# ETH-Hentai 

This package contains common Ethereum smart contracts, and related utilities, designed
for automated testing, interaction from backends and building trading bots.  

[![ETH-Hentai](./docs/source/_static/hentai_teacher_mikisugi_by_ilmaris_d6tjrn8-fullview.jpg)](https://www.deviantart.com/ilmaris)

Features include 

* [Made for 99% developers](https://future.a16z.com/software-development-building-for-99-developers/)
* [High-quality API documentation](https://smart-contracts-for-testing.readthedocs.io/)
* [Fully type hinted](https://smart-contracts-for-testing.readthedocs.io/) for good developer experience
* [ERC-20 token issuance and manipulation](https://smart-contracts-for-testing.readthedocs.io/en/latest/token.html)
* [Uniswap v2 tools](https://github.com/sushiswap/sushiswap): deployment, trading, price estimation for Sushiswap, PancakeSwape, QuickSwap, Trader Joe, others
* [Parallel transaction execution](https://smart-contracts-for-testing.readthedocs.io/en/latest/txmonitor.html)
* As opposite to slower and messier [mainnet forking workflows](https://www.quicknode.com/guides/web3-sdks/how-to-fork-ethereum-blockchain-with-ganache), 
this project aims to explicit clean deployments and very fast test execution.
* (More integrations to come)

Table of contents

* [Precompiled ABI file distribution](#precompiled-abi-file-distribution)
* [Python usage](#python-usage)
   * [Prerequisites](#prerequisites)
   * [ERC-20 token example](#erc-20-token-example)
   * [Uniswap swap example](#uniswap-swap-example)
   * [How to use hhe library in your Python project](#how-to-use-hhe-library-in-your-python-project)
* [Development](#development)
   * [Requires](#requires)
   * [Make](#make)
* [Version history](#version-history)
* [Discord](#discord)
* [Notes](#notes)
* [License](#license)

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

Just download and embed in your project. 
The compiled source code files are mixture of MIT and GPL v2 license.

# Python usage

The Python support is available as `smart_contracts_for_testing` Python package.

The package depends only on [web3.py](github.com/ethereum/web3.py) and not others, like [Brownie](https://eth-brownie.readthedocs.io/).
It grabs popular ABI files with their bytecode and compilation artifacts so that the contracts
are easily deployable on any Ethereum tester interface. No Ganache is needed and everything
can be executed on faster [eth-tester enginer](https://github.com/ethereum/eth-tester).

Unlike Brownie, which is a framework, `smart_contracts_for_testing` is a library. It is designed
to be included in any other Python application and you can only use bits of its that you need.
There are no expectations on configuration files or folder structure.

[Read the full API documnetation]([High-quality API documentation](https://smart-contracts-for-testing.readthedocs.io/)).
For code examples please see below.

## Prerequisites

* [Proficient in Python programming](https://wiki.python.org/moin/BeginnersGuide)
* [Understanding of Web3.py library](https://web3py.readthedocs.io/en/stable/) 
* [pytest basics](https://docs.pytest.org/)

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

## Uniswap swap example

```python
import pytest
from web3 import Web3
from web3.contract import Contract

from smart_contracts_for_testing.uniswap_v2 import UniswapV2Deployment, deploy_trading_pair, FOREVER_DEADLINE


def test_swap(web3: Web3, deployer: str, user_1: str, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """User buys WETH on Uniswap v2 using mock USDC."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    router = uniswap_v2.router

    # Give user_1 500 dollars to buy ETH and approve it on the router
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]  # Path tell how the swap is routed
    # https://docs.uniswap.org/protocol/V2/reference/smart-contracts/router-02#swapexacttokensfortokens
    router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({
        "from": user_1
    })

    # Check the user_1 received ~0.284 ethers
    assert weth.functions.balanceOf(user_1).call() / 1e18 == pytest.approx(0.28488156127668085)
```

[See the full example](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/blob/master/tests/test_uniswap_v2_pair.py).

## How to use the library in your Python project

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

To build the ABI distribution:

```shell
git submodule update --recursive --init
make all
```

[See SushiSwap continuous integration files for more information](https://github.com/sushiswap/sushiswap/blob/canary/.github/workflows/sushiswap.yml).

# Version history

[See change log](https://github.com/tradingstrategy-ai/smart-contracts-for-testing/blob/master/CHANGELOG.md).

# Discord

[Join Discord for any questions](https://tradingstrategy.ai/community).

# Notes

Currently there is no [Brownie](https://eth-brownie.readthedocs.io/) support.
To support Brownie, one would need to figure out how to import an existing Hardhat
based project (Sushiswap) to Brownie project format.

Cover art by [Ilmaris](https://www.deviantart.com/ilmaris).

# History

[Originally created for Trading Strategy](https://tradingstrategy.ai).

# License 

MIT