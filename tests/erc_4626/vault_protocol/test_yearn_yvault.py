"""YVault token symbol tests"""

import os
from pathlib import Path

import pytest

from web3 import Web3
import flaky

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.erc_4626.vault_protocol.untangle.vault import UntangleVault
from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=392_313_989)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_yvault_usdce_symbol(
    web3: Web3,
    tmp_path: Path,
):
    """Make sure we can separate USDC/USDC.e from each other on Arbitrum vault output"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x9fa306b1f4a6a83fec98d8ebbabedff78c407f6b",
    )

    assert isinstance(vault, YearnV3Vault)
    assert vault.get_protocol_name() == "Yearn"
    assert vault.denomination_token.address == "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"
    assert vault.denomination_token.symbol == "USDC.e"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.00

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit == 0
    assert max_redeem == 0

    # Yearn vaults don't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
