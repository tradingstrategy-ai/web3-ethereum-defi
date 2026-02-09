"""Generic ECR-4626 vault reader implementation."""

import datetime
import logging
from decimal import Decimal
from functools import cached_property
from typing import Any, Iterable, Literal, TypeAlias

import eth_abi
from eth_typing import HexAddress
from requests.exceptions import HTTPError
from web3 import Web3
from web3.contract import Contract

from eth_defi.compat import WEB3_PY_V7
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.broken_provider import get_safe_cached_latest_block_number
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.flag import VaultFlag

if WEB3_PY_V7:
    from web3.exceptions import BadFunctionCallOutput, BlockNumberOutOfRange
else:
    from web3.exceptions import BadFunctionCallOutput
    from web3.exceptions import BlockNumberOutofRange as BlockNumberOutOfRange

from web3.types import BlockIdentifier

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.balances import fetch_erc20_balances_fallback, fetch_erc20_balances_multicall
from eth_defi.erc_4626.core import ERC4626Feature, get_deployed_erc_4626_contract
from eth_defi.event_reader.conversion import BadAddressError, convert_int256_bytes_to_int, convert_uint256_bytes_to_address
from eth_defi.event_reader.multicall_batcher import BatchCallState, EncodedCall, EncodedCallResult
from eth_defi.token import TokenDetails, fetch_erc20_details, is_stablecoin_like
from eth_defi.vault.base import DEPOSIT_CLOSED_CAP_REACHED, DEPOSIT_CLOSED_PAUSED, REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY, REDEMPTION_CLOSED_PAUSED, TradingUniverse, VaultBase, VaultFlowManager, VaultHistoricalRead, VaultHistoricalReader, VaultInfo, VaultPortfolio, VaultSpec

logger = logging.getLogger(__name__)


#: The exchange rate we use for all unknown denomination tokens
UNKNOWN_EXCHANGE_RATE = Decimal(0.99)

#: Known error messages that indicate that share() accessor function
#: is not accessible and contract is ERC-4626, not ERC-7540.
#: Because all EVM clones have different behavior on execution reverted,
#: this is a bit of a shitshow.
KNOWN_SHARE_TOKEN_ERROR_MESSAGES = frozenset(
    {
        "Execution reverted",
        "execution reverted",
        "out of gas",
        "Bad Request",
        "VM execution error",
        # HYperEVM spits out
        # fetch_share_token(): Not sure about exception {'code': -32603, 'message': 'Failed to call: InvalidTransaction(Revert(RevertError { output: None }))', 'data': None}
        "InvalidTransaction",
    }
)


class ERC4626VaultInfo(VaultInfo):
    """Capture information about ERC- vault deployment."""

    #: The ERC-20 token that nominates the vault assets
    address: HexAddress

    #: The address of the underlying token used for the vault for accounting, depositing, withdrawing.
    #:
    #: Some broken vaults do not expose this, and may be None.
    #: e.g. https://arbiscan.io/address/0x9d0fbc852deccb7dcdd6cb224fa7561efda74411#code
    #:
    #: E.g. USDC.
    #:
    asset: HexAddress | None


#: What is the reason how often we poll this
VaultPollFrequency: TypeAlias = Literal["peaked", "faded", "large_tvl", "small_tvl", "tiny_tvl", "first_read", "not_started", "early"]


