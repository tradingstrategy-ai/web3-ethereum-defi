"""MultiProviderWeb3 configuration tests."""

import os

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.chain import has_graphql_support
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderConfigurationError
from eth_defi.provider.named import get_provider_name
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.abi import ZERO_ADDRESS
from eth_defi.tx import get_tx_broadcast_data


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


def test_multi_provider_mev_and_fallback():
    """Configure complex Web3 instance correctly."""
    config = """ 
    mev+https://rpc.mevblocker.io
    https://polygon-rpc.com
    https://bsc-dataseed2.bnbchain.org
    """
    web3 = create_multi_provider_web3(config)
    assert "fallbacks" in get_provider_name(web3.get_fallback_provider())
    assert len(web3.get_fallback_provider().providers) == 2
    assert get_provider_name(web3.get_active_transact_provider()) == "rpc.mevblocker.io"
    assert web3.eth.block_number > 0

    mev_blocker = web3.get_configured_transact_provider()
    assert mev_blocker.provider_counter == {"call": 3, "transact": 0}


@pytest.mark.skip(reason="polygon-rpc.com is unreliable public RPC")
def test_multi_provider_fallback_only():
    config = """
    https://polygon-rpc.com
    """
    web3 = create_multi_provider_web3(config)
    assert "polygon-rpc.com" in get_provider_name(web3.get_fallback_provider())


def test_multi_provider_no_graphql():
    """GraphQL check for multi provider config"""
    config = """ 
    mev+https://rpc.mevblocker.io
    https://polygon-rpc.com
    https://bsc-dataseed2.bnbchain.org
    """

    # Public Polygon RPC does not support GraphQL
    web3 = create_multi_provider_web3(config)
    assert not has_graphql_support(web3.provider)


@pytest.mark.skipif(
    os.environ.get("JSON_RPC_POLYGON_PRIVATE") is None,
    reason="Set JSON_RPC_POLYGON_PRIVATE environment variable to a privately configured Polygon node with GraphQL turned on",
)
def test_multi_provider_no_graphql():
    """GraphQL check for multi provider config"""
    config = f"""{os.environ["JSON_RPC_POLYGON_PRIVATE"]}"""

    # Public Polygon RPC does not support GraphQL
    web3 = create_multi_provider_web3(config)
    assert has_graphql_support(web3.provider)


def test_multi_provider_empty_config():
    """Cannot start with empty config."""
    config = """
    """
    with pytest.raises(MultiProviderConfigurationError):
        create_multi_provider_web3(config)


def test_multi_provider_bad_url():
    """Cannot start with bad urls config."""
    config = """
    mev+https:/rpc.mevblocker.io
    polygon-rpc.com    
    """
    with pytest.raises(MultiProviderConfigurationError):
        create_multi_provider_web3(config)


CI = os.environ.get("CI") == "true"


@pytest.mark.skipif(CI, reason="polygon-rpc.com is unreliable public RPC on CI")
def test_multi_provider_transact(anvil):
    """See we use MEV Blocker for doing transactions."""

    # Use Anvil as MEV blocker instance
    config = f""" 
    mev+{anvil.json_rpc_url}
    https://polygon-rpc.com
    """

    web3 = create_multi_provider_web3(config)

    # Need to connect to Anvil directly
    anvil_web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    wallet = HotWallet.create_for_testing(anvil_web3)

    signed_tx = wallet.sign_transaction_with_new_nonce(
        {
            "from": wallet.address,
            "to": ZERO_ADDRESS,
            "value": 1,
            "gas": 100_000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    raw_bytes = get_tx_broadcast_data(signed_tx)
    tx_hash = web3.eth.send_raw_transaction(raw_bytes)
    assert_transaction_success_with_explanation(anvil_web3, tx_hash)

    mev_blocker = web3.get_configured_transact_provider()
    assert mev_blocker.provider_counter == {"call": 3, "transact": 1}
