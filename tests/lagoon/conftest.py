"""Base mainnet fork based tests for Lagoon.

Explore the static deployment which we fork from the Base mainnet:

- Vault UI: https://trading-stategy-users-frontend.vercel.app/vault/8453/0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c
- Vault contract: https://basescan.org/address/0xab4ac28d10a4bc279ad073b1d74bfa0e385c010c#readProxyContract
- Safe UI: https://app.safe.global/home?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F
- Safe contract: https://basescan.org/address/0x20415f3Ec0FEA974548184bdD6e67575D128953F#readProxyContract
- Roles: https://app.safe.global/apps/open?safe=base:0x20415f3Ec0FEA974548184bdD6e67575D128953F&appUrl=https%3A%2F%2Fzodiac.gnosisguild.org%2F
"""
import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def vault_owner() -> HexAddress:
    # Vaut owner
    return "0x0c9db006f1c7bfaa0716d70f012ec470587a8d4f"


@pytest.fixture()
def usdc_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"



@pytest.fixture()
def valuation_manager() -> HexAddress:
    """Unlockable account set as the vault valuation manager."""
    return "0x8358bBFb4Afc9B1eBe4e8C93Db8bF0586BD8331a"


@pytest.fixture()
def safe_address() -> HexAddress:
    """Unlockable Safe multisig as spoofed Anvil account."""
    return "0x20415f3Ec0FEA974548184bdD6e67575D128953F"


@pytest.fixture()
def anvil_base_fork(request, vault_owner, usdc_holder, asset_manager, valuation_manager) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[vault_owner, usdc_holder, asset_manager, valuation_manager],
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
        web3 = create_multi_provider_web3(anvil_base_fork.json_rpc_url)
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def base_usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
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
        eth_amount=10
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
    return LagoonVault(web3, base_test_vault_spec)


@pytest.fixture()
def asset_manager() -> HexAddress:
    """The asset manager role."""
    return "0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947"


@pytest.fixture()
def topped_up_asset_manager(web3, asset_manager):
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction({
        "to": asset_manager,
        "from": web3.eth.accounts[0],
        "value": 9 * 10**18,
    })
    assert_transaction_success_with_explanation(web3, tx_hash)
    return asset_manager



@pytest.fixture()
def topped_up_valuation_manager(web3, valuation_manager):
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction({
        "to": valuation_manager,
        "from": web3.eth.accounts[0],
        "value": 9 * 10**18,
    })
    assert_transaction_success_with_explanation(web3, tx_hash)
    return valuation_manager


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