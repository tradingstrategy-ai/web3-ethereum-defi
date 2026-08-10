"""Test infiniFi vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.infinifi.vault import InfiniFiVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:24263313"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only InfiniFi fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, 24_263_313, web3_retries=2)


@flaky.flaky
@pytest.mark.skip(reason="Too slow")
def test_infinifi(
    web3: Web3,
    tmp_path: Path,
):
    """Read infiniFi siUSD vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xdbdc1ef57537e34680b898e1febd3d68c7389bcb",
    )

    assert isinstance(vault, InfiniFiVault)
    assert vault.get_protocol_name() == "infiniFi"
    assert vault.features == {ERC4626Feature.infinifi_like}

    # Fee assertions
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is None  # Not publicly documented
    assert vault.has_custom_fees() is False

    # Check the link
    assert vault.get_link() == "https://app.infinifi.xyz/deposit"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # infiniFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
