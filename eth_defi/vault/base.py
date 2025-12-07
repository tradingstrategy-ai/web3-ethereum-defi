"""Generic Vault adapter base classes.

- Create unified interface across different vault protocols and their investment flows

- Helps to create automated trading agents against any vault easily

- Handle both trading (asset management role) and investor management (deposits/redemptions)

- See :py:class:`VaultBase` to get started
"""

import dataclasses
import datetime

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from typing import Iterable, TypedDict, Tuple


from eth_typing import BlockIdentifier, HexAddress, BlockNumber

from web3 import Web3


from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.token import DEFAULT_TOKEN_CACHE, TokenAddress, TokenDetails, fetch_erc20_details
from eth_defi.vault.deposit_redeem import VaultDepositManager
from eth_defi.vault.lower_case_dict import LowercaseDict
from .fee import VaultFeeMode, get_vault_fee_mode, FeeData
from .flag import VaultFlag, get_vault_special_flags, get_notes

from .risk import VaultTechnicalRisk, get_vault_risk

BlockRange = Tuple[BlockNumber, BlockNumber]


@dataclass(slots=True)
class VaultSpec:
    """Unique id for a vault.

    - Each vault can be identified by smart contract address by one of the contracts,
      related to its deployment. Usually this contract is vault contract itself.

    - We need both chain and address to specify vault we mean.
    """

    #: Ethereum chain id
    chain_id: int

    #: Vault smart contract address or whatever is the primary address for unravelling a vault deployment for a vault protocol.
    #:
    #: Always forced to lowercase.
    vault_address: HexAddress | str

    def __post_init__(self):
        assert isinstance(self.chain_id, int)
        assert isinstance(self.vault_address, str), f"Expected str, got {self.vault_address}"
        assert self.vault_address.startswith("0x")
        # assert self.vault_address == self.vault_address.lower(), f"Vault address not lowercase: {self.vault_address}"
        # TODO: Get rid of old codepaths so we can make this dataclass frozen
        self.vault_address = self.vault_address.lower()

    def __hash__(self):
        return hash((self.chain_id, self.vault_address))

    def __eq__(self, other):
        if not isinstance(other, VaultSpec):
            return False
        return self.chain_id == other.chain_id and self.vault_address == other.vault_address

    def as_string_id(self) -> str:
        return f"{self.chain_id}-{self.vault_address}"

    @staticmethod
    def parse_string(spec: str, separator="auto") -> "VaultSpec":
        """Parse vault spec from a string.

        :param spec:
            String in the format of "chain_id,address" or "chain_id-address"

        :param separator:
            Either "auto" or "-" or ","

        :return:
            :py:class:`VaultSpec` instance
        """

        if separator == "auto":
            if "-" in spec:
                separator = "-"
            elif "," in spec:
                separator = ","
            else:
                raise ValueError(f"Cannot parse vault spec from string: {spec}. No separator found.")

        try:
            chain_id, address = spec.split(separator)
            chain_id = chain_id.strip()
            address = address.strip()
            return VaultSpec(chain_id=int(chain_id), vault_address=address)
        except Exception as e:
            raise ValueError(f"Cannot parse vault spec from string: {spec}") from e


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
        assert isinstance(self.spot_erc20, LowercaseDict), f"Got: {type(self.spot_erc20)}"

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


_nan = float("nan")


