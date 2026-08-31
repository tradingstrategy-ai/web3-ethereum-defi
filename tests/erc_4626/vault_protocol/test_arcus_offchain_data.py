"""Tests for reviewed Arcus pToken display data."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BTC_3X_LONG_VAULT, ARCUS_HOOD_3X_LONG_VAULT
from eth_defi.erc_4626.vault_protocol.arcus.offchain_data import get_arcus_vault_offchain_data


@pytest.mark.parametrize(
    ("vault_address", "market"),
    (
        (ARCUS_BTC_3X_LONG_VAULT, "BTC"),
        (ARCUS_HOOD_3X_LONG_VAULT, "HOOD"),
    ),
)
def test_arcus_offchain_data_is_address_scoped(vault_address: HexAddress, market: str) -> None:
    """Return announcement-backed copy only for reviewed pTokens.

    The overlay is intentionally local so scans do not depend on Arcus's live
    vault API.

    :param vault_address:
        Reviewed Arcus production pToken address.
    :param market:
        Expected perpetual market represented in the overlay.
    """

    metadata = get_arcus_vault_offchain_data(vault_address)

    assert metadata is not None
    assert metadata["short_description"] == f"Arcus pToken targeting 3x long {market} perpetual exposure."
    assert f"targets 3x long {market} exposure" in metadata["description"]
    assert "automatic threshold-based rebalancing" in metadata["notes"]
    assert "Read the [announcement](https://arcus.xyz/blog/ptokens-a-new-primitive-on-arcus) for more details." in metadata["notes"]
    assert f"{market} perpetual position" in metadata["notes"]
    assert metadata["deposit_fee"] == pytest.approx(0.0025)
    assert metadata["performance_fee"] == pytest.approx(0.0)


def test_arcus_offchain_data_excludes_unreviewed_p_tokens() -> None:
    """Avoid inferring product copy for an unreviewed family member."""

    # This known test pToken shares the family detection signal but has no
    # reviewed production display data.
    assert get_arcus_vault_offchain_data(HexAddress("0x1193bcbfafeb2f25c516817c46bd3143936d1d5c")) is None
