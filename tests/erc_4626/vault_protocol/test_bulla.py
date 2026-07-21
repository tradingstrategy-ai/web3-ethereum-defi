"""Test Bulla Network classification and its safe read-only capability."""

import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_probe_calls, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.bulla.vault import BullaFeeData, BullaVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import get_vault_fee_mode
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
BULLA_VAULT_ADDRESS = "0xc099773267308D8e9E805f47EABf9ab13bBc9e37"
BULLA_FORK_BLOCK = 486_151_800
BULLA_PROTOCOL_FEE_BPS = 30
BULLA_TARGET_YIELD_BPS = 800
BULLA_INVOICE_UPFRONT_BPS = 10_000


def test_bulla_uses_one_protocol_specific_probe() -> None:
    """Keep Bulla detection constrained to its canonical DAO getter selector."""
    calls = list(create_probe_calls([BULLA_VAULT_ADDRESS], chain_id=42161))
    bulla_calls = [call for call in calls if call.func_name == "bullaDao"]

    assert len(bulla_calls) == 1
    assert bulla_calls[0].data == Web3.keccak(text="bullaDao()")[:4]


@pytest.fixture(scope="module")
def anvil_arbitrum_fork() -> AnvilLaunch:
    """Fork Arbitrum at the Bulla integration's recorded latest block."""
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=BULLA_FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 client for the deterministic Arbitrum fork."""
    return create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)


@pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run this test")
@flaky.flaky
def test_bulla_factoring_classification(web3: Web3) -> None:
    """Classify Bulla's TCS Settlement Pool without advertising transaction support."""
    vault = create_vault_instance_autodetect(web3, vault_address=BULLA_VAULT_ADDRESS)

    assert isinstance(vault, BullaVault)
    assert vault.features == {ERC4626Feature.bulla_like}
    assert vault.get_protocol_name() == "Bulla Network"
    assert vault.name == "TCS Settlement Pool Token V2.1"
    assert vault.denomination_token.symbol == "PYUSD"
    bulla_fees = vault.fetch_bulla_fee_data(BULLA_FORK_BLOCK)
    assert bulla_fees.protocol_fee_bps == BULLA_PROTOCOL_FEE_BPS
    assert bulla_fees.admin_fee_bps == 0
    assert bulla_fees.protocol_fee_balance == Decimal("838.30947")
    assert bulla_fees.admin_fee_balance == Decimal(0)
    assert bulla_fees.target_yield_bps == BULLA_TARGET_YIELD_BPS
    assert bulla_fees.protocol_fee == pytest.approx(0.003)
    assert bulla_fees.admin_fee == 0.0
    assert bulla_fees.target_yield == pytest.approx(0.08)

    # Invoice id zero is a recorded TCS pool approval and gives deterministic
    # ABI coverage for the nested V2.1 FeeParams decoder, including spread.
    invoice_fees = vault.fetch_bulla_invoice_fee_data(0, BULLA_FORK_BLOCK)
    assert invoice_fees.approved is True
    assert invoice_fees.target_yield_bps == BULLA_TARGET_YIELD_BPS
    assert invoice_fees.underwriter_spread_bps == 0
    assert invoice_fees.upfront_bps == BULLA_INVOICE_UPFRONT_BPS
    assert invoice_fees.protocol_fee_bps == BULLA_PROTOCOL_FEE_BPS
    assert invoice_fees.admin_fee_bps == 0
    assert invoice_fees.protocol_fee_amount == Decimal("4.5")

    assert vault.get_management_fee(BULLA_FORK_BLOCK) == 0.0
    assert vault.get_performance_fee(BULLA_FORK_BLOCK) is None
    assert vault.get_deposit_fee(BULLA_FORK_BLOCK) == 0.0
    assert vault.get_withdraw_fee(BULLA_FORK_BLOCK) == 0.0
    assert vault.get_fee_data().management == 0.0
    assert vault.get_fee_data().performance is None
    assert vault.get_fee_data().deposit == 0.0
    assert vault.get_fee_data().withdraw == 0.0
    assert vault.has_custom_fees() is True
    assert vault.get_estimated_lock_up() is None
    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(NotImplementedError, match="permissioned deposits and queued redemptions"):
        vault.get_deposit_manager()
    assert vault.get_link() == "https://banker.bulla.network/#/yield"


def test_bulla_fee_data_maps_only_the_comparable_administrator_rate() -> None:
    """Keep invoice protocol and spread economics out of generic fee fields."""
    bulla_fees = BullaFeeData(
        block_identifier=1,
        protocol_fee_bps=30,
        admin_fee_bps=125,
        protocol_fee_balance=Decimal("12.34"),
        admin_fee_balance=Decimal("56.78"),
        target_yield_bps=800,
    )

    generic_fees = bulla_fees.as_generic_fee_data()
    assert generic_fees.fee_mode is None
    assert generic_fees.management == pytest.approx(0.0125)
    assert generic_fees.performance is None
    assert generic_fees.deposit == 0.0
    assert generic_fees.withdraw == 0.0


def test_bulla_protocol_metadata_risk_and_fee_data() -> None:
    """Export Bulla metadata while preserving its pool-specific fee and risk model."""
    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/bulla.yaml"), "https://example.invalid")

    assert metadata["name"] == "Bulla Network"
    assert metadata["slug"] == "bulla"
    assert metadata["logos"]["generic"] == "https://example.invalid/vault-protocol-metadata/bulla/generic.png"
    assert metadata["logos"]["light"] == "https://example.invalid/vault-protocol-metadata/bulla/light.png"
    assert metadata["logos"]["dark"] == "https://example.invalid/vault-protocol-metadata/bulla/dark.png"
    assert get_vault_protocol_name({ERC4626Feature.bulla_like}) == "Bulla Network"
    assert get_vault_risk("Bulla Network") == VaultTechnicalRisk.low
    assert get_vault_fee_mode("Bulla Network", BULLA_VAULT_ADDRESS) is None
