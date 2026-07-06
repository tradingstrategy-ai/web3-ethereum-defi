"""Test Ember vault classification."""

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name

EMBER_Y10K_ADDRESS = "0x953972ea0c1703c58f09fb6fd2477fdcf0fee074"


def test_ember_y10k_hardcoded_protocol() -> None:
    """Ember Y10K resolves to the Ember protocol.

    The website protocol export relies on ``HARDCODED_PROTOCOLS`` for Ember
    vaults whose contract shape cannot be identified by generic probes alone.

    :return:
        None
    """

    features = HARDCODED_PROTOCOLS[EMBER_Y10K_ADDRESS]

    assert features == {ERC4626Feature.ember_like}
    assert get_vault_protocol_name(features) == "Ember"
