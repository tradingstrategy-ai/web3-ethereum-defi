"""Multicall balance tests.

You must have a live BNB Chain node URL to run these tests:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_balances_multicall

"""
import json
import os
from decimal import Decimal
from typing import Dict

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3, HTTPProvider
from web3.contract import Contract

from eth_hentai.balances import (
    fetch_erc20_balances_by_token_list,
    fetch_erc20_balances_by_transfer_event, fetch_erc20_balances_by_multicall,
)
from eth_hentai.token import create_token




@pytest.fixture
def web3(ganache_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    return Web3(HTTPProvider(mainnet_rpc))


@pytest.fixture
def token_data() -> Dict[str, str]:
    """Get test token list.

    address -> symbol mapping
    """
    path = os.path.join(os.path.dirname(__file__), "bnb-chain-token-list.json")
    with open(path, "rt") as inp:
        token_list = json.read(inp)
    return token_list


def account_with_multiple_tokens() -> HexAddress:
    """A random account containing a lot of tokens,

    `To find large holder accounts, use bscscan <https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))



def test_fetch_balances_multicall(
    web3: Web3,
    token_data: dict,
    account_with_multiple_tokens: HexAddress,

):
    """Read live balances of an address from the production BNB Chain node.

    We test against live node to
    """
    token_addresses = list(token_data.keys())
    fetch_erc20_balances_by_multicall(
        web3,
        owner=account_with_multiple_tokens,
        token_addresses
    )


