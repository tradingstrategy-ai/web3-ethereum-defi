"""Generic Vault adapter base classes.

- Create unified interface across different vault protocols and their investment flows

- Helps to create automated trading agents against any vault easily

- Handle both trading (asset management role) and investor management (deposits/redemptions)

- See :py:class:`VaultBase` to get started
"""
import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from typing import TypedDict, Iterable

from eth.typing import BlockRange, Block
from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import TokenAddress, fetch_erc20_details, TokenDetails
from eth_defi.vault.lower_case_dict import LowercaseDict


@dataclass(slots=True, frozen=True)
class VaultSpec:
    """Unique id for a vault.

    - Each vault can be identified by smart contract address by one of the contracts,
      related to its deployment. Usually this contract is vault contract itself.

    - We need both chain and address to specify vault we mean.
    """

    #: Ethereum chain id
    chain_id: int

    #: Vault smart contract address or whatever is the primary address for unravelling a vault deployment for a vault protocol
    vault_address: HexAddress | str
    def __post_init__(self):
        assert isinstance(self.chain_id, int)
        assert isinstance(self.vault_address, str), f"Expected str, got {self.vault_address}"
        assert self.vault_address.startswith("0x")


class VaultInfo(TypedDict):
    """Vault-protocol specific intormation about the vault.

    - A dictionary of data we gathered about the vault deployment,
      like various smart contracts associated with the vault

    - Not standardised yet
    """


@dataclass
class TradingUniverse:
    """Describe assets vault can manage.

    - Because of brainrotten and awful ERC-20 token standard, the vault does not know what tokens it owns
      and this needs to be specific offchain
    """

    spot_token_addresses: set[TokenAddress]


@dataclass
class VaultPortfolio:
    """Track assets and balances in a vault.

    - Offchain method to track what assets a vault contains

    - Takes :py:class:`TradingUniverse` as an input and resolves all relevant balances the vault holds for this trading universe

    - Because of brainrotten and awful ERC-20 token standard, the vault does not know what tokens it owns
      and this needs to be specific offchain

    - See :py:meth:`VaultBase.fetch_portfolio`
    """

    #: List of tokens and their amounts
    #:
    #: Addresses not checksummed
    #:
    spot_erc20: LowercaseDict

    #: For route finding, which DEX tokens should use.
    #:
    #: Token address -> DEX id string mapping
    dex_hints: dict[HexAddress, list[str]] = field(default_factory=dict)

    def __post_init__(self):

        assert isinstance(self.spot_erc20, LowercaseDict)

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

    def get_raw_spot_balances(self, web3: Web3) -> LowercaseDict:
        """Convert spot balances to raw token balances"""
        chain_id = web3.eth.chain_id
        return LowercaseDict(**{addr: fetch_erc20_details(web3, addr, chain_id=chain_id).convert_to_raw(value) for addr, value in self.spot_erc20.items()})


@dataclass(slots=True, frozen=True)
class VaultHistoricalRead:
    """Vault share price and fee structure at the point of time."""

    #: Vault for this result is
    vault: "VaultBase"

    #: block number of the reade
    block_number: int

    #: Naive datetime in UTC
    timestamp: datetime.datetime

    #: What was the share price in vault denomination token
    share_price: Decimal

    #: NAV / Assets under management in denomination token
    total_assets: Decimal

    #: Number of share tokens
    total_supply: Decimal

    #: What was the vault performance fee around the time
    performance_fee: float | None

    #: What was the vault management fee around the time
    management_fee: float | None

    def export(self) -> dict:
        """Convert historical read for a DataFrame export."""
        return {
            "block_number": self.block_number,
            "timestamp": self.timestamp,
            "share_price": float(self.share_price),
            "total_assets": float(self.total_assets),
            "total_supply": float(self.total_supply),
            "performance_fee": float(self.performance_fee) if self.performance_fee is not None else float("nan"),
            "management_fee": float(self.management_fee) if self.management_fee is not None else float("nan"),
        }


