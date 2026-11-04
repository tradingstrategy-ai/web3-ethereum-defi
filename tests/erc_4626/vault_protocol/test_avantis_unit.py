"""Offline Avantis identity, fees, admission and exact-call preflight checks."""

import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.avantis.constants import AVANTIS_VAULT_ADDRESS, IMPLEMENTATION_SLOT
from eth_defi.erc_4626.vault_protocol.avantis.vault import AvantisVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission, VaultFlowUnavailable
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault_protocols.repairs.strategy_tags import resolve_strategy_tags


@pytest.fixture
def adapter() -> AvantisVault:
    """Bind real adapter behaviour to deterministic contract replies.

    :return: Adapter with mocked provider and fee accessors, never a live RPC.
    """
    web3 = Mock()
    web3.eth.chain_id = 8453
    web3.eth.get_storage_at.return_value = bytes.fromhex("00" * 12 + "c3769e440e3515ba374372837f76aafdc4fba26d")
    vault = AvantisVault(web3, VaultSpec(8453, AVANTIS_VAULT_ADDRESS), features={ERC4626Feature.avantis_like}, default_block_identifier=123)
    contract = Mock()
    contract.functions.feesOn.return_value.call.return_value = True
    contract.functions.balancingFee.return_value.call.return_value = 0
    contract.functions.collateralHealthFee.return_value.call.return_value = 500
    vault.__dict__["vault_contract"] = contract
    return vault


def test_avantis_chain_scoped_classification() -> None:
    """A known address establishes identity only on the reviewed chain.

    :return: None; wrong and unspecified chains remain unclassified.
    """
    flags = {ERC4626Feature.avantis_like, ERC4626Feature.gains_tranche_like}
    assert _get_hardcoded_protocol_features(AVANTIS_VAULT_ADDRESS.upper(), 8453) == flags
    assert _get_hardcoded_protocol_features(AVANTIS_VAULT_ADDRESS, 1) is None
    assert _get_hardcoded_protocol_features(AVANTIS_VAULT_ADDRESS) is None
    web3 = Mock()
    web3.eth.chain_id = 8453
    vault = create_vault_instance(web3, AVANTIS_VAULT_ADDRESS, features=flags)
    assert isinstance(vault, AvantisVault)
    assert vault.get_protocol_name() == "Avantis"
    assert vault.get_deposit_manager_capability().can_redeem is True


def test_avantis_tags(adapter: AvantisVault) -> None:
    """Keep maintained tags immutable through callers and unknown addresses.

    :param adapter: Offline adapter.
    :return: None; returned collections are independent.
    """
    tags = {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.perpetual_futures}
    assert adapter.get_strategy_tags() == tags
    adapter.get_strategy_tags().clear()
    assert adapter.get_strategy_tags() == tags
    other = AvantisVault(None, VaultSpec(8453, "0x" + "11" * 20))
    assert other.get_strategy_tags() is None
    assert AvantisVault(None, VaultSpec(1, AVANTIS_VAULT_ADDRESS)).get_strategy_tags() is None


def test_avantis_pinned_fees_and_disabled_switch(adapter: AvantisVault) -> None:
    """Read all fee inputs at one block and honour the master switch.

    :param adapter: Offline adapter.
    :return: None; nominal rates retain their 100,000 scale.
    """
    fees = adapter.get_fee_data(42)
    assert fees.withdraw == pytest.approx(0.005)
    assert fees.deposit == 0
    assert fees.management is None and fees.performance is None
    functions = adapter.vault_contract.functions
    functions.collateralHealthFee.return_value.call.assert_called_once_with(block_identifier=42)
    for call in functions.feesOn.return_value.call.call_args_list:
        assert call.kwargs == {"block_identifier": 42}
    functions.feesOn.return_value.call.return_value = False
    functions.collateralHealthFee.return_value.call.reset_mock()
    assert adapter.get_fee_data(43).withdraw == 0
    functions.collateralHealthFee.return_value.call.assert_not_called()


def test_avantis_permission_unknown_upgrade_and_read_failure(adapter: AvantisVault) -> None:
    """Do not turn an unreadable or unreviewed policy into public admission.

    :param adapter: Offline source-reviewed deployment.
    :return: None; scanner unknown handling remains intact.
    """
    assert fetch_deposit_permission(adapter) is VaultDepositPermission.permissionless
    adapter.web3.eth.get_storage_at.assert_called_with(adapter.address, IMPLEMENTATION_SLOT, block_identifier=123)
    adapter.web3.eth.get_storage_at.return_value = bytes(32)
    assert fetch_deposit_permission(adapter) is VaultDepositPermission.unknown
    adapter.web3.eth.get_storage_at.side_effect = TimeoutError("RPC unavailable")
    assert fetch_deposit_permission(adapter) is VaultDepositPermission.unknown


