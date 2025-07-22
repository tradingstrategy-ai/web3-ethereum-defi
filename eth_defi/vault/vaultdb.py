"""Describe vault database pickle format."""

import datetime
from decimal import Decimal
from typing import TypedDict, TypeAlias

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec


class VaultLead(TypedDict):
    """Vault info gathered during the vault discovery from the chain.

    - Avaulable as VaultDb pickle
    - Human readable entry
    - Machine readable data in :py:attr:`_detection_data`
    """

    Symbol: str

    Name: str

    Address: str

    Denomination: str

    NAV: Decimal

    Shares: Decimal

    Protocol: str

    _detection_data: ERC4262VaultDetection

    _denomination_token: dict

    features: set[ERC4626Feature]

    __annotations__ = {
        "First seen at": datetime.datetime,
        "Mgmt fee": float,
        "Perf fee": float,
    }


#: All vaults across all chains we know about.
#:
#: The pickle format dump for vault-db.pickle
VaultDatabase: TypeAlias = dict[VaultSpec, VaultLead]
