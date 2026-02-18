"""Accountable Capital protocol tests.

This is slow as hell.
"""

import datetime
import logging
import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata import (
    fetch_accountable_vaults,
)
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableHistoricalReader, AccountableVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_MONAD = os.environ.get("JSON_RPC_MONAD")

pytestmark = pytest.mark.skipif(JSON_RPC_MONAD is None, reason="JSON_RPC_MONAD needed to run these tests")


@pytest.fixture(scope="module")
def anvil_monad_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_MONAD, fork_block_number=48_417_887)
    try:
        yield launch
    finally:
        launch.close(log_level=logging.INFO)


@pytest.fixture(scope="module")
def web3(anvil_monad_fork):
    web3 = create_multi_provider_web3(
        anvil_monad_fork.json_rpc_url,
        retries=2,
        default_http_timeout=(10, 60),
    )
    return web3


@flaky.flaky
def test_accountable_susn_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test sUSN Delta Neutral Yield Vault detection.

    https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x58ba69b289De313E66A13B7D1F822Fc98b970554",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"

    # Management fee not available, performance fee from offchain metadata
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is not None

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_accountable_yuzu_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test Yuzu Money Vault detection.

    https://monadscan.com/address/0x3a2c4aAae6776dC1c31316De559598f2f952E2cB
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x3a2c4aAae6776dC1c31316De559598f2f952E2cB",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_accountable_asia_credit_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test Asia Credit Yield Vault detection.

    https://monadscan.com/address/0x4C0d041889281531fF060290d71091401Caa786D
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x4C0d041889281531fF060290d71091401Caa786D",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_accountable_aegis_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test Aegis Yield Vault detection and corrected NAV.

    Accountable's totalAssets() only returns idle liquidity, excluding deployed capital.
    fetch_total_assets() must use convertToAssets(totalSupply()) for the true NAV.

    https://monadscan.com/address/0x0a4AfB907672279926c73Dc1F77151931c2A55cC
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0a4AfB907672279926c73Dc1F77151931c2A55cC",
    )

    assert isinstance(vault, AccountableVault)
    assert ERC4626Feature.accountable_like in vault.features
    assert vault.get_protocol_name() == "Accountable"
    assert vault.denomination_token.symbol == "USDC"

    # fetch_total_assets uses convertToAssets(totalSupply()) for the true NAV
    nav = vault.fetch_total_assets("latest")
    assert nav == Decimal("367585.610526")

    # fetch_idle_capital returns the raw totalAssets() = idle liquidity only
    idle = vault.fetch_idle_capital()
    assert idle == Decimal("199.808362")
    assert nav >= idle

    # fetch_available_liquidity delegates to fetch_idle_capital
    assert vault.fetch_available_liquidity() == idle

    # Utilisation = deployed capital / true NAV
    utilisation = vault.fetch_utilisation_percent()
    assert utilisation == pytest.approx(float((nav - idle) / nav), rel=0.001)
    assert utilisation > 0.99  # nearly all capital is deployed

    # fetch_nav should match fetch_total_assets
    nav_from_fetch = vault.fetch_nav()
    assert nav_from_fetch == Decimal("367585.610526")

    # Accountable doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_accountable_historical_reader(
    web3: Web3,
):
    """Test AccountableHistoricalReader computes correct NAV from multicall results.

    The historical reader must override total_assets with share_price * total_supply
    instead of using the raw totalAssets() value (which is only idle liquidity).
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0a4AfB907672279926c73Dc1F77151931c2A55cC",
    )

    assert isinstance(vault, AccountableVault)

    # Verify Accountable-specific historical reader is returned
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, AccountableHistoricalReader)

    # Read vault state at the fork block using the historical reader
    block_number = web3.eth.block_number
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.timezone.utc).replace(tzinfo=None)

    calls = list(reader.construct_multicalls())
    call_results = [c.call_as_result(web3=web3, block_identifier=block_number) for c in calls]
    vault_read = reader.process_result(block_number, timestamp, call_results)

    assert vault_read.block_number == block_number
    assert vault_read.share_price == Decimal("1.000497")
    assert vault_read.total_supply == Decimal("367402.862611")

    # total_assets from the reader is the corrected NAV (share_price * total_supply),
    # not the raw idle-only totalAssets() which would be ~199 USDC
    assert vault_read.total_assets == pytest.approx(Decimal("367585.461833"), rel=Decimal("0.001"))
    assert vault_read.total_assets == pytest.approx(vault_read.share_price * vault_read.total_supply, rel=Decimal("0.001"))

    # available_liquidity is the raw totalAssets() = idle capital for withdrawals
    assert vault_read.available_liquidity == Decimal("199.808362")

    # Utilisation reflects nearly all capital deployed
    assert vault_read.utilisation is not None
    assert vault_read.utilisation > 0.99

    # The corrected NAV should match the direct fetch_total_assets call
    direct_nav = vault.fetch_total_assets(block_number)
    assert vault_read.total_assets == pytest.approx(direct_nav, rel=Decimal("0.001"))


@flaky.flaky
def test_accountable_metadata(
    web3: Web3,
):
    """Read Accountable vault metadata from offchain yield app API.

    Uses the sUSN vault which is already detected in the Anvil fork.
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x58ba69b289De313E66A13B7D1F822Fc98b970554",
    )

    assert isinstance(vault, AccountableVault)
    assert vault.accountable_metadata is not None
    assert vault.description is not None
    assert len(vault.description) > 10
    assert vault.short_description is not None
    assert vault.accountable_metadata.get("company_name") is not None
    assert vault.accountable_metadata.get("performance_fee") is not None


@flaky.flaky
def test_accountable_metadata_cache(tmp_path: Path):
    """Verify disk caching works for Accountable metadata."""
    vaults = fetch_accountable_vaults(cache_path=tmp_path)
    assert isinstance(vaults, dict)
    assert len(vaults) > 0

    # Should have cached the file
    cache_file = tmp_path / "accountable_vaults.json"
    assert cache_file.exists()
    assert cache_file.stat().st_size > 0

    # Second call should use cache (no API calls)
    vaults2 = fetch_accountable_vaults(cache_path=tmp_path)
    assert vaults2 == vaults

    # Check that at least one vault has a description
    has_description = any(v.get("description") for v in vaults.values())
    assert has_description, "Expected at least one Accountable vault with a description"
