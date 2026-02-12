import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

import eth_abi
import pytest
from eth_account import Account
from eth_pydantic_types import HexStr
from eth_typing import HexAddress
from eth_utils import keccak, to_checksum_address
from web3 import HTTPProvider, Web3

from eth_defi.chain import get_chain_id_by_name, install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import NETWORK_TOKENS, get_contract_addresses
from eth_defi.gmx.core import GetAvailableLiquidity, GetBorrowAPR, GetClaimableFees, GetFundingFee, GetOpenPositions, GetPoolTVL, Markets
from eth_defi.gmx.core.glv_stats import GlvStats
from eth_defi.gmx.data import GMXMarketData
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.gmx.order.base_order import BaseOrder
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.synthetic_tokens import get_gmx_synthetic_token_by_symbol
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import addr
from tests.gmx.fork_helpers import setup_mock_oracle

# Fork configuration constants
FORK_BLOCK_ARBITRUM = 401729535  # Updated: old block 392496384 had empty getMarkets() data
FORK_BLOCK_WORKING = 392496384

# Set up logging for debugging
logger = logging.getLogger(__name__)


def get_gmx_address(chain_id: int, symbol: str) -> str:
    """
    Simple helper to get token address from GMX API.

    This wrapper function serves two purposes:
    1. Makes the configuration more readable by hiding the .address access
    2. Provides a single place to handle the case where a token isn't found

    Args:
        chain_id: The blockchain chain ID
        symbol: Token symbol (e.g., "USDC", "WBTC")

    Returns:
        Token address as string

    Raises:
        ValueError: If token is not found in GMX API
    """
    # As GMX uses own synthetic BTC, it's listed there as BTC. WBTC listed as WBTC.b
    # Same for WETH, WAVAX, WSOL is listed as ETH, AVAX, SOL respectively
    if symbol == "WETH":
        symbol = "ETH"
    if symbol == "WBTC":
        avalanche_chain_id = get_chain_id_by_name("avalanche")
        if chain_id == avalanche_chain_id:
            symbol = "BTC"  # For Avalanche, it's BTC, Address: 0x152b9d0FdC40C096757F570A51E494bd4b943E50
        else:
            symbol = "WBTC.b"
    elif symbol == "WSOL":
        symbol = "SOL"
    elif symbol == "WAVAX":
        symbol = "AVAX"

    token = get_gmx_synthetic_token_by_symbol(chain_id, symbol)
    if token is None:
        raise ValueError(f"Token '{symbol}' not found on chain {chain_id}")
    return token.address


# Configure chain-specific parameters
def get_chain_config(chain_name):
    """Get chain configuration with lazy-loaded token addresses."""
    base_config = {
        "arbitrum": {
            "rpc_env_var": "JSON_RPC_ARBITRUM",
            "chain_id": get_chain_id_by_name("arbitrum"),
            "fork_block_number": 338206286,
        },
        "avalanche": {
            "rpc_env_var": "AVALANCHE_JSON_RPC_URL",
            "chain_id": get_chain_id_by_name("avalanche"),
            "fork_block_number": 60491219,
        },
    }

    config = base_config[chain_name].copy()

    # Add token addresses lazily to avoid network calls at import time
    chain_id = get_chain_id_by_name(chain_name)

    if chain_name == "arbitrum":
        config.update(
            {
                "wbtc_address": get_gmx_address(chain_id, "WBTC"),
                "usdc_address": get_gmx_address(chain_id, "USDC"),
                "usdt_address": get_gmx_address(chain_id, "USDT"),
                "link_address": get_gmx_address(chain_id, "LINK"),
                "wsol_address": get_gmx_address(chain_id, "WSOL"),
                "arb_address": get_gmx_address(chain_id, "ARB"),
                "native_token_address": get_gmx_address(chain_id, "WETH"),
                "aave_address": get_gmx_address(chain_id, "AAVE"),
            }
        )
    elif chain_name == "avalanche":
        config.update(
            {
                "wbtc_address": get_gmx_address(chain_id, "WBTC"),
                "usdc_address": get_gmx_address(chain_id, "USDC"),
                "usdt_address": get_gmx_address(chain_id, "USDT"),
                "wavax_address": get_gmx_address(chain_id, "WAVAX"),
                "native_token_address": get_gmx_address(chain_id, "AVAX"),
            }
        )

    return config


# Cache for lazily-loaded chain configs
_CHAIN_CONFIG_CACHE = {}


def _get_arbitrum_config():
    """Get Arbitrum config with addresses from GMX API.

    This is called lazily only when GMX tests actually run, not at import time.
    Results are cached to avoid multiple API calls.
    """
    # Return cached config if available
    if "arbitrum" in _CHAIN_CONFIG_CACHE:
        return _CHAIN_CONFIG_CACHE["arbitrum"]

    chain_id = get_chain_id_by_name("arbitrum")
    config = {
        "rpc_env_var": "JSON_RPC_ARBITRUM",
        "chain_id": chain_id,
        "fork_block_number": 338206286,
        # Fetch token addresses from GMX API instead of hardcoding
        "wbtc_address": get_gmx_address(chain_id, "WBTC"),
        "usdc_address": get_gmx_address(chain_id, "USDC"),
        "usdt_address": get_gmx_address(chain_id, "USDT"),
        "link_address": get_gmx_address(chain_id, "LINK"),
        "wsol_address": get_gmx_address(chain_id, "WSOL"),
        "arb_address": get_gmx_address(chain_id, "ARB"),
        "native_token_address": get_gmx_address(chain_id, "WETH"),
        "aave_address": get_gmx_address(chain_id, "AAVE"),
    }

    # Cache the result
    _CHAIN_CONFIG_CACHE["arbitrum"] = config
    return config


# Static chain config - token addresses are loaded lazily for Arbitrum
CHAIN_CONFIG = {
    "arbitrum": {
        "rpc_env_var": "JSON_RPC_ARBITRUM",
        "chain_id": get_chain_id_by_name("arbitrum"),
        "fork_block_number": 338206286,
        # Token addresses will be fetched lazily when GMX tests run
    },
    "avalanche": {
        "rpc_env_var": "AVALANCHE_JSON_RPC_URL",
        "chain_id": get_chain_id_by_name("avalanche"),
        "fork_block_number": 60491219,
        # Hardcoded token addresses for Avalanche
        "wbtc_address": "0x152b9d0FdC40C096757F570A51E494bd4b943E50",
        "usdc_address": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "usdt_address": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
        "wavax_address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
        "native_token_address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
        "link_address": "0x5947BB275c521040051D82396192181b413227A3",
    },
}


