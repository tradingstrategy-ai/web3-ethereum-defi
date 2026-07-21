"""Test Yearn TokenizedStrategy compounder fee metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yearn.compounder import YearnCompounderVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

HYPERITHM_VAULT = "0xba8704c18b55f60f5d84b53c3f39a0189a0965b3"
HYPERITHM_FORK_BLOCK = 24_140_000
HYPERITHM_PERFORMANCE_FEE = 0.2
MOONWELL_WETH_BORROWER_VAULT = "0xfdb431e661372fa1146efb70bf120ecded944a78"
MOONWELL_FORK_BLOCK = 48_900_000
MOONWELL_PERFORMANCE_FEE = 0.1


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum after the Hyperithm compounder deployment.

    :return:
        Anvil process connected to the deterministic fork block.
    """
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=HYPERITHM_FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 client for the local Ethereum fork.

    :param anvil_ethereum_fork:
        Running Ethereum mainnet fork.

    :return:
        Web3 client connected to Anvil.
    """
    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)


@flaky.flaky
def test_yearn_legacy_compounder_fee_data(web3: Web3) -> None:
    """Read Yearn legacy compounder fees from the Hyperithm USDC strategy.

    The vault implements the older Yearn TokenizedStrategy surface: it does
    not expose ``GOV()`` or ``tokenizedStrategyAddress()``, so classification
    must recognise the joint ``apiVersion()`` and ``performanceFee()`` probe.

    :param web3:
        Web3 client connected to the deterministic Ethereum fork.
    """
    vault = create_vault_instance_autodetect(web3, vault_address=HYPERITHM_VAULT)

    assert isinstance(vault, YearnCompounderVault)
    assert vault.features == {ERC4626Feature.yearn_compounder_like}
    assert vault.get_protocol_name() == "Yearn"

    fee_data = vault.get_fee_data()
    assert fee_data.fee_mode == VaultFeeMode.internalised_skimming
    assert fee_data.management == 0.0
    assert fee_data.performance == HYPERITHM_PERFORMANCE_FEE
    assert fee_data.deposit == 0.0
    assert fee_data.withdraw == 0.0


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run this test")
def test_yearn_modern_compounder_fee_data() -> None:
    """Read Yearn modern compounder fees from Moonwell USDC Lender WETH Borrower.

    The modern proxy exposes ``tokenizedStrategyAddress()`` and must therefore
    be detected independently of the legacy ``apiVersion()`` path.
    """
    launch = fork_network_anvil(JSON_RPC_BASE, fork_block_number=MOONWELL_FORK_BLOCK)
    try:
        base_web3 = create_multi_provider_web3(launch.json_rpc_url)
        vault = create_vault_instance_autodetect(base_web3, vault_address=MOONWELL_WETH_BORROWER_VAULT)

        assert isinstance(vault, YearnCompounderVault)
        assert vault.features == {ERC4626Feature.yearn_compounder_like}
        assert vault.get_fee_data().performance == MOONWELL_PERFORMANCE_FEE
    finally:
        launch.close()
