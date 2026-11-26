"""Test ShiftVault hardcoded routing and safe public-flow handling."""

import importlib
import pickle  # noqa: S403 - Compatibility test uses a fixed trusted pickle literal.
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.shift.constants import SHIFT_HARDCODED_LEADS, SHIFT_HOMEPAGE, SHIFT_VAULT_PRODUCTS, ShiftVaultProduct
from eth_defi.erc_4626.vault_protocol.shift.descriptions import get_shift_vault_description
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission, VaultFlowUnavailable
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk

LTLLP_SPEC = VaultSpec(42161, "0x956bdd9c18b786b082fd50c52722d254f0cb6964")


@pytest.mark.parametrize("product", SHIFT_VAULT_PRODUCTS.values(), ids=lambda product: product.product_name)
def test_shift_hardcoded_vault_routing_is_chain_aware(product: ShiftVaultProduct) -> None:
    """Route reviewed ShiftVault addresses without a generic ERC-4626 probe."""

    features = _get_hardcoded_protocol_features(product.vault_address, chain_id=product.chain_id)

    assert features == {ERC4626Feature.shift_like}
    assert _get_hardcoded_protocol_features(product.vault_address, chain_id=1) is None
    assert get_vault_protocol_name(features) == "Shift"

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=product.chain_id))
    vault = create_vault_instance(web3, product.vault_address, features=features)

    assert isinstance(vault, ShiftVault)
    assert VaultFlag.tokenised_fund not in vault.get_flags()
    description = get_shift_vault_description(product.chain_id, product.vault_address)
    assert description is not None
    assert vault.short_description == description.short_description
    assert vault.description == description.long_description
    assert vault.get_link() == SHIFT_HOMEPAGE


def test_shift_discovery_and_protocol_metadata() -> None:
    """Expose the discovered deployment and its minted-fee and risk classifications."""
    assert ("Shift", SHIFT_HARDCODED_LEADS) in DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
    assert any(lead[:3] == (LTLLP_SPEC.chain_id, LTLLP_SPEC.vault_address, 434_181_142) for lead in SHIFT_HARDCODED_LEADS)
    assert get_vault_fee_mode("Shift", LTLLP_SPEC.vault_address) == VaultFeeMode.internalised_minting
    assert get_vault_risk("Shift") == VaultTechnicalRisk.low


def test_shift_public_flow_support() -> None:
    """Expose a read-only capability and the adapter's public flow error."""
    vault = ShiftVault(Web3(), LTLLP_SPEC)
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}
    with pytest.raises(VaultFlowUnavailable, match="Shift transaction automation is not implemented") as error:
        vault.get_deposit_manager()
    assert error.value.protocol == "Shift"
    assert error.value.vault_address == vault.address


@pytest.fixture(params=SHIFT_VAULT_PRODUCTS.values(), ids=lambda product: product.product_name)
def shift_vault(request: pytest.FixtureRequest) -> ShiftVault:
    """Create every registered Shift adapter with controllable contract reads.

    :param request:
        Parametrised registry product.
    :return:
        Actual adapter with a mocked read-only contract binding.
    """

    product: ShiftVaultProduct = request.param
    vault = ShiftVault(Web3(), VaultSpec(product.chain_id, product.vault_address))
    vault.__dict__["shift_contract"] = Mock()
    return vault


@pytest.mark.parametrize(
    ("paused", "whitelisted", "permission", "deposit_reason", "redemption_reason"),
    [
        pytest.param(False, False, VaultDepositPermission.permissionless, None, None, id="open"),
        pytest.param(False, True, VaultDepositPermission.whitelisted, "Shift deposit allow-list is enabled", None, id="allow-list"),
        pytest.param(True, False, VaultDepositPermission.permissionless, "Shift deposits are paused", "Shift withdrawals are paused", id="paused"),
        pytest.param(True, True, VaultDepositPermission.whitelisted, "Shift deposits are paused", "Shift withdrawals are paused", id="paused-with-allow-list"),
    ],
)
def test_shift_admission_and_closure_reasons(
    shift_vault: ShiftVault,
    *,
    paused: bool,
    whitelisted: bool,
    permission: VaultDepositPermission,
    deposit_reason: str | None,
    redemption_reason: str | None,
) -> None:
    """Keep investor admission independent of the vault's current availability."""
    shift_vault.shift_contract.functions.whitelistEnabled().call.return_value = whitelisted
    shift_vault.shift_contract.functions.paused().call.return_value = paused
    assert fetch_deposit_permission(shift_vault) == permission
    assert shift_vault.fetch_deposit_closed_reason() == deposit_reason
    assert shift_vault.fetch_redemption_closed_reason() == redemption_reason


@pytest.mark.parametrize(
    ("module_name", "symbol"),
    [
        ("constants", "SHIFT_VAULT_PRODUCTS"),
        ("descriptions", "get_shift_vault_description"),
        ("historical", "ShiftVaultHistoricalReader"),
        ("vault", "ShiftVault"),
    ],
)
def test_shift_legacy_imports_are_identical(module_name: str, symbol: str) -> None:
    """Preserve public compatibility imports and the persisted adapter path."""
    legacy = importlib.import_module(f"eth_defi.tokenised_fund.shift.{module_name}")
    canonical = importlib.import_module(f"eth_defi.erc_4626.vault_protocol.shift.{module_name}")
    assert getattr(legacy, symbol) is getattr(canonical, symbol)
    if module_name == "vault":
        assert legacy.SHIFT_VAULT_READ_ABI is canonical.SHIFT_VAULT_READ_ABI
        assert legacy.SHIFT_TVL_FEED_READ_ABI is canonical.SHIFT_TVL_FEED_READ_ABI
        old_pickle = b"ceth_defi.tokenised_fund.shift.vault\nShiftVault\n."
        assert pickle.loads(old_pickle) is ShiftVault  # noqa: S301 - Fixed trusted legacy global reference.