def _get_chain_config_with_tokens(chain_name: str) -> dict:
    """Get chain config with token addresses, lazily loading them if needed.

    For Arbitrum, this will fetch from GMX API on first access and cache results.
    For other chains, returns static config.
    """
    if chain_name == "arbitrum":
        return _get_arbitrum_config()
    else:
        return CHAIN_CONFIG[chain_name]


def pytest_generate_tests(metafunc):
    """Generate parametrised tests for Arbitrum only (Avalanche skipped)."""
    if "chain_name" in metafunc.fixturenames:
        # Only test Arbitrum if RPC URL is available
        available_chains = []
        if os.environ.get(CHAIN_CONFIG["arbitrum"]["rpc_env_var"]):
            available_chains.append("arbitrum")

        # Skip all tests if no chains are available
        if not available_chains:
            pytest.skip("No JSON_RPC_ARBITRUM environment variable available")

        # Parametrise tests with available chains (only arbitrum)
        metafunc.parametrize("chain_name", available_chains)


@pytest.fixture()
def execution_buffer() -> float:
    """Default execution buffer multiplier used across GMX tests."""
    return 30


@pytest.fixture()
def test_address(anvil_private_key) -> HexAddress:
    """Return the default anvil test address."""
    account = Account.from_key(anvil_private_key)
    assert to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266") == account.address
    return account.address


@pytest.fixture()
def large_eth_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of ETH.

    This account is unlocked on Anvil, so you have access to good ETH stash.

    `To find large holder accounts, use arbiscan <https://arbiscan.io/accounts>`_.
    """
    # Binance Hot Wallet 20
    return addr("0xF977814e90dA44bFA03b6295A0616a897441aceC")


@pytest.fixture()
def large_wbtc_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of WBTC.

    This account is unlocked on Anvil, so you have access to good WBTC stash.

    `To find large holder accounts, use arbiscan <https://arbiscan.io/accounts>`_.
    """
    # https://arbiscan.io/address/0xdcf711cb8a1e0856ff1cb1cfd52c5084f5b28030
    return addr("0xdcF711cB8A1e0856fF1cB1CfD52C5084f5B28030")


@pytest.fixture()
def large_wavax_holder() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of WAVAX.

    This account is unlocked on Anvil, so you have access to good WAVAX stash.

    `To find large holder accounts, use arbiscan <https://snowtrace.io/accounts>`_.
    """
    # https://snowtrace.io/address/0xefdc8FC1145ea88e3f5698eE7b7b432F083B4246
    # Upbit: Hot Wallet 1
    return addr("0x73AF3bcf944a6559933396c1577B257e2054D935")


@pytest.fixture()
def large_wbtc_holder_avalanche() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of WBTC.

    This account is unlocked on Anvil, so you have access to good WBTC stash.

    `To find large holder accounts, use arbiscan <https://snowtrace.io/accounts>`_.
    """
    # https://snowtrace.io/address/0x8ffDf2DE812095b1D19CB146E4c004587C0A0692
    return addr("0x8ffDf2DE812095b1D19CB146E4c004587C0A0692")


@pytest.fixture()
def large_usdc_holder_arbitrum() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.
    """
    # https://arbiscan.io/token/0xaf88d065e77c8cc2239327c5edb3a432268e5831
    # This address has consistent USDC balance across different blocks
    # backup 0x463f5D63e5a5EDB8615b0e485A090a18Aba08578
    return to_checksum_address("0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7")


@pytest.fixture()
def large_usdc_holder_avalanche() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.
    """
    # https://snowscan.xyz/address/0x9f8c163cba728e99993abe7495f06c0a3c8ac8b9
    return addr("0x9f8c163cBA728e99993ABe7495F06c0A3c8Ac8b9")


@pytest.fixture()
def large_weth_holder_arbitrum() -> HexAddress:
    # # https://arbiscan.io/address/0x70d95587d40A2caf56bd97485aB3Eec10Bee6336
    return to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")


