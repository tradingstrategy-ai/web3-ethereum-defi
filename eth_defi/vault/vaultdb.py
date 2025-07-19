"""Describe vault database pickle format."""
import datetime
from typing import TypedDict, TypeAlias

from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.vault.base import VaultSpec


class VaultLead(TypedDict):
    """Vault info gathered during the vault discovery from the chain.

    - Avaulable as VaultDb pickle
    """

    symbol: str

    name: str

    address: str

    denomination_token: str

    nav: float

    shares: float

    _detection_data: ERC4262VaultDetection

    __annotations__ = {
        "First seen at": datetime.datetime,
        "Mgmt fee": float,
        "Perf fee": float,
    }



#: All vaults across all chains we know about.
#:
#: The pickle format dump for vault-db.pickle
VaultDatabase: TypeAlias = dict[VaultSpec, VaultLead]