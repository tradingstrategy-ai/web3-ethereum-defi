"""Velvet capital tests.

- Test against live deployed vault on Base
"""

import os

import pytest
from web3 import Web3

from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec, TradingUniverse
from eth_defi.velvet import VelvetVault

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE", "https://mainnet.base.org")

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture(scope='module')
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture(scope='module')
def base_test_vault_spec() -> VaultSpec:
    """Vault https://dapp.velvet.capital/ManagerVaultDetails/0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25"""
    return VaultSpec(1, "0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25")


@pytest.fixture(scope='module')
def vault(web3, base_test_vault_spec: VaultSpec) -> VelvetVault:
    return VelvetVault(web3, base_test_vault_spec)


def test_fetch_info(vault: VelvetVault):
    """Read vault metadata from private Velvet endpoint."""
    data = vault.fetch_info()
    assert data["owner"] == "0x0c9db006f1c7bfaa0716d70f012ec470587a8d4f"


def test_fetch_vault_portfolio(vault: VelvetVault):
    """Read vault token balances."""
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921b130d297cc43754afba22e5eac0fbf8db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    print(portfolio.spot_erc20)
    import ipdb ; ipdb.set_trace()
