"""Native gTrade vault-link tests."""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.vault import (
    GTRADE_VAULT_ADDRESSES,
    GTRADE_VAULT_APP_URL,
    GTRADE_VAULT_LINK_MATRIX,
    GainsVault,
)
from eth_defi.vault.base import VaultSpec


def make_gtrade_vault(address: str) -> GainsVault:
    """Create a gTrade vault adapter without performing JSON-RPC reads."""
    return GainsVault(
        web3=None,
        spec=VaultSpec(chain_id=42161, vault_address=address),
        features={ERC4626Feature.gains_like},
    )


def test_gtrade_vault_link_matrix_covers_every_known_vault() -> None:
    """Known gTrade vaults link to an official Gains Network destination."""
    assert set(GTRADE_VAULT_LINK_MATRIX) == GTRADE_VAULT_ADDRESSES

    for address, expected_link in GTRADE_VAULT_LINK_MATRIX.items():
        assert make_gtrade_vault(address).get_link() == expected_link
        assert expected_link.startswith("https://gains.trade/")


def test_gtrade_vault_links_use_verified_specific_and_generic_pages() -> None:
    """Use token pages where published and the gTrade application otherwise."""
    assert make_gtrade_vault("0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0").get_link() == "https://gains.trade/vaults/gUSDC"
    assert make_gtrade_vault("0xd85e038593d7a098614721eae955ec2022b9b91b").get_link() == "https://gains.trade/vaults/gDAI"
    assert make_gtrade_vault("0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d").get_link() == "https://gains.trade/vaults/gUSDM"
    assert make_gtrade_vault("0xfb34af2138280e13b0759fd322fe63fccc7508a6").get_link() == GTRADE_VAULT_APP_URL


def test_unknown_gains_like_vault_retains_base_adapter_link() -> None:
    """Do not incorrectly point another Gains-like protocol at gTrade."""
    vault = make_gtrade_vault("0x000000000000000000000000000000000000f00d")

    assert vault.get_link().lower() == "https://routescan.io/address/0x000000000000000000000000000000000000f00d"