class VaultReaderState(BatchCallState):
    """Adaptive reading frequency for vaults.

    - This class maintains the per-vault state of reading between different eth_call reads over time

    - Most vaults are uninteresting, but we do not know ahead of time which ones

    - We need 1h data for interesting vaults to make good trade decisions

    - We switch to 1h scanning if the TVL is above a threshold, otherwise we read it once per day

    .. note ::

        Due to filtering, only handles stablecoin vaults correctly at the moment.
        Lacks exchange rate support.
    """

    #: All attributes we store when we serialise the read state between runs
    SERIALISABLE_ATTRIBUTES = (
        "last_tvl",
        "last_share_price",
        "max_tvl",
        "first_seen_at_block",
        "first_block",
        "first_read_at",
        "last_call_at",
        "last_block",
        "peaked_at",
        "peaked_tvl",
        "faded_at",
        "entry_count",
        "chain_id",
        "vault_address",
        "denomination_token_address",
        "share_token_address",
        "one_raw_share",
        "reading_restarted_count",
        "vault_poll_frequency",
        "token_symbol",
        "unsupported_token",
        "invoke_count_passed",
        "invoke_count_first_read",
        "invoke_count_missing_freq",
        "invoke_count_throttled",
        "write_filtered",
        "write_done",
        "rpc_error_count",
        "last_rpc_error",
        "call_status",
    )

    def __init__(
        self,
        vault: "ERC4626Vault",
        tvl_threshold_1d_read=Decimal(10_000),
        tiny_tvl_threshold_rare_read=Decimal(1000),
        peaked_tvl_threshold=Decimal(200_000),
        min_tvl_threshold=Decimal(1_500),
        down_hard=0.98,
        traction_period: datetime.timedelta = datetime.timedelta(days=2 * 30),
    ):
        """
        :param vault:
            The vault we are reading historical data for
        :param tvl_threshold_1d_read:
            If the TVL is below this threshold, we will not read it more than once per day,
            otherwise hourly.
        :param down_hard:
            Stop reading the vault if the TVL is down by this percentage from the peak.
        :parm peaked_tvl_threshold:
            The TVL value we first need to reach to trigger down hard condition.
        :param min_tvl_threshold:
            If the vault never reaches this TVL, we stop reading it after the traction period.
        :param traction_period:
            How long we wait for the vault to get traction before we stop reading it.
        """
        super().__init__()
        self.vault = vault

        self.tvl_threshold_1d_read = tvl_threshold_1d_read
        self.tiny_tvl_threshold_rare_read = tiny_tvl_threshold_rare_read
        self.peaked_tvl_threshold = peaked_tvl_threshold
        self.down_hard = down_hard

        #: Passed from the vault discovery reader,
        #: pass the block number as args when we know this vault popped in to the existing
        self.first_seen_at_block = vault.first_seen_at_block

        #: TVL from the last read
        self.last_tvl: Decimal = None
        self.last_price: Decimal = None

        #: Timestamp of the block of the first successful read of this vault.
        self.first_read_at: datetime.datetime = None
        self.first_block: int = None

        #: Start with zero TVL
        self.max_tvl: Decimal = Decimal(0)

        #: Start with zero share price
        self.last_share_price: Decimal = Decimal(0)

        #: When this vault received its last eth_call update
        self.last_call_at: datetime.datetime | None = None

        #: When this vault received its last eth_call update
        self.last_block: int | None = None

        #: Disable reading if the vault has peaked (TVL too much down) and is no longer active
        self.peaked_at: datetime.datetime = None

        #: What was TVL when we disabled reading due to peaking
        self.peaked_tvl: float = None

        #: Disable reading if the vault has never gotten any traction
        self.faded_at: datetime.datetime = None

        #: How much time after deployment we allow to get traction
        self.traction_period = traction_period

        #: Minimum TVL traction threshold to start reading the vault
        self.min_tvl_threshold = min_tvl_threshold

        #: Vaults we do no really care about
        self.tiny_tvl_threshold_rare_read = tiny_tvl_threshold_rare_read

        #: How many on_called() invocations have we had
        self.entry_count = 0

        #: How many should_invoke() invocations have we had
        self.invoke_count_passed = self.invoke_count_first_read = self.invoke_count_missing_freq = self.invoke_count_throttled = 0

        # Track Parquet writes
        self.write_filtered = self.write_done = 0

        #: Track RPCc errors
        self.rpc_error_count = 0

        #: Cache denomination token address when preparing readers
        self.denomination_token_address = None

        #: Cache share token address when preparing readers
        self.share_token_address = None

        #: One share in its raw units
        self.one_raw_share = None

        self.reading_restarted_count = 0

        #: Cache denomination token address when preparing readers
        self.one = None

        #: Copy for state debuggin
        self.chain_id = vault.spec.chain_id
        self.vault_address = vault.vault_address

        #: Cache for how often we are polling this vault,
        #: the mode name
        self.vault_poll_frequency = None

        #: Cache for debuggin
        self.token_symbol = None

        #: Cache for debuggin
        self.unsupported_token = None
        self.last_rpc_error: str | None = None

        #: Map of function names to their call status for warmup system
        #: Key is the function name from extra_data["function"]
        #: Value is tuple (check_block: int, reverts: bool)
        #: Example: {"maxDeposit": (12345678, True)} means maxDeposit reverts, detected at block 12345678
        self.call_status: dict[str, tuple[int, bool]] = {}

    def __repr__(self):
        return f"<{self.__class__.__name__} vault={self.vault} last_tvl={self.last_tvl} last_share_price={self.last_share_price} max_tvl={self.max_tvl} last_call_at={self.last_call_at} peaked_at={self.peaked_at} faded_at={self.faded_at} denomination_token={self.denomination_token_address}>"

    def save(self) -> dict:
        return {k: getattr(self, k) for k in self.SERIALISABLE_ATTRIBUTES}

    def load(self, data: dict):
        """Load the state from a dictionary."""
        for k, v in data.items():
            assert k in VaultReaderState.SERIALISABLE_ATTRIBUTES, f"Unknown key {k} in VaultReaderState.load()"
            setattr(self, k, v)

    def should_skip_call(self, function_name: str) -> bool:
        """Check if a specific function call should be skipped for this vault.

        Part of the warmup system to detect and skip broken contract calls.
        See README-reader-states.md for documentation.

        :param function_name:
            The function name as stored in extra_data["function"]

        :return:
            True if the call was marked as reverting
        """
        status = self.call_status.get(function_name)
        if status is None:
            return False
        _check_block, reverts = status
        return reverts

    def get_call_status(self, function_name: str) -> tuple[int, bool] | None:
        """Get the status of a function call.

        :param function_name:
            The function name to check

        :return:
            Tuple of (check_block, reverts) or None if not checked yet
        """
        return self.call_status.get(function_name)

    def set_call_status(self, function_name: str, check_block: int, reverts: bool) -> None:
        """Record the status of a function call.

        :param function_name:
            The function name to record

        :param check_block:
            The block number when we checked

        :param reverts:
            True if the call reverted
        """
        self.call_status[function_name] = (check_block, reverts)

    def get_broken_calls(self) -> dict[str, int]:
        """Get all calls marked as broken.

        :return:
            Dict of function_name -> check_block for broken calls
        """
        return {fn: block for fn, (block, reverts) in self.call_status.items() if reverts}

    @cached_property
    def exchange_rate(self) -> Decimal:
        """Get the exchange rate for TVL estimation"""
        # TODO: Approx hardcoded rules for now for TVL conversion.
        # Latest add exchange rate orcale.
        token = self.vault.denomination_token.symbol or ""

        # Try to cover common case ~approx
        if "BTC" in token:
            return Decimal(100_000)
        elif "ETH" in token:
            return Decimal(3000)
        elif "AVA" in token:
            return Decimal(50)
        elif "POL" in token or "MATIC" in token:
            return Decimal(1)
        elif "BERA" in token:
            return Decimal(2)
        elif "BNB" in token:
            return Decimal(500)
        elif "ARB" in token:
            return Decimal(0.4)
        elif "S" == token or "WS" == token:
            return Decimal(0.2)
        elif "OP" == token or "WOP" == token:
            return Decimal(0.5)
        elif "MNT" in token:
            return Decimal(0.5)
        elif is_stablecoin_like(token):
            # Assume stablecoin / some non-supported token
            return Decimal(1)
        else:
            # Marker value
            return UNKNOWN_EXCHANGE_RATE

    def should_invoke(
        self,
        call: "EncodedCall",
        block_identifier: BlockIdentifier,
        timestamp: datetime.datetime,
    ) -> bool:
        if self.first_seen_at_block:
            if block_identifier < self.first_seen_at_block:
                # We do not read historical data before the first seen block
                self.vault_poll_frequency = "not_started"
                return False

        if self.last_call_at is None:
            # First read, we always read it
            self.vault_poll_frequency = "first_read"
            self.invoke_count_first_read += 1
            return True

        vault_poll_frequency, freq = self.get_frequency()
        self.vault_poll_frequency = vault_poll_frequency

        if freq is None:
            # Further reads disabled
            self.invoke_count_missing_freq += 1
            return False

        refresh_needed = (timestamp - self.last_call_at) >= freq
        if refresh_needed:
            self.invoke_count_passed += 1
            return True

        self.invoke_count_throttled += 1
        return False

    def get_frequency(self) -> tuple[VaultPollFrequency, datetime.timedelta | None]:
        """How fast we are reading this vault or should the further reading be skipped."""

        if self.peaked_at:
            # For peaked vaults, only poll each 14 days
            return "peaked", datetime.timedelta(days=7)
        elif self.faded_at:
            # For faded vaults, only poll each 14 days
            return "faded", datetime.timedelta(days=7)

        if self.last_tvl < self.tiny_tvl_threshold_rare_read:
            if self.last_call_at - self.first_read_at < datetime.timedelta(days=14):
                # For start of each vault, sample daily for two weeks
                # despite tiny TVL to avoid early breakage
                return "early", datetime.timedelta(days=1)

            # Trash vaults
            return "tiny_tvl", datetime.timedelta(days=7)
        elif self.last_tvl < self.tvl_threshold_1d_read:
            # Small vaults daily
            return "small_tvl", datetime.timedelta(days=1)
        else:
            # Do large vaults hourly
            return "large_tvl", datetime.timedelta(hours=1)

    def on_called(
        self,
        result: "EncodedCallResult",
        total_assets: Decimal | None = None,
        share_price: Decimal | None = None,
    ):
        """
        :param result:
            Result of convertToAssets() call
        """
        assert result.timestamp, f"EncodedCallResult {result} has no timestamp, cannot update state"

        if share_price is None:
            assert result.revert_exception, f"EncodedCallResult {result} has no total assets, but no revert exception either"
            # Cannot read total assets from this vault for some reason as the call is failing.
            # We will mark these broken vaults with special -1 TVL value in the vault reader state.
            share_price = Decimal(-1)

        # Just in the case something breaks
        if total_assets is None:
            total_assets = Decimal(0)

        exchange_rate = self.exchange_rate
        if self.exchange_rate == UNKNOWN_EXCHANGE_RATE:
            self.unsupported_token = True

        total_assets = total_assets * exchange_rate

        timestamp = result.timestamp
        self.last_call_at = timestamp

        if self.first_read_at is None:
            self.first_read_at = timestamp

        if self.first_block is None:
            self.first_block = result.block_identifier

        self.last_tvl = total_assets
        self.last_call_at = timestamp
        self.last_block = result.block_identifier
        existing_max_tvl = self.max_tvl or 0
        self.max_tvl = max(existing_max_tvl, total_assets) if total_assets != -1 else total_assets
        self.last_share_price = share_price

        # The vault TVL has fell too much, disable
        if self.max_tvl > self.peaked_tvl_threshold:
            #  The vault TVL drops so low we should actively stopp tracking it
            threshold = self.max_tvl * Decimal(1 - self.down_hard)
            if self.last_tvl < threshold:
                if not self.peaked_at:
                    logger.debug(f"{self.last_call_at}: Vault {self.vault} peaked at {self.max_tvl}, now TVL is {self.last_tvl}, no longer reading it")
                    self.peaked_at = timestamp
                    self.peaked_tvl = self.last_tvl
            else:
                # Reset peaked condition,
                # see first_read comments in read historical
                if self.peaked_at:
                    logger.debug(f"{self.last_call_at}: Vault {self.vault} un-peaked. Max TVL is {self.max_tvl}, TVL now is {self.last_tvl}, threshold is {threshold}, starting to read again, peaked at was {self.peaked_at} at TVL {self.peaked_tvl}")
                    self.peaked_at = None
                    self.peaked_tvl = None
                    self.reading_restarted_count += 1

        # The vault never got any traction, disable
        if self.last_call_at - self.first_read_at > self.traction_period:
            if self.max_tvl < self.min_tvl_threshold:
                if not self.faded_at:
                    logger.debug(f"{self.last_call_at}:  Vault {self.vault} disabled at {self.max_tvl}, never reached min TVL {self.min_tvl_threshold}, no longer reading it, first read at {self.first_read_at}, last call at {self.last_call_at}, traction period was {self.traction_period}")
                    self.faded_at = timestamp

        # Cache for debugging
        # self.token_symbol = self.vault.denomination_token.symbol if self.vault.denomination_token else "-"

        # Diagnostics counter
        self.entry_count += 1

    def pformat(self) -> str:
        """Pretty print the current state."""
        lines = []
        for attr in self.SERIALISABLE_ATTRIBUTES:
            value = getattr(self, attr)
            lines.append(f"{attr}: {value}")
        return "\n".join(lines)