def test_avantis_exact_redemption_revert_and_transport(adapter: AvantisVault) -> None:
    """Distinguish post-capacity EVM refusal from a provider failure.

    :param adapter: Offline adapter with a positive advertised maximum.
    :return: None; EVM failures are typed and transport failures propagate.
    """
    adapter.vault_contract.functions.maxRedeem.return_value.call.return_value = 100
    manager = adapter.get_deposit_manager()
    request = Mock()
    request.raw_shares = 10
    func = Mock()
    request.funcs = [func]
    with patch.object(ERC4626DepositManager, "create_redemption_request", return_value=request):
        func.call.side_effect = ContractLogicError("execution reverted", data="0x12345678")
        with pytest.raises(VaultFlowUnavailable) as failure:
            manager.create_redemption_request(adapter.address, raw_shares=10)
        assert failure.value.direction == "redeem"
        assert failure.value.preflight_result == "redemption_call_reverted"
        assert failure.value.raw_revert_data == bytes.fromhex("12345678")
        assert failure.value.error_selector == bytes.fromhex("12345678")
        assert failure.value.requested_raw_amount == 10
        func.call.side_effect = ValueError("provider unavailable")
        with pytest.raises(ValueError, match="provider unavailable"):
            manager.create_redemption_request(adapter.address, raw_shares=10)
        func.call.side_effect = None
        func.call.return_value = 9
        assert manager.create_redemption_request(adapter.address, raw_shares=10) is request
        func.call.return_value = 0
        with pytest.raises(VaultFlowUnavailable) as failure:
            manager.create_redemption_request(adapter.address, raw_shares=10)
        assert failure.value.preflight_result == "zero_payout"
    adapter.vault_contract.functions.maxRedeem.return_value.call.return_value = 0
    with pytest.raises(VaultFlowUnavailable) as failure:
        manager.create_redemption_request(adapter.address, raw_shares=10)
    assert failure.value.available_raw_amount == 0
    assert failure.value.requested_raw_amount == 10


def test_avantis_metadata_export() -> None:
    """Check public metadata and required listing assets together.

    :return: None; the real exporter discovers the protocol and logos.
    """
    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/avantis.yaml"), "https://example.invalid")
    assert metadata["name"] == "Avantis"
    assert metadata["logos"]
    assert Path("eth_defi/data/vaults/formatted_logos/avantis/light.png").is_file()


def test_avantis_legacy_cap_and_wrong_chain_tags(adapter: AvantisVault) -> None:
    """Do not accept the legacy unlimited quote or misattribute another chain.

    :param adapter: Reviewed adapter backed by deterministic contract replies.
    :return: None; real caps and chain identity constrain the shared resolvers.
    """
    functions = adapter.vault_contract.functions
    functions.maxDeposit.return_value.call.return_value = 2**256 - 1
    functions.depositCap.return_value.call.return_value = 1000
    functions.totalAssets.return_value.call.return_value = 500
    assert adapter.get_deposit_manager().fetch_depositable_raw_assets(adapter.address) == 499
    functions.totalAssets.return_value.call.return_value = 1000
    assert adapter.get_deposit_manager().fetch_depositable_raw_assets(adapter.address) == 0
    now = datetime.datetime(2026, 11, 4)
    detection = ERC4262VaultDetection(1, AVANTIS_VAULT_ADDRESS, 1, now, {ERC4626Feature.avantis_like}, now, 0, 0)
    assert resolve_strategy_tags(VaultSpec(1, AVANTIS_VAULT_ADDRESS), {"_detection_data": detection}) is None


@pytest.mark.parametrize("fails", [False, True])
def test_avantis_failed_quote_does_not_use_gross_price(adapter: AvantisVault, fails: bool) -> None:
    """Never replace a zero or reverted net preview with an optimistic quote.

    :param adapter: Offline adapter using the real shared estimator.
    :param fails: Whether previewRedeem reverts or returns zero.
    :return: None; estimation fails before a gross share-price fallback.
    """
    token = Mock()
    token.name = "avUSDC"
    token.symbol = "avUSDC"
    token.convert_to_raw.return_value = 10
    adapter.__dict__["share_token"] = token
    call = adapter.vault_contract.functions.previewRedeem.return_value.call
    call.return_value = 0
    if fails:
        call.side_effect = ContractLogicError("execution reverted")
    with patch.object(adapter, "fetch_share_price", side_effect=AssertionError("Unexpected gross quote")) as gross_quote:
        with patch("eth_defi.erc_4626.estimate.format_debug_instructions", return_value=""):
            with pytest.raises(RuntimeError, match="previewRedeem"):
                adapter.get_deposit_manager().estimate_redeem(adapter.address, Decimal(1), block_identifier=42)
        gross_quote.assert_not_called()


def test_avantis_assessed_risk_and_fee_mode(adapter: AvantisVault) -> None:
    """Expose the reviewed technical rating and investor-facing fee mode.

    Risk resolves from maintained configuration without rewriting manual
    database overrides. Revenue-sharing percentages must not become investor
    management or performance fee rates.

    :param adapter: Reviewed Avantis adapter with deterministic fee replies.
    :return: None; registry and adapter agree while unverified fees stay unknown.
    """
    assert get_vault_risk("Avantis", AVANTIS_VAULT_ADDRESS) is VaultTechnicalRisk.severe
    assert adapter.get_risk() is VaultTechnicalRisk.severe
    assert get_vault_fee_mode("Avantis", AVANTIS_VAULT_ADDRESS) is VaultFeeMode.externalised
    assert adapter.get_fee_mode() is VaultFeeMode.externalised
    fees = adapter.get_fee_data(123)
    assert fees.fee_mode is VaultFeeMode.externalised
    assert fees.management is None
    assert fees.performance is None
    assert fees.protocol is None
