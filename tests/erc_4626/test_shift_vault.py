"""Test ShiftVault hardcoded routing and safe public-flow handling."""

import datetime
import importlib
import pickle  # noqa: S403 - Compatibility test uses a fixed trusted pickle literal.
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.shift.constants import SHIFT_HARDCODED_LEADS, SHIFT_HOMEPAGE, SHIFT_VAULT_PRODUCTS
from eth_defi.erc_4626.vault_protocol.shift.descriptions import SHIFT_VAULT_DESCRIPTIONS, get_shift_vault_description
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission, VaultFlowUnavailable
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk


@pytest.mark.parametrize("product", SHIFT_VAULT_PRODUCTS.values())
def test_shift_hardcoded_vault_routing_is_chain_aware(product) -> None:
    """Route reviewed ShiftVault addresses without a generic ERC-4626 probe."""

    features = _get_hardcoded_protocol_features(product.vault_address, chain_id=product.chain_id)

    assert features == {ERC4626Feature.shift_like}
    assert _get_hardcoded_protocol_features(product.vault_address, chain_id=1) is None
    assert get_vault_protocol_name(features) == "Shift"

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=product.chain_id))
    vault = create_vault_instance(web3, product.vault_address, features=features)

    assert isinstance(vault, ShiftVault)
    assert not isinstance(vault, TokenisedFundVault)
    assert VaultFlag.tokenised_fund not in vault.get_flags()
    description = get_shift_vault_description(product.chain_id, product.vault_address)
    assert description is not None
    assert vault.short_description == description.short_description
    assert vault.description == description.long_description
    assert vault.get_link() == SHIFT_HOMEPAGE
    assert vault.get_deposit_manager_capability().can_deposit is False
    assert vault.get_deposit_manager_capability().can_redeem is False


def test_shift_protocol_risk_and_fee_mode() -> None:
    """Expose the documented minted-fee model and executor risk assessment."""

    assert get_vault_fee_mode("Shift", "0xaf69bf9ea9e0166498c0502af5b5945980ed1e0e") == VaultFeeMode.internalised_minting
    assert get_vault_risk("Shift") == VaultTechnicalRisk.low


def test_shift_hardcoded_leads_are_registered_for_discovery() -> None:
    """Discover custom ShiftVaults without relying on ERC-4626 events."""

    assert ("Shift", SHIFT_HARDCODED_LEADS) in DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
    assert {(chain_id, address) for chain_id, address, _block, _time in SHIFT_HARDCODED_LEADS} == {(product.chain_id, product.vault_address) for product in SHIFT_VAULT_PRODUCTS.values()}


def test_every_reviewed_shift_vault_has_static_description() -> None:
    """Keep deterministic descriptions aligned with the hardcoded registry."""

    assert set(SHIFT_VAULT_DESCRIPTIONS) == set(SHIFT_VAULT_PRODUCTS)


@pytest.fixture(params=SHIFT_VAULT_PRODUCTS.values(), ids=lambda product: product.product_name)
def shift_vault(request: pytest.FixtureRequest) -> ShiftVault:
    """Create every registered Shift adapter with controllable contract reads.

    :param request:
        Parametrised registry product.
    :return:
        Actual adapter with a mocked read-only contract binding.
    """

    product = request.param
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=product.chain_id))
    vault = ShiftVault(web3, VaultSpec(product.chain_id, product.vault_address))
    vault.__dict__["shift_contract"] = contract = Mock()
    contract.functions.whitelistEnabled.return_value.call.return_value = False
    contract.functions.paused.return_value.call.return_value = False
    return vault


@pytest.mark.parametrize("whitelisted", [False, True])
@pytest.mark.parametrize("paused", [False, True])
def test_shift_admission_is_independent_of_pause(shift_vault: ShiftVault, *, whitelisted: bool, paused: bool) -> None:
    """Keep account admission separate from pause and transaction automation."""

    shift_vault.shift_contract.functions.whitelistEnabled().call.return_value = whitelisted
    shift_vault.shift_contract.functions.paused().call.return_value = paused

    assert shift_vault.is_whitelisted_deposit() is whitelisted
    permission = VaultDepositPermission.whitelisted if whitelisted else VaultDepositPermission.permissionless
    assert fetch_deposit_permission(shift_vault) == permission
    assert VaultFlag.tokenised_fund not in shift_vault.get_flags()
    assert shift_vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}
    with pytest.raises(VaultFlowUnavailable, match="Shift transaction automation is not implemented") as error:
        shift_vault.get_deposit_manager()
    assert error.value.protocol == "Shift"
    assert error.value.vault_address == shift_vault.address


def test_shift_failed_policy_read_is_unknown(shift_vault: ShiftVault) -> None:
    """Retain an unknown policy rather than inventing permissionless access."""

    shift_vault.shift_contract.functions.whitelistEnabled().call.side_effect = ContractLogicError("unavailable")
    with pytest.raises(ContractLogicError):
        shift_vault.is_whitelisted_deposit()
    assert fetch_deposit_permission(shift_vault) == VaultDepositPermission.unknown


@pytest.mark.parametrize(
    ("paused", "whitelisted", "deposit_reason", "redemption_reason"),
    [
        (False, False, None, None),
        (False, True, "Shift deposit allow-list is enabled", None),
        (True, False, "Shift deposits are paused", "Shift withdrawals are paused"),
        (True, True, "Shift deposits are paused", "Shift withdrawals are paused"),
    ],
)
def test_shift_closure_reasons_describe_protocol_restrictions(shift_vault: ShiftVault, *, paused: bool, whitelisted: bool, deposit_reason: str | None, redemption_reason: str | None) -> None:
    """Do not report unimplemented automation or settlement delays as closure."""

    shift_vault.shift_contract.functions.paused().call.return_value = paused
    shift_vault.shift_contract.functions.whitelistEnabled().call.return_value = whitelisted
    assert shift_vault.fetch_deposit_closed_reason() == deposit_reason
    assert shift_vault.fetch_redemption_closed_reason() == redemption_reason
    extra = shift_vault.fetch_scan_record_extra_data()
    assert extra["_deposit_closed_reason"] == deposit_reason
    assert extra["_redemption_closed_reason"] == redemption_reason


def test_shift_historical_automation_does_not_imply_closure(shift_vault: ShiftVault, monkeypatch: pytest.MonkeyPatch) -> None:
    """Export unknown historical availability when it has not been read."""

    monkeypatch.setattr(shift_vault, "get_performance_fee", Mock(return_value=0))
    monkeypatch.setattr(shift_vault, "get_management_fee", Mock(return_value=0))
    record = shift_vault.get_historical_reader(stateful=False).process_result(1, datetime.datetime(2026, 10, 27, tzinfo=datetime.UTC).replace(tzinfo=None), [])
    assert record.deposits_open is None
    assert record.redemption_open is None


@pytest.mark.parametrize("module_name", ["constants", "descriptions", "historical", "vault"])
def test_shift_legacy_imports_are_identical(module_name: str) -> None:
    """Preserve imports and old pickle global references after moving Shift."""

    legacy = importlib.import_module(f"eth_defi.tokenised_fund.shift.{module_name}")
    canonical = importlib.import_module(f"eth_defi.erc_4626.vault_protocol.shift.{module_name}")
    for name, value in vars(legacy).items():
        if not name.startswith("_"):
            assert value is getattr(canonical, name)
    if module_name == "vault":
        old_pickle = b"ceth_defi.tokenised_fund.shift.vault\nShiftVault\n."
        assert pickle.loads(old_pickle) is ShiftVault  # noqa: S301 - Fixed trusted legacy global reference.
