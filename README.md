[![PyPI version](https://badge.fury.io/py/web3-ethereum-defi.svg)](https://badge.fury.io/py/web3-ethereum-defi)

[![Automated test suite](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml/badge.svg)](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml)

[![Documentation Status](https://readthedocs.org/projects/web3-ethereum-defi/badge/?version=latest)](https://web3-ethereum-defi.readthedocs.io/)

# Web3-Ethereum-Defi

This project contains high level Python API for smart contracts, 
DeFi trading, wallet management, automated test suites and backend integrations on EVM based blockchains.
Supported blockchains include Ethereum, BNB Chain, Polygon, Avalanche C-chain, Arbitrum, others.
 
* [Use Cases](#use-cases)
* [Features](#features)
* [Prerequisites](#prerequisites)
* [Install](#install)
* [Code examples](#code-examples)
   * [Deploy and transfer ERC-20 token between wallets](#deploy-and-transfer-erc-20-token-between-wallets)
   * [Uniswap v2 trade example](#uniswap-v2-trade-example)
   * [Uniswap v2 price estimation example](#uniswap-v2-price-estimation-example)
* [How to use the library in your Python project](#how-to-use-the-library-in-your-python-project)
* [Documentation](#documentation)
* [Development and contributing](#development-and-contributing)
* [Version history](#version-history)
* [Support](#support)
* [Social media](#social-media)
* [Notes](#notes)
* [History](#history)
* [License](#license)

![Pepe chooses Web3-Ethereum-DeFi and Python](https://raw.githubusercontent.com/tradingstrategy-ai/web3-ethereum-defi/master/docs/source/_static/pepe.jpg)

**Pepe chooses web3-ethereum-defi and Python**.

# Use Cases

* Web3 development
* DeFi trading
* Market data services
* On-chain data research
* Ethereum integration: token payments, hot wallets, monitors and such 

# Features

Features include 

* [Made for 99% developers](https://future.a16z.com/software-development-building-for-99-developers/)
* [High-quality API documentation](https://web3-ethereum-defi.readthedocs.io/)
* [Fully type hinted](https://web3-ethereum-defi.readthedocs.io/) for good developer experience
* [Parallel transaction execution](https://web3-ethereum-defi.readthedocs.io/en/latest/_autosummary/eth_defi.txmonitor.html)
* [Mainnet forking with Anvil](https://web3-ethereum-defi.readthedocs.io/api/_autosummary/eth_defi.anvil.html#module-eth_defi.anvil)
* [Solidity stack traces](https://web3-ethereum-defi.readthedocs.io/api/_autosummary/eth_defi.trace.html)
* [Trading](https://web3-ethereum-defi.readthedocs.io/api/index.html)
* [Loans](https://web3-ethereum-defi.readthedocs.io/api/index.html)
* [ERC-20 token issuance and manipulation](https://web3-ethereum-defi.readthedocs.io/en/latest/_autosummary/eth_defi.token.html#module-eth_defi.token)

Web3-Ethereum-Defi supports 

* Uniswap (both v2 and v3)
* Sushi
* Aave 
* Enzyme Protocol
* dHEDGE Protocol
* More integrations to come
* Built-in integration for over 600 smart contracts with precompiled Solidity ABI files 

[Read the full API documentation](https://web3-ethereum-defi.readthedocs.io/)).
For code examples please see below.

# Prerequisites

To use this package you need to

* Have Python 3.10 or higher
* [Be proficient in Python programming](https://wiki.python.org/moin/BeginnersGuide)
* [Understand of Web3.py library](https://web3py.readthedocs.io/en/stable/) 
* [Understand Pytest basics](https://docs.pytest.org/)

# Install

With `pip`:

```shell
pip install "web3-ethereum-defi[data]"
```

With `poetry`:

```shell
# Poetry version
poetry add -E data web3-ethereum-defi
```

With `poetry` - master Git branch: 

```shell
git clone git@github.com:tradingstrategy-ai/web3-ethereum-defi.git
cd web3-ethereum-defi
poetry shell
poetry install -E data -E docs 
```



# Code examples

For more code examples, see [the tutorials section in the documentation](https://web3-ethereum-defi.readthedocs.io/tutorials/index.html).  

## Deploy and transfer ERC-20 token between wallets

To use the package to deploy a simple ERC-20 token in [pytest](https://docs.pytest.org/) testing:

```python
import pytest
from web3 import Web3, EthereumTesterProvider

from eth_defi.token import create_token


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
  token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10 ** 18)
  assert token.functions.name().call() == "Hentai books token"
  assert token.functions.symbol().call() == "HENTAI"
  assert token.functions.totalSupply().call() == 100_000 * 10 ** 18
  assert token.functions.decimals().call() == 18


def test_tranfer_tokens_between_users(web3: Web3, deployer: str, fund_owner, fund_client):
  """Transfer tokens between users."""
  token = create_token(web3, deployer, "Telos EVM rocks", "TELOS", 100_000 * 10 ** 18)

  # Move 10 tokens from deployer to user1
  token.functions.transfer(fund_owner, 10 * 10 ** 18).transact({"from": deployer})
  assert token.functions.balanceOf(fund_owner).call() == 10 * 10 ** 18

  # Move 10 tokens from deployer to user1
  token.functions.transfer(fund_client, 6 * 10 ** 18).transact({"from": fund_owner})
  assert token.functions.balanceOf(fund_owner).call() == 4 * 10 ** 18
  assert token.functions.balanceOf(fund_client).call() == 6 * 10 ** 18
```

[See full example](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_token.py).

[For more information how to user Web3.py in testing, see Web3.py documentation](https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python).

## Uniswap v2 trade example

```python
import pytest
from web3 import Web3
from web3.contract import Contract

from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, deploy_trading_pair, FOREVER_DEADLINE


def test_swap(web3: Web3, deployer: str, fund_owner, uniswap_v2: UniswapV2Deployment, weth: Contract, usdc: Contract):
    """User buys WETH on Uniswap v2 using mock USDC."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10 ** 18,  # 10 ETH liquidity
        17_000 * 10 ** 18,  # 17000 USDC liquidity
    )

    router = uniswap_v2.router

    # Give user_1 500 dollars to buy ETH and approve it on the router
    usdc_amount_to_pay = 500 * 10 ** 18
    usdc.functions.transfer(fund_owner, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": fund_owner})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]  # Path tell how the swap is routed
    # https://docs.uniswap.org/protocol/V2/reference/smart-contracts/router-02#swapexacttokensfortokens
    router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        fund_owner,
        FOREVER_DEADLINE,
    ).transact({
        "from": fund_owner
    })

    # Check the user_1 received ~0.284 ethers
    assert weth.functions.balanceOf(fund_owner).call() / 1e18 == pytest.approx(0.28488156127668085)
```

[See the full example](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_uniswap_v2_pair.py).

## Uniswap v2 price estimation example

```python
# Create the trading pair and add initial liquidity
deploy_trading_pair(
    web3,
    deployer,
    uniswap_v2,
    weth,
    usdc,
    1_000 * 10**18,  # 1000 ETH liquidity
    1_700_000 * 10**18,  # 1.7M USDC liquidity
)

# Estimate the price of buying 1 ETH
usdc_per_eth = estimate_buy_price_decimals(
    uniswap_v2,
    weth.address,
    usdc.address,
    Decimal(1.0),
)
assert usdc_per_eth == pytest.approx(Decimal(1706.82216820632059904))
```

# How to use the library in your Python project

Add `web3-ethereum-defi` as a development dependency:

Using [Poetry](https://python-poetry.org/):

```shell
# Data optional dependencies include pandas and gql, needed to fetch Uniswap v3 data
poetry add -D "web3-ethereum-defi[data]"
```

# Documentation

- [Browse API documentation](https://web3-ethereum-defi.readthedocs.io/).
- [Browse tutorials](https://web3-ethereum-defi.readthedocs.io/tutorials/index.html).

# Development and contributing

- [Read development instructions](https://web3-ethereum-defi.readthedocs.io/development.html).

# Version history

- [Read changelog](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/CHANGELOG.md).
- [See releases](https://pypi.org/project/web3-ethereum-defi/#history).

# Support 

- [Join Discord for any questions](https://tradingstrategy.ai/community).

# Social media

- [Follow on Twitter](https://twitter.com/TradingProtocol)
- [Follow on Telegram](https://t.me/trading_protocol)
- [Follow on LinkedIn](https://www.linkedin.com/company/trading-strategy/)

# History

[Originally created for Trading Strategy](https://tradingstrategy.ai). 
[Originally the package was known as eth-hentai](https://raw.githubusercontent.com/tradingstrategy-ai/web3-ethereum-defi/master/docs/source/_static/hentai_teacher_mikisugi_by_ilmaris_d6tjrn8-fullview.jpg).

# License 

MIT
