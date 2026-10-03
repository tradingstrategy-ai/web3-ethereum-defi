"""Policy classification tests for Gains and unsupported Ostium vault routes."""

import pytest

from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault, OstiumVault


@pytest.mark.parametrize(
    "vault_class",
    [
        GainsVault,
        # 2026-10-03: Ostium is unsupported after the hack.
        pytest.param(OstiumVault, marks=pytest.mark.skip(reason="Ostium unsupported after the hack")),
    ],
)
def test_supported_deposit_routes_are_permissionless(vault_class: type[GainsVault]) -> None:
    """Epochs, caps and async settlement never make public routes a whitelist.

    Keep Gains policy coverage enabled while the unsupported Ostium case is
    explicitly skipped.

    :param vault_class:
        Protocol adapter whose public deposit policy is checked.
    :return:
        None; assertions verify permissionless classification.
    """
    assert vault_class.is_whitelisted_deposit(object()) is False
