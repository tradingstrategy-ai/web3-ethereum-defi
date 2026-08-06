"""Test Mainstreet Finance vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.mainstreet.vault import MainstreetVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool

JSON_RPC_SONIC = os.environ.get("JSON_RPC_SONIC")
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(not JSON_RPC_ETHEREUM or not JSON_RPC_SONIC, reason="JSON_RPC_SONIC and JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Sonic Mainstreet fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_SONIC, 59_684_622)


@flaky.flaky
@pytest.mark.xdist_group("fork:sonic:59684622")
def test_mainstreet_legacy_smsUSD(
    web3: Web3,
    tmp_path: Path,
):
    """Read Mainstreet Finance legacy smsUSD vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xc7990369DA608C2F4903715E3bD22f2970536C29",
    )

    assert isinstance(vault, MainstreetVault)
    assert vault.get_protocol_name() == "Mainstreet Finance"
    assert vault.features == {ERC4626Feature.mainstreet_like}

    # Mainstreet has 20% performance fee (10% insurance + 10% treasury)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://mainstreet.finance/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Mainstreet doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@pytest.fixture(scope="module")
def web3_ethereum(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Ethereum Mainstreet fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, 24_217_821)


@flaky.flaky
@pytest.mark.xdist_group("fork:ethereum:24217821")
def test_mainstreet_staked_msusd_ethereum(
    web3_ethereum: Web3,
    tmp_path: Path,
):
    """Read Mainstreet Finance Staked msUSD vault metadata on Ethereum.

    The smart contract is developed by Mainstreet Labs.
    https://etherscan.io/address/0x890a5122aa1da30fec4286de7904ff808f0bd74a
    """

    vault = create_vault_instance_autodetect(
        web3_ethereum,
        vault_address="0x890a5122aa1da30fec4286de7904ff808f0bd74a",
    )

    assert isinstance(vault, MainstreetVault)
    assert vault.get_protocol_name() == "Mainstreet Finance"
    assert vault.features == {ERC4626Feature.mainstreet_like}

    # Check vault name override
    assert vault.name == "Staked msUSD"

    # Mainstreet has 20% performance fee (10% insurance + 10% treasury)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://mainstreet.finance/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Mainstreet doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
