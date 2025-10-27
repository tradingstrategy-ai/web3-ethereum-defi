"""Describe vault database pickle format."""

import datetime
import pickle
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TypedDict, TypeAlias, Iterable
from atomicwrites import atomic_write

from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.vault.base import VaultSpec


#: Where we store the vault metadata database by default
DEFAULT_VAULT_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "vault-metadata-db.pickle"


class VaultRow(TypedDict):
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
VaultDatabaseOld: TypeAlias = dict[VaultSpec, VaultRow]


@dataclass(slots=True)
class VaultDatabase:
    """Metadata for all vaults across all chains we know about.

    - The pickle format dump for vault-db.pickle
    - Does not include share price/TVL data, only metadata
    """

    #: Correctly detected vaults.
    #:
    #: (chain id, address)  -> vault info mapping for detected vaults
    rows: dict[VaultSpec, VaultRow] = field(default_factory=dict)

    #: (chain id, address)  -> vault info mapping for ongoing scans on which we are still unsure
    #:
    #: Persistent to continue scan
    leads: dict[VaultSpec, PotentialVaultMatch] = field(default_factory=dict)

    #: Keep the track of the last scanned block for each chain so we do not start from the beginning,
    #
    #: Chain id -> block number.
    last_scanned_block: dict[int, int] = field(default_factory=dict)

    @staticmethod
    def read(path: Path = DEFAULT_VAULT_DATABASE) -> "VaultDatabase":
        """Load the picked file.

        Example:

        .. code-block:: python

            from eth_defi.vault.vaultdb import VaultDatabase

            # Load from the default location on local filesystem
            vault_db = VaultDatabase.read()

            print(f"We have data for {vault_db.get_lead_count()} potential vaults")

        """
        try:
            existing_db = pickle.load(path.open("rb"))
        except Exception as e:
            raise RuntimeError(f"Could not read vault database from {path}: {e}") from e
        return existing_db

    def write(self, path: Path):
        """Do an atomic write to avoid corrupted data."""

        with atomic_write(path, mode="wb", overwrite=True) as f:
            pickle.dump(self, f)

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
        rows: dict[VaultSpec, VaultRow],
    ):
        assert type(chain_id) == int
        assert type(last_scanned_block) == int
        assert type(rows) == dict
        assert type(leads) == dict
        self.last_scanned_block[chain_id] = last_scanned_block
        self.leads.update({VaultSpec(chain_id, addr): lead for addr, lead in leads.items()})
        self.rows.update(rows)

    #
    # Backwards compatibility methods, do not use in the future
    #

    def __len__(self):
        return len(self.rows)

    def keys(self) -> Iterable[VaultSpec]:
        """Iterable human readable vault (chain, address) tuples."""
        return self.rows.keys()

    def values(self) -> Iterable[VaultRow]:
        """Iterable human readable rows."""
        return self.rows.values()

    def items(self) -> Iterable[tuple[HexAddress, VaultRow]]:
        """Iterable human readable rows."""
        return self.rows.items()

    def get(self, key: VaultSpec, default=None) -> VaultRow | None:
        """Get vault row by spec."""
        return self.rows.get(key, default)
