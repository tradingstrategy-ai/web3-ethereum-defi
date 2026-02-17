"""Test deterministic Safe deployment across multiple chains.

Verifies that:
1. The raw helper produces the same Safe address on Base and Arbitrum forks
2. The Lagoon automated deployment produces the same Safe address when given a salt nonce
"""

import logging
import os

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.deployment import (
    calculate_deterministic_safe_address,
    deploy_safe_with_deterministic_address,
)
from eth_defi.token import USDC_NATIVE_TOKEN

logger = logging.getLogger(__name__)

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(
    not JSON_RPC_BASE or not JSON_RPC_ARBITRUM,
    reason="JSON_RPC_BASE and JSON_RPC_ARBITRUM environment variables required",
)

#: Fixed private key so the deployer address is the same on both chains.
DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

#: Fixed owner addresses (Anvil default accounts).
OWNER_1 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OWNER_2 = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


@pytest.fixture()
def deployer() -> LocalAccount:
    return Account.from_key(DEPLOYER_PRIVATE_KEY)


@pytest.fixture()
def owners() -> list[HexAddress]:
    return [OWNER_1, OWNER_2]


@pytest.fixture()
def anvil_base(request) -> AnvilLaunch:
    """Base mainnet fork."""
    launch = fork_network_anvil(JSON_RPC_BASE)
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_arbitrum(request) -> AnvilLaunch:
    """Arbitrum mainnet fork."""
    launch = fork_network_anvil(JSON_RPC_ARBITRUM)
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_base(anvil_base) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_base.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def web3_arbitrum(anvil_arbitrum) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_arbitrum.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 42161
    return web3


def test_deterministic_safe_same_address_base_and_arbitrum(
    web3_base: Web3,
    web3_arbitrum: Web3,
    deployer: LocalAccount,
    owners: list[HexAddress],
):
    """Deploy a deterministic Safe on both Base and Arbitrum forks and verify same address.

    - Pre-compute the address on both chains, verify they match
    - Deploy on both chains, verify deployed addresses match
    - Verify owners are correctly set on both chains
    """
    salt_nonce = 12345
    threshold = 2

    # Fund deployer on both chains
    web3_base.provider.make_request("anvil_setBalance", [deployer.address, hex(10 * 10**18)])
    web3_arbitrum.provider.make_request("anvil_setBalance", [deployer.address, hex(10 * 10**18)])

    # Pre-compute addresses — should be identical
    predicted_base = calculate_deterministic_safe_address(
        web3_base,
        owners,
        threshold,
        salt_nonce,
    )
    predicted_arbitrum = calculate_deterministic_safe_address(
        web3_arbitrum,
        owners,
        threshold,
        salt_nonce,
    )
    assert predicted_base == predicted_arbitrum, f"Predicted addresses differ: Base={predicted_base}, Arbitrum={predicted_arbitrum}"
    logger.info("Predicted Safe address (both chains): %s", predicted_base)

    # Deploy on Base
    safe_base = deploy_safe_with_deterministic_address(
        web3_base,
        deployer,
        owners,
        threshold,
        salt_nonce,
    )
    logger.info("Base Safe deployed at: %s", safe_base.address)

    # Deploy on Arbitrum
    safe_arbitrum = deploy_safe_with_deterministic_address(
        web3_arbitrum,
        deployer,
        owners,
        threshold,
        salt_nonce,
    )
    logger.info("Arbitrum Safe deployed at: %s", safe_arbitrum.address)

    # Verify addresses match
    assert safe_base.address == safe_arbitrum.address, f"Deployed addresses differ: Base={safe_base.address}, Arbitrum={safe_arbitrum.address}"
    assert safe_base.address == predicted_base, f"Deployed address does not match prediction: {safe_base.address} != {predicted_base}"

    # Verify owners on both chains
    assert safe_base.retrieve_owners() == [Web3.to_checksum_address(a) for a in owners]
    assert safe_arbitrum.retrieve_owners() == [Web3.to_checksum_address(a) for a in owners]


def test_lagoon_deterministic_safe_base_and_arbitrum(
    web3_base: Web3,
    web3_arbitrum: Web3,
    deployer: LocalAccount,
):
    """Deploy Lagoon automated vaults on Base and Arbitrum with deterministic Safe.

    - Both deployments use the same salt nonce
    - Verify the Safe addresses from deploy_automated_lagoon_vault() are identical across chains
    - Verify the vault addresses are (expectedly) different

    .. note::

        This test deploys full Lagoon setups which include many transactions
        (vault, module, guard whitelisting, ownership). If the underlying RPC
        is slow, individual transactions may time out. The core deterministic Safe
        behaviour is verified by the simpler test above.
    """
    salt_nonce = 42

    deployer_wallet_base = HotWallet(deployer)
    deployer_wallet_arb = HotWallet(deployer)

    # Fund deployer on both chains
    web3_base.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])
    web3_arbitrum.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    asset_manager = deployer.address
    safe_owners = [OWNER_1, OWNER_2]

    # Deploy on Base
    deployer_wallet_base.sync_nonce(web3_base)
    base_params = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[8453],
        name="Test Vault",
        symbol="TV",
    )
    base_deploy = deploy_automated_lagoon_vault(
        web3=web3_base,
        deployer=deployer_wallet_base,
        asset_manager=asset_manager,
        parameters=base_params,
        safe_owners=safe_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=True,
        safe_salt_nonce=salt_nonce,
    )
    base_safe_address = base_deploy.vault.safe_address
    base_vault_address = base_deploy.vault.address
    logger.info("Base: Safe=%s, Vault=%s", base_safe_address, base_vault_address)

    # Deploy on Arbitrum
    deployer_wallet_arb.sync_nonce(web3_arbitrum)
    arb_params = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[42161],
        name="Test Vault",
        symbol="TV",
    )
    arb_deploy = deploy_automated_lagoon_vault(
        web3=web3_arbitrum,
        deployer=deployer_wallet_arb,
        asset_manager=asset_manager,
        parameters=arb_params,
        safe_owners=safe_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=True,
        safe_salt_nonce=salt_nonce,
    )
    arb_safe_address = arb_deploy.vault.safe_address
    arb_vault_address = arb_deploy.vault.address
    logger.info("Arbitrum: Safe=%s, Vault=%s", arb_safe_address, arb_vault_address)

    # Safe addresses must be the same
    assert base_safe_address == arb_safe_address, f"Safe addresses differ: Base={base_safe_address}, Arbitrum={arb_safe_address}"

    # Vault addresses should be different (different chain, different init code)
    assert base_vault_address != arb_vault_address, "Vault addresses unexpectedly identical across chains"
