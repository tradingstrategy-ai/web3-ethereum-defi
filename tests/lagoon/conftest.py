"""Base mainnet fork based tests for Lagoon.

Explore the static deployment which we fork from the Base mainnet:

- Vault UI: https://trading-stategy-users-frontend.vercel.app/vault/8453/0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c
- Vault contract: https://basescan.org/address/0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c#readProxyContract
- Safe UI: https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
- Safe contract: https://basescan.org/address/0x20415f3Ec0FEA974548184bdD6e67575D128953F#readProxyContract
- Roles: https://app.safe.global/apps/open?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F&appUrl=https%3A%2F%2Fzodiac.gnosisguild.org%2F
"""

import os
from decimal import Decimal

import pytest
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault, LagoonAutomatedDeployment
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDC_NATIVE_TOKEN, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.utils import addr
from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def vault_owner() -> HexAddress:
    # Vaut owner
    return addr("0x0c9db006f1c7bfaa0716d70f012ec470587a8d4f")


@pytest.fixture()
def depositor() -> HexAddress:
    # Someone how deposited assets to the vault earlier
    return addr("0x20415f3Ec0FEA974548184bdD6e67575D128953F")


@pytest.fixture()
def usdc_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return USDC_WHALE[8453]


@pytest.fixture()
def valuation_manager() -> HexAddress:
    """Unlockable account set as the vault valuation manager."""
    return addr("0x8358bBFb4Afc9B1eBe4e8C93Db8bF0586BD8331a")


@pytest.fixture()
def safe_address() -> HexAddress:
    """Unlockable Safe multisig as spoofed Anvil account."""
    return addr("0x20415f3Ec0FEA974548184bdD6e67575D128953F")


@pytest.fixture()
def test_block_number() -> int:
    """Fork height for our tests."""
    return 41_950_000


@pytest.fixture()
def anvil_base_fork(
    request,
    vault_owner,
    usdc_holder,
    asset_manager,
    valuation_manager,
    test_block_number,
) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[vault_owner, usdc_holder, asset_manager, valuation_manager],
        fork_block_number=test_block_number,
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
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
            anvil_base_fork.json_rpc_url,
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def base_usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[8453],
    )


@pytest.fixture()
def base_weth(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x4200000000000000000000000000000000000006",
    )


@pytest.fixture()
def base_dino(web3) -> TokenDetails:
    """A token that trades as DINO/WETH on Uniswap v2

    https://app.uniswap.org/explore/pools/base/0x6a77CDeC82EFf6A6A5D273F18C1c27CD3d71A588
    """
    return fetch_erc20_details(
        web3,
        "0x85E90a5430AF45776548ADB82eE4cD9E33B08077",
    )


@pytest.fixture()
def hot_wallet_user(web3, usdc, usdc_holder) -> HotWallet:
    """A test account with USDC balance."""

    hw = HotWallet.create_for_testing(
        web3,
        test_account_n=1,
        eth_amount=10,
    )
    hw.sync_nonce(web3)

    # give hot wallet some native token
    web3.eth.send_transaction(
        {
            "from": web3.eth.accounts[9],
            "to": hw.address,
            "value": 1 * 10**18,
        }
    )

    # Top up with 999 USDC
    tx_hash = usdc.contract.functions.transfer(hw.address, 999 * 10**6).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return hw


@pytest.fixture()
def base_test_vault_spec() -> VaultSpec:
    """Vault is 0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c

    - https://trading-stategy-users-frontend.vercel.app/vault/8453/0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c
    - https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
    """
    return VaultSpec(1, "0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c")


@pytest.fixture()
def lagoon_vault(web3, base_test_vault_spec: VaultSpec) -> LagoonVault:
    """Get the predeployed lagoon vault.

    - This is an early vault without TradingStrategyModuleV0 - do not use in new tests
    """
    return LagoonVault(web3, base_test_vault_spec)


