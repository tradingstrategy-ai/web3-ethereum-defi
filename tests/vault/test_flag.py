"""Test vault manual flags."""

import pytest

from eth_defi.vault.flag import BAD_FLAGS, VaultFlag, get_notes, get_vault_special_flags, is_flagged_vault
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk


@pytest.mark.parametrize(
    ("address", "protocol", "expected_flag", "expected_note"),
    [
        ("0x4f55e28d36b30a638c3aa1d5cbf9c4ccb3831506", "Silo Finance", VaultFlag.illiquid, "likely illiquid"),
        ("0xbed7c02887efd6b5eb9a547ac1a4d5e582791647", "<protocol not yet identified>", VaultFlag.abnormal_share_price, "abnormal high returns"),
        ("0x5424293637cc59ad7580ad1cac46e28d4801a587", "<protocol not yet identified>", VaultFlag.abnormal_share_price, "abnormal high returns"),
        ("0x7db7bcd6746f4dcfa2fdcdd80c1c313cc371f166", "<unknown ERC-7540>", VaultFlag.unofficial, "test vault"),
        ("0x25b4dc5f96312c7083a58d80d8ecad6ecddbbdfb", "<unknown ERC-7540>", VaultFlag.unofficial, "test vault"),
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
