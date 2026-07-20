"""Test Frax vault metadata"""

import os
from unittest.mock import Mock

import flaky
import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.frax.vault import FRAXLEND_CURRENT_RATE_INFO_SELECTOR, FraxlendPairVault, FraxStakingVault, FraxVault, fetch_fraxlend_protocol_fee
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EXPECTED_NINE_PERCENT_FEE = 0.09
EXPECTED_TEN_PERCENT_FEE = 0.10

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


def test_frax_product_features_share_protocol_name() -> None:
    """Map both concrete Frax product families to one protocol."""

    assert get_vault_protocol_name({ERC4626Feature.frax_like}) == "Frax"
    assert get_vault_protocol_name({ERC4626Feature.frax_staking_like}) == "Frax"


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_331_904)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_frax(
    web3: Web3,
):
    """Read Frax Fraxlend vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xeE847a804b67f4887c9e8fe559a2da4278defb52",
    )

    assert isinstance(vault, FraxlendPairVault)
    assert isinstance(vault, FraxVault)
    assert vault.get_protocol_name() == "Frax"
    assert vault.features == {ERC4626Feature.frax_like}
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == EXPECTED_NINE_PERCENT_FEE
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting
    assert vault.has_custom_fees() is False
    assert vault.get_risk() == VaultTechnicalRisk.low
    assert vault.short_description == "Earn interest by lending assets to an isolated Fraxlend borrowing market."
    assert "lenders can absorb bad debt" in vault.get_notes()
    assert "Fraxlend technical documentation" in vault.get_notes()

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_frax_fraxlend_pair_is_detected_without_hardcoded_address(
    web3: Web3,
) -> None:
    """Classify a second Fraxlend pair through its shared contract interface.

    This pair was previously exported as a generic ERC-4626 vault. It proves
    Fraxlend support is not limited to the historical USDC/sfrxETH example.
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x0601b72bef2b3f09e9f48b7d60a8d7d2d3800c6e",
    )

    assert isinstance(vault, FraxlendPairVault)
    assert vault.get_protocol_name() == "Frax"
    assert vault.features == {ERC4626Feature.frax_like}
    assert vault.get_performance_fee(24_331_904) == EXPECTED_TEN_PERCENT_FEE


def test_frax_fraxlend_fee_decoder_supports_zero() -> None:
    """Decode a zero per-pair fee without applying a family default."""

    web3 = Mock()
    web3.eth.call.return_value = HexBytes(bytes(32 * 5))
    address = "0x1c0c222989a37247d974937782cebc8bf4f25733"

    assert fetch_fraxlend_protocol_fee(web3, address, 24_331_904) == 0.0
    web3.eth.call.assert_called_once_with(
        {
            "to": Web3.to_checksum_address(address),
            "data": FRAXLEND_CURRENT_RATE_INFO_SELECTOR,
        },
        block_identifier=24_331_904,
    )


@pytest.mark.parametrize(
    ("vault_address", "expected_short_description", "expected_note_fragment"),
    (
        (
            "0x03cb4438d015b9646d666316b617a694410c216d",
            "Legacy sFRAX vault that distributed Frax protocol yield to staked FRAX.",
            "Legacy sFRAX deployment",
        ),
        (
            "0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32",
            "Stake FRAX to receive weekly Frax protocol yield through sFRAX.",
            "IORB benchmark rate",
        ),
        (
            "0xcf62f905562626cfcdd2261162a51fd02fc9c5b6",
            "Stake frxUSD in Frax's benchmark-strategy savings vault to earn automatically compounded yield.",
            "Benchmark Yield Strategy",
        ),
    ),
)
@flaky.flaky
def test_frax_staking_vaults_use_their_own_reader(
    web3: Web3,
    vault_address: HexAddress,
    expected_short_description: str,
    expected_note_fragment: str,
) -> None:
    """Route reviewed sFRAX and sfrxUSD deployments to the staking reader."""

    vault = create_vault_instance_autodetect(web3, vault_address=vault_address)

    assert isinstance(vault, FraxStakingVault)
    assert isinstance(vault, FraxVault)
    assert vault.get_protocol_name() == "Frax"
    assert vault.features == {ERC4626Feature.frax_staking_like}
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_fee_mode() == VaultFeeMode.feeless
    assert vault.get_estimated_lock_up().days == 0
    assert vault.get_link() == "https://frax.com/earn"
    assert vault.short_description == expected_short_description
    assert expected_note_fragment in vault.get_notes()
    assert vault.get_notes() != vault.short_description
