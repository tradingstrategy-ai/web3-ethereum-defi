"""Test curator identification inferred from Enzyme Blue listing metadata."""

import pytest

from eth_defi.vault.curator import identify_curator


@pytest.mark.parametrize(
    ("chain_id", "vault_address", "vault_name", "expected_slug"),
    [
        (1, "0xd618b03c7a1c0f3248ae049954d69e8d96a142c0", "ACC Metaverse Fund", "asymmetry-crypto-capital"),
        (1, "0xb8f69b26316818db0ea3b6d1639fedf744a2df41", "B100", "bgroup"),
        (1, "0xd89551d350532d001ad3105968fecb24b1c3cec8", "CASΦBTC", "casphi"),
        (1, "0x16770d642e882e1769ce4ac8612b8bc0601506fc", "Diva Early Stakers ETH Vault", "diva"),
        (1, "0x7dbfc77b308356a5d90c586c7e3f0e089b8e37ec", "HEXADEFI CAPITAL", "hexadefi-capital"),
        (1, "0x308b02c6a4e346f1f6fb5c7d79d0de2c4f3abb82", "Niska Capital Fund I (Ethereum)", "niska-capital"),
        (1, "0x1c6a1591d4a25e1ab258e3476bab4c022df79055", "Olatu Capital", "olatu-capital"),
        (1, "0xc368f3f4f5c1637321091bfa53de694fdf1f6740", "Stratum DeFi Yield Vault", "stratum-finance"),
        (1, "0x15ce0ce914f97ed7b0e3fe4da0c696002b3d2964", "Walled Fund ETH", "walled-capital"),
        (137, "0x62cd97c6900d07a72eb318fdd0dff462b4a3d7e8", "Artemis Trust", "artemis-trust"),
        (137, "0xc98e070cc35b98a0f8ba7579a68a1796b014c0b9", "Ewpple DeFi Crypto Index Fund", "ewpple"),
        (8453, "0x80dc1c8ad380c8ce9f45c94422b70edc52aa8804", "StarzFi Smart Savings Vault", "starzfi"),
        (42161, "0xd065f37a0ea7f277bf36d93043d20bfb58b93761", "Gemini BTC", "arc"),
        (1, "0xf67e2dc041b8a3c39d066037d29f500757b1e886", "seed USDN", "smardex"),
    ],
)
def test_identify_enzyme_blue_curator(
    chain_id: int,
    vault_address: str,
    vault_name: str,
    expected_slug: str,
) -> None:
    """Resolve manager labels inferred from reviewed Enzyme Blue listings.

    The Enzyme API does not provide a manager field for these rows. The
    maintained mappings consequently use only the published vault title and
    the few listings whose manager family is documented in the description.

    :param chain_id: Chain where the Enzyme Blue vault is deployed.
    :param vault_address: Canonical Enzyme VaultProxy address.
    :param vault_name: Published Enzyme vault title.
    :param expected_slug: Curator slug established from listing evidence.
    :return: None after the curator identity has been resolved.
    """

    assert identify_curator(chain_id, "", vault_name, vault_address, protocol_slug="enzyme") == expected_slug
