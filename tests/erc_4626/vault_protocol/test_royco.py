"""Test Royco Protocol and ZeroLend vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.royco.vault import RoycoVault
from eth_defi.erc_4626.vault_protocol.zerolend.vault import ZeroLendVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24167930)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_zerolend_royco_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read ZeroLend vault metadata.

    ZeroLend RWA USDC vault wrapped by Royco.
    This vault has both zerolend_like and royco_like features.
    https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x887d57a509070a0843c6418eb5cffc090dcbbe95",
    )

    # ZeroLendVault is a subclass of RoycoVault
    assert isinstance(vault, ZeroLendVault)
    assert isinstance(vault, RoycoVault)

    # Protocol name should be ZeroLend (more specific)
    assert vault.get_protocol_name() == "ZeroLend"

    # Both features should be present
    assert ERC4626Feature.zerolend_like in vault.features
    assert ERC4626Feature.royco_like in vault.features

    # Fees are handled by the underlying vault (inherited from RoycoVault)
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Link should point to ZeroLend
    assert vault.get_link() == "https://zerolend.xyz/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # ZeroLend/Royco doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
