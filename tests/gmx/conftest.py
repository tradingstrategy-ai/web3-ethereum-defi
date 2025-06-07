import logging
import os
from typing import Generator, Any

from eth_account import Account
from eth_pydantic_types import HexStr
from eth_utils import to_checksum_address
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData
from eth_defi.gmx.liquidity import GMXLiquidityManager
from eth_defi.gmx.order import GMXOrderManager
from eth_defi.gmx.trading import GMXTrading
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.token import fetch_erc20_details, TokenDetails

import pytest
from eth_typing import HexAddress


# Configure chain-specific parameters
CHAIN_CONFIG = {
    "arbitrum": {
        "rpc_env_var": "ARBITRUM_JSON_RPC_URL",
        "chain_id": 42161,
        "fork_block_number": 338206286,
        "wbtc_address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "usdc_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC on Arbitrum
        "usdt_address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",  # USDT on Arbitrum
        "link_address": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
        "wsol_address": "0x2bcC6D6CdBbDC0a4071e48bb3B969b06B3330c07",  # WSOL on Arbitrum
        "arb_address": "0x912CE59144191C1204E64559FE8253a0e49E6548",  # ARB on Arbitrum
        "native_token_address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
        "aave_address": "0xba5DdD1f9d7F570dc94a51479a000E3BCE967196",  # AAVE
    },
    "avalanche": {
        "rpc_env_var": "AVALANCHE_JSON_RPC_URL",
        "chain_id": 43114,
        "fork_block_number": 60491219,
        "wbtc_address": "0x152b9d0FdC40C096757F570A51E494bd4b943E50",
        "usdc_address": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",  # USDC on Avalanche
        "usdt_address": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",  # USDT on Avalanche
        "wavax_address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX on Avalanche
        "native_token_address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX
    },
}

# Suppress logs to avoid anvil logs cluttering test output
original_log_handlers = logging.getLogger().handlers[:]
for handler in original_log_handlers:
    logging.getLogger().removeHandler(handler)


def pytest_generate_tests(metafunc):
    """Generate parametrized tests for multiple chains if the test uses 'chain_name' parameter."""
    if "chain_name" in metafunc.fixturenames:
        # Check which chains have their environment variables set
        available_chains = []
        for chain in CHAIN_CONFIG:
            if os.environ.get(CHAIN_CONFIG[chain]["rpc_env_var"]):
                available_chains.append(chain)

        # Skip all tests if no chains are available
        if not available_chains:
            pytest.skip("No chain RPC URLs available")

        # Parametrize tests with available chains
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

    `To find large holder accounts, use bscscan <https://arbiscan.io/accounts>`_.
    """
    # Binance Hot Wallet 20
    return HexAddress(HexStr("0xF977814e90dA44bFA03b6295A0616a897441aceC"))


@pytest.fixture()
def large_wbtc_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of WBTC.

    This account is unlocked on Anvil, so you have access to good WBTC stash.

    `To find large holder accounts, use arbiscan <https://arbiscan.io/accounts>`_.
    """
    # https://arbiscan.io/address/0xdcf711cb8a1e0856ff1cb1cfd52c5084f5b28030
    return HexAddress(HexStr("0xdcF711cB8A1e0856fF1cB1CfD52C5084f5B28030"))


@pytest.fixture()
def large_wavax_holder() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of WAVAX.

    This account is unlocked on Anvil, so you have access to good WAVAX stash.

    `To find large holder accounts, use bscscan <https://snowtrace.io/accounts>`_.
    """
    # https://snowtrace.io/address/0xefdc8FC1145ea88e3f5698eE7b7b432F083B4246
    # Upbit: Hot Wallet 1
    return HexAddress(HexStr("0x73AF3bcf944a6559933396c1577B257e2054D935"))


@pytest.fixture()
def large_wbtc_holder_avalanche() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of WBTC.

    This account is unlocked on Anvil, so you have access to good WBTC stash.

    `To find large holder accounts, use arbiscan <https://snowtrace.io/accounts>`_.
    """
    # https://snowtrace.io/address/0x8ffDf2DE812095b1D19CB146E4c004587C0A0692
    return HexAddress(HexStr("0x8ffDf2DE812095b1D19CB146E4c004587C0A0692"))


