"""Offline Veranta identity, fees, admission and exact-call preflight checks."""

import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from eth_typing import HexAddress
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.veranta.constants import IMPLEMENTATION_SLOT, VERANTA_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.veranta.vault import VerantaVault
from eth_defi.research.vault_metrics import slugify_protocol, slugify_vaults
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission, VaultFlowUnavailable
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.handwritten_metadata import VERANTA_VAULT_METADATA, get_handwritten_vault_metadata
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault_protocols.repairs.strategy_tags import resolve_strategy_tags


@pytest.fixture
def adapter() -> VerantaVault:
    """Bind real adapter behaviour to deterministic contract replies.

    :return: Adapter with mocked provider and fee accessors, never a live RPC.
    """
    web3 = Mock()
    web3.eth.chain_id = 8453
    web3.eth.get_storage_at.return_value = bytes.fromhex("00" * 12 + "c3769e440e3515ba374372837f76aafdc4fba26d")
    vault = VerantaVault(web3, VaultSpec(8453, VERANTA_VAULT_ADDRESS), features={ERC4626Feature.veranta_like}, default_block_identifier=123)
    contract = Mock()
    contract.functions.feesOn.return_value.call.return_value = True
    contract.functions.balancingFee.return_value.call.return_value = 0
    contract.functions.collateralHealthFee.return_value.call.return_value = 500
    vault.__dict__["vault_contract"] = contract
    return vault


def test_veranta_chain_scoped_classification() -> None:
    """A known address establishes identity only on the reviewed chain.

    :return: None; wrong and unspecified chains remain unclassified.
    """
    flags = {ERC4626Feature.veranta_like, ERC4626Feature.gains_tranche_like}
    assert _get_hardcoded_protocol_features(VERANTA_VAULT_ADDRESS.upper(), 8453) == flags
    assert _get_hardcoded_protocol_features(VERANTA_VAULT_ADDRESS, 1) is None
    assert _get_hardcoded_protocol_features(VERANTA_VAULT_ADDRESS) is None
    web3 = Mock()
    web3.eth.chain_id = 8453
    vault = create_vault_instance(web3, VERANTA_VAULT_ADDRESS, features=flags)
    assert isinstance(vault, VerantaVault)
    assert vault.get_protocol_name() == "Veranta"
    assert slugify_protocol(vault.get_protocol_name()) == "veranta"
    assert slugify_protocol("Avantis") == "veranta"
    assert vault.get_deposit_manager_capability().can_redeem is True


def test_veranta_public_vault_slug() -> None:
    """Use current branding for URLs while preserving the onchain token name.

    Exports derive the slug from the current protocol identity established by
    the one-vault metadata repair without persisting an export-only field.

    :return: None; public slugs use Veranta and the raw token name stays intact.
    """
    timestamp = datetime.datetime(2026, 1, 1)
    spec = VaultSpec(8453, VERANTA_VAULT_ADDRESS)
    detection = ERC4262VaultDetection(8453, VERANTA_VAULT_ADDRESS, 1, timestamp, {ERC4626Feature.veranta_like}, timestamp, 1, 1)
    row = {"Name": "Avantis USDC Vault", "Share token": "avUSDC", "Address": VERANTA_VAULT_ADDRESS, "Protocol": "Veranta", "_detection_data": detection}
    slugify_vaults({spec: row})
    assert row["vault_slug"] == "veranta-usdc-vault"
    assert row["protocol_slug"] == "veranta"
    assert row["Name"] == "Avantis USDC Vault"


def test_veranta_tags(adapter: VerantaVault) -> None:
    """Keep maintained tags immutable through callers and unknown addresses.

    :param adapter: Offline adapter.
    :return: None; returned collections are independent.
    """
    tags = {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.perpetual_futures}
    assert adapter.get_strategy_tags() == tags
    adapter.get_strategy_tags().clear()
    assert adapter.get_strategy_tags() == tags
    other = VerantaVault(None, VaultSpec(8453, "0x" + "11" * 20))
    assert other.get_strategy_tags() is None
    assert VerantaVault(None, VaultSpec(1, VERANTA_VAULT_ADDRESS)).get_strategy_tags() is None


def test_veranta_pinned_fees_and_disabled_switch(adapter: VerantaVault) -> None:
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


def test_veranta_permission_unknown_upgrade_and_read_failure(adapter: VerantaVault) -> None:
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


