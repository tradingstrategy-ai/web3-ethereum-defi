"""Policy classification tests for Gains and Ostium vault routes."""

from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault, OstiumVault


def test_gains_and_ostium_supported_deposit_routes_are_permissionless() -> None:
    """Epochs, caps and async settlement never make the public routes a whitelist."""
    assert GainsVault.is_whitelisted_deposit(object()) is False
    assert OstiumVault.is_whitelisted_deposit(object()) is False
