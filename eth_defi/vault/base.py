from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, TypedDict

from eth.typing import BlockRange
from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.token import TokenAddress, TokenDetails, fetch_erc20_details


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

    spot_erc20: dict[HexAddress, Decimal]

    def __post_init__(self):
        for token, value in self.spot_erc20.items():
            assert type(token) == str
            assert isinstance(value, Decimal)

    @property
    def tokens(self) -> set[HexAddress]:
        """Get list of tokens held in this portfolio"""
        return set(self.spot_erc20.keys())

    def is_spot_only(self) -> bool:
        """Do we have only ERC-20 hold positions in this portfolio"""
        return True  # Other positiosn not supported yet

    def get_position_count(self):
        return len(self.spot_erc20)

    def get_raw_spot_balances(self, web3: Web3) -> dict[HexAddress, int]:
        """Convert spot balances to raw token balances"""
        chain_id = web3.eth.chain_id
        return {addr: fetch_erc20_details(web3, addr, chain_id=chain_id).convert_to_raw(value) for addr, value in self.spot_erc20.items()}



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