@dataclass(slots=True, frozen=False)
class VaultHistoricalRead:
    """Vault share price and fee structure at the point of time."""

    #: Vault for this result is
    vault: "VaultBase"

    #: block number of the reade
    block_number: int

    #: Naive datetime in UTC
    timestamp: datetime.datetime

    #: What was the share price in vault denomination token
    #:
    #: None if the read failed (call execution reverted)
    share_price: Decimal | None

    #: NAV / Assets under management in denomination token
    #:
    #: None if the read failed (call execution reverted)
    total_assets: Decimal | None

    #: Number of share tokens
    #:
    #: None if the read failed (call execution reverted)
    total_supply: Decimal | None

    #: What was the vault performance fee around the time
    performance_fee: float | None

    #: What was the vault management fee around the time
    management_fee: float | None

    #: Add RPC error messages and such related to this read
    #:
    #: Exported as empty string in Parquet if no errors, otherwise concat strings
    errors: list[str] | None

    #: What dynamic read frequency was used at the time of taking this sample
    #:
    #: Useful for diagnostics of scanning process
    vault_poll_frequency: str | None = None

    def __eq__(self, other: "VaultHistoricalRead | None") -> bool:
        """Check if the read statistics match.

        - Used in read_historical()
        """

        if other is None:
            return False

        assert self.vault.address == other.vault.address
        return self.share_price == other.share_price and self.total_assets == other.total_assets and self.total_supply == other.total_supply and self.performance_fee == other.performance_fee and self.management_fee == other.management_fee

    def is_almost_equal(
        self,
        other: "VaultHistoricalRead | None",
        epsilon: float = 0.001,
    ) -> bool:
        """Check if the read statistics match.

        - Throttle with epsilon relative difference to get rid of small increment rows

        :param epsilon:
            Write changes with 10 BPS granularity
        """
        if other is None:
            return False

        # Cannot do relative comparison as some values are zero or missing
        if (not self.share_price) or (not self.total_assets) or (not self.total_supply) or (not other.share_price) or (not other.total_assets) or (not other.total_supply):
            return self.share_price == other.share_price and self.total_assets == other.total_assets and self.total_supply == other.total_supply

        share_price_diff = (other.share_price - self.share_price) / self.share_price
        total_assets_diff = (other.total_assets - self.total_assets) / self.total_assets
        total_supply_diff = (other.total_supply - self.total_supply) / self.total_supply

        return abs(share_price_diff) <= epsilon and abs(total_assets_diff) <= epsilon and abs(total_supply_diff) <= epsilon

    def export(self) -> dict:
        """Convert historical read for a Parquet/DataFrame export."""
        error_msgs = ", ".join(self.errors) if self.errors else None
        data = {
            "chain": self.vault.chain_id,
            "address": self.vault.address.lower(),
            "block_number": self.block_number,
            "timestamp": self.timestamp,
            "share_price": float(self.share_price) if self.share_price is not None else _nan,
            "total_assets": float(self.total_assets) if self.total_assets is not None else _nan,
            "total_supply": float(self.total_supply) if self.total_supply is not None else _nan,
            "performance_fee": float(self.performance_fee) if self.performance_fee is not None else _nan,
            "management_fee": float(self.management_fee) if self.management_fee is not None else _nan,
            "errors": error_msgs if error_msgs else "",
            "vault_poll_frequency": self.vault_poll_frequency if self.vault_poll_frequency else "",
        }
        return data

    @classmethod
    def to_pyarrow_schema(cls) -> "pyarrow.Schema":
        """Get parquet schema for writing this data.

        - Write multiple chains, multiple vaults, to a single Parquet file
        """
        import pyarrow as pa

        schema = pa.schema(
            [
                ("chain", pa.uint32()),
                ("address", pa.string()),  # Lowercase
                ("block_number", pa.uint64()),
                ("timestamp", pa.timestamp("ms")),  # s accuracy does not seem to work on rewrite
                ("share_price", pa.float64()),
                ("total_assets", pa.float64()),
                ("total_supply", pa.float64()),
                ("performance_fee", pa.float32()),
                ("management_fee", pa.float32()),
                ("errors", pa.string()),
                ("vault_poll_frequency", pa.string()),
            ]
        )
        return schema


@dataclasses.dataclass(slots=True, frozen=True)
class VaultReadCondition:
    last_timestamp: datetime.datetime

    last_tvl: Decimal

    def __init__(self):
        pass

    def __call__(self):
        pass


