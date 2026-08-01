"""GMX on Arbitrum fixtures.

- Set up GMX CCXT adapter using Arbitrum configuration.
"""

import logging
import os

import pytest
from eth_account import Account
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from tests.gmx.conftest import _approve_tokens_for_config, _get_chain_config_with_tokens
from tests.gmx.fork_helpers import set_balance, setup_mock_oracle


def _fund_wallet_on_fork(
    web3: Web3,
    wallet_address: str,
    large_usdc_holder: str,
    large_weth_holder: str,
):
    """Fund wallet with ETH, WETH, and USDC on the given fork.

    The ``test_wallet`` fixture does not fund this fork, so this fixture
    establishes its own balances.

    :param web3: Web3 instance connected to the fork
    :param wallet_address: Address to fund
    :param large_usdc_holder: Address of USDC whale (must be unlocked in anvil)
    :param large_weth_holder: Address of WETH whale (must be unlocked in anvil)
    """
    # Fund with ETH for gas and execution fees
    eth_amount_wei = 100_000_000 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])

    # Fund whales with gas so they can transfer tokens
    gas_eth = 100_000_000 * 10**18
    web3.provider.make_request("anvil_setBalance", [large_usdc_holder, hex(gas_eth)])
    web3.provider.make_request("anvil_setBalance", [large_weth_holder, hex(gas_eth)])

    # Transfer WETH from whale
    config = _get_chain_config_with_tokens("arbitrum")
    weth_address = config["native_token_address"]
    weth = fetch_erc20_details(web3, weth_address)
    weth_amount = 100_000_000 * 10**18
    weth.contract.functions.transfer(wallet_address, weth_amount).transact(
        {"from": large_weth_holder},
    )

    # Transfer USDC from whale (required for short positions)
    usdc_address = config["usdc_address"]
    usdc = fetch_erc20_details(web3, usdc_address)
    usdc_amount = 100_000_000 * 10**6  # 100M USDC
    usdc.contract.functions.transfer(wallet_address, usdc_amount).transact(
        {"from": large_usdc_holder},
    )


@pytest.fixture()
def anvil_chain_fork_ccxt_long(
    anvil_chain_fork: AnvilLaunch,
) -> str:
    """Return the isolated fixed-block fork URL for a CCXT long-position test."""
    return anvil_chain_fork.json_rpc_url


@pytest.fixture()
def anvil_chain_fork_ccxt_short(
    anvil_chain_fork: AnvilLaunch,
) -> str:
    """Return the isolated fixed-block fork URL for a CCXT short-position test."""
    return anvil_chain_fork.json_rpc_url


@pytest.fixture()
def web3_arbitrum_fork_ccxt_long(anvil_chain_fork_ccxt_long: str) -> Web3:
    """Set up web3 for long position tests."""
    web3 = create_multi_provider_web3(
        anvil_chain_fork_ccxt_long,
        default_http_timeout=(3.0, 180.0),
    )
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture()
def web3_arbitrum_fork_ccxt_short(anvil_chain_fork_ccxt_short: str) -> Web3:
    """Set up web3 for short position tests."""
    web3 = create_multi_provider_web3(
        anvil_chain_fork_ccxt_short,
        default_http_timeout=(3.0, 180.0),
    )
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture
def ccxt_gmx_fork_open_close(
    web3_arbitrum_fork_ccxt_long,
    test_wallet,
    large_usdc_holder_arbitrum,
    large_weth_holder_arbitrum,
) -> GMX:
    """CCXT GMX exchange with wallet for open/close long position testing.

    Uses an isolated Anvil fork to avoid state pollution with other tests.
    Uses RPC loading (default) for complete market data.
    """
    setup_mock_oracle(web3_arbitrum_fork_ccxt_long)

    # Fund wallet on the isolated fork.
    _fund_wallet_on_fork(
        web3_arbitrum_fork_ccxt_long,
        test_wallet.address,
        large_usdc_holder_arbitrum,
        large_weth_holder_arbitrum,
    )

    config = GMXConfig(
        web3_arbitrum_fork_ccxt_long,
        user_wallet_address=test_wallet.address,
    )
    _approve_tokens_for_config(
        config,
        web3_arbitrum_fork_ccxt_long,
        test_wallet.address,
    )

    # Sync nonce AFTER approve transactions — _approve_tokens_for_config()
    # uses transact() which increments the on-chain nonce without going
    # through HotWallet's internal counter
    test_wallet.sync_nonce(web3_arbitrum_fork_ccxt_long)

    gmx = GMX(
        params={
            "rpcUrl": web3_arbitrum_fork_ccxt_long.provider.endpoint_uri if hasattr(web3_arbitrum_fork_ccxt_long.provider, "endpoint_uri") else None,
            "wallet": test_wallet,
        }
    )
    # Load markets using RPC mode (REST API won't work with forked chain)
    gmx.load_markets(params={"rest_api_mode": False, "graphql_only": False})
    return gmx


