from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, TypedDict

from eth.typing import BlockRange
from eth_typing import BlockIdentifier, HexAddress

from eth_defi.token import TokenAddress


class VaultEvent:
    pass


@dataclass(slots=True, frozen=True)
class VaultSpec:
    """Unique id for a vault"""
    chain_id: int
    vault_address: HexAddress

    def __post_init__(self):
        assert isinstance(self.chain_id, int)
        assert isinstance(self.vault_address, str)
        assert self.vault_address.startswith("0x")


class VaultInfo(TypedDict):
    """Vault-protocol specific intormation about the vault."""


class VaultDeploymentParameters(TypedDict):
    """Input needed to deploy a vault."""


@dataclass
class TradingUniverse:
    """Input needed to deploy a vault."""

    spot_token_addresses: set[TokenAddress]


@dataclass
class VaultPortfolio:
    """Input needed to deploy a vault."""

    spot_erc20: dict[TokenAddress, Decimal]


class VaultFlowManager(ABC):

    @abstractmethod
    def fetch_pending_deposits(
        self,
        range: BlockRange,
    ) -> None:
        """Read incoming pending deposits."""

    @abstractmethod
    def fetch_pending_redemptions(
        self,
        range: BlockRange,
    ) -> None:
        """Read outgoing pending withdraws."""

    @abstractmethod
    def fetch_processed_deposits(
        self,
        range: BlockRange,
    ) -> None:
        """Read incoming pending deposits."""

    @abstractmethod
    def fetch_processed_redemptions(
        self,
        vault: VaultSpec,
        range: BlockRange,
    ) -> None:
        """Read outgoing pending withdraws."""


class VaultBase(ABC):
    """Base class for vault protocol adapters.

    - Takes :py:class:`VaultSpec` as a constructor argument and builds a proxy class
      for accessing the vault based on this
    """

    @abstractmethod
    def has_block_range_event_support(self) -> bool:
        """Can we query delta changes by block ranges."""

    @abstractmethod
    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
    ) -> VaultPortfolio:
        """Read the current token balances of a vault.

        - SHould be supported by all implementations
        """

    @abstractmethod
    def fetch_info(self) -> VaultInfo:
        """Read vault parameters from the chain."""

    @abstractmethod
    def get_flow_manager(self) -> VaultFlowManager:
        """Get flow manager to read individial events.

        - Only supported if :py:meth:`has_block_range_event_support` is True
        """