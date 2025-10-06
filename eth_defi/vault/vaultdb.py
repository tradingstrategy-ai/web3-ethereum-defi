"""Describe vault database pickle format."""

import datetime
from dataclasses import dataclass, field
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


#: Legacy pickle format
VaultDatabaseOld: TypeAlias = dict[VaultSpec, VaultLead]


@dataclass(slots=True)
class VaultDatabase:
    """All vaults across all chains we know about.

    The pickle format dump for vault-db.pickle
    """

    #: (chain id, address)  -> vault info mapping
    leads: dict[VaultSpec, VaultLead] = field(default_factory=dict)

    #: Keep the track of the last scanned block for each chain so we do not start from the beginning
    last_scanned_block: dict[int, int] = field(default_factory=dict)

    def get_chain_start_block(self, chain_id: int, default_start_block=1) -> int:
        """Get the block to start scanning from for a given chain id.

        - Used to for iterative scanning
        """
        return self.last_scanned_block.get(chain_id, default_start_block)

    def get_existing_leads_by_chain(self, chain_id: int) -> dict[VaultSpec, VaultLead]:
        """Get existing leads for a given chain id.

        - Used to for iterative scanning
        """
        return {spec: lead for spec, lead in self.leads.items() if spec.chain_id == chain_id}

    def update_leads(
        self,
        chain_id: int,
        last_scanned_block: int,
        new_leads: dict[VaultSpec, VaultLead],
    ):
        self.last_scanned_block[chain_id] = last_scanned_block
        self.leads.update(new_leads)