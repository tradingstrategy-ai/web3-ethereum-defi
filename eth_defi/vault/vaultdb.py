"""Describe vault database pickle format."""

import datetime
import pickle
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TypedDict, TypeAlias

from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
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
    """Metadata for all vaults across all chains we know about.

    - The pickle format dump for vault-db.pickle
    - Does not include share price/TVL data, only metadata
    """

    #: Correctly detected vaults.
    #:
    #: (chain id, address)  -> vault info mapping for detected vaults
    rows: dict[VaultSpec, VaultLead] = field(default_factory=dict)

    #: (chain id, address)  -> vault info mapping for ongoing scans on which we are still unsure
    #:
    #: Persistent to continue scan
    leads: dict[VaultSpec, PotentialVaultMatch] = field(default_factory=dict)

    #: Keep the track of the last scanned block for each chain so we do not start from the beginning
    last_scanned_block: dict[int, int] = field(default_factory=dict)

    @staticmethod
    def read(path: Path) -> "VaultDatabase":
        existing_db = pickle.load(path.open("rb"))
        return existing_db

    def write(self, path: Path):
        pickle.dump(self, path.open("wb"))

    def get_lead_count(self) -> int:
        return len(self.leads)

    def get_chain_start_block(self, chain_id: int, default_start_block=1) -> int:
        """Get the block to start scanning from for a given chain id.

        - Used to for iterative scanning
        """
        last_block = self.last_scanned_block.get(chain_id)
        if last_block is not None:
            return last_block + 1
        return default_start_block

    def get_existing_leads_by_chain(self, chain_id: int) -> dict[HexAddress, PotentialVaultMatch]:
        """Get existing leads for a given chain id.

        - Used to for iterative scanning
        """
        return {spec.vault_address: lead for spec, lead in self.leads.items() if spec.chain_id == chain_id}

    def update_leads_and_rows(
        self,
        chain_id: int,
        last_scanned_block: int,
        leads: dict[HexAddress, PotentialVaultMatch],
        rows: dict[VaultSpec, VaultLead],
    ):
        assert type(chain_id) == int
        assert type(last_scanned_block) == int
        assert type(rows) == dict
        assert type(leads) == dict
        self.last_scanned_block[chain_id] = last_scanned_block
        self.leads.update({VaultSpec(chain_id, addr): lead for addr, lead in leads.items()})
        self.rows.update(rows)