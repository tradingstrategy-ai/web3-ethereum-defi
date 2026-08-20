"""Test Ember vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.ember.offchain_metadata import fetch_ember_vaults
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
MIN_DESCRIPTION_LENGTH = 10
EXPECTED_LOCKUP_DAYS = 4
MIN_EMBER_VAULTS = 5
EMBER_THIRD_EYE_NAME = "Ember Third Eye"
#: The cached API record may retain the predecessor name until cache expiry.
KNOWN_THIRD_EYE_NAMES = frozenset({"Crosschain USD Vault", EMBER_THIRD_EYE_NAME})

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Shared with the other Ethereum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Ethereum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_ember(
    web3: Web3,
):
    """Read Ember vault metadata with offchain data.

    https://etherscan.io/address/0xf3190a3ecc109f88e7947b849b281918c798a0c4
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf3190a3ecc109f88e7947b849b281918c798a0c4",
    )

    assert isinstance(vault, EmberVault)
    assert vault.get_protocol_name() == "Ember"
    assert vault.features == {ERC4626Feature.ember_like}
    assert vault.is_whitelisted_deposit() is False

    # Check risk level (open-source contracts on GitHub)
    assert vault.get_risk() == VaultTechnicalRisk.low

    # Offchain metadata from Ember's Bluefin API
    assert vault.ember_metadata is not None
    assert vault.ember_metadata["name"] in KNOWN_THIRD_EYE_NAMES
    assert vault.description is not None
    assert len(vault.description) > MIN_DESCRIPTION_LENGTH
    assert vault.short_description is not None

    # New metadata fields
    assert vault.ember_metadata["status"] is not None
    assert vault.ember_metadata["long_name"] is not None
    assert isinstance(vault.ember_metadata["tags"], list)
    assert vault.ember_metadata["total_depositors_count"] is not None
    assert vault.ember_metadata["total_depositors_count"] >= 0
    assert isinstance(vault.ember_metadata["supported_coins"], list)
    assert isinstance(vault.ember_metadata["rewards"], list)

    # Fees from offchain API (management fee is 0% for this vault)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is not None
    assert vault.get_performance_fee("latest") >= 0

    # The live API no longer populates its optional ``managers`` field, but
    # retains the curator attribution in the public vault description.
    assert "Third Eye" in vault.description

    # Withdrawal period from offchain API
    assert vault.get_estimated_lock_up().days == EXPECTED_LOCKUP_DAYS

    # Check link
    assert vault.get_link() == "https://ember.so/earn"


def test_ember_offchain_fetch(tmp_path: Path):
    """Test Ember offchain metadata fetch and caching."""

    vaults = fetch_ember_vaults(cache_path=tmp_path)

    # Should find Ethereum vaults
    assert len(vaults) >= MIN_EMBER_VAULTS

    # Check the Ember Third Eye vault is present.
    third_eye_vault = vaults.get("0xf3190A3ECC109F88e7947b849b281918c798A0C4")
    assert third_eye_vault is not None
    assert third_eye_vault["name"] == EMBER_THIRD_EYE_NAME
    assert third_eye_vault["description"] is not None
    assert third_eye_vault["management_fee"] is not None
    assert third_eye_vault["weekly_performance_fee"] is not None
    assert third_eye_vault["withdrawal_period_days"] is not None
    assert third_eye_vault["reported_apy"] is not None

    # New fields
    assert third_eye_vault["long_name"] is not None
    assert third_eye_vault["status"] is not None
    assert isinstance(third_eye_vault["tags"], list)
    assert third_eye_vault["total_depositors_count"] is not None
    assert third_eye_vault["total_depositors_count"] >= 0
    assert third_eye_vault["created_at"] is not None
    assert isinstance(third_eye_vault["rewards"], list)
    assert isinstance(third_eye_vault["supported_coins"], list)

    # Verify cache file was written
    cache_file = tmp_path / "ember_vaults.json"
    assert cache_file.exists()
    assert cache_file.stat().st_size > 0