class ERC4626HistoricalReader(VaultHistoricalReader):
    """A reader that reads the historcal state of one specific vaults.

    - Generate a list of multicall instances that is needed to capture the vault state in a specific block height
    - All calls share the same state object which we use to track disabling reads for inactive vaults
    - Share price (returns), supply, NAV
    - For performance fees etc. there are no standards so you need to subclass this for
      each protocol
    - All calls for this reader share the same
    """

    def __init__(self, vault: "ERC4626Vault", stateful: bool):
        super().__init__(vault)
        if stateful:
            self.reader_state = VaultReaderState(vault)
        else:
            # Stateful reading cannot be used in unordered multiprocess reads
            self.reader_state = None

    def get_warmup_calls(self) -> Iterable[tuple[str, callable, Any]]:
        """Yield (function_name, callable, contract_call) tuples for warmup testing.

        Each callable should execute a single contract call. If it raises,
        the function is marked as broken.

        The optional contract_call is used for gas estimation to detect expensive
        calls before executing them. If provided, calls using excessive gas
        (>1M gas) will be marked as broken without execution.

        Override in subclasses to add protocol-specific calls.

        :return:
            Iterable of (function_name, test_callable, contract_call) tuples.
            contract_call may be None if gas estimation is not needed.
        """
        vault_contract = self.vault.vault_contract

        total_assets_call = vault_contract.functions.totalAssets()
        yield ("total_assets", lambda: total_assets_call.call(), total_assets_call)

        total_supply_call = vault_contract.functions.totalSupply()
        yield ("total_supply", lambda: total_supply_call.call(), total_supply_call)

        convert_call = vault_contract.functions.convertToAssets(self.one_raw_share)
        yield ("convertToAssets", lambda: convert_call.call(), convert_call)

        max_deposit_call = vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR)
        yield ("maxDeposit", lambda: max_deposit_call.call(), max_deposit_call)

    def should_skip_call(self, function_name: str) -> bool:
        """Check if a specific function call should be skipped.

        Uses the reader state's call_status map if available.

        :param function_name:
            The function name to check

        :return:
            True if the call should be skipped
        """
        if self.reader_state is None:
            return False
        return self.reader_state.should_skip_call(function_name)

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Get the onchain calls that are needed to read the share price."""
        try:
            yield from self.construct_core_erc_4626_multicall()
        except Exception as e:
            raise RuntimeError(f"Could not construct multicalls for vault {self.vault}, share token is {self.vault.share_token}, share is {self.one_raw_share}") from e

    @cached_property
    def one_raw_share(self) -> int:
        # 99 marks a broken read on fetch_erc20_details()
        assert self.vault.share_token.decimals != 99, f"Vault {self.vault}, {self.vault.name} has busted share token {self.vault.share_token} with broken decimals. Clear token cache?"
        one_share = self.vault.share_token.convert_to_raw(Decimal(1))
        return one_share

    def construct_core_erc_4626_multicall(self) -> Iterable[EncodedCall]:
        """Polling endpoints defined in ERC-4626 spec.

        - Does not include fee calls which do not have standard
        """

        # TODO: use asset / supply as it is more reliable
        if self.vault.denomination_token is not None:
            # amount = self.vault.denomination_token.convert_to_raw(Decimal(1))
            # share_price_call = EncodedCall.from_contract_call(
            #     self.vault.vault_contract.functions.convertToShares(amount),
            #     extra_data = {
            #         "function": "share_price",
            #         "vault": self.vault.address,
            #         "amount": amount,
            #         "denomination_token": self.vault.denomination_token.symbol,
            #         "decimals": self.vault.denomination_token.decimals,
            #     },
            #     first_block_number=self.first_block,
            # )
            # yield share_price_call
            pass

        total_assets = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.totalAssets(),
            extra_data={
                "function": "total_assets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield total_assets

        total_supply = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.totalSupply(),
            extra_data={
                "function": "total_supply",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield total_supply

        # A vault can have a non-standard formulas to calculate share price,
        # and these may include dynamic variables.
        # See
        # https://medium.com/gains-network/introducing-gtoken-vaults-ea98f10a49d5

        convert_to_assets = EncodedCall.from_contract_call(
            self.vault.vault_contract.functions.convertToAssets(self.one_raw_share),
            extra_data={
                "function": "convertToAssets",
                "vault": self.vault.address,
            },
            first_block_number=self.first_block,
        )
        yield convert_to_assets

        # Only add maxDeposit if not flagged to skip
        # Some vaults have extremely expensive maxDeposit implementations
        if not self.should_skip_call("maxDeposit"):
            max_deposit = EncodedCall.from_contract_call(
                self.vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR),
                extra_data={
                    "function": "maxDeposit",
                    "vault": self.vault.address,
                },
                first_block_number=self.first_block,
            )
            yield max_deposit

    def process_core_erc_4626_result(
        self,
        call_by_name: dict[str, EncodedCallResult],
    ) -> tuple:
        """Decode common ERC-4626 calls."""

        errors = []

        share_token = self.vault.share_token
        total_supply_result = call_by_name.get("total_supply")
        if total_supply_result is None:
            logger.warning("total_supply call missing for %s, we got %s", self.vault, list(call_by_name.keys()))
            errors.append("total_supply call missing")
            total_supply = None
        elif total_supply_result.success and share_token is not None:
            raw_total_supply = convert_int256_bytes_to_int(total_supply_result.result)
            total_supply = self.vault.share_token.convert_to_decimals(raw_total_supply)
        else:
            errors.append("total_supply call failed")
            total_supply = None

        total_assets_call_result = call_by_name.get("total_assets")
        if total_assets_call_result is None:
            logger.warning("total_assets call missing for %s, we got %s", self.vault, list(call_by_name.keys()))
            errors.append("total_assets call missing")
            total_assets = None
        elif self.vault.denomination_token is not None and total_assets_call_result.success:
            raw_total_assets = convert_int256_bytes_to_int(total_assets_call_result.result)
            total_assets = self.vault.denomination_token.convert_to_decimals(raw_total_assets)

        else:
            errors.append("total_assets call failed")
            total_assets = None

        if total_assets == 0:
            errors.append(f"total_assets zero: {total_assets_call_result}")

        if total_supply == 0:
            errors.append(f"total_supply zero: {total_supply_result}")

        convert_to_assets_call_result = call_by_name.get("convertToAssets")
        if self.vault.denomination_token is not None and convert_to_assets_call_result is not None and convert_to_assets_call_result.success:
            # Take one unit of assets
            raw_total_assets = convert_int256_bytes_to_int(convert_to_assets_call_result.result)
            share_price = self.vault.denomination_token.convert_to_decimals(raw_total_assets)

            # Handle dealing with the adaptive frequency
            state = total_assets_call_result.state
            if state:
                state.on_called(
                    convert_to_assets_call_result,
                    total_assets=total_assets,
                    share_price=share_price,
                )
        else:
            share_price = None

        max_deposit_result = call_by_name.get("maxDeposit")
        if max_deposit_result and max_deposit_result.success and self.vault.denomination_token is not None:
            raw_max_deposit = convert_int256_bytes_to_int(max_deposit_result.result)
            max_deposit = self.vault.denomination_token.convert_to_decimals(raw_max_deposit)
        else:
            max_deposit = None

        return share_price, total_supply, total_assets, (errors or None), max_deposit

    def dictify_multicall_results(
        self,
        block_number: int,
        call_results: list[EncodedCallResult],
        allow_failure=True,
    ) -> dict[str, EncodedCallResult]:
        """Convert batch of multicalls made for this vault to more digestible dict.

        - Assert that all multicalls succeed

        :return:
            Dictionary where each multicall is keyed by its ``EncodedCall.extra_data["function"]``
        """
        call_by_name = {r.call.extra_data["function"]: r for r in call_results}

        # Check that all multicalls succeed for this vault
        if not allow_failure:
            for result in call_by_name.values():
                assert result.success, f"Multicall failed at block {block_number:,}: {result.call} for vault {self.vault}\nDebug info for Tenderly: {result.call.get_debug_info()}"

        return call_by_name

    def process_result(
        self,
        block_number: int,
        timestamp: datetime.datetime,
        call_results: list[EncodedCallResult],
    ) -> VaultHistoricalRead:
        call_by_name = self.dictify_multicall_results(block_number, call_results)

        # Sanity check that all calls are from the same block
        if not all(c.block_identifier == block_number for c in call_by_name.values()):
            msg = "Mismatch of block numbers in multicall results:\n"
            for c in call_by_name.values():
                msg += f"{c.call.func_name} has block number {c.block_identifier:,}, expected {block_number:,}\n"
            raise AssertionError(msg)

        # Decode common variables
        share_price, total_supply, total_assets, errors, max_deposit = self.process_core_erc_4626_result(call_by_name)

        return VaultHistoricalRead(
            vault=self.vault,
            block_number=block_number,
            timestamp=timestamp,
            share_price=share_price,
            total_assets=total_assets,
            total_supply=total_supply,
            performance_fee=None,
            management_fee=None,
            errors=errors or None,
            max_deposit=max_deposit,
        )


class ERC4626Vault(VaultBase):
    """ERC-4626 vault adapter

    Handle vault operations:

    - Metadata
    - Deposit and redeem from the vault
    - Vault historical price reader
    - Also partial support for ERC-7575 extensions

    More info:

    - `Find the interface here <https://github.com/OpenZeppelin/openzeppelin-contracts/blob/master/contracts/token/ERC20/extensions/ERC4626.sol>`__
    - `EIP-7575 <https://eips.ethereum.org/EIPS/eip-7575>`__
    """

    def __init__(
        self,
        web3: Web3,
        spec: VaultSpec,
        token_cache: dict | None = None,
        features: set[ERC4626Feature] | None = None,
        default_block_identifier: BlockIdentifier | None = None,
    ):
        """
        :param web3:
            Connection we bind this instance to

        :param spec:
            Chain, address tuple

        :param token_cache:
            Cache used with :py:meth:`fetch_erc20_details` to avoid multiple calls to the same token.

            Reduces the number of RPC calls when scanning multiple vaults.

        :param features:
            Pass vault feature flags along, externally detected.

        :param default_block_identifier:
            Override block identifier for on-chain metadata reads.

            When ``None``, use :py:func:`get_safe_cached_latest_block_number` (the default, safe for broken RPCs).
            Set to ``"latest"`` for freshly deployed vaults whose contracts do not exist at the safe-cached block.
        """

        if type(features) == set:
            assert len(features) >= 1, "If given, the vault features set should contain at least one feature"

        super().__init__(token_cache=token_cache)
        self.web3 = web3
        self.spec = spec
        self.features = features
        self.default_block_identifier = default_block_identifier

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.spec}>"

    def _get_block_identifier(self) -> BlockIdentifier:
        """Resolve which block identifier to use for metadata reads.

        - If :py:attr:`default_block_identifier` was set at construction time, use that
        - Otherwise fall back to :py:func:`get_safe_cached_latest_block_number`
          to work around broken L2 RPC providers
        """
        if self.default_block_identifier is not None:
            return self.default_block_identifier
        return get_safe_cached_latest_block_number(
            self.web3,
            chain_id=self.chain_id,
        )

    def is_valid(self) -> bool:
        """Check if this vault is valid.

        - Call a known smart contract function to verify the function exists
        """
        denomination_token = self.fetch_denomination_token_address()
        return denomination_token is not None

    @property
    def chain_id(self) -> int:
        return self.spec.chain_id

    @property
    def address(self) -> HexAddress:
        """Get the vault smart contract address."""
        return self.vault_address

    @cached_property
    def vault_address(self) -> HexAddress:
        return Web3.to_checksum_address(self.spec.vault_address)

    @property
    def vault_address_checksumless(self) -> HexAddress:
        return self.spec.vault_address

    @property
    def name(self) -> str:
        return self.share_token.name

    @property
    def symbol(self) -> str:
        return self.share_token.symbol

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
        )

    @property
    def underlying_token(self) -> TokenDetails:
        """Alias for :py:meth:`denomination_token`"""
        return self.denomination_token

    @cached_property
    def erc_7540(self) -> bool:
        """Is this ERC-7540 vault with asynchronous deposits.

        - For example ``previewDeposit()`` function and other functions will revert
        """

        block_identifier = self._get_block_identifier()

        try:
            # isOperator() function is only part of 7545 ABI and will revert is missing
            double_address = eth_abi.encode(["address", "address"], [ZERO_ADDRESS_STR, ZERO_ADDRESS_STR])
            erc_7540_call = EncodedCall.from_keccak_signature(
                address=self.address,
                signature=Web3.keccak(text="isOperator(address,address)")[0:4],
                function="isOperator",
                data=double_address,
                extra_data=None,
            )
            erc_7540_call.call(self.web3, block_identifier=block_identifier)
            return True
        except (ValueError, BadFunctionCallOutput, ProbablyNodeHasNoBlock):
            return False

    def fetch_denomination_token_address(self) -> HexAddress | None:
        # Try to check if we are ERC-7575 first
        # https://eips.ethereum.org/EIPS/eip-7575

        block_identifier = self._get_block_identifier()

        call = EncodedCall.from_contract_call(
            self.vault_contract.functions.asset(),
        )
        try:
            result = call.call(
                self.web3,
                block_identifier=block_identifier,
                silent_error=True,
                ignore_error=True,
                attempts=2,
            )
            return convert_uint256_bytes_to_address(result)
        except (ValueError, BadFunctionCallOutput, BadAddressError, ProbablyNodeHasNoBlock):
            pass
        return None

    def fetch_denomination_token(self) -> TokenDetails | None:
        token_address = self.fetch_denomination_token_address()
        # eth_defi.token.TokenDetailError: Token 0x4C36388bE6F416A29C8d8Eee81C771cE6bE14B18 missing symbol
        if token_address:
            return fetch_erc20_details(
                self.web3,
                token_address,
                chain_id=self.spec.chain_id,
                raise_on_error=False,
                cause_diagnostics_message=f"Vault {self.__class__.__name__} {self.address} denominating token lookup",
                cache=self.token_cache,
            )
        else:
            return None

    def fetch_share_token_address(self, block_identifier: BlockIdentifier = "latest") -> HexAddress:
        """Get share token of this vault.

        - Vault itself (ERC-4626)
        - share() accessor (ERc-7575)
        """
        erc_7575 = False

        if block_identifier == "latest":
            block_identifier = self._get_block_identifier()

        try:
            # ERC-7575
            erc_7575_call = EncodedCall.from_keccak_signature(
                address=self.vault_address,
                signature=Web3.keccak(text="share()")[0:4],
                function="share",
                data=b"",
                extra_data=None,
            )

            # Would hope to use ignore_errors here
            # but we cannot make distinction between broken smart contract and broken RPC gateway
            # because of how shitty EVM is
            # Function selector: 0xa8d5fd65
            result = erc_7575_call.call(
                self.web3,
                block_identifier=block_identifier,
                ignore_error=True,
                silent_error=True,
                attempts=2,  # Do not do extensive attempts here
            )
            if len(result) == 32:
                erc_7575 = True
                share_token_address = convert_uint256_bytes_to_address(result)
            else:
                # Could not read ERC4626Vault 0x0271353E642708517A07985eA6276944A708dDd1 (set()):
                share_token_address = self.vault_address

        except (
            ValueError,
            BadFunctionCallOutput,
            ExtraValueError,
            ProbablyNodeHasNoBlock,
        ) as e:
            # ProbablyNodeHasNoBlock is a known exception type for node issues - always fall back gracefully
            if isinstance(e, ProbablyNodeHasNoBlock):
                logger.warning(f"fetch_share_token(): Node lacks block data for vault {self.vault_address}: {e}")
            else:
                parsed_error = str(e)
                # Try to figure out broken ERC-4626 contract and have all conditions
                # to gracefully handle failed erc_7575_call()
                # Mantle
                # Could not read ERC4626Vault 0x32F6D2c91FF3C3d2f1fC2cCAb4Afcf2b6ecF24Ef (set()): {'message': 'out of gas', 'code': -32000}
                # Hyperliquid:
                # ValueError: Call failed: 400 Client Error: Bad Request for url: https://lb.drpc.org/ogrpc?network=hyperliquid&dkey=AiWA4TvYpkijvapnvFlyx_WBfO5CICoR76hArr3WfgV4
                # Hyperliquid:
                #  {'code': -32603, 'message': 'Failed to call: InvalidTransaction(Revert(RevertError { output: None }))'}
                if not any(msg in parsed_error for msg in KNOWN_SHARE_TOKEN_ERROR_MESSAGES):
                    logger.error(f"fetch_share_token(): Not sure about exception %s", e)
                    raise

            if isinstance(e, HTTPError):
                # eRPC brokeness trap.
                # requests.exceptions.HTTPError: 502 Server Error: Bad Gateway for url: https://edge.goldsky.com/standard/base?secret=x
                if e.response and e.response.status_code in (502,):
                    logger.warning(f"fetch_share_token(): Ignoring HTTPError from RPC for vault {self.vault_address}: {e}")
                    pass

            share_token_address = self.vault_address
        except Exception as e:
            raise RuntimeError(f"Failed to poke vault: {self.vault_address}") from e

        return share_token_address

    def fetch_share_token(self) -> TokenDetails:
        # eth_defi.token.TokenDetailError: Token 0xDb7869Ffb1E46DD86746eA7403fa2Bb5Caf7FA46 missing symbol
        share_token_address = self.fetch_share_token_address()
        logger.info("Attempting to fetch share token details: %s for vault %s", share_token_address, self.address)
        return fetch_erc20_details(
            self.web3,
            self.fetch_share_token_address(),
            raise_on_error=False,
            chain_id=self.spec.chain_id,
            cache=self.token_cache,
            cause_diagnostics_message=f"Share token for vault {self.address}",
        )

    def fetch_vault_info(self) -> ERC4626VaultInfo:
        """Get all information we can extract from the vault smart contracts."""
        vault = self.vault_contract

        # roles_tuple = vault.functions.getRolesStorage().call()
        # whitelistManager, feeReceiver, safe, feeRegistry, valuationManager = roles_tuple

        return {
            "address": vault.address,
            # "asset": asset,
        }

    def fetch_total_assets(self, block_identifier: BlockIdentifier) -> Decimal | None:
        """What is the total NAV of the vault.

        Example:

        .. code-block:: python

            assert vault.denomination_token.symbol == "USDC"
            assert vault.share_token.symbol == "ipUSDCfusion"
            assert vault.fetch_total_assets(block_identifier=test_block_number) == Decimal("1437072.77357")
            assert vault.fetch_total_supply(block_identifier=test_block_number) == Decimal("1390401.22652875")

        :param block_identifier:
            Block number to read.

            Use `web3.eth.block_number` for the last block.

        :return:
            The vault value in underlyinh token
        """
        raw_amount = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        if self.underlying_token is not None:
            return self.underlying_token.convert_to_decimals(raw_amount)
        return None

    def fetch_total_supply(self, block_identifier: BlockIdentifier) -> Decimal:
        """What is the current outstanding shares.

        Example:

        .. code-block: python

            assert vault.denomination_token.symbol == "USDC"
            assert vault.share_token.symbol == "ipUSDCfusion"
            assert vault.fetch_total_assets(block_identifier=test_block_number) == Decimal('1437072.77357')
            assert vault.fetch_total_supply(block_identifier=test_block_number) == Decimal('1390401.22652875')

        :param block_identifier:
            Block number to read.

            Use `web3.eth.block_number` for the last block.

        :return:
            The vault value in underlyinh token
        """
        assert isinstance(block_identifier, (int, str)), f"Block identifier should be int or str, got {type(block_identifier)}"
        try:
            raw_amount = self.share_token.contract.functions.totalSupply().call(block_identifier=block_identifier)
        except BlockNumberOutOfRange as e:
            raise RuntimeError(f"Cannot fetch total supply for block number: {block_identifier} for vault {self}") from e
        return self.share_token.convert_to_decimals(raw_amount)

    def fetch_share_price(self, block_identifier: BlockIdentifier) -> Decimal:
        """Get the current share price.

        :return:
            The share price in underlying token.

            If supply is zero return zero.
        """

        #     function _convertToAssets(
        #         uint256 shares,
        #         uint40 requestId,
        #         Math.Rounding rounding
        #     ) internal view returns (uint256) {
        #         ERC7540Storage storage $ = _getERC7540Storage();
        #
        #         // cache
        #         uint40 settleId = $.epochs[requestId].settleId;
        #
        #         uint256 _totalAssets = $.settles[settleId].totalAssets + 1;
        #         uint256 _totalSupply = $.settles[settleId].totalSupply + 10 ** _decimalsOffset();
        #
        #         return shares.mulDiv(_totalAssets, _totalSupply, rounding);
        #     }
        total_assets = self.fetch_total_assets(block_identifier)
        total_supply = self.fetch_total_supply(block_identifier)
        if total_supply == 0:
            return Decimal(0)
        return total_assets / self.fetch_total_supply(block_identifier)

    def fetch_portfolio(
        self,
        universe: TradingUniverse,
        block_identifier: BlockIdentifier | None = None,
        allow_fallback: bool = True,
    ) -> VaultPortfolio:
        if allow_fallback:
            erc20_balances = fetch_erc20_balances_fallback(
                self.web3,
                self.safe_address,
                universe.spot_token_addresses,
                block_identifier=block_identifier,
                decimalise=True,
            )
        else:
            # Test path - f@#$@#$ Anvil issues
            erc20_balances = fetch_erc20_balances_multicall(
                self.web3,
                self.safe_address,
                universe.spot_token_addresses,
                block_identifier=block_identifier,
                decimalise=True,
            )
        return VaultPortfolio(
            spot_erc20=erc20_balances,
        )

    def fetch_info(self) -> ERC4626VaultInfo:
        """Use :py:meth:`info` property for cached access.

        :return:
            See :py:class:`LagoonVaultInfo`
        """
        vault_info = self.fetch_vault_info()
        return vault_info

    def fetch_nav(self, block_identifier=None) -> Decimal:
        """Fetch the most recent onchain NAV value.

        - In the case of Lagoon, this is the last value written in the contract with
          `updateNewTotalAssets()` and ` settleDeposit()`

        - TODO: `updateNewTotalAssets()` there is no way to read pending asset update on chain

        :return:
            Vault NAV, denominated in :py:meth:`denomination_token`
        """
        token = self.denomination_token
        raw_amount = self.vault_contract.functions.totalAssets().call(block_identifier=block_identifier)
        return token.convert_to_decimals(raw_amount)

    def get_flow_manager(self) -> VaultFlowManager:
        return NotImplementedError()

    def get_deposit_manager(self) -> "eth_defi.erc_4626.deposit_redeem.ERC4626DepositManager":
        from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager

        return ERC4626DepositManager(self)

    def has_block_range_event_support(self):
        raise NotImplementedError()

    def has_deposit_distribution_to_all_positions(self):
        raise NotImplementedError()

    def get_historical_reader(self, stateful) -> VaultHistoricalReader:
        return ERC4626HistoricalReader(self, stateful=stateful)

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """ERC-4626 vaults do not have a lock up by fault.

        .. note ::

            Because of so many protocol specific lockups, this must be explicitly set to zero.
        """
        return None

    def can_check_deposit(self) -> bool:
        """Check if maxDeposit(address(0)) can be used to check global deposit availability.

        Most ERC-4626 vaults implement maxDeposit in a way that returns meaningful
        values when called with address(0):

        - Returns 0 when deposits are globally closed/capped
        - Returns a positive value indicating maximum deposit allowed

        Override to return False in subclasses where maxDeposit(address(0)) doesn't
        provide meaningful global availability information.

        :return:
            True if maxDeposit(address(0)) returns meaningful values for global
            deposit availability checking.
        """
        return True

    def can_check_redeem(self) -> bool:
        """Check if maxRedeem(address(0)) can be used to check global redemption availability.

        Most protocols return 0 for maxRedeem(address(0)) because that address has no
        balance/shares, not because redemptions are closed:

        - Gearbox: maxRedeem returns min(balanceOf(owner), convertToShares(availableLiquidity))
        - Most vaults: Return 0 because address(0) has no shares

        Some protocols do use maxRedeem(address(0)) meaningfully:

        - Morpho, IPOR, Plutus: Return 0 when redemptions are globally blocked

        Override to return True in subclasses that support address(0) redemption checks.

        :return:
            True if maxRedeem(address(0)) returns meaningful values for global
            redemption availability checking.
        """
        return False

    def fetch_deposit_closed_reason(self) -> str | None:
        """Check if deposits are closed using maxDeposit(address(0)).

        Uses the ERC-4626 standard maxDeposit function to determine if deposits
        are available. Returns a human-readable reason with the max deposit amount
        if deposits are restricted.

        :return:
            Human-readable string if deposits are closed/restricted,
            or None if deposits are open (maxDeposit > 0).
        """
        if not self.can_check_deposit():
            return None

        try:
            max_deposit_raw = self.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
            if max_deposit_raw == 0:
                return f"{DEPOSIT_CLOSED_CAP_REACHED} (maxDeposit=0)"

            return None  # Deposits are open
        except Exception:
            return None  # Cannot determine, assume open

    def fetch_redemption_closed_reason(self) -> str | None:
        """Check if redemptions are closed using maxRedeem(address(0)).

        Only works for protocols that implement maxRedeem in a way that returns
        meaningful values for address(0). Most protocols return 0 because
        address(0) has no shares, not because redemptions are closed.

        :return:
            Human-readable string if redemptions are closed,
            or None if redemptions are open or check is not supported.
        """
        if not self.can_check_redeem():
            return None

        try:
            max_redeem_raw = self.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
            if max_redeem_raw == 0:
                return f"{REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY} (maxRedeem=0)"
            return None  # Redemptions are open
        except Exception:
            return None  # Cannot determine, assume open

    def get_flags(self) -> set[VaultFlag]:
        flags = super().get_flags()

        block_identifier = self._get_block_identifier()

        # OpenZeppelin pausable
        # https://docs.openzeppelin.com/contracts/4.x/api/security#Pausable
        paused_call = EncodedCall.from_keccak_signature(
            address=self.vault_address,
            signature=Web3.keccak(text="paused()")[0:4],
            function="paused",
            data=b"",
            extra_data=None,
        )
        try:
            result = paused_call.call(
                self.web3,
                block_identifier=block_identifier,
                silent_error=True,
                ignore_error=True,
                attempts=2,
            )
            paused = convert_int256_bytes_to_int(result) != 0
        except (ValueError, BadFunctionCallOutput, BadAddressError, ProbablyNodeHasNoBlock):
            paused = False

        if paused:
            flags = flags | {VaultFlag.paused}

        return flags
