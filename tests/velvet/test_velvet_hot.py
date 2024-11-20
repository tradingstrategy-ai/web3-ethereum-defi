"""Live trading capital tests with Velvet.

- Because Enso is a piece of crap protocol and does not offer any kind of test environment.
- Uses real money, only run these tests manually
"""
import logging
import os
from decimal import Decimal

import pytest
from eth_account import Account
from web3 import Web3

from eth_defi.confirmation import wait_transactions_to_complete
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec, TradingUniverse
from eth_defi.velvet import VelvetVault

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE", "https://mainnet.base.org")
VELVET_REAL_PRIVATE_KEY = os.environ.get("VELVET_REAL_PRIVATE_KEY")

pytestmark = pytest.mark.skipif(not (JSON_RPC_BASE and VELVET_REAL_PRIVATE_KEY), reason="No JSON_RPC_BASE or VELVET_REAL_PRIVATE_KEY environment variable")


logger = logging.getLogger(__name__)


@pytest.fixture()
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def hot_wallet(web3) -> HotWallet:
    account = Account.from_key(VELVET_REAL_PRIVATE_KEY)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3)
    return wallet


@pytest.fixture()
def base_test_vault_spec() -> VaultSpec:
    """Vault https://dapp.velvet.capital/ManagerVaultDetails/0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25"""
    return VaultSpec(1, "0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25")


@pytest.fixture()
def vault(web3, base_test_vault_spec: VaultSpec) -> VelvetVault:
    return VelvetVault(web3, base_test_vault_spec)


def test_hot_vault_swap_partially(
    vault: VelvetVault,
    hot_wallet: HotWallet,
):
    """Perform real swap tokens using Enzo.

    - Swap 0.01 SUDC to DogInMe

    """

    logger.info("test_hot_vault_swap_partially() - live swap test")

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
        swap_amount=10_000,  # 0.01 USDC
        slippage=0.01,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
    )

    signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx_data)

    logger.info("Broadcasting LIVE tx: https://basescan.org/tx/%s", signed_tx.hash.hex())
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

    wait_transactions_to_complete(
        web3,
        [tx_hash],
    )

    logger.info("All ok")
