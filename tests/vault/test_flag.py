"""Test vault manual flags."""

import pytest

from eth_defi.vault.flag import BAD_FLAGS, VaultFlag, get_notes, get_vault_special_flags, is_flagged_vault
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS, VaultTechnicalRisk, get_vault_risk


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


def test_securitize_risk_is_low() -> None:
    """Securitize protocol risk is classified as low."""
    assert get_vault_risk("Securitize") == VaultTechnicalRisk.low


def test_superstate_risk_is_low() -> None:
    """Superstate protocol risk is classified as low."""

    assert get_vault_risk("Superstate") == VaultTechnicalRisk.low


@pytest.mark.parametrize(
    ("address", "expected_note"),
    [
        ("0x7712c34205737192402172409a8f7ccef8aa2aec", "**Curator:** BlackRock / Securitize"),
        ("0x252739487c1fa66eaeae7ced41d6358ab2a6bca9", "**Curator:** Arca / Securitize"),
        ("0x0324dd195d0cd53f9f07bee6a48ee7a20bad738f", "**Curator:** SPiCE VC / Securitize"),
        ("0xda2ffa104356688e74d9340519b8c17f00d7752e", "**Curator:** Hamilton Lane / Securitize"),
        ("0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9", "**Curator:** Blockchain Capital / Securitize"),
        ("0xc0c61c29ef8beabc694987c93e5fe4af647042e7", "**Curator:** COSIMO digital / Securitize"),
        ("0x682ef9cc637ef56577092b29ae9275a629aae7db", "**Curator:** Science Inc. / Securitize"),
        ("0x5e17f6f450dcb0bc69b232ea554e224d7e88067a", "**Curator:** Protos Asset Management / Securitize"),
        ("0x09864f52b035ae22ee739dfa5c748fa080d07bd8", "**Curator:** J.P. Morgan"),
        ("0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e", "**Curator:** Superstate"),
    ],
)
def test_tokenised_fund_vaults_have_descriptive_flag_and_notes(address: str, expected_note: str) -> None:
    """Known tokenised funds have product notes without becoming bad vaults."""

    note = get_notes(address)

    assert note is not None
    assert expected_note in note
    assert get_vault_special_flags(address) == {VaultFlag.tokenised_fund}
    assert not is_flagged_vault(address)
    assert VaultFlag.tokenised_fund not in BAD_FLAGS


def test_summer_fi_protocol_vaults_are_blacklisted() -> None:
    """Summer.fi vaults are blacklisted after the 2026-07-06 exploit report."""
    address = "0x98c49e13bf99d7cad8069faa2a370933ec9ecf17"
    protocol = "Summer.fi"

    assert get_vault_special_flags(address, protocol) == {VaultFlag.illiquid}
    assert is_flagged_vault(address, protocol)
    assert "illiquid" in get_notes(address, protocol_name=protocol)
    assert get_vault_risk(protocol, address) == VaultTechnicalRisk.blacklisted


def test_hyperevm_out_of_gas_vault_is_blacklisted() -> None:
    """HyperEVM vaults that poison Multicall3 batches are blacklisted."""
    address = "0x2eee42a0704dd4c0ff8141f85e24de9085a76093"

    assert get_vault_risk("ERC-4626", address) == VaultTechnicalRisk.blacklisted
    assert address in BROKEN_VAULT_CONTRACTS


def test_old_mainnet_out_of_gas_contract_is_skipped_by_multicall_blacklist() -> None:
    """Old mainnet contracts that poison Multicall3 batches are skipped."""
    address = "0xffaa9f9aa5e4361f552bada90dcacdd08e5b41eb"

    assert address in BROKEN_VAULT_CONTRACTS