@pytest.fixture()
def large_usdc_holder_arbitrum() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.
    """
    # https://arbiscan.io/address/0xb38e8c17e38363af6ebdcb3dae12e0243582891d#asset-multichain
    return HexAddress(HexStr("0xB38e8c17e38363aF6EbdCb3dAE12e0243582891D"))


@pytest.fixture()
def large_usdc_holder_avalanche() -> HexAddress:
    """A random account picked from Avalanche Smart chain that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.
    """
    # https://snowscan.xyz/address/0x9f8c163cba728e99993abe7495f06c0a3c8ac8b9
    return HexAddress(HexStr("0x9f8c163cBA728e99993ABe7495F06c0A3c8Ac8b9"))


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
    return HexAddress(HexStr("0x4e9f683A27a6BdAD3FC2764003759277e93696e6"))


@pytest.fixture()
def large_arb_holder_arbitrum() -> HexAddress:
    # Binance Hot wallet 20
    return HexAddress(HexStr("0xF977814e90dA44bFA03b6295A0616a897441aceC"))


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
) -> Generator[str, Any, None]:
    """Create a testable fork of the live chain using Anvil."""
    unlocked_addresses = [large_eth_holder, large_wbtc_holder]

    if chain_name == "arbitrum":
        unlocked_addresses.append(large_usdc_holder_arbitrum)
        unlocked_addresses.append(gmx_controller_arbitrum)
        unlocked_addresses.append(large_weth_holder_arbitrum)
        unlocked_addresses.append(gmx_keeper_arbitrum)
    elif chain_name == "avalanche":
        unlocked_addresses.append(large_wavax_holder)
        unlocked_addresses.append(large_usdc_holder_avalanche)
        unlocked_addresses.append(large_wbtc_holder_avalanche)
        unlocked_addresses.append(large_link_holder_avalanche)

    fork_block = CHAIN_CONFIG[chain_name]["fork_block_number"]

    launch = fork_network_anvil(
        chain_rpc_url,
        unlocked_addresses=unlocked_addresses,
        test_request_timeout=30,
        fork_block_number=fork_block,
        launch_wait_seconds=40,
    )

    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_fork(anvil_chain_fork: str) -> Web3:
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
        pytest.skip(f"Connected to chain ID {chain_id}, but expected {chain_name.upper()} ({expected_chain_id})")

    return web3


@pytest.fixture()
def gmx_config(web3_mainnet, chain_name) -> GMXConfig:
    """Create a GMX configuration for the specified chain."""
    return GMXConfig(web3_mainnet)


@pytest.fixture()
def market_data(gmx_config) -> GMXMarketData:
    """Create a GMXMarketData instance for the specified chain."""
    return GMXMarketData(gmx_config)


@pytest.fixture()
def api(gmx_config):
    """Create a GMXAPI instance for the specified chain."""
    return GMXAPI(gmx_config)


# Token fixtures for specific chains
@pytest.fixture()
def wbtc_arbitrum(web3_fork: Web3, chain_name) -> TokenDetails:
    """WBTC token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    return fetch_erc20_details(web3_fork, CHAIN_CONFIG["arbitrum"]["wbtc_address"])


