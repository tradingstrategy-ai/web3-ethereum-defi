"""Test 3Jane vault metadata.

3Jane is a hardcoded-address protocol: its USD3 (senior) and sUSD3 (junior)
ERC-4626 tranche vaults are detected via
:py:data:`eth_defi.erc_4626.classification.HARDCODED_PROTOCOLS`.
"""

import datetime
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.threejane.vault import SUSD3_LOCK_DURATION, ThreeJaneVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:25293000"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only ThreeJane fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, 25_293_000, web3_retries=2)


@flaky.flaky
def test_threejane_usd3(
    web3: Web3,
    tmp_path: Path,
):
    """Read 3Jane USD3 (senior tranche) vault metadata.

    1. Autodetect the USD3 vault by its hardcoded address.
    2. Confirm it resolves to ThreeJaneVault with the threejane_like feature.
    3. Confirm the protocol name and underlying denomination.
    """

    # 1. Autodetect the USD3 vault by its hardcoded address.
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x056B269Eb1f75477a8666ae8C7fE01b64dD55eCc",
    )

    # 2. Confirm it resolves to ThreeJaneVault with the threejane_like feature.
    assert isinstance(vault, ThreeJaneVault)
    assert vault.features == {ERC4626Feature.threejane_like}
    assert vault.get_protocol_name() == "3Jane"

    # 3. Confirm the protocol name and underlying denomination.
    assert vault.share_token.symbol == "USD3"
    assert vault.denomination_token.symbol == "USDC"
    # No management fee; the 10% performance fee is the senior->junior tranche
    # waterfall (performanceFeeRecipient is the sUSD3 vault), read live on-chain.
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == pytest.approx(0.1)
    # Senior tranche has no redemption lock.
    assert vault.get_estimated_lock_up() == datetime.timedelta(0)


@flaky.flaky
def test_threejane_susd3(
    web3: Web3,
    tmp_path: Path,
):
    """Read 3Jane sUSD3 (junior tranche) vault metadata.

    1. Autodetect the sUSD3 vault by its hardcoded address.
    2. Confirm it resolves to ThreeJaneVault.
    3. Confirm sUSD3 is denominated in USD3 (it wraps the senior tranche).
    """

    # 1. Autodetect the sUSD3 vault by its hardcoded address.
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf689555121e529Ff0463e191F9Bd9d1E496164a7",
    )

    # 2. Confirm it resolves to ThreeJaneVault.
    assert isinstance(vault, ThreeJaneVault)
    assert vault.get_protocol_name() == "3Jane"

    # 3. Confirm sUSD3 is denominated in USD3 (it wraps the senior tranche).
    assert vault.share_token.symbol == "sUSD3"
    assert vault.denomination_token.symbol == "USD3"
    # Junior tranche carries a one-month redemption lock (SUSD3_LOCK_DURATION).
    assert vault.get_estimated_lock_up() == SUSD3_LOCK_DURATION
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=30)
    # Junior tranche has no performance fee (it is the senior-tranche waterfall recipient).
    assert vault.get_performance_fee("latest") == 0.0
