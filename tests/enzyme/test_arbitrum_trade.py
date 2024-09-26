"""Get Enzyme trade on Arbitrum.

- Use Arbitrum live RPC for testing

- Deploy a vault on a live mainnet fork and do a Uniswap v3 trade as an asset manager

"""
import os
import datetime
import random

import pytest

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress

from web3 import Web3
from web3.contract import Contract
from web3.middleware import construct_sign_and_send_raw_middleware

from eth_defi.abi import get_contract
from eth_defi.enzyme.deployment import ARBITRUM_DEPLOYMENT
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.terms_of_service.acceptance_message import (
    generate_acceptance_message,
    get_signing_hash,
    sign_terms_of_service,
)
from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
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
def weth(web3) -> TokenDetails:
    details = fetch_erc20_details(web3, "0xec32aad0e8fc6851f4ba024b33de09607190ce9b")
    return details


@pytest.fixture
def wbtc(web3) -> TokenDetails:
    details = fetch_erc20_details(web3, "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f")
    return details


@pytest.fixture()
def enzyme(
    web3,
) -> EnzymeDeployment:
    """Deploy Enzyme protocol with few Chainlink feeds mocked with a static price."""
    deployment = EnzymeDeployment.fetch_deployment(web3, ARBITRUM_DEPLOYMENT)
    return deployment


@pytest.fixture()
def terms_of_service(web3) -> Contract:
    tos = get_contract(
        web3,
        "terms-of-service/TermsOfService.json",
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

    # Note that the only way to deploy vault is with a local private key,
    # because we call external foundry processes
    local_signer: LocalAccount = Account.create()
    stash = web3.eth.get_balance(deployer)
    tx_hash = web3.eth.send_transaction({"from": deployer, "to": local_signer.address, "value": stash // 2})
    assert_transaction_success_with_explanation(web3, tx_hash)

    hot_wallet = HotWallet(local_signer)
    hot_wallet.sync_nonce(web3)

    # TODO: Hack
    enzyme.deployer = hot_wallet.address

    web3.middleware_onion.add(construct_sign_and_send_raw_middleware_anvil(local_signer))

    return deploy_vault_with_generic_adapter(
        enzyme,
        deployer=hot_wallet,
        asset_manager=asset_manager,
        owner=deployer,
        denomination_asset=usdt.contract,
        terms_of_service=terms_of_service,
        whitelisted_assets=[weth, wbtc, usdt],
        uniswap_v3=True,
        one_delta=False,
        aave=True,
    )


@pytest.fixture()
def uniswap(
    web3: Web3,
    weth: Contract,
    usdc: Contract,
    mln: Contract,
    deployer: str,
) -> UniswapV3Deployment:
    addresses = UNISWAP_V3_DEPLOYMENTS["arbitrum"]
    uniswap = fetch_deployment(
        web3,
        addresses["factory"],
        addresses["router"],
        addresses["position_manager"],
        addresses["quoter_address"],
    )
    return uniswap


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
    vault_investor: LocalAccount,
    weth: TokenDetails,
    usdt: TokenDetails,
    wbtc: TokenDetails,
    uniswap_v3: UniswapV3Deployment,
    weth_usdt_pool: PoolDetails,
):
    """Make a swap that goes through the call guard."""

    assert vault.is_supported_asset(usdt.address)
    assert vault.is_supported_asset(weth.address)
    assert vault.is_supported_asset(wbtc.address)

    tx_hash = usdt.contract.functions.transfer(
        user_1,
        500 * 10 ** 6,
    ).transact({"from": usdt_whale})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = usdt.contract.functions.transfer(user_1, 500 * 10 ** 6).transact({"from": deployer})
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
    assert weth.contract.functions.balanceOf(vault.address).call() == pytest.approx(0.123090978678222650 * 10**18)
