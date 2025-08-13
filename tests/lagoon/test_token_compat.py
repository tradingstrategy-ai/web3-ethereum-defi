import os

import pytest

from eth_typing import HexAddress

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDT_WHALE

JSON_RPC_BINANCE = os.environ.get("JSON_RPC_BINANCE", None)
pytestmark = pytest.mark.skipif(not JSON_RPC_BINANCE, reason="JSON_RPC_BINANCE not set, skipping BNB smart chain tests")


@pytest.fixture()
def usdt_holder() -> HexAddress:
    # https://bscscan.com/token/0x55d398326f99059ff775485246999027b3197955#readContract
    # https://bscscan.com/token/0x55d398326f99059ff775485246999027b3197955#balances
    return addr("0xF977814e90dA44bFA03b6295A0616a897441aceC")


@pytest.fixture()
def web3(anvil_binance_fork) -> Web3:
    """Create a web3 connector.

    - By default use Anvil forked Base

    - Eanble Tenderly testnet with `JSON_RPC_TENDERLY` to debug
      otherwise impossible to debug Gnosis Safe transactions
    """

    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_binance_fork.json_rpc_url,
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
        )
    assert web3.eth.chain_id == 56
    return web3


@pytest.fixture()
def usdt(web3) -> TokenDetails:
    return fetch_erc20_details(web3, USDT_WHALE[web3.eth.chain_id])


@pytest.fixture()
def token_list(web3) -> list[HexAddress]:
    """List of different tokens to test token compatibility."""


@pytest.fixture()
def vault(web3) -> LagoonVault:
    return LagoonVault(
        web3,
        vault_address
    )


def test_token_compat():
    check_multiple_tokens()



