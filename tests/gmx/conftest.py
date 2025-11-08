import logging
import os
from typing import Generator, Any

import eth_abi
from eth_pydantic_types import HexStr
from eth_utils import to_checksum_address, keccak
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware, get_chain_id_by_name
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core import GetOpenPositions, GetPoolTVL, Markets, GetFundingFee, GetClaimableFees, GetBorrowAPR, GetAvailableLiquidity
from eth_defi.gmx.core.glv_stats import GlvStats
from eth_defi.gmx.data import GMXMarketData

from eth_defi.gmx.order.base_order import BaseOrder
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.contracts import NETWORK_TOKENS, get_contract_addresses
from eth_defi.gmx.synthetic_tokens import get_gmx_synthetic_token_by_symbol
from eth_defi.gmx.trading import GMXTrading
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_account import Account
from eth_defi.hotwallet import HotWallet

import pytest
from eth_typing import HexAddress

from eth_defi.utils import addr

# Fork configuration constants
FORK_BLOCK_ARBITRUM = 392496384
MOCK_ETH_PRICE = 3450  # USD
MOCK_USDC_PRICE = 1  # USD


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
            "rpc_env_var": "ARBITRUM_JSON_RPC_URL",
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


# CHAIN_CONFIG with dynamic address fetching for Arbitrum using GMX API
def _get_arbitrum_config():
    """Get Arbitrum config with addresses from GMX API."""
    chain_id = get_chain_id_by_name("arbitrum")
    return {
        "rpc_env_var": "ARBITRUM_JSON_RPC_URL",
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


CHAIN_CONFIG = {
    "arbitrum": _get_arbitrum_config(),
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


def pytest_generate_tests(metafunc):
    """Generate parametrised tests for Arbitrum only (Avalanche skipped)."""
    if "chain_name" in metafunc.fixturenames:
        # Only test Arbitrum if RPC URL is available
        available_chains = []
        if os.environ.get(CHAIN_CONFIG["arbitrum"]["rpc_env_var"]):
            available_chains.append("arbitrum")

        # Skip all tests if no chains are available
        if not available_chains:
            pytest.skip("No ARBITRUM_JSON_RPC_URL environment variable available")

        # Parametrize tests with available chains (only arbitrum)
        metafunc.parametrize("chain_name", available_chains)


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
    # This address has consistent USDC balance across different blocks
    return to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")


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
        test_request_timeout=30,
        fork_block_number=FORK_BLOCK_ARBITRUM,
        launch_wait_seconds=40,
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
            request_kwargs={"timeout": 30},
        )
    )
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


# TODO: Rename it to web3_abitrum
@pytest.fixture()
def web3_mainnet(chain_name, chain_rpc_url):
    """Set up a Web3 connection to the mainnet chain."""
    web3 = Web3(HTTPProvider(chain_rpc_url))
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
    return fetch_erc20_details(
        web3_arbitrum_fork,
        CHAIN_CONFIG["arbitrum"]["wbtc_address"],
    )


