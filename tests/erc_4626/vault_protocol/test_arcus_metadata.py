"""Offline Arcus pToken metadata tests."""

from pathlib import Path

from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_HOOD_3X_LONG_VAULT
from eth_defi.vault.fee import get_vault_fee_mode
from eth_defi.vault.protocol_metadata import build_metadata_json


def test_arcus_protocol_metadata() -> None:
    """Expose Arcus's conservative risk and fee metadata with logos.

    This intentionally contains no Robinhood JSON-RPC read, so it remains
    covered when CI does not provide a Robinhood Chain endpoint.
    """

    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/arcus.yaml"), "https://example.invalid")

    assert metadata["name"] == "Arcus"
    assert metadata["slug"] == "arcus"
    assert metadata["links"]["homepage"] == "https://arcus.xyz/"
    assert metadata["logos"]["generic"] == "https://example.invalid/vault-protocol-metadata/arcus/generic.png"
    assert metadata["logos"]["light"] == "https://example.invalid/vault-protocol-metadata/arcus/light.png"
    assert get_vault_fee_mode("Arcus", ARCUS_HOOD_3X_LONG_VAULT) is None
