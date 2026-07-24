"""Test Symbiotic Core V2 vault classification and metadata."""

import os
from pathlib import Path
from types import SimpleNamespace

import eth_abi
import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import _ProbeResultsDict, _should_yield_probe, create_vault_instance_autodetect, identify_vault_features  # noqa: PLC2701 - checks missing-probe handling
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.symbiotic.vault import SymbioticVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

SYMBIOTIC_VAULT_ADDRESS = "0x007e0b8e99c6134e81a1eaae754460e3202cb671"
SYMBIOTIC_FORK_BLOCK = 25_587_299


def test_symbiotic_probe_requires_v2_withdrawal_queue() -> None:
    """Classify Core V2 vaults from their Ethereum-only withdrawal queue accessor."""
    erc4626_probe = SimpleNamespace(success=True, result=eth_abi.encode(["uint256"], [1]))
    address_probe = SimpleNamespace(success=True, result=eth_abi.encode(["address"], ["0x0000000000000000000000000000000000000001"]))
    features = identify_vault_features(
        SYMBIOTIC_VAULT_ADDRESS,
        _ProbeResultsDict(
            {
                "convertToShares": erc4626_probe,
                "withdrawalQueue": address_probe,
            }
        ),
        debug_text=None,
        chain_id=1,
    )
    assert features == {ERC4626Feature.symbiotic_like}

    missing_queue_features = identify_vault_features(
        SYMBIOTIC_VAULT_ADDRESS,
        _ProbeResultsDict({"convertToShares": erc4626_probe}),
        debug_text=None,
        chain_id=1,
    )
    assert ERC4626Feature.symbiotic_like not in missing_queue_features
    assert _should_yield_probe("withdrawalQueue", 1) is True
    assert _should_yield_probe("withdrawalQueue", 42161) is False


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum after the Keyrock Flagship USDC vault's first deposit."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=SYMBIOTIC_FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 client for the deterministic Symbiotic fork."""
    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
@flaky.flaky
def test_symbiotic_vault_classification(web3: Web3) -> None:
    """Classify the verified Keyrock Flagship USDC Symbiotic Core V2 vault."""
    vault = create_vault_instance_autodetect(web3, vault_address=SYMBIOTIC_VAULT_ADDRESS)

    assert isinstance(vault, SymbioticVault)
    assert vault.features == {ERC4626Feature.symbiotic_like}
    assert vault.get_protocol_name() == "Symbiotic"
    assert vault.name == "Keyrock Flagship USDC"
    assert vault.get_management_fee(SYMBIOTIC_FORK_BLOCK) == pytest.approx(0.01)
    assert vault.get_performance_fee(SYMBIOTIC_FORK_BLOCK) == pytest.approx(0.10)
    assert vault.get_estimated_lock_up() is None
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_link().lower() == f"https://app.symbiotic.fi/vault/{SYMBIOTIC_VAULT_ADDRESS}"


def test_symbiotic_protocol_metadata_and_risk() -> None:
    """Export Symbiotic metadata and its initial risk and fee classifications."""
    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/symbiotic.yaml"), "https://example.invalid")

    assert metadata["name"] == "Symbiotic"
    assert metadata["slug"] == "symbiotic"
    assert metadata["logos"]["generic"] == "https://example.invalid/vault-protocol-metadata/symbiotic/generic.png"
    assert get_vault_protocol_name({ERC4626Feature.symbiotic_like}) == "Symbiotic"
    assert get_vault_risk("Symbiotic") == VaultTechnicalRisk.low
    assert get_vault_fee_mode("Symbiotic", SYMBIOTIC_VAULT_ADDRESS) == VaultFeeMode.internalised_minting
