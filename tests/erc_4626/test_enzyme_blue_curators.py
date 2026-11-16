"""Test curator identification inferred from Enzyme Blue listing metadata."""

import pytest

from eth_defi.vault.curator import identify_curator


@pytest.mark.parametrize(
    ("chain_id", "vault_address", "vault_name", "expected_slug"),
    [
        (1, "0xd89551d350532d001ad3105968fecb24b1c3cec8", "CASΦBTC", "casphi"),
        (1, "0xc6ab5794cd8d9169abacfe7412da4e3e5c12a29d", "Hill View Assets", "hill-view-assets"),
        (137, "0x3abee8231fc8d67406fa4c2b247d22b0e338e73e", "Sasquatch", "hill-view-assets"),
        (1, "0x308b02c6a4e346f1f6fb5c7d79d0de2c4f3abb82", "Niska Capital Fund I (Ethereum)", "niska-capital"),
        (137, "0xe113eee61b9cda3ec99b02645d361d8d0aa08a63", "Niska Capital Fund I", "niska-capital"),
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
