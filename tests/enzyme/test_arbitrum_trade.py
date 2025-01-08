"""Enzyme trade on Arbitrum.

- Use Arbitrum mainnet fork for testing

- Deploy a vault with a guard

- Do swap and credit supply tests
"""
import os

import pytest

from eth_account import Account
from eth_typing import HexAddress

from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.constants import AAVE_V3_NETWORKS, AAVE_V3_DEPLOYMENTS
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.loan import supply
from eth_defi.abi import get_deployed_contract, encode_function_call
from eth_defi.enzyme.deployment import ARBITRUM_DEPLOYMENT
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.generic_adapter_vault import deploy_vault_with_generic_adapter
from eth_defi.enzyme.uniswap_v3 import prepare_swap
from eth_defi.enzyme.vault import Vault
from eth_defi.hotwallet import HotWallet
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import (
    assert_transaction_success_with_explanation,
)
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment, fetch_deployment,
)
from eth_defi.uniswap_v3.pool import PoolDetails, fetch_pool_details
from eth_defi.aave_v3.deployment import fetch_deployment as fetch_aave_v3_deployment

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")


@pytest.fixture()
def usdt_whale() -> HexAddress:
    """A random account picked, holds a lot of stablecoin"""
    # https://arbiscan.io/token/0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9#balances
    return HexAddress("0x8f9c79B9De8b0713dCAC3E535fc5A1A92DB6EA2D")


@pytest.fixture()
def anvil(usdt_whale) -> AnvilLaunch:
    """Launch Polygon fork."""

    anvil = launch_anvil(
        fork_url=JSON_RPC_ARBITRUM,
        unlocked_addresses=[usdt_whale],
    )
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture
def deployer(web3) -> Account:
    return web3.eth.accounts[0]


@pytest.fixture
def vault_owner(web3) -> Account:
    return web3.eth.accounts[1]


@pytest.fixture
def asset_manager(web3) -> Account:
    return web3.eth.accounts[2]


@pytest.fixture
def user_1(web3) -> Account:
    return web3.eth.accounts[3]


@pytest.fixture
def usdt(web3) -> TokenDetails:
    details = fetch_erc20_details(web3, "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9")
    return details


@pytest.fixture
def ausdt(web3) -> TokenDetails:
    details = fetch_erc20_details(web3, AAVE_V3_NETWORKS["arbitrum"].token_contracts["USDT"].deposit_address)
    return details


@pytest.fixture
def weth(web3) -> TokenDetails:
    # https://arbiscan.io/token/0x82af49447d8a07e3bd95bd0d56f35241523fbab1
    details = fetch_erc20_details(web3, "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    return details


@pytest.fixture
def wbtc(web3) -> TokenDetails:
    details = fetch_erc20_details(web3, "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f")
    return details


@pytest.fixture()
def enzyme(
    web3,
) -> EnzymeDeployment:
    deployment = EnzymeDeployment.fetch_deployment(web3, ARBITRUM_DEPLOYMENT)
    return deployment


@pytest.fixture()
def terms_of_service(web3) -> Contract:
    tos = get_deployed_contract(
        web3,
        "terms-of-service/TermsOfService.json",
        "0xDCD7C644a6AA72eb2f86781175b18ADc30Aa4f4d", # https://github.com/tradingstrategy-ai/terms-of-service
    )
    return tos


@pytest.fixture()
def vault(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    weth: TokenDetails,
    wbtc: TokenDetails,
    usdt: TokenDetails,
    terms_of_service: Contract,
) -> Vault:
    """Deploy an Enzyme vault.

    Set up a forge compatible deployer account.

    - GuardV0
    - GuardedGenericAdapter
    - TermsOfService
    - TermedVaultUSDCPaymentForwarder
    """

    hot_wallet = HotWallet.create_for_testing(web3)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware_anvil(hot_wallet.account))

    # TODO: Hack
    enzyme.deployer = hot_wallet.address

    return deploy_vault_with_generic_adapter(
        enzyme,
        deployer=hot_wallet,
        asset_manager=asset_manager,
        owner=deployer,
        denomination_asset=usdt.contract,
        terms_of_service=terms_of_service,
        whitelisted_assets=[weth, wbtc, usdt],
        uniswap_v3=True,
        uniswap_v2=False,
        one_delta=False,
        aave=True,
    )


@pytest.fixture()
def uniswap_v3(
    web3: Web3,
) -> UniswapV3Deployment:
    addresses = UNISWAP_V3_DEPLOYMENTS["arbitrum"]
    uniswap = fetch_deployment(
        web3,
        addresses["factory"],
        addresses["router"],
        addresses["position_manager"],
        addresses["quoter"],
    )
    return uniswap


