"""Offline pToken metadata tests."""

from pathlib import Path

from eth_defi.erc_4626.vault_protocol.ptoken.constants import PTOKEN_HOOD_3X_LONG_VAULT
from eth_defi.vault.fee import get_vault_fee_mode
from eth_defi.vault.protocol_metadata import build_metadata_json


def test_ptoken_protocol_metadata() -> None:
    """Export unknown-issuer pToken metadata without a fabricated logo."""

    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/ptoken.yaml"), "https://example.invalid")

    assert metadata["name"] == "pToken"
    assert metadata["slug"] == "ptoken"
    assert metadata["short_description"].startswith("Currently not yet identified")
    assert "does not by itself identify the pToken issuer" in metadata["long_description"]
    assert metadata["logos"] == {"generic": None, "dark": None, "light": None}
    assert metadata["links"]["fact_sheet"] is None
    assert get_vault_fee_mode("pToken", PTOKEN_HOOD_3X_LONG_VAULT) is None
