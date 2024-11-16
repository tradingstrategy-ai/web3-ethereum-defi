from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, TypedDict


class VaultEvent:
    pass


@dataclass(slots=True)
class VaultSpec:
    """Unique id for a vault"""
    chain_id: int
    vault_address: str


class VaultInfo(TypedDict):
    """Vault-protocol specific intormation about the vault."""


class VaultDeploymentParameters(TypedDict):
    """Input needed to deploy a vault."""


class Vault(ABC):
    """Base class for vault protocol adapters."""

    @abstractmethod
    def has_block_range_support(self) -> bool:
        """Can we query delta changes by block ranges."""

    @abstractmethod
    def fetch_balances(self, token_universe: set[str]) -> dict[str, Decimal]:
        """Read token balances of a vault."""

    @abstractmethod
    def fetch_flow(
        self,
        start_block: int,
        end_block: int,
    ) -> Iterable[VaultEvent]:
        """Read token balances of a vault."""

    @abstractmethod
    def fetch_info(self, vault: VaultSpec) -> VaultInfo:
        """Read vault parameters from the chain."""

    @abstractmethod
    def deploy(self, params: VaultDeploymentParameters) -> VaultSpec:
        """Deploy a new vault."""