@pytest.fixture()
def aave_v3(web3) -> AaveV3Deployment:
    deployment_info = AAVE_V3_DEPLOYMENTS["arbitrum"]
    return fetch_aave_v3_deployment(
        web3,
        pool_address=deployment_info["pool"],
        data_provider_address=deployment_info["data_provider"],
        oracle_address=deployment_info["oracle"],
    )


@pytest.fixture()
def weth_usdt_pool(web3) -> PoolDetails:
    # https://tradingstrategy.ai/trading-view/arbitrum/uniswap-v3/eth-usdt-fee-5
    return fetch_pool_details(web3, "0x641c00a822e8b671738d32a431a4fb6074e5c79d")


def test_enzyme_uniswap_v3_arbitrum(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    user_1,
    usdt_whale,
    enzyme: EnzymeDeployment,
    vault: Vault,
    weth: TokenDetails,
    usdt: TokenDetails,
    wbtc: TokenDetails,
    uniswap_v3: UniswapV3Deployment,
    weth_usdt_pool: PoolDetails,
):
    """Make a vault swap USDT->WETH."""

    # Check that all the assets are supported on the Enzyme protocol level
    # (Separate from our guard whitelist)
    assert vault.is_supported_asset(usdt.address)
    assert vault.is_supported_asset(weth.address)
    assert vault.is_supported_asset(wbtc.address)

    assert usdt.fetch_balance_of(usdt_whale) > 500, f"Whale balance is {usdt.fetch_balance_of(usdt_whale)}"

    # Get USDT, to the initial shares buy
    tx_hash = usdt.contract.functions.transfer(user_1, 500 * 10 ** 6,).transact({"from": usdt_whale})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = usdt.contract.functions.approve(vault.comptroller.address, 500 * 10 ** 6).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = vault.comptroller.functions.buyShares(500 * 10 ** 6, 1).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded

    pool_fee_raw = 500  # 5 BPS

    # Vault swaps USDC->ETH for both users
    # Buy ETH worth of 200 USD
    prepared_tx = prepare_swap(
        enzyme,
        vault,
        uniswap_v3,
        vault.generic_adapter,
        token_in=usdt.contract,
        token_out=weth.contract,
        pool_fees=[pool_fee_raw],
        token_in_amount=200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Bought ETH landed in the vault
    assert 0.01 < weth.fetch_balance_of(vault.address) < 1


def test_enzyme_aave_arbitrum(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    user_1,
    usdt_whale,
    enzyme: EnzymeDeployment,
    vault: Vault,
    weth: TokenDetails,
    usdt: TokenDetails,
    ausdt: TokenDetails,
    aave_v3: AaveV3Deployment,
):
    """Make a Aave deposit USDT -> aUSDT that goes through a vault."""

    # Check that all the assets are supported on the Enzyme protocol level
    # (Separate from our guard whitelist)
    assert vault.is_supported_asset(usdt.address)
    assert vault.is_supported_asset(weth.address)

    assert usdt.fetch_balance_of(usdt_whale) > 500, f"Whale balance is {usdt.fetch_balance_of(usdt_whale)}"

    # Get USDT, to the initial shares buy
    tx_hash = usdt.contract.functions.transfer(user_1, 500 * 10 ** 6, ).transact({"from": usdt_whale})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = usdt.contract.functions.approve(vault.comptroller.address, 500 * 10 ** 6).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = vault.comptroller.functions.buyShares(500 * 10 ** 6, 1).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert vault.get_gross_asset_value() == 500 * 10 ** 6  # Vault has been funded

    # Deposit $100 USDT
    raw_amount = 100 * 10 ** 6

    # Supply to USDT reserve
    vault_delivery_address = vault.generic_adapter.address
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3,
        wallet_address=vault_delivery_address,
        token=usdt.contract,
        amount=raw_amount,
    )

    # The vault performs a swap on Uniswap v3
    encoded_approve = encode_function_call(
        approve_fn,
        approve_fn.arguments,
    )

    encoded_supply = encode_function_call(
        supply_fn,
        supply_fn.arguments,
    )

    prepared_tx = execute_calls_for_generic_adapter(
        comptroller=vault.comptroller,
        external_calls=(
            (usdt.contract, encoded_approve),
            (aave_v3.pool, encoded_supply),
        ),
        generic_adapter=vault.generic_adapter,
        incoming_assets=[ausdt.address],
        integration_manager=enzyme.contracts.integration_manager,
        min_incoming_asset_amounts=[raw_amount],
        spend_asset_amounts=[raw_amount],
        spend_assets=[usdt.address],
    )

    tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Supplied aUSDT landed in the vault
    assert ausdt.fetch_balance_of(vault.address) == pytest.approx(Decimal(100))
