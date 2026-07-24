"""Test Liquid Royalty vault metadata."""

import datetime
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.liquid_royalty.vault import LiquidRoyaltyVault

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import BERACHAIN_MIDNIGHT_BLOCK

JSON_RPC_BERACHAIN = os.environ.get("JSON_RPC_BERACHAIN")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BERACHAIN is None, reason="JSON_RPC_BERACHAIN needed to run these tests"),
    # Shared with the other Berachain midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:berachain:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Berachain fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_BERACHAIN, BERACHAIN_MIDNIGHT_BLOCK)


@flaky.flaky
def test_liquid_royalty_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Liquid Royalty (ALAR SailOut Royalty) vault metadata.

    https://berascan.com/address/0x09cea16a2563c2d7d807c86f5b8da760389b5915
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x09cea16a2563c2d7d807c86f5b8da760389b5915",
    )

    assert isinstance(vault, LiquidRoyaltyVault)
    assert vault.get_protocol_name() == "Liquid Royalty"
    assert vault.features == {ERC4626Feature.liquid_royalty_like}

    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.get_link() == "https://www.liquidroyalty.com/vaults"


@flaky.flaky
def test_liquid_royalty_senior_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Liquid Royalty Senior Vault Master metadata.

    https://berascan.com/address/0xc38421e5577250eba177bc5bc832e747bea13ee0
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xc38421e5577250eba177bc5bc832e747bea13ee0",
    )

    assert isinstance(vault, LiquidRoyaltyVault)
    assert vault.get_protocol_name() == "Liquid Royalty"
    assert vault.features == {ERC4626Feature.liquid_royalty_like}
    assert vault.name == "Senior Vault Master"

    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)


@flaky.flaky
def test_liquid_royalty_junior_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Liquid Royalty Junior vault metadata.

    Previously tracked as "Liquidity Royalty Tranching", now merged into Liquid Royalty.

    https://berascan.com/address/0x3a0A97DcA5e6CaCC258490d5ece453412f8E1883
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x3a0A97DcA5e6CaCC258490d5ece453412f8E1883",
    )

    assert isinstance(vault, LiquidRoyaltyVault)
    assert vault.get_protocol_name() == "Liquid Royalty"
    assert vault.features == {ERC4626Feature.liquid_royalty_like}

    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.get_link() == "https://www.liquidroyalty.com/vaults"