@pytest.mark.parametrize(
    ("address", "protocol", "expected_flag", "expected_note"),
    [
        ("0x4f55e28d36b30a638c3aa1d5cbf9c4ccb3831506", "Silo Finance", VaultFlag.illiquid, "likely illiquid"),
        ("0xae79b0d94e1c53cd2e8160899b8d58ec138d341f", "Silo Finance", VaultFlag.illiquid, "illiquid"),
        ("0xbed7c02887efd6b5eb9a547ac1a4d5e582791647", "<protocol not yet identified>", VaultFlag.abnormal_share_price, "abnormal high returns"),
        ("0x5424293637cc59ad7580ad1cac46e28d4801a587", "<protocol not yet identified>", VaultFlag.abnormal_share_price, "abnormal high returns"),
        ("0x7db7bcd6746f4dcfa2fdcdd80c1c313cc371f166", "<unknown ERC-7540>", VaultFlag.unofficial, "test vault"),
        ("0x25b4dc5f96312c7083a58d80d8ecad6ecddbbdfb", "<unknown ERC-7540>", VaultFlag.unofficial, "test vault"),
        ("0x3094b241aade60f91f1c82b0628a10d9501462f9", "Morpho", VaultFlag.illiquid, "illiquid"),
        ("0xfa17f7aadbfac2c5d3c8125555404c1ae17df853", "Morpho", VaultFlag.illiquid, "illiquid"),
        ("0xc5e7d3f76a03006540f17668a0267c668ffb5b75", "Liquity", VaultFlag.illiquid, "illiquid"),
        ("0x82c4c641ccc38719ae1f0fbd16a64808d838fdfd", "Morpho", VaultFlag.illiquid, "illiquid"),
        ("0x7193794ec82f527efb618ac50c078d348ecba4b6", "Morpho", VaultFlag.illiquid, "illiquid"),
        ("0xed9278c5188f37670b33ef3b00729e38260cd5d5", "Euler", VaultFlag.illiquid, "illiquid"),
        ("0xcbc9b61177444a793b85442d3a953b90f6170b7d", "Euler", VaultFlag.illiquid, "illiquid"),
        ("0x01864ae3c7d5f507cc4c24ca67b4cabbdda37ecd", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x49c5733d71511a78a3e12925ea832f49031c97e9", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xf1ba8c5ca5ab011d06f31e64dad313d204acb9eb", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x138c289bb8b855cf271305c8bcf91dc31ba30194", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x1ad2d433b5e95077eb2855eab854b72ea9ee9d6c", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x27934d4879fc28a74703726edae15f757e45a48a", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x57c582346b7d49a46af3745a8278917d1c1311b8", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xa9c251f8304b1b3fc2b9e8fcae78d94eff82ac66", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xb5526491742fee67e9e0d0d8c619a95d422fd398", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xf90cf999de728a582e154f926876b70e93a747b7", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x3799251bd81925cfccf2992f10af27a4e62bf3f7", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x66be42a0bda425a8c3b3c2cf4f4cb9edfcaed21d", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x8adb906421f65c27155f44f1829ca1e5b024c3f6", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xf675fbe777e992f5d5d84adf41161dc0f20104a6", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xa5eed1615cd883dd6883ca3a385f525e3beb4e79", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0x70c329d6f06b33fa6b75e335b35168b1de84217b", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xeaf77df5d03306bca4ee8b58b6821e6aca76309d", "Euler", VaultFlag.illiquid, "Stream xUSD"),
        ("0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29", "Altura", VaultFlag.controversial, "controversial"),
        ("0xda2f1b3cba732d779cff56f0cf9d3bc8aea6cd8d", "Yearn", VaultFlag.subvault, "not intended"),
        ("0x8092c20351cf4048b464df2144dc8a4dd49ce71d", "Morpho", VaultFlag.subvault, "not intended"),
        ("0x049e8aab2d3ca187e47d74cf8171ad266f18643e", "Yearn", VaultFlag.subvault, "not intended"),
        ("0xc9f01b5c6048b064e6d925d1c2d7206d4feef8a3", "Yearn", VaultFlag.subvault, "not intended"),
        ("0x93fec6639717b6215a48e5a72a162c50dcc40d68", "Yearn", VaultFlag.subvault, "not intended"),
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
