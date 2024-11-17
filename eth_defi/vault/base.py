from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, TypedDict

from eth_defi.token import TokenAddress


class VaultEvent:
    pass


@dataclass(slots=True)
class VaultSpec:
    """Unique id for a vault"""
    chain_id: int
    vault_address: str


@dataclass(slots=True)
class BlockRange:
    """Block range for reading onchain data.

    - All our operations are based on a certain block number when actions happen.

    - For many operations like deposits, we need to sync the events since the last end block
    """

    #: Start block (inclusive)
    start_block: int

    #: End block (inclusive)
    end_block: int


class VaultInfo(TypedDict):
    """Vault-protocol specific intormation about the vault."""


class VaultDeploymentParameters(TypedDict):
    """Input needed to deploy a vault."""


@dataclass
class TradingUniverse:
    """Input needed to deploy a vault."""

    spot_token_addresses: set[TokenAddress]


@dataclass
class VaultPortfolio(TypedDict):
    """Input needed to deploy a vault."""

    spot_erc20: dict[TokenAddress, Decimal]



class VaultBase(ABC):
    """Base class for vault protocol adapters."""

    @abstractmethod
    def has_block_range_event_support(self) -> bool:
        """Can we query delta changes by block ranges."""

    @abstractmethod
    def fetch_portfolio(self, universe: TradingUniverse) -> VaultPortfolio:
        """Read token balances of a vault."""

    @abstractmethod
    def fetch_flow(
        self,
        range: BlockRange,
    ) -> Iterable[VaultEvent]:
        """Read token balances of a vault."""

    @abstractmethod
    def fetch_info(self, vault: VaultSpec) -> VaultInfo:
        """Read vault parameters from the chain."""

    @abstractmethod
    def deploy(self, params: VaultDeploymentParameters) -> VaultSpec:
        """Deploy a new vault."""

    @abstractmethod
    def fetch_deposit_queue(
        self,
        vault: VaultSpec,
        range: BlockRange,
    ) -> None:
        """Read incoming pending deposits."""

    @abstractmethod
    def fetch_withdraw_queue(
        self,
        vault: VaultSpec,
        range: BlockRange,
    ) -> None:
        """Read outgoing pending withdraws."""


