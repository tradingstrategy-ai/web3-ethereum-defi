"""Fetch K3's first-party deployment metadata without trusting it as live NAV.

The official registry at https://github.com/K3-Capital/k3-vault-contracts/tree/main/deployments
contains historical deployment evidence. Its activation status and privileged
accounts must not override live contract state or expand the reviewed allowlist.
"""

import logging
from dataclasses import dataclass

import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.k3.constants import K3_REGISTRY_URL, K3_REGISTRY_VAULTS

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class K3VaultMetadata:
    """Validated descriptive identity from one deployment record."""

    #: Provider's deployment name, not a replacement for the onchain name.
    name: str
    #: Provider's share symbol.
    symbol: str
    #: Exact record used as provenance.
    source_url: str


def fetch_k3_vault_metadata(chain_id: int, address: HexAddress | str) -> K3VaultMetadata | None:
    """Fetch a reviewed deployment's identity, returning unknown on an outage.

    The Base and Monad MetaVaultWrapper deployments have no registry JSON
    and use maintained copy instead. Each adapter caches this result for its
    lifetime. Financial fields and old
    activation snapshots are intentionally excluded.

    :param chain_id: Deployment chain.
    :param address: Canonical wrapper proxy.
    :return: Validated descriptive identity, or ``None`` when unavailable.
    """
    key = (chain_id, address.lower())
    if key not in K3_REGISTRY_VAULTS:
        return None
    url = f"{K3_REGISTRY_URL}/{chain_id}/{Web3.to_checksum_address(address)}/deployment.json"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if payload["network"]["chainId"] != chain_id or payload["contracts"]["wrapperProxy"]["address"].lower() != key[1]:
            message = "K3 registry identity mismatch"
            raise ValueError(message)
        name, symbol = payload["vault"]["name"], payload["vault"]["symbol"]
        if not isinstance(name, str) or not name.strip() or not isinstance(symbol, str) or not symbol.strip():
            message = "K3 registry has invalid display metadata"
            raise ValueError(message)
        return K3VaultMetadata(name.strip(), symbol.strip(), url)
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.warning("K3 metadata unavailable for %s-%s: %s", chain_id, address, error)
        return None
