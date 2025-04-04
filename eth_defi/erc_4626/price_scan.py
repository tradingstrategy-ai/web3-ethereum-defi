"""Scan all historical prices."""
from typing import Iterator

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import VaultFeatureProbe, create_vault_instance
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.token import get_chain_stablecoins
from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.historical import VaultHistoricalReadMulticaller


def read_chain(
    web3: Web3,
    web3factory: Web3Factory,
    vault_probes: list[VaultFeatureProbe],
) -> Iterator[VaultHistoricalRead]:
    """Read historical prices of all vaults on a chain.

    - You need to get

    :param vault_probes:
        Get beforehand using :py:mod:`eth_defi.erc_4626.hypersync_discovery`

    :return:
        Iterator allowing you to push data to a container you wish
    """

    supported_quote_tokens = get_chain_stablecoins()

    for probe in vault_probes:
        vault = create_vault_instance(web3, probe.address, probe.features)

    reader = VaultHistoricalReadMulticaller(
        web3factory,
        s
    )