@pytest.fixture()
def large_link_holder_avalanche() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of LINK.

    This account is unlocked on Anvil, so you have access to good LINK stash.
    """
    # https://snowscan.xyz/address/0x4e9f683A27a6BdAD3FC2764003759277e93696e6
    return addr("0x4e9f683A27a6BdAD3FC2764003759277e93696e6")


@pytest.fixture()
def large_arb_holder_arbitrum() -> HexAddress:
    # Binance Hot wallet 20
    return addr("0xF977814e90dA44bFA03b6295A0616a897441aceC")


# GMX Actors for passing checks
@pytest.fixture()
def gmx_controller_arbitrum() -> HexAddress:
    return to_checksum_address("0xf5F30B10141E1F63FC11eD772931A8294a591996")


@pytest.fixture()
def gmx_keeper_arbitrum() -> HexAddress:
    return to_checksum_address("0xE47b36382DC50b90bCF6176Ddb159C4b9333A7AB")


@pytest.fixture()
def chain_rpc_url(chain_name):
    """Get the RPC URL for the specified chain."""
    env_var = CHAIN_CONFIG[chain_name]["rpc_env_var"]
    rpc_url = os.environ.get(env_var)
    if not rpc_url:
        pytest.skip(f"No {env_var} environment variable")
    return rpc_url


@pytest.fixture()
def anvil_chain_fork(
    request,
    chain_name,
    chain_rpc_url,
    large_eth_holder,
    large_wbtc_holder,
    large_wavax_holder,
    large_usdc_holder_arbitrum,
    large_usdc_holder_avalanche,
    large_wbtc_holder_avalanche,
    large_link_holder_avalanche,
    gmx_controller_arbitrum,
    large_weth_holder_arbitrum,
    gmx_keeper_arbitrum,
    large_gm_eth_usdc_holder_arbitrum,
) -> Generator[str, Any, None]:
    """Create a testable fork of the live chain using Anvil."""
    unlocked_addresses = [large_eth_holder, large_wbtc_holder]

    if chain_name == "arbitrum":
        unlocked_addresses.append(large_usdc_holder_arbitrum)
        unlocked_addresses.append(gmx_controller_arbitrum)
        unlocked_addresses.append(large_weth_holder_arbitrum)
        unlocked_addresses.append(gmx_keeper_arbitrum)
        unlocked_addresses.append(large_gm_eth_usdc_holder_arbitrum)
    elif chain_name == "avalanche":
        unlocked_addresses.append(large_wavax_holder)
        unlocked_addresses.append(large_usdc_holder_avalanche)
        unlocked_addresses.append(large_wbtc_holder_avalanche)
        unlocked_addresses.append(large_link_holder_avalanche)

    launch = fork_network_anvil(
        chain_rpc_url,
        unlocked_addresses=unlocked_addresses,
        test_request_timeout=100,
        # fork_block_number=FORK_BLOCK_ARBITRUM,
        launch_wait_seconds=60,
    )

    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_arbitrum_fork(anvil_chain_fork: str) -> Web3:
    """Set up a local unit testing blockchain with the forked chain."""
    web3 = Web3(
        HTTPProvider(
            anvil_chain_fork,
            request_kwargs={"timeout": 100},
        )
    )
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture()
def mock_oracle_fork(web3_arbitrum_fork: Web3) -> str:
    """Set up mock oracle for fork testing with dynamic price fetching.

    Replaces the production Chainlink oracle with a mock oracle that allows
    setting custom prices for testing. This is required for fork testing since
    the real Chainlink oracle may not work on forked chains.

    Prices are fetched dynamically from on-chain oracle to match GMX validation.

    Returns:
        Address of the mock oracle provider
    """
    setup_mock_oracle(web3_arbitrum_fork)
    return to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")


# TODO: Rename it to web3_abitrum
@pytest.fixture()
def web3_mainnet(chain_name, chain_rpc_url):
    """Set up a Web3 connection to the mainnet chain."""
    web3 = create_multi_provider_web3(chain_rpc_url)
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    # Skip tests if we can't connect
    if not web3.is_connected():
        pytest.skip(f"Could not connect to {chain_name.upper()} RPC at {chain_rpc_url}")

    # Verify we're connected to the right chain
    chain_id = web3.eth.chain_id
    expected_chain_id = CHAIN_CONFIG[chain_name]["chain_id"]
    if chain_id != expected_chain_id:
        pytest.skip(
            f"Connected to chain ID {chain_id}, but expected {chain_name.upper()} ({expected_chain_id})",
        )

    return web3


@pytest.fixture()
def gmx_config(web3_mainnet) -> GMXConfig:
    """Create a GMX configuration for the specified chain."""
    return GMXConfig(web3_mainnet)


@pytest.fixture()
def market_data(gmx_config) -> GMXMarketData:
    """Create a GMXMarketData instance for the specified chain."""
    return GMXMarketData(gmx_config)


@pytest.fixture()
def get_pool_tvl(gmx_config) -> GetPoolTVL:
    """Create a GetPoolTVL instance for the specified chain."""
    return GetPoolTVL(gmx_config)


@pytest.fixture()
def api(gmx_config):
    """Create a GMXAPI instance for the specified chain."""
    return GMXAPI(gmx_config)


# Token fixtures for specific chains
@pytest.fixture()
def wbtc_arbitrum(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """WBTC token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    config = _get_chain_config_with_tokens("arbitrum")
    return fetch_erc20_details(
        web3_arbitrum_fork,
        config["wbtc_address"],
    )


