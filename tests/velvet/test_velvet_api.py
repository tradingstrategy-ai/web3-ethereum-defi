"""Velvet capital tests.

- Test against mainnet fork of live deployed vault on Base

- Vault meta https://api.velvet.capital/api/v3/portfolio/0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25

- Vault UI https://dapp.velvet.capital/ManagerVaultDetails/0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25
"""

import os
from decimal import Decimal

import flaky
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment, fetch_deployment
from eth_defi.uniswap_v3.price import estimate_buy_received_amount
from eth_defi.vault.base import VaultSpec, TradingUniverse
from eth_defi.velvet import VelvetVault

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE", "https://mainnet.base.org")

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
def anvil_base_fork(request, vault_owner, usdc_holder) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[vault_owner, usdc_holder],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    web3 = create_multi_provider_web3(anvil_base_fork.json_rpc_url)
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
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
    """Vault https://dapp.velvet.capital/ManagerVaultDetails/0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25"""
    return VaultSpec(1, "0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25")


@pytest.fixture()
def vault(web3, base_test_vault_spec: VaultSpec) -> VelvetVault:
    return VelvetVault(web3, base_test_vault_spec)


def test_fetch_info(vault: VelvetVault):
    """Read vault metadata from private Velvet endpoint."""
    data = vault.fetch_info()
    assert data["owner"] == "0x0c9db006f1c7bfaa0716d70f012ec470587a8d4f"
    assert data["vaultAddress"] == "0x9d247fbc63e4d50b257be939a264d68758b43d04"

    assert vault.vault_address == "0x9d247fbc63e4d50b257be939a264d68758b43d04"
    assert vault.owner_address == "0x0c9db006f1c7bfaa0716d70f012ec470587a8d4f"


def test_fetch_vault_portfolio(vault: VelvetVault):
    """Read vault token balances."""
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > 0
    assert portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"] > 0


@pytest.mark.skipif(CI, reason="Enso is such unstable crap that there is no hope we could run any tests with in CI")
def test_vault_swap_partially(
    vault: VelvetVault,
    vault_owner: HexAddress,
):
    """Simulate swap tokens using Enzo.

    - Swap 1 SUDC to DogInMe

    - See balances update in the vault
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)

    existing_dogmein_balance = portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    existing_usdc_balance = portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        swap_amount=1_000_000,  # 1 USDC
        slippage=0.01,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
        from_=vault_owner,
    )

    # Perform swap
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check our balances updated
    latest_block = web3.eth.block_number
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"] > existing_dogmein_balance
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] < existing_usdc_balance


@pytest.mark.skipif(CI, reason="Enso is such unstable crap that there is no hope we could run any tests with in CI")
def test_vault_swap_very_little(
    vault: VelvetVault,
    vault_owner: HexAddress,
):
    """Simulate swap tokens using Enzo.

    - Do a very small amount of USDC
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        swap_amount=1,  # 1 USDC
        slippage=0.01,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
        from_=vault_owner,
    )

    # Perform swap
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)


@pytest.mark.skipif(CI, reason="Enso is such unstable crap that there is no hope we could run any tests with in CI")
def test_vault_swap_sell_to_usdc(
    vault: VelvetVault,
    vault_owner: HexAddress,
):
    """Simulate swap tokens using Enzo.

    - Sell base token to get more USDC
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    existing_usdc_balance = portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        token_out="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        swap_amount=500 * 10**18,
        slippage=0.01,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
        from_=vault_owner,
    )

    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    latest_block = web3.eth.block_number
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > existing_usdc_balance


@pytest.mark.skip(reason="Only manual test available")
def test_velvet_api_deposit(
    vault: VelvetVault,
    vault_owner: HexAddress,
    hot_wallet_user: HotWallet,
    usdc: TokenDetails,
):
    """Use Velvet API to perform deposit"""

    web3 = vault.web3

    # Velvet vault tracked assets
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )

    # Check the existing portfolio USDC balance before starting the
    # the deposit process
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    existing_usdc_balance = portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)

    # Approve USDC deposit to a vault contract
    deposit_amount = 500 * 10 ** 6
    signed_tx = hot_wallet_user.transact_with_contract(
        usdc.contract.functions.approve,
        Web3.to_checksum_address(vault.rebalance_address),
        deposit_amount
    )
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Prepare the deposit tx payload
    tx_data = vault.prepare_deposit_with_enso(
        from_=hot_wallet_user.address,
        deposit_token_address=usdc.address,
        amount=deposit_amount,
    )
    # Broadcast the deposit x
    hot_wallet_user.fill_in_gas_price(web3, tx_data)
    signed_tx = hot_wallet_user.sign_transaction_with_new_nonce(tx_data)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # USDC balance has increased after the deposit
    portfolio = vault.fetch_portfolio(universe, web3.eth.block_number)
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > existing_usdc_balance
