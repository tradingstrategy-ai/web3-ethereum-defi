"""Tests for reviewed Arcus pToken display data."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BTC_3X_LONG_VAULT, ARCUS_HOOD_3X_LONG_VAULT
from eth_defi.erc_4626.vault_protocol.arcus.offchain_data import get_arcus_vault_offchain_data


@pytest.mark.parametrize(
    ("vault_address", "expected_name"),
    (
        (ARCUS_BTC_3X_LONG_VAULT, "BTC (3x Long)"),
        (ARCUS_HOOD_3X_LONG_VAULT, "HOOD (3x Long)"),
    ),
)
def test_arcus_offchain_data_is_address_scoped(vault_address: HexAddress, expected_name: str) -> None:
    """Return conservative copy only for individually reviewed pTokens.

    The overlay is intentionally local: Arcus's exchange-market API does not
    provide pToken accounting or product terms.

    :param vault_address:
        Reviewed Arcus production pToken address.
    :param expected_name:
        Expected onchain product label represented in the overlay.
    """

    metadata = get_arcus_vault_offchain_data(vault_address)

    assert metadata is not None
    assert metadata["curator_name"] == "Arcus"
    assert metadata["short_description"] == f"Arcus pToken labelled {expected_name}."
    assert "does not independently verify" in metadata["description"]


def test_arcus_offchain_data_excludes_unreviewed_p_tokens() -> None:
    """Avoid inferring product copy for an unreviewed family member."""

    # This known test pToken shares the family detection signal but has no
    # reviewed production display data.
    assert get_arcus_vault_offchain_data(HexAddress("0x1193bcbfafeb2f25c516817c46bd3143936d1d5c")) is None
