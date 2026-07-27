"""Test ShiftVault hardcoded routing and safe public-flow handling."""

from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
from eth_defi.tokenised_fund.shift.constants import SHIFT_HARDCODED_LEADS, SHIFT_HOMEPAGE, SHIFT_VAULT_PRODUCTS
from eth_defi.tokenised_fund.shift.descriptions import SHIFT_VAULT_DESCRIPTIONS, get_shift_vault_description
from eth_defi.tokenised_fund.shift.vault import ShiftVault
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
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
