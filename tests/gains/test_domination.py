"""Domination Finance vault tests."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.vault import DominationFinanceVault, GainsVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="Set JSON_RPC_BASE to run this test")

DOMINATION_DFUSDC_ADDRESS = "0xA194082Aabb75Dd1Ca9Dc1BA573A5528BeB8c2Fb"


@pytest.fixture(scope="module")
def anvil_base_fork() -> AnvilLaunch:
    """Fork Base at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=46_854_858)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_base_fork: AnvilLaunch) -> Web3:
    """Create Web3 connection to the Anvil fork."""
    return create_multi_provider_web3(anvil_base_fork.json_rpc_url, retries=2)


def test_domination_features(web3: Web3):
    """Domination uses hardcoded address detection."""
    features = detect_vault_features(web3, DOMINATION_DFUSDC_ADDRESS, verbose=True)
    assert features == {ERC4626Feature.domination_finance_like}


@flaky.flaky
def test_domination_read_data(web3: Web3):
    """Read Domination Finance dfUSDC vault metadata."""
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=DOMINATION_DFUSDC_ADDRESS,
    )

    assert isinstance(vault, DominationFinanceVault)
    assert isinstance(vault, GainsVault)
    assert vault.get_protocol_name() == "Domination Finance"
    assert vault.features == {ERC4626Feature.domination_finance_like}
    assert vault.name == "DomFi USDC LP"
    assert vault.symbol == "dfUSDC"
    assert vault.denomination_token.symbol == "USDC"
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_fee_mode() == VaultFeeMode.feeless
    assert vault.get_risk() == VaultTechnicalRisk.severe
    assert vault.get_link() == "https://app.domination.finance/vault"
    assert vault.get_max_discount_percent() >= 0
