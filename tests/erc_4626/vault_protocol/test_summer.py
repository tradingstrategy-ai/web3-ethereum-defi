"Summer.fi Earn vault tests"

import os
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.summer.vault import SummerVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:392313989"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Summer fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, 392_313_989)


@pytest.mark.skip(reason="Too slow")
def test_summer(
    web3: Web3,
    tmp_path: Path,
):
    """Read Summer.fi Earn vault metadata"""

    vault = create_vault_instance(
        web3,
        address="0x4f63cfea7458221cb3a0eee2f31f7424ad34bb58",
        features={ERC4626Feature.summer_like},
    )

    assert vault.features == {ERC4626Feature.summer_like}
    assert isinstance(vault, SummerVault)
    assert vault.name == "Summer.fi USDC"
    assert vault.get_protocol_name() == "Summer.fi"
    assert vault.get_management_fee("latest") == 0.01
    assert vault.get_performance_fee("latest") == 0.00
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # Summer.fi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