class VaultHistoricalReader(ABC):
    """Support reading historical vault share prices.

    - Allows to construct historical returns
    """

    def __init__(self, vault: "VaultBase"):
        assert isinstance(vault, VaultBase)
        self.vault = vault

    @property
    def first_block(self) -> int | None:
        return self.vault.first_seen_at_block

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

    def __init__(self, token_cache: dict | None = None):
        """
        :param token_cache:
            Token cache for vault tokens.

            Allows to pass :py:class:`eth_defi.token.TokenDiskCache` to speed up operations.
        """
        self.first_seen_at_block = None
        if token_cache is None:
            token_cache = DEFAULT_TOKEN_CACHE

        self.token_cache = token_cache

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name} {self.symbol} at {self.address}>"

    def get_spec(self) -> VaultSpec:
        return VaultSpec(
            chain_id=self.chain_id,
            vault_address=self.address,
        )

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

    @cached_property
    def flow_manager(self) -> VaultFlowManager:
        """Flow manager associated with this vault"""
        return self.get_flow_manager()

    @cached_property
    def deposit_manager(self) -> VaultDepositManager:
        """Deposit manager assocaited with this vault"""
        return self.get_deposit_manager()

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
        """Get flow manager to read indiviaul settle events.

        - Only supported if :py:meth:`has_block_range_event_support` is True
        """

    @abstractmethod
    def get_deposit_manager(self) -> VaultDepositManager:
        """Get deposit manager to deposit/redeem from the vault."""

    @abstractmethod
    def get_historical_reader(self, stateful: bool) -> VaultHistoricalReader:
        """Get share price reader to fetch historical returns.

        :param stateful:
            If True, use a stateful reading strategy.

        :return:
            None if unsupported
        """

    def fetch_denomination_token_address(self) -> HexAddress:
        """Get the address for the denomination token.

        Triggers RCP call
        """
        raise NotImplementedError()

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

    def get_protocol_name(self) -> str:
        """Return the name of the vault protocol."""

        # TODO: Refactor modules
        from ..erc_4626.core import get_vault_protocol_name, ERC4626Feature

        features = getattr(self, "features", None)
        if features is None:
            features = {ERC4626Feature.broken}
        return get_vault_protocol_name(features)

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current management fee as a percent.

        Internal: Use :py:meth:`get_fee_data`.

        :return:
            0.1 = 10%
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement get_management_fee()")

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Get the current performance fee as a percent.

        Internal: Use :py:meth:`get_fee_data`.

        :return:
            0.1 = 10%
        """
        raise NotImplementedError(f"Class {self.__class__.__name__} does not implement get_performance_fee()")

    def has_custom_fees(self) -> bool:
        """Does this vault have custom fee structure reading methods.

                Causes risk in the vault comparison.

                -E.g.
                - Withdraw fee
                - Deposit fee
        s
                :return:
                    True if custom fee reading methods are implemented
        """
        return False

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Deposit fee is set to zero by default as vaults usually do not have deposit fees.

        Internal: Use :py:meth:`get_fee_data`.
        """
        return 0.0

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:
        """Withdraw fee is set to zero by default as vaults usually do not have withdraw fees.

        Internal: Use :py:meth:`get_fee_data`.
        """
        return 0.0

    def get_risk(self) -> VaultTechnicalRisk | None:
        """Get risk profile of this vault."""
        address = self.address
        protocol = self.get_protocol_name()
        return get_vault_risk(protocol, address)

    def get_fee_mode(self) -> VaultFeeMode | None:
        """Get how this vault accounts its fees."""
        address = self.address
        protocol = self.get_protocol_name()
        return get_vault_fee_mode(protocol, address)

    def get_fee_data(self) -> FeeData:
        """Get fee data structure for this vault.

        :raise ValueError:
            In the case of broken or unimplemented fee reading methods in the smart contract
        """

        fee_mode = self.get_fee_mode()

        fees = FeeData(
            fee_mode=fee_mode,
            management=self.get_management_fee(block_identifier="latest"),
            performance=self.get_performance_fee(block_identifier="latest"),
            deposit=self.get_deposit_fee(block_identifier="latest"),
            withdraw=self.get_withdraw_fee(block_identifier="latest"),
        )
        return fees

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """What is the estimated lock-up period for this vault.

        :return:
            None if not know
        """
        return None

    def get_flags(self) -> set[VaultFlag]:
        """Get various vault state flags from the smart contract.

        - Override to add status flags
        - Also add flags from our manual flag list in :py:mod:`eth_defi.vault.flag`

        :return:
            Flag set.

            Do not modify in place.
        """
        return get_vault_special_flags(self.address)

    def get_notes(self) -> str | None:
        """Get a human readable message if we know somethign special is going on with this vault."""
        return get_notes(self.address)

    def get_link(self, referral: str | None = None) -> str:
        """Get a link to the vault dashboard on its native site.

        - By default, give RouteScan link

        :param referral:
            Optional referral code to append to the URL.

        :return:
            URL string
        """
        return f"https://routescan.io/address/{self.vault_address}"
