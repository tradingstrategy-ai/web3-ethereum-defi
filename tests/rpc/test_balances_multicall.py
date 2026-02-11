"""Read token balalances using multicall"""

import os
from decimal import Decimal

import pytest
from web3 import Web3
import flaky

from eth_defi.balances import fetch_erc20_balances_multicall, BalanceFetchFailed
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.mev_blocker import MEVBlockerProvider
from eth_defi.provider.multi_provider import create_multi_provider_web3


CI = os.environ.get("CI") == "true"

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE", "https://mainnet.base.org")

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def web3() -> Web3:
    """Create Web3 instance with middleware compatibility."""
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    assert web3.eth.chain_id == 8453
    return web3


@pytest.mark.skipif(CI, reason="Anvil hangs too much on Github CI")
def test_fetch_erc20_balances_multicall(web3):
    """Base mainnet based test to check multicall ERC-20 balance read works on base."""

    tokens = {
        "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
    }

    # Velvet vault
    address = "0x9d247fbc63e4d50b257be939a264d68758b43d04"

    block_number = get_almost_latest_block_number(web3)

    balances = fetch_erc20_balances_multicall(
        web3,
        address,
        tokens,
        block_identifier=block_number,
    )

    existing_dogmein_balance = balances["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    existing_usdc_balance = balances["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)


@pytest.mark.skipif(CI, reason="Anvil hangs too much on Github CI")
def test_fetch_erc20_balances_multicall_failure(web3):
    """Multicall ERC-20 with a broken token."""

    tokens = {
        "0x9d247fbc63e4d50b257be939a264d68758b43d04",  # Not a token
    }

    # Velvet vault
    address = "0x9d247fbc63e4d50b257be939a264d68758b43d04"

    block_number = get_almost_latest_block_number(web3)

    with pytest.raises(BalanceFetchFailed):
        fetch_erc20_balances_multicall(
            web3,
            address,
            tokens,
            block_identifier=block_number,
        )


# FAILED tests/rpc/test_balances_multicall.py::test_fetch_erc20_balances_multicall_mev_blocker - requests.exceptions.ConnectionError: HTTPConnectionPool(host='localhost', port=25226): Max retries exceeded with url: / (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x7fd1e2616300>: Failed to establish a new connection: [Errno 111] Connection refused'))
@pytest.mark.skipif(CI, reason="tests flaky on CI")
def test_fetch_erc20_balances_multicall_mev_blocker():
    """See fetch_erc20_balances_multicall() works with MEV blocker configurations."""

    # eth_call call should not hit this propvider
    mev_blocker_rpc = "mev+https://mainnet-sequencer.base.org"
    web3 = create_multi_provider_web3(f"{mev_blocker_rpc} {JSON_RPC_BASE}")

    assert isinstance(web3.provider, MEVBlockerProvider)

    tokens = {
        "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
    }

    # Velvet vault
    address = "0x9d247fbc63e4d50b257be939a264d68758b43d04"

    block_number = get_almost_latest_block_number(web3)

    balances = fetch_erc20_balances_multicall(
        web3,
        address,
        tokens,
        block_identifier=block_number,
    )

    existing_dogmein_balance = balances["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    existing_usdc_balance = balances["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)


# Add compatibility test to verify middleware works
def test_web3_middleware_compatibility():
    """Test that Web3 instance creation works with current middleware setup."""

    # This should not raise errors about middleware compatibility
    web3 = create_multi_provider_web3(JSON_RPC_BASE)

    # Basic functionality test
    chain_id = web3.eth.chain_id
    assert chain_id == 8453

    # Test that we can make a simple call
    latest_block = web3.eth.block_number
    assert latest_block > 0
