"""Test reviewed pToken display data."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.ptoken.constants import PTOKEN_BTC_3X_LONG_VAULT, PTOKEN_HOOD_3X_LONG_VAULT
from eth_defi.erc_4626.vault_protocol.ptoken.offchain_data import get_ptoken_vault_offchain_data


@pytest.mark.parametrize("vault_address", (PTOKEN_BTC_3X_LONG_VAULT, PTOKEN_HOOD_3X_LONG_VAULT))
def test_ptoken_offchain_data_is_address_scoped(vault_address: HexAddress) -> None:
    """Return unknown-issuer copy only for reviewed pToken addresses.

    :param vault_address:
        Reviewed pToken address.
    """

    metadata = get_ptoken_vault_offchain_data(vault_address)

    assert metadata is not None
    assert metadata["short_description"].startswith("Currently not yet identified")
    assert metadata["description"].startswith("Currently not yet identified.")
    assert "Arcus ownership" in metadata["description"]


def test_ptoken_offchain_data_excludes_unreviewed_family_members() -> None:
    """Avoid assigning an unknown issuer to another pToken family member."""

    assert get_ptoken_vault_offchain_data(HexAddress("0x1193bcbfafeb2f25c516817c46bd3143936d1d5c")) is None
