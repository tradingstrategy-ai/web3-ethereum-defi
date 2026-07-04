"""Test vault manual flags."""

import pytest

from eth_defi.vault.flag import BAD_FLAGS, VaultFlag, get_notes, get_vault_special_flags, is_flagged_vault
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk


def test_not_in_morpho_api_is_bad_flag():
    """Morpho API missing vaults are blacklisted."""
    assert VaultFlag.not_in_morpho_api in BAD_FLAGS


def test_controversial_is_bad_flag():
    """Controversial vaults are blacklisted."""
    assert VaultFlag.controversial in BAD_FLAGS


def test_paused_is_bad_flag():
    """Paused vaults are blacklisted."""
    assert VaultFlag.paused in BAD_FLAGS


def test_oda_fact_risk_is_low() -> None:
    """ODA-FACT protocol risk is classified as low."""
    assert get_vault_risk("Kinexys") == VaultTechnicalRisk.low


def test_oda_fact_vault_note_is_not_bad_flag() -> None:
    """ODA-FACT JLTXX note is descriptive and does not flag the vault."""
    address = "0x09864f52b035ae22ee739dfa5c748fa080d07bd8"
    note = get_notes(address)

    assert note is not None
    assert "**Curator:** J.P. Morgan" in note
    assert "JLTXX fact sheet" in note
    assert not is_flagged_vault(address)


@pytest.mark.parametrize(
    ("address", "protocol", "expected_flag", "expected_note"),
    [
        ("0x4f55e28d36b30a638c3aa1d5cbf9c4ccb3831506", "Silo Finance", VaultFlag.illiquid, "likely illiquid"),
        ("0xbed7c02887efd6b5eb9a547ac1a4d5e582791647", "<protocol not yet identified>", VaultFlag.abnormal_share_price, "abnormal high returns"),
        ("0x5424293637cc59ad7580ad1cac46e28d4801a587", "<protocol not yet identified>", VaultFlag.abnormal_share_price, "abnormal high returns"),
        ("0x7db7bcd6746f4dcfa2fdcdd80c1c313cc371f166", "<unknown ERC-7540>", VaultFlag.unofficial, "test vault"),
        ("0x25b4dc5f96312c7083a58d80d8ecad6ecddbbdfb", "<unknown ERC-7540>", VaultFlag.unofficial, "test vault"),
        ("0x3094b241aade60f91f1c82b0628a10d9501462f9", "Morpho", VaultFlag.illiquid, "illiquid"),
        ("0xfa17f7aadbfac2c5d3c8125555404c1ae17df853", "Morpho", VaultFlag.illiquid, "illiquid"),
        ("0xed9278c5188f37670b33ef3b00729e38260cd5d5", "Euler", VaultFlag.illiquid, "illiquid"),
        ("0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29", "Altura", VaultFlag.controversial, "controversial"),
        ("0xc9f01b5c6048b064e6d925d1c2d7206d4feef8a3", "Yearn", VaultFlag.subvault, "not intended"),
        ("0xad755c6c31515aef8d2f830767d846774f7e9ea9", "Morpho", VaultFlag.malicious, "malicious"),
    ],
)
def test_abnormal_main_listing_vaults_are_hidden(
    address: str,
    protocol: str,
    expected_flag: VaultFlag,
    expected_note: str,
):
    """Abnormal main listing vaults are marked with bad manual flags."""

    flags = get_vault_special_flags(address)

    assert flags == {expected_flag}
    assert expected_flag in BAD_FLAGS
    assert is_flagged_vault(address)
    assert expected_note in get_notes(address)
    assert get_vault_risk(protocol, address) == VaultTechnicalRisk.blacklisted
