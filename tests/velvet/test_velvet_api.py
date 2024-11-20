"""Velvet capital tests.

- Test against live deployed vault on Base
"""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec, TradingUniverse
from eth_defi.velvet import VelvetVault

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE", "https://mainnet.base.org")

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def vault_owner() -> HexAddress:
    # Vaut owner
    return "0x0c9db006f1c7bfaa0716d70f012ec470587a8d4f"


@pytest.fixture()
def anvil_base_fork(request, vault_owner) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[vault_owner],
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
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > Decimal(1.0)
    existing_dogmein_balance = portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        swap_amount=1_000_000,  # 1 USDC
        slippage=0.01,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
    )

    tx_data["from"] = Web3.to_checksum_address(vault_owner)

    # Perform swap
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check our balances updated
    latest_block = web3.eth.block_number
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"] > existing_dogmein_balance


def test_vault_swap_very_little(vault: VelvetVault):
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
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > Decimal(1.0)
    existing_dogmein_balance = portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        swap_amount=1,  # 1 USDC
        slippage=0.01,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
    )

    # TODO: Test the actual swap


def test_vault_swap_sell_to_usdc(vault: VelvetVault):
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
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > Decimal(1.0)
    existing_dogmein_balance = portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        token_out="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        swap_amount=1_000_000,
        slippage=0.01,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
    )

    # TODO: Test the actual swap

