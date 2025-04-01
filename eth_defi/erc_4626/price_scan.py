"""Scan all historical prices."""
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import VaultFeatureProbe, create_vault_instance
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.vault.base import VaultHistoricalRead


def read_chain(
    web3: Web3,
    web3factory: Web3Factory,
    vault_probes: list[VaultFeatureProbe],
) -> VaultHistoricalRead:
    """Read all vaults on a chain."""

    for probe in vault_probes:
        vault = create_vault_instance(web3, probe.address, probe.features)

