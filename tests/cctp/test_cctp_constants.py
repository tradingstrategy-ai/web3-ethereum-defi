"""CCTP mainnet domain mappings."""

from eth_defi.cctp.constants import (
    CCTP_DOMAIN_AVALANCHE,
    CCTP_DOMAIN_NAMES,
    CCTP_DOMAIN_TO_CHAIN_ID,
    CHAIN_ID_TO_CCTP_DOMAIN,
)

AVALANCHE_CHAIN_ID = 43_114


def test_avalanche_cctp_v2_domain_mapping() -> None:
    """Map Avalanche C-Chain and CCTP domain 1 in both directions."""
    assert CCTP_DOMAIN_AVALANCHE == 1
    assert CHAIN_ID_TO_CCTP_DOMAIN[AVALANCHE_CHAIN_ID] == CCTP_DOMAIN_AVALANCHE
    assert CCTP_DOMAIN_TO_CHAIN_ID[CCTP_DOMAIN_AVALANCHE] == AVALANCHE_CHAIN_ID
    assert CCTP_DOMAIN_NAMES[CCTP_DOMAIN_AVALANCHE] == "Avalanche"