@pytest.fixture()
def automated_lagoon_vault(
    web3,
    deployer_hot_wallet,
    asset_manager,
    multisig_owners,
    uniswap_v2,
) -> LagoonAutomatedDeployment:
    """Deploy a new Lagoon vault with TradingStrategyModuleV0.

    - Whitelist any Uniswap v2 token for trading using TradingStrategyModuleV0 and asset_manager
    """

    chain_id = web3.eth.chain_id
    deployer = deployer_local_account

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=uniswap_v2,
        uniswap_v3=None,
        any_asset=True,
    )

    return deploy_info


@pytest.fixture()
def asset_manager() -> HexAddress:
    """The asset manager role."""
    return "0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947"


@pytest.fixture()
def topped_up_asset_manager(web3, asset_manager) -> HexAddress:
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction(
        {
            "to": asset_manager,
            "from": web3.eth.accounts[0],
            "value": 9 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return asset_manager


@pytest.fixture()
def topped_up_valuation_manager(web3, valuation_manager) -> HexAddress:
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction(
        {
            "to": valuation_manager,
            "from": web3.eth.accounts[0],
            "value": 9 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return valuation_manager


@pytest.fixture()
def new_depositor(web3, base_usdc, usdc_holder) -> HexAddress:
    """User with some USDC ready to deposit.

    - Start with 500 USDC
    """
    new_depositor = web3.eth.accounts[5]
    tx_hash = base_usdc.transfer(new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return new_depositor


@pytest.fixture()
def another_new_depositor(web3, base_usdc, usdc_holder) -> HexAddress:
    """User with some USDC ready to deposit.

    - Start with 500 USDC
    - We need two test users
    """
    another_new_depositor = web3.eth.accounts[6]
    tx_hash = base_usdc.transfer(another_new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return another_new_depositor


@pytest.fixture()
def uniswap_v2(web3):
    """Uniswap V2 on Base"""
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


@pytest.fixture()
def deployer_hot_wallet(web3) -> HotWallet:
    """Manual nonce manager used for Lagoon deployment"""
    hot_wallet = HotWallet.create_for_testing(web3, eth_amount=1)
    return hot_wallet


@pytest.fixture()
def deployer_local_account(deployer_hot_wallet) -> LocalAccount:
    """Account that we use for Lagoon deployment"""
    return deployer_hot_wallet.account


@pytest.fixture()
def multisig_owners(web3) -> list[HexAddress]:
    """Accouunts that are set as the owners of deployed Safe w/valt"""
    return [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]


# @pytest.fixture()
# def spoofed_safe(web3, safe_address):
#     # Topped up with some ETH
#     tx_hash = web3.eth.send_transaction({
#         "to": safe_address,
#         "from": web3.eth.accounts[0],
#         "value": 9 * 10**18,
#     })
#     assert_transaction_success_with_explanation(web3, tx_hash)
#     return safe_address


# Some addresses for the roles set:
"""

## Vault Roles ##

## Address responsible to receive fees ##
FEE_RECEIVER=0xbc253b0918EE6f029637c91b3aEf7113e548eA3B

## Vault Admin : Owner of the Vault ##
ADMIN=0x6Ce4B6b4CDBe697885Ef7D2D8201584cd00826A5

## VALUATION MANAGER : Address responsible to propose the NAV of the Vault ##
VALUATION_MANAGER=0x8358bBFb4Afc9B1eBe4e8C93Db8bF0586BD8331a

## VALUATION VALIDATOR : Address responsible to accept and enforce the NAV of the Vault ##
VALUATION_VALIDATOR=0xFaE478e68B5C9337499656113326BdF5fe79B936

## ASSET MANAGER : Address responsible to execute transaction of the Asset Manager ##
ASSET_MANAGER=0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947

## Owners of SAFE : List of address responsible to update the Whitelist of Protocols managed by ASSET_MANAGER ##
## Exemple of MULTISIGS_THRESHOLD=3/5 ##
## Exemple of MULTISIGS_SIGNERS=[0x0000, 0x0000] ##
MULTISIGS_SIGNERS=[0xc690827Ca7AFD92Ccff616F73Ec5AB7c273295f4]
MULTISIGS_THRESHOLD=1%               
"""