@pytest.fixture()
def usdc_arbitrum(web3_fork: Web3, chain_name) -> TokenDetails:
    """USDC token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    return fetch_erc20_details(web3_fork, CHAIN_CONFIG["arbitrum"]["usdc_address"])


@pytest.fixture()
def usdt_arbitrum(web3_fork: Web3, chain_name) -> TokenDetails:
    """USDT token on Arbitrum."""
    if chain_name != "arbitrum":
        pytest.skip("This fixture is for Arbitrum only")
    return fetch_erc20_details(web3_fork, CHAIN_CONFIG["arbitrum"]["usdt_address"])


@pytest.fixture()
def wbtc_avalanche(web3_fork: Web3, chain_name) -> TokenDetails:
    """WBTC token on Avalanche."""
    if chain_name != "avalanche":
        pytest.skip("This fixture is for Avalanche only")
    return fetch_erc20_details(web3_fork, CHAIN_CONFIG["avalanche"]["wbtc_address"])


@pytest.fixture()
def wavax_avalanche(web3_fork: Web3, chain_name) -> TokenDetails:
    """WAVAX token on Avalanche."""
    if chain_name != "avalanche":
        pytest.skip("This fixture is for Avalanche only")
    return fetch_erc20_details(
        web3_fork,
        CHAIN_CONFIG["avalanche"]["wavax_address"],
        contract_name="./WAVAX.json",
    )


# Generic token fixtures that adapt to the current chain
@pytest.fixture()
def wbtc(web3_fork: Web3, chain_name) -> TokenDetails:
    """WBTC token details for the specified chain."""
    wbtc_address = CHAIN_CONFIG[chain_name]["wbtc_address"]
    return fetch_erc20_details(web3_fork, wbtc_address)


@pytest.fixture()
def usdc(web3_fork: Web3, chain_name) -> TokenDetails:
    """USDC token details for the specified chain."""
    usdc_address = CHAIN_CONFIG[chain_name]["usdc_address"]
    return fetch_erc20_details(web3_fork, usdc_address)


@pytest.fixture()
def wsol(web3_fork: Web3, chain_name) -> TokenDetails:
    """WSOL token details for the specified chain."""
    wsol_address = CHAIN_CONFIG[chain_name]["wsol_address"]
    return fetch_erc20_details(web3_fork, wsol_address)


@pytest.fixture()
def link(web3_fork: Web3, chain_name) -> TokenDetails:
    """USDC token details for the specified chain."""
    usdc_address = CHAIN_CONFIG[chain_name]["link_address"]
    return fetch_erc20_details(web3_fork, usdc_address)


@pytest.fixture()
def arb(web3_fork: Web3, chain_name) -> TokenDetails:
    """ARB token details for the specified chain."""
    arb_address = CHAIN_CONFIG[chain_name]["arb_address"]
    return fetch_erc20_details(web3_fork, arb_address)


@pytest.fixture()
def usdt(web3_fork: Web3, chain_name) -> TokenDetails:
    """USDT token details for the specified chain."""
    usdt_address = CHAIN_CONFIG[chain_name]["usdt_address"]
    return fetch_erc20_details(web3_fork, usdt_address)


@pytest.fixture()
def aave(web3_fork: Web3, chain_name) -> TokenDetails:
    """AAVE token details for the specified chain."""
    aave_address = CHAIN_CONFIG[chain_name]["aave_address"]
    return fetch_erc20_details(web3_fork, aave_address)


@pytest.fixture()
def wrapped_native_token(web3_fork: Web3, chain_name) -> TokenDetails:
    """Get the native wrapped token (WETH for Arbitrum, WAVAX for Avalanche)."""
    native_address = CHAIN_CONFIG[chain_name]["native_token_address"]
    contract_name = "./WAVAX.json" if chain_name == "avalanche" else "ERC20MockDecimals.json"
    return fetch_erc20_details(web3_fork, native_address, contract_name=contract_name)


# Wallet funding fixtures
@pytest.fixture()
def wallet_with_native_token(
    web3_fork: Web3,
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
        wavax = fetch_erc20_details(web3_fork, wavax_address, contract_name="./WAVAX.json")
        wavax.contract.functions.deposit().transact({"from": test_address, "value": amount})
    else:
        # Fund the account with native gas tokens of arbitrum
        amount_wei = 5000000 * 10**18
        web3_fork.provider.make_request("tenderly_setBalance", [gmx_controller_arbitrum, hex(amount_wei)])
        web3_fork.provider.make_request("tenderly_setBalance", [test_address, hex(amount_wei)])


@pytest.fixture()
def wallet_with_usdc(
    web3_fork: Web3,
    chain_name,
    test_address: HexAddress,
    large_usdc_holder_arbitrum,
    large_usdc_holder_avalanche,
) -> None:
    """Fund the test wallet with USDC."""
    if chain_name == "arbitrum":
        usdc_address = CHAIN_CONFIG["arbitrum"]["usdc_address"]
        usdc = fetch_erc20_details(web3_fork, usdc_address)
        large_holder = large_usdc_holder_arbitrum
        amount = 100_000 * 10**6  # 100,000 USDC (6 decimals)
    else:  # avalanche
        usdc_address = CHAIN_CONFIG["avalanche"]["usdc_address"]
        usdc = fetch_erc20_details(web3_fork, usdc_address)
        large_holder = large_usdc_holder_avalanche
        amount = 100_000 * 10**6  # 100,000 USDC (6 decimals)

    try:
        usdc.contract.functions.transfer(test_address, amount).transact({"from": large_holder})
    except Exception as e:
        # If the transfer fails, skip the test instead of failing
        pytest.skip(f"Could not transfer USDC to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_wbtc(
    web3_fork: Web3,
    chain_name,
    test_address: HexAddress,
    large_wbtc_holder,
    large_wbtc_holder_avalanche,
) -> None:
    """Fund the test wallet with WBTC."""
    if chain_name == "arbitrum":
        wbtc_address = to_checksum_address(CHAIN_CONFIG["arbitrum"]["link_address"])
        amount = 5 * 10**8  # 5 WBTC (8 decimals)
        # else:  # avalanche
        #     wbtc_address = CHAIN_CONFIG["avalanche"]["wbtc_address"]
        #     wbtc = fetch_erc20_details(web3_fork, wbtc_address)
        #     large_holder = large_wbtc_holder_avalanche
        #     amount = 5 * 10 ** 8  # 1 WBTC (8 decimals)
        try:
            web3_fork.provider.make_request("tenderly_addErc20Balance", [wbtc_address, [test_address], hex(amount)])
            # wbtc.contract.functions.transfer(test_address, amount).transact({"from": large_holder})
        except Exception as e:
            # If the transfer fails, skip the test instead of failing
            pytest.skip(f"Could not transfer WBTC to test wallet: {str(e)}")


@pytest.fixture()
def wallet_with_link(web3_fork, chain_name, test_address: HexAddress, large_link_holder_avalanche) -> None:
    """Fund the test wallet with LINK."""
    amount = 10000 * 10**18
    if chain_name == "avalanche":
        link_address = "0x5947BB275c521040051D82396192181b413227A3"
        link = fetch_erc20_details(web3_fork, link_address)
        # 10k LINK tokens
        try:
            link.contract.functions.transfer(test_address, amount).transact({"from": large_link_holder_avalanche})
        except Exception as e:
            # If the transfer fails, skip the test instead of failing
            pytest.skip(f"Could not transfer LINK to test wallet: {str(e)}")
    # else:
    #     link_address = to_checksum_address(CHAIN_CONFIG[chain_name]["link_address"])
    #
    #     web3_fork.provider.make_request("tenderly_addErc20Balance", [link_address, [test_address], hex(amount)])


@pytest.fixture()
def wallet_with_arb(web3_fork, chain_name, test_address: HexAddress, large_arb_holder_arbitrum: HexAddress) -> None:
    """Fund the test wallet with LINK."""
    amount = 1000000 * 10**18
    if chain_name == "arbitrum":
        try:
            arb_address = to_checksum_address(CHAIN_CONFIG[chain_name]["arb_address"])
            arb = fetch_erc20_details(web3_fork, arb_address)
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


@pytest.fixture()
def gmx_config_fork(
    web3_fork: Web3,
    chain_name: str,
    test_address: HexAddress,
    anvil_private_key: HexAddress,
    wallet_with_all_tokens,
) -> GMXConfig:
    """Create a GMX configuration with a wallet for testing transactions."""
    from eth_account import Account
    from eth_defi.hotwallet import HotWallet

    # Create a hot wallet with the anvil private key
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_fork)

    # The wallet_with_all_tokens fixture ensures the wallet has all necessary tokens
    return GMXConfig(web3_fork, wallet=wallet, user_wallet_address=test_address)


@pytest.fixture()
def liquidity_manager(gmx_config_fork):
    """Create a GMXLiquidityManager instance for the specified chain."""
    return GMXLiquidityManager(gmx_config_fork)


@pytest.fixture()
def order_manager(gmx_config_fork):
    """Create a GMXOrderManager instance for the specified chain."""
    return GMXOrderManager(gmx_config_fork)


@pytest.fixture()
def trading_manager(gmx_config_fork):
    """Create a GMXTrading instance for the specified chain."""
    return GMXTrading(gmx_config_fork)


@pytest.fixture
def account_with_positions(chain_name):
    """Return an address known to have open positions on the specified chain."""
    addresses = {
        "arbitrum": HexAddress(HexStr("0x9dd1497FF0775bab1FAEb45ea270F66b11496dDf")),
        "avalanche": HexAddress(HexStr("0x83806fe5D4166868498eB95e32c972E07A5C065D")),
    }
    return addresses[chain_name]