@pytest.fixture()
def usdc_arbitrum(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDC token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    return fetch_erc20_details(
        web3_arbitrum_fork,
        CHAIN_CONFIG["arbitrum"]["usdc_address"],
    )


@pytest.fixture()
def usdt_arbitrum(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDT token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    return fetch_erc20_details(
        web3_arbitrum_fork,
        CHAIN_CONFIG["arbitrum"]["usdt_address"],
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
    wbtc_address = CHAIN_CONFIG[chain_name]["wbtc_address"]
    return fetch_erc20_details(web3_arbitrum_fork, wbtc_address)


@pytest.fixture()
def usdc(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDC token details for the specified chain."""
    usdc_address = CHAIN_CONFIG[chain_name]["usdc_address"]
    return fetch_erc20_details(web3_arbitrum_fork, usdc_address)


@pytest.fixture()
def wsol(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """WSOL token details for the specified chain."""
    wsol_address = CHAIN_CONFIG[chain_name]["wsol_address"]
    return fetch_erc20_details(web3_arbitrum_fork, wsol_address)


@pytest.fixture()
def link(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """LINK token details for the specified chain."""
    link_address = CHAIN_CONFIG[chain_name]["link_address"]
    return fetch_erc20_details(web3_arbitrum_fork, link_address)


@pytest.fixture()
def arb(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """ARB token details for the specified chain."""
    arb_address = CHAIN_CONFIG[chain_name]["arb_address"]
    return fetch_erc20_details(web3_arbitrum_fork, arb_address)


@pytest.fixture()
def usdt(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """USDT token details for the specified chain."""
    usdt_address = CHAIN_CONFIG[chain_name]["usdt_address"]
    return fetch_erc20_details(web3_arbitrum_fork, usdt_address)


@pytest.fixture()
def aave(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """AAVE token details for the specified chain."""
    aave_address = CHAIN_CONFIG[chain_name]["aave_address"]
    return fetch_erc20_details(web3_arbitrum_fork, aave_address)


@pytest.fixture()
def wrapped_native_token(web3_arbitrum_fork: Web3, chain_name) -> TokenDetails:
    """Get the native wrapped token (WETH for Arbitrum, WAVAX for Avalanche)."""
    native_address = CHAIN_CONFIG[chain_name]["native_token_address"]
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
        wavax_address = CHAIN_CONFIG["avalanche"]["native_token_address"]
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
    if chain_name == "arbitrum":
        usdc_address = CHAIN_CONFIG["arbitrum"]["usdc_address"]
        usdc = fetch_erc20_details(web3_arbitrum_fork, usdc_address)
        large_holder = large_usdc_holder_arbitrum
        amount = 100_000 * 10**6  # 100,000 USDC (6 decimals)
    else:  # avalanche
        usdc_address = CHAIN_CONFIG["avalanche"]["usdc_address"]
        usdc = fetch_erc20_details(web3_arbitrum_fork, usdc_address)
        large_holder = large_usdc_holder_avalanche
        amount = 100_000 * 10**6  # 100,000 USDC (6 decimals)

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
    if chain_name == "arbitrum":
        wbtc_address = CHAIN_CONFIG["arbitrum"]["wbtc_address"]
        wbtc = fetch_erc20_details(web3_arbitrum_fork, wbtc_address)
        large_holder = large_wbtc_holder
        amount = 5 * 10**8  # 5 WBTC (8 decimals)
    else:  # avalanche
        wbtc_address = CHAIN_CONFIG["avalanche"]["wbtc_address"]
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

        link_address = CHAIN_CONFIG[chain_name]["link_address"]
        link = fetch_erc20_details(web3_arbitrum_fork, link_address)
        # 10k LINK tokens
        try:
            link.contract.functions.transfer(test_address, amount).transact(
                {"from": large_link_holder_avalanche},
            )
        except Exception as e:
            # If the transfer fails, skip the test instead of failing
            pytest.skip(f"Could not transfer LINK to test wallet: {str(e)}")
    # else:
    #     link_address = to_checksum_address(CHAIN_CONFIG[chain_name]["link_address"])
    #
    #     web3_arbitrum_fork.provider.make_request("anvil_addErc20Balance", [link_address, [test_address], hex(amount)])


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
            arb_address = to_checksum_address(CHAIN_CONFIG[chain_name]["arb_address"])
            arb = fetch_erc20_details(web3_arbitrum_fork, arb_address)
            arb.contract.functions.transfer(test_address, amount).transact({"from": large_arb_holder_arbitrum})
        except Exception as e:
            # If the transfer fails, skip the test instead of failing
            pytest.skip(f"Could not transfer ARB to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_all_tokens(
    wallet_with_native_token,
    wallet_with_usdc,
    wallet_with_wbtc,
    wallet_with_link,
    wallet_with_arb,
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
# def order_manager(gmx_config_fork):
#     """Create a GMXOrderManager instance for the specified chain."""
#     return GMXOrderManager(gmx_config_fork)


@pytest.fixture()
def trading_manager(gmx_config_fork):
    """Create a GMXTrading instance for the specified chain."""
    return GMXTrading(gmx_config_fork)


@pytest.fixture()
def test_wallet(web3_arbitrum_fork, anvil_private_key):
    """Create a HotWallet for testing transactions."""
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_arbitrum_fork)
    return wallet


@pytest.fixture()
def base_order(gmx_config_fork):
    """Create a BaseOrder instance for the specified chain."""
    return BaseOrder(gmx_config_fork)


@pytest.fixture()
def swap_order_weth_usdc(gmx_config_fork, chain_name):
    """Create a SwapOrder instance for WETH->USDC swap."""
    tokens = NETWORK_TOKENS[chain_name]
    return SwapOrder(gmx_config_fork, tokens["WETH"], tokens["USDC"])


@pytest.fixture()
def swap_order_usdc_weth(gmx_config_fork, chain_name):
    """Create a SwapOrder instance for USDC->WETH swap."""
    tokens = NETWORK_TOKENS[chain_name]
    return SwapOrder(gmx_config_fork, tokens["USDC"], tokens["WETH"])


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
    launch = fork_network_anvil(
        chain_rpc_url,
        test_request_timeout=30,
        fork_block_number=373279955,
        launch_wait_seconds=40,
    )
    anvil_chain_fork = launch.json_rpc_url

    web3 = Web3(
        HTTPProvider(
            anvil_chain_fork,
            request_kwargs={"timeout": 30},
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


@pytest.fixture()
def arbitrum_fork_config(
    web3_arbitrum_fork,
    anvil_private_key,
    wallet_with_all_tokens,
) -> GMXConfig:
    """
    GMX config for Arbitrum mainnet fork with funded wallet.

    This fixture:
    - Creates a HotWallet from anvil default private key
    - wallet_with_all_tokens already funds the wallet with all needed tokens
    - Approves tokens for GMX routers
    - Returns configured GMXConfig
    """

    # Create wallet from anvil private key
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_arbitrum_fork)
    wallet_address = wallet.get_main_address()

    # Note: wallet_with_all_tokens dependency already funded the wallet with:
    # - Native ETH, USDC, WETH, WBTC, LINK, ARB
    # No need to manually transfer tokens again

    # Create GMX config
    config = GMXConfig(web3_arbitrum_fork, user_wallet_address=wallet_address)

    # Approve tokens for GMX routers
    _approve_tokens_for_config(config, web3_arbitrum_fork, wallet_address)

    return config


@pytest.fixture()
def trading_manager_fork(arbitrum_fork_config) -> GMXTrading:
    """
    GMXTrading instance for Arbitrum mainnet fork.
    Used by test_trading.py tests.
    """
    return GMXTrading(arbitrum_fork_config)


@pytest.fixture()
def position_verifier_fork(arbitrum_fork_config) -> GetOpenPositions:
    """
    GetOpenPositions instance for Arbitrum mainnet fork.
    Used by test_trading.py tests to verify positions.
    """
    return GetOpenPositions(arbitrum_fork_config)
