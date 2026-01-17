"""Accountable Capital protocol tests.

This is slow as hell.
"""

import logging
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
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

    # Fees are not publicly available
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None


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


@flaky.flaky
def test_accountable_aegis_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Test Aegis Yield Vault detection.

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

    assert vault.fetch_total_assets("latest") > 0
