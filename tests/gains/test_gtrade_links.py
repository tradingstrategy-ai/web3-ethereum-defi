"""Deployment-aware Gains-family vault-link tests."""

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.metadata import REVIEWED_GAINS_VAULTS
from eth_defi.erc_4626.vault_protocol.gains.vault import GTRADE_VAULT_ADDRESSES, GTRADE_VAULT_LINK_MATRIX, GainsVault
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS
from eth_defi.vault.base import VaultSpec


@pytest.mark.parametrize(("chain_id", "address"), REVIEWED_GAINS_VAULTS)
def test_reviewed_vault_destination(chain_id: int, address: str) -> None:
    """Every reviewed deployment has an app or an explicit explorer fallback."""
    vault = GainsVault(web3=None, spec=VaultSpec(chain_id, address), features={ERC4626Feature.gains_like})
    assert vault.get_link().lower() == GTRADE_VAULT_LINK_MATRIX[address].lower()
    assert set(GTRADE_VAULT_LINK_MATRIX) == GTRADE_VAULT_ADDRESSES


@pytest.mark.parametrize(
    ("chain_id", "address", "expected"),
    [
        (42161, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0", "https://gains.trade/vaults/gUSDC"),
        (137, "0x91993f2101cc758d0deb7279d41e880f7defe827", "https://gains.trade/vaults/gDAI"),
        (137, "0x1544e1ff1a6f6bdbfb901622c12bb352a43464fb", "https://gains.trade/vaults/gETH"),
        (42161, "0x4beef1113f968326905224d2ca272f3032a9a9f4", "https://gains.trade/vaults/gGNS"),
        (8453, "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6", "https://gains.trade/vaults/gBTCUSD"),
        (4326, "0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d", "https://gains.trade/vaults/gUSDM"),
    ],
)
def test_verified_product_pages(chain_id: int, address: str, expected: str) -> None:
    """Published token routes and Nova's independently operated app are selected."""
    vault = GainsVault(web3=None, spec=VaultSpec(chain_id, address.upper().replace("0X", "0x")), features={ERC4626Feature.gains_like})
    assert vault.get_link() == expected


@pytest.mark.parametrize(
    ("chain_id", "address"),
    [
        (42161, "0x000000000000000000000000000000000000f00d"),
        (42161, "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7"),
        (56, "0xfb34af2138280e13b0759fd322fe63fccc7508a6"),
        (1, NOVA_POOL_ADDRESS),
        (1, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"),
        (42161, "0x20d419a8e12c45f88fda7c5760bb6923cee27f98"),
    ],
)
def test_unverified_or_different_chain_retains_explorer(chain_id: int, address: str) -> None:
    """An address or compatible contract alone cannot select a deposit app."""
    vault = GainsVault(web3=None, spec=VaultSpec(chain_id, address), features={ERC4626Feature.gains_like})
    assert vault.get_link().lower() == f"https://routescan.io/address/{address}"
