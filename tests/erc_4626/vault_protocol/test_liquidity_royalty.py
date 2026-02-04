"""Test Liquidity Royalty Tranching vault metadata."""

import datetime
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.liquidity_royalty.vault import LiquidityRoyalyJuniorVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_BERACHAIN = os.environ.get("JSON_RPC_BERACHAIN")

pytestmark = pytest.mark.skipif(JSON_RPC_BERACHAIN is None, reason="JSON_RPC_BERACHAIN needed to run these tests")


@pytest.fixture(scope="module")
def anvil_berachain_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_BERACHAIN, fork_block_number=15_127_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_berachain_fork):
    web3 = create_multi_provider_web3(anvil_berachain_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_liquidity_royalty_junior_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Liquidity Royalty Tranching Junior vault metadata.

    https://berascan.com/address/0x3a0A97DcA5e6CaCC258490d5ece453412f8E1883
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x3a0A97DcA5e6CaCC258490d5ece453412f8E1883",
    )

    assert isinstance(vault, LiquidityRoyalyJuniorVault)
    assert vault.get_protocol_name() == "Liquidity Royalty Tranching"
    assert vault.features == {ERC4626Feature.liquidity_royalty_like}

    # Check custom name override
    assert vault.name == "Liquidity Royalty Tranching: Junior"

    # Check fees
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check lock-up period
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)

    # Check link
    assert vault.get_link() == "https://github.com/stratosphere-network/LiquidRoyaltyContracts"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit == 0
    assert max_redeem == 0

    # Liquidity Royalty doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