@pytest.fixture()
def usdc_arbitrum(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDC token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    config = _get_chain_config_with_tokens("arbitrum")
    return fetch_erc20_details(
        web3_arbitrum_fork,
        config["usdc_address"],
    )


@pytest.fixture()
def usdt_arbitrum(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDT token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    config = _get_chain_config_with_tokens("arbitrum")
    return fetch_erc20_details(
        web3_arbitrum_fork,
        config["usdt_address"],
    )


@pytest.fixture()
def wbtc_avalanche(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """WBTC token on Avalanche."""
    if chain_name != "avalanche":
        pytest.skip("This fixture is for Avalanche only")
    return fetch_erc20_details(
        web3_arbitrum_fork,
        CHAIN_CONFIG["avalanche"]["wbtc_address"],
    )


@pytest.fixture()
def wavax_avalanche(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """WAVAX token on Avalanche."""
    if chain_name != "avalanche":
        pytest.skip("This fixture is for Avalanche only")
    return fetch_erc20_details(
        web3_arbitrum_fork,
        CHAIN_CONFIG["avalanche"]["wavax_address"],
        contract_name="./WAVAX.json",
    )


# Generic token fixtures that adapt to the current chain
@pytest.fixture()
def wbtc(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """WBTC token details for the specified chain."""
    config = _get_chain_config_with_tokens(chain_name)
    wbtc_address = config["wbtc_address"]
    return fetch_erc20_details(web3_arbitrum_fork, wbtc_address)


@pytest.fixture()
def usdc(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDC token details for the specified chain."""
    config = _get_chain_config_with_tokens(chain_name)
    usdc_address = config["usdc_address"]
    return fetch_erc20_details(web3_arbitrum_fork, usdc_address)


@pytest.fixture()
def wsol(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """WSOL token details for the specified chain."""
    config = _get_chain_config_with_tokens(chain_name)
    wsol_address = config["wsol_address"]
    return fetch_erc20_details(web3_arbitrum_fork, wsol_address)


@pytest.fixture()
def link(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """LINK token details for the specified chain."""
    config = _get_chain_config_with_tokens(chain_name)
    link_address = config["link_address"]
    return fetch_erc20_details(web3_arbitrum_fork, link_address)


@pytest.fixture()
def arb(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """ARB token details for the specified chain."""
    config = _get_chain_config_with_tokens(chain_name)
    arb_address = config["arb_address"]
    return fetch_erc20_details(web3_arbitrum_fork, arb_address)


@pytest.fixture()
def usdt(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDT token details for the specified chain."""
    config = _get_chain_config_with_tokens(chain_name)
    usdt_address = config["usdt_address"]
    return fetch_erc20_details(web3_arbitrum_fork, usdt_address)


@pytest.fixture()
def aave(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """AAVE token details for the specified chain."""
    config = _get_chain_config_with_tokens(chain_name)
    aave_address = config["aave_address"]
    return fetch_erc20_details(web3_arbitrum_fork, aave_address)


@pytest.fixture()
def wrapped_native_token(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """Get the native wrapped token (WETH for Arbitrum, WAVAX for Avalanche)."""
    config = _get_chain_config_with_tokens(chain_name)
    native_address = config["native_token_address"]
    contract_name = "./WAVAX.json" if chain_name == "avalanche" else "ERC20MockDecimals.json"
    return fetch_erc20_details(
        web3_arbitrum_fork,
        native_address,
        contract_name=contract_name,
    )


# Wallet funding fixtures
@pytest.fixture()
def wallet_with_native_token(
    web3_arbitrum_fork: Web3,
    chain_name,
    test_address: HexAddress,
    gmx_controller_arbitrum: HexAddress,
    large_eth_holder: HexAddress,
) -> None:
    """Set up the anvil wallet with the chain's native token for testing."""
    # Native ETH is already available in the test account on both forks

    amount: int = 100 * 10**18
    # Wrap some native token if needed
    if chain_name == "avalanche":
        # For Avalanche, we need to wrap AVAX
        config = _get_chain_config_with_tokens(chain_name)
        wavax_address = config["native_token_address"]
        wavax = fetch_erc20_details(
            web3_arbitrum_fork,
            wavax_address,
            contract_name="./WAVAX.json",
        )
        wavax.contract.functions.deposit().transact(
            {"from": test_address, "value": amount},
        )
    else:
        # Fund the account with native gas tokens of arbitrum
        amount_wei = 5000000 * 10**18
        web3_arbitrum_fork.provider.make_request(
            "anvil_setBalance",
            [gmx_controller_arbitrum, hex(amount_wei)],
        )
        web3_arbitrum_fork.provider.make_request(
            "anvil_setBalance",
            [test_address, hex(amount_wei)],
        )


@pytest.fixture()
def wallet_with_usdc(
    web3_arbitrum_fork: Web3,
    chain_name,
    test_address: HexAddress,
    large_usdc_holder_arbitrum,
    large_usdc_holder_avalanche,
) -> None:
    """Fund the test wallet with USDC."""
    config = _get_chain_config_with_tokens(chain_name)
    if chain_name == "arbitrum":
        usdc_address = config["usdc_address"]
        usdc = fetch_erc20_details(web3_arbitrum_fork, usdc_address)
        large_holder = large_usdc_holder_arbitrum
        amount = 100_000_000 * 10**6  # 100,000 USDC (6 decimals)
    else:  # avalanche
        usdc_address = config["usdc_address"]
        usdc = fetch_erc20_details(web3_arbitrum_fork, usdc_address)
        large_holder = large_usdc_holder_avalanche
        amount = 100_000_000 * 10**6  # 100,000 USDC (6 decimals)

    # Fund the whale holder with ETH for gas
    eth_amount_wei = 10 * 10**18  # 10 ETH for gas
    web3_arbitrum_fork.provider.make_request(
        "anvil_setBalance",
        [large_holder, hex(eth_amount_wei)],
    )

    try:
        usdc.contract.functions.transfer(test_address, amount).transact(
            {"from": large_holder},
        )
    except Exception as e:
        # If the transfer fails, skip the test instead of failing
        pytest.skip(f"Could not transfer USDC to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_wbtc(
    web3_arbitrum_fork: Web3,
    chain_name,
    test_address: HexAddress,
    large_wbtc_holder,
    large_wbtc_holder_avalanche,
) -> None:
    """Fund the test wallet with WBTC."""
    config = _get_chain_config_with_tokens(chain_name)
    if chain_name == "arbitrum":
        wbtc_address = config["wbtc_address"]
        wbtc = fetch_erc20_details(web3_arbitrum_fork, wbtc_address)
        large_holder = large_wbtc_holder
        amount = 5 * 10**8  # 5 WBTC (8 decimals)
    else:  # avalanche
        wbtc_address = config["wbtc_address"]
        wbtc = fetch_erc20_details(web3_arbitrum_fork, wbtc_address)
        large_holder = large_wbtc_holder_avalanche
        amount = 5 * 10**8  # 5 WBTC (8 decimals)

    # Fund the whale holder with ETH for gas
    eth_amount_wei = 10 * 10**18  # 10 ETH for gas
    web3_arbitrum_fork.provider.make_request(
        "anvil_setBalance",
        [large_holder, hex(eth_amount_wei)],
    )

    try:
        wbtc.contract.functions.transfer(test_address, amount).transact({"from": large_holder})
    except Exception as e:
        # If the transfer fails, skip the test instead of failing
        pytest.skip(f"Could not transfer WBTC to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_link(
    web3_arbitrum_fork,
    chain_name,
    test_address: HexAddress,
    large_link_holder_avalanche,
) -> None:
    """Fund the test wallet with LINK."""
    amount = 10000 * 10**18
    if chain_name == "avalanche":
        # First, fund the LINK holder with AVAX for gas
        eth_amount_wei = 10 * 10**18  # 10 AVAX for gas
        web3_arbitrum_fork.provider.make_request(
            "anvil_setBalance",
            [large_link_holder_avalanche, hex(eth_amount_wei)],
        )

        config = _get_chain_config_with_tokens(chain_name)
        link_address = config["link_address"]
        link = fetch_erc20_details(web3_arbitrum_fork, link_address)
        # 10k LINK tokens
        try:
            link.contract.functions.transfer(test_address, amount).transact(
                {"from": large_link_holder_avalanche},
            )
        except Exception as e:
            # If the transfer fails, skip the test instead of failing
            pytest.skip(f"Could not transfer LINK to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_arb(
    web3_arbitrum_fork,
    chain_name,
    test_address: HexAddress,
    large_arb_holder_arbitrum: HexAddress,
) -> None:
    """Fund the test wallet with ARB."""
    amount = 1000000 * 10**18
    if chain_name == "arbitrum":
        # Fund the whale holder with ETH for gas
        eth_amount_wei = 10 * 10**18  # 10 ETH for gas
        web3_arbitrum_fork.provider.make_request(
            "anvil_setBalance",
            [large_arb_holder_arbitrum, hex(eth_amount_wei)],
        )

        try:
            config = _get_chain_config_with_tokens(chain_name)
            arb_address = to_checksum_address(config["arb_address"])
            arb = fetch_erc20_details(web3_arbitrum_fork, arb_address)
            arb.contract.functions.transfer(test_address, amount).transact({"from": large_arb_holder_arbitrum})
        except Exception as e:
            # If the transfer fails, skip the test instead of failing
            pytest.skip(f"Could not transfer ARB to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_weth(
    web3_arbitrum_fork,
    chain_name,
    test_address: HexAddress,
    large_weth_holder_arbitrum: HexAddress,
) -> None:
    """Fund the test wallet with WETH."""
    if chain_name == "arbitrum":
        # Fund the whale holder with ETH for gas
        eth_amount_wei = 10 * 10**18  # 10 ETH for gas
        web3_arbitrum_fork.provider.make_request(
            "anvil_setBalance",
            [large_weth_holder_arbitrum, hex(eth_amount_wei)],
        )

        try:
            config = _get_chain_config_with_tokens(chain_name)
            weth_address = config["native_token_address"]  # WETH is the native token on Arbitrum
            weth = fetch_erc20_details(web3_arbitrum_fork, weth_address)
            amount = 1000 * 10**18  # 1000 WETH
            weth.contract.functions.transfer(test_address, amount).transact({"from": large_weth_holder_arbitrum})
        except Exception as e:
            # If the transfer fails, skip the test instead of failing
            pytest.skip(f"Could not transfer WETH to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_all_tokens(
    wallet_with_native_token,
    wallet_with_usdc,
    wallet_with_wbtc,
    wallet_with_link,
    wallet_with_arb,
    wallet_with_weth,
) -> None:
    """Set up the wallet with all tokens needed for testing."""
    # This fixture combines all token fixtures to ensure the wallet has all needed tokens
    pass


@pytest.fixture()
def anvil_private_key() -> HexAddress:
    """The default private key for the first Anvil test account."""
    return HexAddress(HexStr("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"))


def _approve_tokens_for_config(
    config: GMXConfig,
    web3_arbitrum_fork,
    test_address,
):
    """Helper function to approve tokens for the GMX routers."""

    # Approve tokens for GMX routers
    chain_name = config.get_chain()
    tokens = NETWORK_TOKENS[chain_name]

    # Define tokens that need approval for swaps and trading
    token_addresses = []
    if chain_name == "arbitrum":
        # Add all typical tokens for Arbitrum that might be used in swaps
        token_addresses = [tokens["USDC"], tokens["WETH"], tokens["WBTC"], tokens["USDT"], tokens["LINK"]]
    elif chain_name == "avalanche":
        token_addresses = [tokens["USDC"], tokens["WAVAX"], tokens["WBTC"]]

    # Get the GMX router addresses for approvals
    contract_addresses = get_contract_addresses(chain_name)
    # Need to approve for BOTH routers:
    # - syntheticsrouter: for trading orders (swaps, increase, decrease)
    # - exchangerouter: for liquidity operations (deposits, withdrawals)
    router_addresses = [contract_addresses.syntheticsrouter, contract_addresses.exchangerouter]

    # Approve each token for both routers
    test_address_checksum = to_checksum_address(test_address)
    large_amount = 2**256 - 1  # Maximum value for uint256

    for token_addr in token_addresses:
        try:
            token_details = fetch_erc20_details(web3_arbitrum_fork, token_addr)
            for router_address in router_addresses:
                try:
                    approve_tx = token_details.contract.functions.approve(router_address, large_amount)
                    approve_tx.transact({"from": test_address_checksum})
                except Exception:
                    pass
        except Exception:
            # If approval fails, that's ok - we'll handle that in tests that need approval
            pass

    # Note: GM tokens (market tokens) need approval for withdrawals but we'll skip
    # auto-approval here to avoid too many RPC calls during test setup.
    # Individual tests that need GM token approvals should handle them explicitly.


# TODO: Replace with the new Order class
# @pytest.fixture()
# def order_manager(arbitrum_fork_config):
#     """Create a GMXOrderManager instance for the specified chain."""
#     return GMXOrderManager(arbitrum_fork_config)


@pytest.fixture()
def trading_manager(arbitrum_fork_config):
    """Create a GMXTrading instance for the specified chain."""
    return GMXTrading(arbitrum_fork_config)


@pytest.fixture()
def test_wallet(
    web3_arbitrum_fork,
    anvil_private_key,
    chain_name,
    large_weth_holder_arbitrum,
    large_usdc_holder_arbitrum,
):
    """Create a HotWallet for testing transactions."""
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_arbitrum_fork)

    # Fund wallet with ETH for gas fees (required for GMX order transactions)
    # GMX orders require ETH for execution fees and gas
    eth_amount_wei = 100_000_000 * 10**18  # 100_000_000 ETH should be enough for multiple transactions
    web3_arbitrum_fork.provider.make_request(
        "anvil_setBalance",
        [wallet.address, hex(eth_amount_wei)],
    )

    # Fund wallet with WETH for collateral (tests use ETH/WETH as collateral)
    if chain_name == "arbitrum":
        try:
            config = _get_chain_config_with_tokens(chain_name)
            weth_address = config["native_token_address"]  # ETH is the native token on Arbitrum
            weth = fetch_erc20_details(web3_arbitrum_fork, weth_address)

            # Fund the whale holder with ETH for gas
            gas_eth = 100000 * 10**18
            web3_arbitrum_fork.provider.make_request(
                "anvil_setBalance",
                [large_weth_holder_arbitrum, hex(gas_eth)],
            )

            # Transfer WETH to test wallet
            weth_amount = 1000 * 10**18  # 1000 WETH for collateral
            weth.contract.functions.transfer(wallet.address, weth_amount).transact(
                {"from": large_weth_holder_arbitrum},
            )
        except Exception:
            # If WETH transfer fails, that's ok - some tests might not need it
            pass

        # Fund wallet with USDC (required for GMX trading operations)
        try:
            config = _get_chain_config_with_tokens(chain_name)
            usdc_address = config["usdc_address"]
            usdc = fetch_erc20_details(web3_arbitrum_fork, usdc_address)

            # Fund the whale holder with ETH for gas
            gas_eth = 10000 * 10**18
            web3_arbitrum_fork.provider.make_request(
                "anvil_setBalance",
                [large_usdc_holder_arbitrum, hex(gas_eth)],
            )

            # Transfer USDC to test wallet
            usdc_amount = 100_000_000 * 10**6  # 100,000 USDC (6 decimals)
            usdc.contract.functions.transfer(wallet.address, usdc_amount).transact(
                {"from": large_usdc_holder_arbitrum},
            )
        except Exception as e:
            raise e

    return wallet


@pytest.fixture()
def base_order(arbitrum_fork_config):
    """Create a BaseOrder instance for the specified chain."""
    return BaseOrder(arbitrum_fork_config)


@pytest.fixture()
def swap_order_weth_usdc(arbitrum_fork_config, chain_name):
    """Create a SwapOrder instance for WETH->USDC swap."""
    tokens = NETWORK_TOKENS[chain_name]
    return SwapOrder(arbitrum_fork_config, tokens["WETH"], tokens["USDC"])


@pytest.fixture()
def swap_order_usdc_weth(arbitrum_fork_config, chain_name):
    """Create a SwapOrder instance for USDC->WETH swap."""
    tokens = NETWORK_TOKENS[chain_name]
    return SwapOrder(arbitrum_fork_config, tokens["USDC"], tokens["WETH"])


@pytest.fixture
def account_with_positions(chain_name):
    """Return an address known to have open positions on the specified chain."""
    addresses = {
        "arbitrum": addr("0x9dd1497FF0775bab1FAEb45ea270F66b11496dDf"),
        "avalanche": addr("0x83806fe5D4166868498eB95e32c972E07A5C065D"),
    }
    return addresses[chain_name]


# GMX Core test fixtures
@pytest.fixture
def get_available_liquidity(gmx_config):
    """Create GetAvailableLiquidity instance."""
    return GetAvailableLiquidity(gmx_config)


@pytest.fixture
def get_borrow_apr(gmx_config):
    """Create GetBorrowAPR instance."""
    return GetBorrowAPR(gmx_config)


@pytest.fixture
def get_claimable_fees(gmx_config):
    """Create GetClaimableFees instance."""

    return GetClaimableFees(gmx_config)


@pytest.fixture
def get_funding_fee(gmx_config):
    """Create GetFundingFee instance."""

    return GetFundingFee(gmx_config)


@pytest.fixture
def markets(gmx_config):
    """Fixture to provide a Markets instance for testing."""
    return Markets(gmx_config)


@pytest.fixture
def get_gm_prices(gmx_config):
    """Create GetGMPrices instance."""
    from eth_defi.gmx.core.gm_prices import GetGMPrices

    return GetGMPrices(gmx_config)


@pytest.fixture
def get_open_interest(gmx_config):
    """Create GetOpenInterest instance."""
    from eth_defi.gmx.core.open_interest import GetOpenInterest

    return GetOpenInterest(gmx_config)


@pytest.fixture
def get_open_positions(gmx_config):
    """Create GetOpenPositions instance."""
    from eth_defi.gmx.core.open_positions import GetOpenPositions

    return GetOpenPositions(gmx_config)


@pytest.fixture
def gmx_open_positions(chain_rpc_url) -> GetOpenPositions:
    # Fork at latest block (no fork_block_number specified)
    # This ensures RPC has all state data available
    launch = fork_network_anvil(
        chain_rpc_url,
        test_request_timeout=100,
        launch_wait_seconds=60,
    )
    anvil_chain_fork = launch.json_rpc_url

    web3 = Web3(
        HTTPProvider(
            anvil_chain_fork,
            request_kwargs={"timeout": 100},
        )
    )
    gmx_config = GMXConfig(web3)
    get_open_positions = GetOpenPositions(gmx_config)

    return get_open_positions


@pytest.fixture
def get_glv_stats(gmx_config):
    """Create GlvStats instance."""

    return GlvStats(gmx_config)


@pytest.fixture()
def large_gm_eth_usdc_holder_arbitrum() -> HexAddress:
    """A random account picked from Arbitrum that holds GM-ETH-USDC tokens.

    GM tokens are liquidity pool tokens on GMX. This account holds the ETH/USDC market token.
    Found using arbiscan token holders page.
    """
    # Top holder of GM ETH/USDC (market: 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336)
    # https://arbiscan.io/token/0x70d95587d40A2caf56bd97485aB3Eec10Bee6336#balances
    return to_checksum_address("0x0628D46b5D145f183AdB6Ef1f2c97eD1C4701C55")  # GMX FeeReceiver


@pytest.fixture()
def wallet_with_gm_tokens(
    web3_arbitrum_fork,
    chain_name,
    test_address: HexAddress,
) -> None:
    """Fund the test wallet with GM tokens using Anvil storage manipulation.

    GM tokens are GMX market/liquidity pool tokens needed for withdrawal operations.
    We use anvil_setStorageAt to directly set the balance instead of transferring.
    """

    # Use different GM markets for different chains
    if chain_name == "avalanche":
        # GM AVAX/USDC market on Avalanche
        gm_market = "0xB7e69749E3d2EDd90ea59A4932EFEa2D41E245d7"
    else:
        # GM ETH/USDC market on Arbitrum (fallback, though we're skipping Arbitrum)
        gm_market = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"

    # Get GM token contract
    gm_token = fetch_erc20_details(web3_arbitrum_fork, gm_market)

    # Calculate storage slot for the balance
    # For most ERC20 tokens, balances are stored in slot 0
    # Storage slot = keccak256(abi.encode(address, uint256(slot)))

    # Try slot 0 (common for ERC20 balances mapping)
    slot = 0
    storage_slot = keccak(
        eth_abi.encode(["address", "uint256"], [test_address, slot]),
    )

    # Set balance to 100 GM tokens (18 decimals)
    balance = 100 * 10**18

    # Use anvil_setStorageAt to set the balance
    web3_arbitrum_fork.provider.make_request(
        "anvil_setStorageAt",
        [
            gm_market,
            "0x" + storage_slot.hex(),
            "0x" + balance.to_bytes(32, byteorder="big").hex(),
        ],
    )

    # Now approve GM tokens for the exchange router
    contract_addresses = get_contract_addresses(chain_name)
    exchange_router = contract_addresses.exchangerouter

    # Approve a large amount of GM tokens
    large_amount = 2**256 - 1  # Max uint256

    gm_token.contract.functions.approve(
        exchange_router,
        large_amount,
    ).transact({"from": test_address, "gas": 100000})


@pytest.fixture(scope="session")
def arbitrum_sepolia_config() -> GMXConfig:
    """
    Create GMX config for Arbitrum Sepolia testnet using real wallet from env vars.

    Requires:
    - ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY: Wallet private key
    - ARBITRUM_SEPOLIA_RPC_URL: RPC endpoint URL

    Skips tests if these environment variables are not set.
    """
    private_key = os.environ.get("ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY")
    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")

    if not private_key:
        pytest.skip("ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY environment variable not set")
    if not rpc_url:
        pytest.skip("ARBITRUM_SEPOLIA_RPC_URL environment variable not set")

    web3 = create_multi_provider_web3(rpc_url)
    install_chain_middleware(web3)

    # Create wallet from private key
    wallet = HotWallet.from_private_key(private_key)
    wallet_address = wallet.get_main_address()

    # Sync nonce
    wallet.sync_nonce(web3)

    # Create GMX config
    config = GMXConfig(web3, user_wallet_address=wallet_address)

    return config


@pytest.fixture(scope="session")
def arbitrum_sepolia_web3(arbitrum_sepolia_config) -> Web3:
    """Get Web3 instance for Arbitrum Sepolia."""
    return arbitrum_sepolia_config.web3


@pytest.fixture()
def trading_manager_sepolia(arbitrum_sepolia_config):
    """
    Create a GMXTrading instance for Arbitrum Sepolia testnet.
    Used by test_trading.py tests.
    """
    return GMXTrading(arbitrum_sepolia_config)


@pytest.fixture()
def position_verifier_sepolia(arbitrum_sepolia_config):
    """
    Create a GetOpenPositions instance to verify positions on Arbitrum Sepolia.
    Used by test_trading.py tests.
    """
    return GetOpenPositions(arbitrum_sepolia_config)


@pytest.fixture(scope="session")
def test_wallet_sepolia(arbitrum_sepolia_config):
    """Create a HotWallet for signing transactions on Sepolia."""
    private_key = os.environ.get("ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY")

    if not private_key:
        pytest.skip("ARBITRUM_GMX_TEST_SEPOLIA_PRIVATE_KEY environment variable not set")

    wallet = HotWallet.from_private_key(private_key)
    wallet.sync_nonce(arbitrum_sepolia_config.web3)

    return wallet


def _create_fork_config(
    web3_arbitrum_fork,
    anvil_private_key,
    wallet_with_all_tokens,
) -> GMXConfig:
    """Helper to create GMX config for Arbitrum mainnet fork.

    Args:
        web3_arbitrum_fork: Web3 instance with mock oracle already set up
        anvil_private_key: Private key for test wallet
        wallet_with_all_tokens: Fixture dependency to fund wallet

    Returns:
        Configured GMXConfig
    """
    # Create wallet from anvil private key
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet_address = wallet.get_main_address()

    # Note: wallet_with_all_tokens dependency already funded the wallet with:
    # - Native ETH, USDC, WETH, WBTC, LINK, ARB
    # No need to manually transfer tokens again

    # Create GMX config
    config = GMXConfig(web3_arbitrum_fork, user_wallet_address=wallet_address)

    # Approve tokens for GMX routers
    _approve_tokens_for_config(config, web3_arbitrum_fork, wallet_address)

    # Sync nonce AFTER approve transactions — _approve_tokens_for_config()
    # uses transact() which increments the on-chain nonce without going
    # through HotWallet's internal counter
    wallet.sync_nonce(web3_arbitrum_fork)

    return config


@pytest.fixture()
def arbitrum_fork_config(
    web3_arbitrum_fork,
    anvil_private_key,
    wallet_with_all_tokens,
    mock_oracle_fork,
) -> GMXConfig:
    """
    GMX config for Arbitrum mainnet fork with funded wallet and mock oracle (ETH price: 3450).
    Used for long position tests.

    This fixture:
    - Sets up mock oracle for price feeds with ETH at 3450
    - Creates a HotWallet from anvil default private key
    - Funds the wallet with all needed tokens (via wallet_with_all_tokens)
    - Approves tokens for GMX routers
    - Returns configured GMXConfig
    """
    return _create_fork_config(
        web3_arbitrum_fork,
        anvil_private_key,
        wallet_with_all_tokens,
    )


@pytest.fixture()
def trading_manager_fork(arbitrum_fork_config) -> GMXTrading:
    """
    GMXTrading instance for Arbitrum mainnet fork.
    """
    return GMXTrading(arbitrum_fork_config)


@pytest.fixture
def graphql_client():
    """Create a GMXSubsquidClient instance for testing."""
    return GMXSubsquidClient(chain="arbitrum")


@pytest.fixture
def account_with_positions():
    """Test account address that has historical positions.

    This is a public address used in the demo script.
    """
    return "0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c"


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
    web3 = Web3(HTTPProvider(tenderly_rpc_url, request_kwargs={"timeout": 100}))
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

    if not web3.is_connected():
        pytest.skip(f"Could not connect to Tenderly RPC at {tenderly_rpc_url}")

    return web3


@pytest.fixture()
def mock_oracle_tenderly(web3_tenderly: Web3) -> str:
    """Set up mock oracle on Tenderly virtual testnet."""
    setup_mock_oracle(web3_tenderly)
    return to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")


@pytest.fixture()
def test_wallet_tenderly(web3_tenderly: Web3, anvil_private_key) -> HotWallet:
    """Create a HotWallet for testing on Tenderly.

    Funds the wallet with ETH using Tenderly's setBalance.
    """
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_tenderly)

    # Fund wallet with ETH using Tenderly RPC
    eth_amount_wei = 1000 * 10**18
    try:
        web3_tenderly.provider.make_request(
            "tenderly_setBalance",
            [wallet.address, hex(eth_amount_wei)],
        )
    except Exception:
        # Try anvil_setBalance as fallback (some Tenderly versions support it)
        web3_tenderly.provider.make_request(
            "anvil_setBalance",
            [wallet.address, hex(eth_amount_wei)],
        )

    # Fund with WETH
    config = _get_chain_config_with_tokens("arbitrum")
    weth_address = config["native_token_address"]
    weth = fetch_erc20_details(web3_tenderly, weth_address)

    # Use a whale address and impersonate it
    large_weth_holder = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    # Fund the whale with gas
    try:
        web3_tenderly.provider.make_request(
            "tenderly_setBalance",
            [large_weth_holder, hex(10 * 10**18)],
        )
    except Exception:
        web3_tenderly.provider.make_request(
            "anvil_setBalance",
            [large_weth_holder, hex(10 * 10**18)],
        )

    # Transfer WETH to test wallet (Tenderly allows tx from any address)
    weth_amount = 1000 * 10**18
    try:
        weth.contract.functions.transfer(wallet.address, weth_amount).transact(
            {"from": large_weth_holder},
        )
    except Exception as e:
        logger.warning(f"Could not transfer WETH: {e}")

    # Fund with USDC
    usdc_address = config["usdc_address"]
    usdc = fetch_erc20_details(web3_tenderly, usdc_address)
    large_usdc_holder = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")

    try:
        web3_tenderly.provider.make_request(
            "tenderly_setBalance",
            [large_usdc_holder, hex(10 * 10**18)],
        )
    except Exception:
        web3_tenderly.provider.make_request(
            "anvil_setBalance",
            [large_usdc_holder, hex(10 * 10**18)],
        )

    usdc_amount = 100_000_000 * 10**6
    try:
        usdc.contract.functions.transfer(wallet.address, usdc_amount).transact(
            {"from": large_usdc_holder},
        )
    except Exception as e:
        logger.warning(f"Could not transfer USDC: {e}")

    return wallet


@pytest.fixture()
def arbitrum_tenderly_config(
    web3_tenderly: Web3,
    anvil_private_key,
    mock_oracle_tenderly,
) -> GMXConfig:
    """GMX config for Tenderly virtual testnet with funded wallet and mock oracle."""
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet_address = wallet.get_main_address()

    # Fund wallet with ETH
    eth_amount_wei = 1000 * 10**18
    try:
        web3_tenderly.provider.make_request(
            "tenderly_setBalance",
            [wallet_address, hex(eth_amount_wei)],
        )
    except Exception:
        web3_tenderly.provider.make_request(
            "anvil_setBalance",
            [wallet_address, hex(eth_amount_wei)],
        )

    # Fund with tokens
    config = _get_chain_config_with_tokens("arbitrum")

    # Fund WETH
    weth_address = config["native_token_address"]
    weth = fetch_erc20_details(web3_tenderly, weth_address)
    large_weth_holder = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    try:
        web3_tenderly.provider.make_request(
            "tenderly_setBalance",
            [large_weth_holder, hex(10 * 10**18)],
        )
        weth.contract.functions.transfer(wallet_address, 1000 * 10**18).transact(
            {"from": large_weth_holder},
        )
    except Exception as e:
        logger.warning(f"Could not transfer WETH: {e}")

    # Fund USDC
    usdc_address = config["usdc_address"]
    usdc = fetch_erc20_details(web3_tenderly, usdc_address)
    large_usdc_holder = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")

    try:
        web3_tenderly.provider.make_request(
            "tenderly_setBalance",
            [large_usdc_holder, hex(10 * 10**18)],
        )
        usdc.contract.functions.transfer(wallet_address, 100_000_000 * 10**6).transact(
            {"from": large_usdc_holder},
        )
    except Exception as e:
        logger.warning(f"Could not transfer USDC: {e}")

    # Create GMX config
    gmx_config = GMXConfig(web3_tenderly, user_wallet_address=wallet_address)

    # Approve tokens for GMX routers
    _approve_tokens_for_config(gmx_config, web3_tenderly, wallet_address)

    # Sync nonce AFTER all transact() calls — they increment the on-chain
    # nonce without going through HotWallet's internal counter
    wallet.sync_nonce(web3_tenderly)

    return gmx_config


@pytest.fixture()
def trading_manager_tenderly(arbitrum_tenderly_config) -> GMXTrading:
    """GMXTrading instance for Tenderly virtual testnet."""
    return GMXTrading(arbitrum_tenderly_config)


@pytest.fixture()
def position_verifier_tenderly(arbitrum_tenderly_config) -> GetOpenPositions:
    """GetOpenPositions instance for Tenderly virtual testnet."""
    return GetOpenPositions(arbitrum_tenderly_config)


# ============================================================================
# ISOLATED FORK FIXTURES - Each test gets its own fresh Anvil instance
# setup order: fork → oracle → wallet → config
# ============================================================================


@dataclass
class IsolatedForkEnv:
    """All components needed for an isolated GMX fork test."""

    web3: Web3
    config: GMXConfig
    wallet: HotWallet
    trading: GMXTrading
    positions: GetOpenPositions
    anvil_launch: Any  # AnvilLaunch object for cleanup


def _create_isolated_fork_env(
    rpc_url: str,
    private_key: str,
) -> IsolatedForkEnv:
    """Create a completely isolated fork environment matching debug.py's flow.

    Order of operations (matches debug.py exactly):
    1. Spawn fresh Anvil fork
    2. Setup mock oracle FIRST
    3. Fund wallet with ETH/WETH/USDC
    4. Create GMX config
    5. Approve tokens

    Args:
        rpc_url: Arbitrum RPC URL to fork from
        private_key: Private key for test wallet
        eth_price_usd: Optional ETH price for mock oracle (None = fetch from chain)

    Returns:
        IsolatedForkEnv with all components
    """
    # Whale addresses for token transfers
    large_usdc_holder = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
    large_weth_holder = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    # === Step 1: Spawn fresh Anvil fork ===
    launch = fork_network_anvil(
        rpc_url,
        unlocked_addresses=[large_usdc_holder, large_weth_holder],
    )

    web3 = create_multi_provider_web3(
        launch.json_rpc_url,
        default_http_timeout=(3.0, 180.0),
    )

    # === Step 2: Setup mock oracle FIRST (like debug.py) ===
    setup_mock_oracle(web3)

    # === Step 3: Setup and fund wallet ===
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3)
    wallet_address = wallet.get_main_address()

    # Fund with ETH (1000 ETH for multiple transactions with execution fees)
    eth_amount_wei = 100_000_000 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])

    # Fund whales with gas
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

    # Transfer USDC from whale
    usdc_address = config["usdc_address"]
    usdc = fetch_erc20_details(web3, usdc_address)
    usdc_amount = 100_000_000 * 10**6
    usdc.contract.functions.transfer(wallet_address, usdc_amount).transact(
        {"from": large_usdc_holder},
    )

    # === Step 4: Create GMX config ===
    gmx_config = GMXConfig(web3, user_wallet_address=wallet_address)

    # === Step 5: Approve tokens for GMX routers ===
    _approve_tokens_for_config(gmx_config, web3, wallet_address)

    # Sync nonce AFTER approve transactions — _approve_tokens_for_config()
    # uses transact() which increments the on-chain nonce without going
    # through HotWallet's internal counter
    wallet.sync_nonce(web3)

    # Create trading and position instances
    trading = GMXTrading(gmx_config)
    positions = GetOpenPositions(gmx_config)

    return IsolatedForkEnv(
        web3=web3,
        config=gmx_config,
        wallet=wallet,
        trading=trading,
        positions=positions,
        anvil_launch=launch,
    )


@pytest.fixture()
def isolated_fork_env() -> Generator[IsolatedForkEnv, None, None]:
    """Completely isolated fork environment for each test.

    Each test gets its own fresh Anvil instance with:
    - Mock oracle set up FIRST (matching debug.py)
    - Funded wallet with ETH/WETH/USDC
    - GMX config with approved tokens

    Usage:
        def test_something(isolated_fork_env):
            env = isolated_fork_env
            order = env.trading.open_position(...)
            signed = env.wallet.sign_transaction_with_new_nonce(order.transaction)
            env.web3.eth.send_raw_transaction(signed.rawTransaction)
    """
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM environment variable not set")

    # Use default Anvil private key
    private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

    env = _create_isolated_fork_env(rpc_url, private_key)

    try:
        yield env
    finally:
        # Clean up Anvil process
        env.anvil_launch.close(log_level=logging.ERROR)


@pytest.fixture()
def isolated_fork_env_short() -> Generator[IsolatedForkEnv, None, None]:
    """Isolated fork with ETH price set to 3550 (for short position tests)."""
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        pytest.skip("JSON_RPC_ARBITRUM environment variable not set")

    private_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

    env = _create_isolated_fork_env(rpc_url, private_key)

    try:
        yield env
    finally:
        env.anvil_launch.close(log_level=logging.ERROR)