def test_veranta_exact_redemption_revert_and_transport(adapter: VerantaVault) -> None:
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


def test_veranta_metadata_export() -> None:
    """Check public metadata and required listing assets together.

    :return: None; the real exporter discovers the protocol and logos.
    """
    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/veranta.yaml"), "https://example.invalid")
    assert metadata["name"] == "Veranta"
    assert "formerly known as Avantis" in metadata["long_description"]
    assert metadata["logos"]
    assert Path("eth_defi/data/vaults/formatted_logos/veranta/light.png").is_file()


@pytest.mark.parametrize("address", [VERANTA_VAULT_ADDRESS, VERANTA_VAULT_ADDRESS.upper()])
def test_veranta_handwritten_descriptions(address: HexAddress) -> None:
    """Resolve maintained copy independently of protocol metadata and RPC.

    The individual vault needs its own adapter fields; protocol YAML does not
    populate them. Address casing must not change the reviewed deployment.

    :param address: Lowercase or uppercase address supplied to the adapter.
    :return: None after checking Markdown, summary and exact maintained text.
    """
    vault = VerantaVault(None, VaultSpec(8453, address))
    metadata = VERANTA_VAULT_METADATA[8453, VERANTA_VAULT_ADDRESS]
    assert get_handwritten_vault_metadata(8453, address) == metadata
    assert vault.description == metadata.description
    assert "\n\n#### Trading income\n\n- " in vault.description
    assert "\n- Vault depositors also receive **100%" in vault.description
    assert vault.short_description == "USDC supplied to back trading positions and earn a share of trading fees."
    assert all(char not in vault.short_description for char in ("\n", "\r", "\\n", "\\r", "*", "["))


@pytest.mark.parametrize("chain_id,address", [(1, VERANTA_VAULT_ADDRESS), (8453, "0x" + "11" * 20), (98866, "0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0")])
def test_veranta_descriptions_exclude_other_deployments(chain_id: int, address: HexAddress) -> None:
    """Do not attach this copy to another chain, address or known product.

    The shared handwritten catalogue includes other protocols. A misconstructed
    Veranta adapter must not inherit another product's maintained text.

    :param chain_id: Unreviewed chain or another handwritten product's chain.
    :param address: Unreviewed address or another handwritten product's address.
    :return: None after checking both accessors remain unknown.
    """
    vault = VerantaVault(None, VaultSpec(chain_id, address))
    assert vault.description is None
    assert vault.short_description is None


def test_veranta_legacy_cap_and_wrong_chain_tags(adapter: VerantaVault) -> None:
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
    detection = ERC4262VaultDetection(1, VERANTA_VAULT_ADDRESS, 1, now, {ERC4626Feature.veranta_like}, now, 0, 0)
    assert resolve_strategy_tags(VaultSpec(1, VERANTA_VAULT_ADDRESS), {"_detection_data": detection}) is None


@pytest.mark.parametrize("fails", [False, True])
def test_veranta_failed_quote_does_not_use_gross_price(adapter: VerantaVault, fails: bool) -> None:
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


def test_veranta_assessed_risk_and_fee_mode(adapter: VerantaVault) -> None:
    """Expose the reviewed technical rating and investor-facing fee mode.

    Risk resolves from maintained configuration without rewriting manual
    database overrides. Revenue-sharing percentages must not become investor
    management or performance fee rates.

    :param adapter: Reviewed Veranta adapter with deterministic fee replies.
    :return: None; registry and adapter agree while unverified fees stay unknown.
    """
    assert get_vault_risk("Veranta", VERANTA_VAULT_ADDRESS) is VaultTechnicalRisk.severe
    assert get_vault_risk("Avantis", VERANTA_VAULT_ADDRESS) is VaultTechnicalRisk.severe
    assert adapter.get_risk() is VaultTechnicalRisk.severe
    assert get_vault_fee_mode("Veranta", VERANTA_VAULT_ADDRESS) is VaultFeeMode.externalised
    assert get_vault_fee_mode("Avantis", VERANTA_VAULT_ADDRESS) is VaultFeeMode.externalised
    assert adapter.get_fee_mode() is VaultFeeMode.externalised
    fees = adapter.get_fee_data(123)
    assert fees.fee_mode is VaultFeeMode.externalised
    assert fees.management is None
    assert fees.performance is None
    assert fees.protocol is None