@pytest.fixture
def ccxt_gmx_fork_short(
    web3_arbitrum_fork_ccxt_short,
    test_wallet,
    large_usdc_holder_arbitrum,
    large_weth_holder_arbitrum,
) -> GMX:
    """CCXT GMX exchange with wallet for short position testing.

    Uses an isolated Anvil fork to avoid state pollution with other tests.
    Uses RPC loading (default) for complete market data.
    Short positions require USDC collateral, which is funded by _fund_wallet_on_fork.
    """
    setup_mock_oracle(web3_arbitrum_fork_ccxt_short)

    # Fund wallet on the isolated fork.
    # This is critical for short positions which require USDC collateral
    _fund_wallet_on_fork(
        web3_arbitrum_fork_ccxt_short,
        test_wallet.address,
        large_usdc_holder_arbitrum,
        large_weth_holder_arbitrum,
    )

    config = GMXConfig(web3_arbitrum_fork_ccxt_short, user_wallet_address=test_wallet.address)
    _approve_tokens_for_config(config, web3_arbitrum_fork_ccxt_short, test_wallet.address)

    # Sync nonce AFTER approve transactions — _approve_tokens_for_config()
    # uses transact() which increments the on-chain nonce without going
    # through HotWallet's internal counter
    test_wallet.sync_nonce(web3_arbitrum_fork_ccxt_short)

    gmx = GMX(
        params={
            "rpcUrl": web3_arbitrum_fork_ccxt_short.provider.endpoint_uri if hasattr(web3_arbitrum_fork_ccxt_short.provider, "endpoint_uri") else None,
            "wallet": test_wallet,
        }
    )
    # Load markets using RPC mode (REST API won't work with forked chain)
    gmx.load_markets(params={"rest_api_mode": False, "graphql_only": False})
    return gmx


@pytest.fixture
def gmx_arbitrum() -> GMX:
    """CCXT GMX exchange for Arbitrum mainnet in view-only mode.

    This fixture creates a GMX instance connected to Arbitrum mainnet
    for testing CCXT endpoint functionality with real API calls.
    No wallet is required as this is for read-only operations.
    """
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM environment variable not set")

    gmx = GMX(
        params={
            "rpcUrl": rpc_url,
        }
    )
    return gmx


@pytest.fixture
def ccxt_gmx_arbitrum() -> GMX:
    """CCXT GMX exchange for Arbitrum mainnet in view-only mode.

    Alias for gmx_arbitrum fixture. This fixture creates a GMX instance
    connected to Arbitrum mainnet for testing CCXT functionality.
    No wallet is required as this is for read-only operations.
    """
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM environment variable not set")

    gmx = GMX(
        params={
            "rpcUrl": rpc_url,
        }
    )
    return gmx


