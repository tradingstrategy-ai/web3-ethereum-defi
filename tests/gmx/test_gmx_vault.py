"""No-RPC tests for the GMX catalogue adapter."""

from types import SimpleNamespace

import pytest
from eth_utils import to_checksum_address

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name, passes_price_scan_activity_filter
from eth_defi.gmx.vault import GMXVault
from eth_defi.vault.base import VaultSpec

GM_TOKEN = to_checksum_address("0x1000000000000000000000000000000000000001")
GLV_TOKEN = to_checksum_address("0x2000000000000000000000000000000000000002")


def test_gmx_features_route_to_catalogue_adapter() -> None:
    """GM and GLV detection features use the same read-only adapter."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))

    gm = create_vault_instance(web3, GM_TOKEN, {ERC4626Feature.gmx_gm})
    glv = create_vault_instance(web3, GLV_TOKEN, {ERC4626Feature.gmx_glv})

    assert isinstance(gm, GMXVault)
    assert isinstance(glv, GMXVault)
    assert get_vault_protocol_name({ERC4626Feature.gmx_gm}) == "GMX"
    assert get_vault_protocol_name({ERC4626Feature.gmx_glv}) == "GMX"


def test_gmx_catalogue_is_not_a_historical_price_scan_candidate() -> None:
    """Do not manufacture a GMX price curve from incomplete event data."""

    detection = SimpleNamespace(deposit_count=100, features={ERC4626Feature.gmx_gm})

    assert not passes_price_scan_activity_filter(detection, min_deposit_threshold=1)


def test_gmx_adapter_explains_usdc_display_denomination() -> None:
    """USDC display metadata does not misstate the GMX valuation currency."""

    vault = GMXVault(SimpleNamespace(eth=SimpleNamespace(chain_id=42161)), VaultSpec(42161, GM_TOKEN))

    assert vault.fetch_denomination_token_address().lower() == "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
    assert "displayed denomination" in vault.get_notes()
    with pytest.raises(NotImplementedError, match="Reader or GlvReader"):
        vault.fetch_nav("latest")
