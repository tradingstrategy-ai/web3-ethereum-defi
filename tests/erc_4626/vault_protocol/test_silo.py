"""Silo finance vault tests"""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, is_lending_protocol
from eth_defi.erc_4626.vault_protocol.silo.vault import SiloVault, SiloVaultHistoricalReader
from eth_defi.erc_4626.vault_protocol.summer.vault import SummerVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode

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


def test_silo(
    web3: Web3,
    tmp_path: Path,
):
    """Read Silo vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xacb7432a4bb15402ce2afe0a7c9d5b738604f6f9",
    )

    assert vault.features == {ERC4626Feature.silo_like}
    assert isinstance(vault, SiloVault)
    assert vault.name == "Borrowable USDC Deposit, SiloId: 146"
    assert vault.get_protocol_name() == "Silo Finance"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.00
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Silo doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False

    # Test lending protocol identification
    assert is_lending_protocol({ERC4626Feature.silo_like}) is True

    # Test utilisation API
    available_liquidity = vault.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = vault.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # Test historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, SiloVaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "getLiquidity" in call_names
    assert "getDebtAssets" in call_names
    assert "getCollateralAssets" in call_names