@pytest.fixture
def ccxt_gmx_fork_graphql(
    web3_arbitrum_fork_ccxt_long,
    test_wallet,
    large_usdc_holder_arbitrum,
    large_weth_holder_arbitrum,
) -> GMX:
    """CCXT GMX exchange with wallet for testing GraphQL market loading.

    Uses an isolated Anvil fork to avoid state pollution with other tests.
    Uses GraphQL loading for fast market data retrieval.
    Markets are pre-loaded so they're immediately available.
    """
    setup_mock_oracle(web3_arbitrum_fork_ccxt_long)

    # Fund wallet on the isolated fork.
    _fund_wallet_on_fork(
        web3_arbitrum_fork_ccxt_long,
        test_wallet.address,
        large_usdc_holder_arbitrum,
        large_weth_holder_arbitrum,
    )

    config = GMXConfig(web3_arbitrum_fork_ccxt_long, user_wallet_address=test_wallet.address)
    _approve_tokens_for_config(config, web3_arbitrum_fork_ccxt_long, test_wallet.address)

    # Sync nonce AFTER approve transactions — _approve_tokens_for_config()
    # uses transact() which increments the on-chain nonce without going
    # through HotWallet's internal counter
    test_wallet.sync_nonce(web3_arbitrum_fork_ccxt_long)

    gmx = GMX(
        params={
            "rpcUrl": web3_arbitrum_fork_ccxt_long.provider.endpoint_uri if hasattr(web3_arbitrum_fork_ccxt_long.provider, "endpoint_uri") else None,
            "wallet": test_wallet,
            "options": {"graphql_only": True},
        }
    )
    # Pre-load markets so they're available immediately
    gmx.load_markets()
    return gmx


# ============================================================================
# TENDERLY FIXTURES - Use TENDERLY_RPC_URL env var to run tests on Tenderly
# ============================================================================


@pytest.fixture()
def tenderly_rpc_url() -> str | None:
    """Get Tenderly RPC URL from environment."""
    url = os.environ.get("TENDERLY_RPC_URL")
    if not url:
        pytest.skip("TENDERLY_RPC_URL environment variable not set")
    return url


@pytest.fixture()
def web3_tenderly(tenderly_rpc_url: str) -> Web3:
    """Web3 instance connected directly to Tenderly virtual testnet."""
    web3 = create_multi_provider_web3(
        tenderly_rpc_url,
        default_http_timeout=(3.0, 180.0),
    )
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    if not web3.is_connected():
        pytest.skip(f"Could not connect to Tenderly RPC at {tenderly_rpc_url}")

    return web3


@pytest.fixture()
def test_wallet_tenderly(web3_tenderly: Web3) -> HotWallet:
    """Create a HotWallet for testing on Tenderly.

    Funds the wallet with ETH, WETH, and USDC using Tenderly's setBalance.
    """
    # Use default anvil private key
    private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_tenderly)

    # Fund wallet with ETH
    eth_amount_wei = 100_000_000 * 10**18
    set_balance(web3_tenderly, wallet.address, hex(eth_amount_wei))

    # Fund WETH
    large_weth_holder = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")
    set_balance(web3_tenderly, large_weth_holder, hex(10 * 10**18))

    weth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    weth = fetch_erc20_details(web3_tenderly, weth_address)
    weth_amount = 1000 * 10**18
    try:
        weth.contract.functions.transfer(wallet.address, weth_amount).transact(
            {"from": large_weth_holder},
        )
    except Exception as e:
        logging.warning(f"Could not transfer WETH: {e}")

    # Fund USDC
    large_usdc_holder = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
    set_balance(web3_tenderly, large_usdc_holder, hex(10 * 10**18))

    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    usdc = fetch_erc20_details(web3_tenderly, usdc_address)
    usdc_amount = 100_000_000 * 10**6
    try:
        usdc.contract.functions.transfer(wallet.address, usdc_amount).transact(
            {"from": large_usdc_holder},
        )
    except Exception as e:
        logging.warning(f"Could not transfer USDC: {e}")

    # Sync nonce after transfers
    wallet.sync_nonce(web3_tenderly)

    return wallet


@pytest.fixture
def ccxt_gmx_tenderly(
    web3_tenderly,
    test_wallet_tenderly,
) -> GMX:
    """CCXT GMX exchange with wallet for testing on Tenderly virtual testnet.

    Uses Tenderly directly (no Anvil fork) for persistent state testing.
    """
    setup_mock_oracle(web3_tenderly)
    config = GMXConfig(web3_tenderly, user_wallet_address=test_wallet_tenderly)
    _approve_tokens_for_config(config, web3_tenderly, test_wallet_tenderly.address)
    gmx = GMX(
        params={
            "rpcUrl": web3_tenderly.provider.endpoint_uri if hasattr(web3_tenderly.provider, "endpoint_uri") else None,
            "wallet": test_wallet_tenderly,
        }
    )
    # Load markets using RPC mode (REST API won't work with Tenderly virtual testnet)
    gmx.load_markets(params={"rest_api_mode": False, "graphql_only": False})
    return gmx