class VaultHistoricalReader(ABC):
    """Support reading historical vault share prices.

    - Allows to construct historical returns
    """

    def __init__(self, vault: "VaultBase"):
        self.vault = vault

    @property
    def address(self) -> HexAddress:
        return self.vault.address

    @abstractmethod
    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Create smart contract calls needed to read the historical state of this vault.

        - Multicall machinery will call these calls at a specific block and report back to :py:meth:`process_result`
        """
        pass

    @abstractmethod
    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        """Process the result of mult

        - Calls are created in :py:meth:`construct_multicalls`

        - This method combines result of this calls to a easy to manage historical record :py:class:`VaultHistoricalRead`
        """


class VaultFlowManager(ABC):
    """Manage deposit/redemption events.

    - For some vault structures, we need to know how much redemptions there are in the queue, so
      we can rebalance to have enough cash

    - Create a replay of flow events that happened for a vault within a specific block range

    - Not implemented yet
    """

    @abstractmethod
    def fetch_pending_redemption(
        self,
        block_identifier: BlockIdentifier,
    ) -> Decimal:
        """Get how much users want to redeem from the vault.

        :param block_identifier:
            Block number

        :return:
            Number of share tokens the users want to redeem from the vault.

            Shares must be valued separately.
        """

    @abstractmethod
    def fetch_pending_deposit(
        self,
        block_identifier: BlockIdentifier,
    ) -> Decimal:
        """Get how much users want to redeem from the vault.

        :param block_identifier:
            Block number

        :return:
            Number of underlying tokens the users want to redeem from the vault.

        """

    @abstractmethod
    def fetch_pending_deposit_events(
        self,
        range: BlockRange,
    ) -> None:
        """Read incoming pending deposits."""

    @abstractmethod
    def fetch_pending_redemption_event(
        self,
        range: BlockRange,
    ) -> None:
        """Read outgoing pending withdraws."""

    @abstractmethod
    def fetch_processed_deposit_event(
        self,
        range: BlockRange,
    ) -> None:
        """Read incoming pending deposits."""

    @abstractmethod
    def fetch_processed_redemption_event(
        self,
        vault: VaultSpec,
        range: BlockRange,
    ) -> None:
        """Read outgoing pending withdraws."""


class VaultBase(ABC):
    """Base class for vault protocol adapters.

    - Allows automated interaction with different `vault protocols <https://tradingstrategy.ai/glossary/vault>`__.

    - Contains various abstract methods that the implementation class must override

    Supported protocols include

    - Lagoon Finance: :py:class:`eth_defi.lagoon.vault.LagoonVault`

    - Velvet Capital: :py:class:`eth_defi.velvet.vault.VelvetVault`

    Code exists, but does not confirm the interface yet:

    - Enzyme Finance: :py:class:`eth_defi.enzyme.vault.Vault`

    **Vault covered functionality**

    - Fetching the current balances, deposits or redemptions

        - Either using naive polling approach with :py:meth:`fetch_portfolio`
        - Listen to vault events for deposits and redemptions using :py:meth:`get_flow_manager`

    - Get vault information with :py:meth:`fetch_info`
        - No standardised data structures or functions yet

    - Build a swap through a vault
        - No standardised data structure yet

    - Update vault position valuations
        - No standardised data structure yet

    **Integration check list**

    Integration tests needed for:

    - ☑️ read vault core info
    - ☑️ read vault investors
    - ☑️ read vault share price
    - ☑️ read vault share token
    - ☑️ read all positions
    - ☑️ read NAV
    - ☑️ read pending redemptions to know how much USDC we will need for the next settlement cycles
    - ☑️ deposit integration test
    - ☑️ redemption integration
    - ☑️ swap integration test
    - ☑️ re-valuation integration test
    - ☑️ only asset manager allowed to swap negative test
    - ☑️ only valuation commitee allowed to update vault valuations (if applicable)
    - ☑️ can redeem if enough USDC to settle
    - ☑️ cannot redeem not enough USDC to settle

    For code examples see `tests/lagoon` and `tests/velvet` on the `Github repository <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`__.
    """

    #: Block number hint when this vault was deployed.
    #:
    #: Must be set externally, as because of shitty Ethereum RPC we cannot query this.
    #: Allows us to avoid unnecessary work when scanning historical price data.
    #:
    first_seen_at_block: int | None

    def __init__(self):
        self.first_seen_at_block = None

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name} {self.symbol} at {self.address}>"

    @property
    @abstractmethod
    def chain_id(self) -> int:
        """Chain this vault is on"""

    @property
    @abstractmethod
    def address(self) -> HexAddress:
        """Vault contract address.

        - Often vault protocols need multiple contracts per vault,
          so what this function returns depends on the protocol
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Vault name."""
        pass

    @property
    @abstractmethod
    def symbol(self) -> str:
        """Vault share token symbol"""
        pass

    @abstractmethod
    def has_block_range_event_support(self) -> bool:
        """Does this vault support block range-based event queries for deposits and redemptions.

        - If not we use chain balance polling-based approach
        """

    @abstractmethod
    def has_deposit_distribution_to_all_positions(self) -> bool:
        """Deposits go automatically to all open positions.

        - Deposits do not land into the vault as cash

        - Instead, smart contracts automatically increase all open positions

        - The behaviour of Velvet Capital
        """

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
        """Read vault parameters from the chain.

        Use :py:meth:`info` property for cached access.
        """

    @abstractmethod
    def get_flow_manager(self) -> VaultFlowManager:
        """Get flow manager to read individial events.

        - Only supported if :py:meth:`has_block_range_event_support` is True
        """

    @abstractmethod
    def get_historical_reader(self) -> VaultHistoricalReader:
        """Get share price reader to fetch historical returns.

        :return:
            None if unsupported
        """

    @abstractmethod
    def fetch_denomination_token(self) -> TokenDetails:
        """Read denomination token from onchain.

        Use :py:meth:`denomination_token` for cached access.
        """

    @abstractmethod
    def fetch_nav(self) -> Decimal:
        """Fetch the most recent onchain NAV value.

        :return:
            Vault NAV, denominated in :py:meth:`denomination_token`
        """

    @cached_property
    def denomination_token(self) -> TokenDetails | None:
        """Get the token which denominates the vault valuation

        - Used in deposits and redemptions

        - Used in NAV calculation

        - Used in profit benchmarks

        - Usually USDC

        :return:
            Token wrapper instance.

            Maybe None for broken vaults like
            https://arbiscan.io/address/0x9d0fbc852deccb7dcdd6cb224fa7561efda74411#code
        """
        return self.fetch_denomination_token()

    @abstractmethod
    def fetch_share_token(self) -> TokenDetails:
        """Read share token details onchain.

        Use :py:meth:`share_token` for cached access.
        """

    @cached_property
    def share_token(self) -> TokenDetails:
        """ERC-20 that presents vault shares.

        - User gets shares on deposit and burns them on redemption
        """
        return self.fetch_share_token()

    @cached_property
    def info(self) -> VaultInfo:
        """Get info dictionary related to this vault deployment.

        - Get cached data on the various vault parameters

        :return:
            Vault protocol specific information dictionary
        """
        return self.fetch_info()

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee as a percent.

        :return:
            0.1 = 10%
        """
        raise NotImplementedError()

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current performancae fee as a percent.

        :return:
            0.1 = 10%
        """
        raise NotImplementedError()

