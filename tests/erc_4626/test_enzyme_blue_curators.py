"""Test curator identification inferred from Enzyme Blue listing metadata."""

import pytest

from eth_defi.vault.curator import identify_curator


@pytest.mark.parametrize(
    ("chain_id", "vault_address", "vault_name", "expected_slug"),
    [
        (1, "0xd618b03c7a1c0f3248ae049954d69e8d96a142c0", "ACC Metaverse Fund", "asymmetry-crypto-capital"),
        (1, "0xb8f69b26316818db0ea3b6d1639fedf744a2df41", "B100", "bgroup"),
        (1, "0xd89551d350532d001ad3105968fecb24b1c3cec8", "CASΦBTC", "casphi"),
        (1, "0x3d81ed103cd7bbd434954b197b9aeed565be3ec2", "Defiable Large Cap", "defiable-asset-management"),
        (1, "0xe0f1a74b6f340d1dfefe4b0268f04b23cc665f27", "Defiable Mid/Small Cap", "defiable-asset-management"),
        (1, "0x16770d642e882e1769ce4ac8612b8bc0601506fc", "Diva Early Stakers ETH Vault", "diva"),
        (1, "0x7dbfc77b308356a5d90c586c7e3f0e089b8e37ec", "HEXADEFI CAPITAL", "hexadefi-capital"),
        (1, "0xc6ab5794cd8d9169abacfe7412da4e3e5c12a29d", "Hill View Assets", "hill-view-assets"),
        (137, "0x3abee8231fc8d67406fa4c2b247d22b0e338e73e", "Sasquatch", "hill-view-assets"),
        (1, "0x7928c8c704b3aa79b6ecdd7b3e878613b194a99f", "First Lomonosov", "first-lomonosov-crypto-fund"),
        (1, "0xa5c5554fff234e26d20daa8e1ba1fffbc8a1da38", "Rix Crypto Fund II", "mavrix-ventures"),
        (1, "0xea2c32c03575433d04898deb28db2f17d710b60a", "Mojomix - mojomix.io", "mojomix-dao"),
        (1, "0x9bd983c846042fe5ccde737eb8caa2a07d074e07", "Morpheus Alpha Swing Trading (MAST)", "morpheus-trading-group"),
        (1, "0x308b02c6a4e346f1f6fb5c7d79d0de2c4f3abb82", "Niska Capital Fund I (Ethereum)", "niska-capital"),
        (137, "0xe113eee61b9cda3ec99b02645d361d8d0aa08a63", "Niska Capital Fund I", "niska-capital"),
        (1, "0x1c6a1591d4a25e1ab258e3476bab4c022df79055", "Olatu Capital", "olatu-capital"),
        (1, "0x04ed87d15ec5cca07c163e9a7ed65e2bc0b1cf33", "Primus Crypto Fund", "primus-crypto-fund"),
        (1, "0xc368f3f4f5c1637321091bfa53de694fdf1f6740", "Stratum DeFi Yield Vault", "stratum-finance"),
        (1, "0x15ce0ce914f97ed7b0e3fe4da0c696002b3d2964", "Walled Fund ETH", "walled-capital"),
        (1, "0xded69068a94776a23f5bdafc6b4c6894bc88e82c", "Techemy Capital - Holistic ETH-BTC Fund", "techemy-capital"),
        (1, "0x01c7b0e79e7599184c2adbf4666e122e88382d2f", "Techemy Capital - Managed DeFi Portfolio", "techemy-capital"),
        (137, "0x671641e3fcc4dd1d31ff6c08598ee4f91bb13203", "Techemy Capital - Holistic ETH-BTC (Polygon)", "techemy-capital"),
        (1, "0xaf408c0bf86bf2eb69470139758483bf367d79ce", "Wonder Labs Fund I", "wonder-labs"),
        (137, "0x62cd97c6900d07a72eb318fdd0dff462b4a3d7e8", "Artemis Trust", "artemis-trust"),
        (137, "0xc98e070cc35b98a0f8ba7579a68a1796b014c0b9", "Ewpple DeFi Crypto Index Fund", "ewpple"),
        (137, "0x508ee35f32d8fcbdfe8caa79e938814ac1bac04f", "Causality Group Fund", "defi-coinoisseurs"),
        (137, "0x4134d87c87df974577c580561b4776c2ab70cb43", "DeFi COINoisseurs", "defi-coinoisseurs"),
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
