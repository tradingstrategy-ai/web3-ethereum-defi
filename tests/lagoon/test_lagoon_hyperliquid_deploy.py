"""Isolated test for Lagoon vault deployment on HyperEVM fork.

Diagnoses the eth_estimateGas timeout issue with TradingStrategyModuleV0 deployment.
"""

import logging
import os
import time

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonConfig,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(
    not JSON_RPC_HYPERLIQUID,
    reason="JSON_RPC_HYPERLIQUID environment variable required",
)

#: Anvil default account #0 private key.
DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
#: Anvil default accounts #1 and #2. Used as Safe owners.
OWNER_1 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OWNER_2 = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


@pytest.fixture()
def deployer() -> LocalAccount:
    return Account.from_key(DEPLOYER_PRIVATE_KEY)


@pytest.fixture()
def anvil_hyperliquid() -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_HYPERLIQUID,
        gas_limit=30_000_000,  # HyperEVM small blocks have 2–3M gas limit; override to large block limit (30M) for TradingStrategyModuleV0 (~5.4M gas). See https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_hyperliquid):
    web3 = create_multi_provider_web3(
        anvil_hyperliquid.json_rpc_url,
        default_http_timeout=(3, 500.0),
    )
    assert web3.eth.chain_id == 999
    return web3


@pytest.mark.timeout(600)
def test_hyperliquid_lagoon_deploy(web3, deployer):
    """Deploy a single Lagoon vault on HyperEVM fork.

    Isolated test to diagnose the eth_estimateGas timeout with TradingStrategyModuleV0.
    """
    web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    wallet = HotWallet(deployer)
    wallet.sync_nonce(web3)

    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=USDC_NATIVE_TOKEN[999],
            name="HyperEVM Test Vault",
            symbol="HTV",
        ),
        asset_manager=deployer.address,
        safe_owners=[OWNER_1, OWNER_2],
        safe_threshold=2,
        any_asset=True,
        safe_salt_nonce=42,
    )

    t0 = time.time()
    logger.info("Starting HyperEVM Lagoon deployment")

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=wallet,
        config=config,
    )

    elapsed = time.time() - t0
    logger.info("HyperEVM deployment completed in %.1f seconds", elapsed)

    assert deploy_info.vault is not None
    assert deploy_info.safe is not None
    assert deploy_info.trading_strategy_module is not None
    logger.info("Vault: %s, Safe: %s", deploy_info.vault.address, deploy_info.safe.address)
