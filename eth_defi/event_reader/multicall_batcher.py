"""Multicall3 contract helpers.

- Read :ref:`multicall3-tutorial` for tutorial
- Perform several smart contract calls in one RPC request using `Multicall <https://www.multicall3.com/>`__ contract
- Increase smart contract call throughput using Multicall smart contract
- Further increase call throughput using multiprocessing with :py:class:`joblib.Parallel`
- Do fast historical reads several blocks with :py:func:`read_multicall_historical`

.. warning::

    See Multicall `private key leak hack warning <https://github.com/mds1/multicall>`__.

"""

import abc
import datetime
import logging
import os
import threading
import time
import zlib

from abc import abstractmethod
from dataclasses import dataclass, field
from http.client import RemoteDisconnected
from itertools import islice
from pprint import pformat
from typing import TypeAlias, Iterable, Generator, Hashable, Any, Final, Callable

from hexbytes import HexBytes
from requests import HTTPError
from tqdm_loggable.auto import tqdm
from requests.exceptions import ReadTimeout, ConnectionError

from eth_typing import HexAddress, BlockIdentifier, BlockNumber
from joblib import Parallel, delayed
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract, ZERO_ADDRESS, encode_function_call, ZERO_ADDRESS_STR, format_debug_instructions
from eth_defi.chain import get_default_call_gas_limit
from eth_defi.compat import native_datetime_utc_now
from eth_defi.event_reader.fast_json_rpc import get_last_headers
from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.middleware import ProbablyNodeHasNoBlock, is_retryable_http_exception
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.named import get_provider_name
from eth_defi.timestamp import get_block_timestamp


logger = logging.getLogger(__name__)

#: Address, arguments tuples
CallData: TypeAlias = tuple[str | HexAddress, tuple]

#: Default Multicall3 address
MULTICALL_DEPLOY_ADDRESS: Final[str] = "0xca11bde05977b3631167028862be2a173976ca11"

#: Per-chain Multicall3 deployemnts
MULTICALL_CHAIN_ADDRESSES = {
    324: "0xF9cda624FBC7e059355ce98a31693d299FACd963",  # https://zksync.blockscout.com/address/0xF9cda624FBC7e059355ce98a31693d299FACd963
}

# The muticall small contract seems unable to fetch token balances at blocks preceding
# the block when it was deployed on a chain. We can thus only use multicall for recent
# enough blocks.
# chain id: (block_number, blok_timestamp)
MUTLICALL_DEPLOYED_AT: Final[dict[int, tuple[BlockNumber, datetime.datetime]]] = {
    1: (14_353_601, datetime.datetime(2022, 3, 9, 16, 17, 56)),
    56: (15_921_452, datetime.datetime(2022, 3, 9, 23, 17, 54)),  # BSC
    137: (25_770_160, datetime.datetime(2022, 3, 9, 15, 58, 11)),  # Poly
    43114: (11_907_934, datetime.datetime(2022, 3, 9, 23, 11, 52)),  # Ava
    42161: (7_654_707, datetime.datetime(2022, 3, 9, 16, 5, 28)),  # Arbitrum
    5000: (304717, datetime.datetime(2023, 6, 29)),  # Mantle
    100: (21022491, datetime.datetime(2022, 4, 9)),  # Gnosis  https://blockscout.com/xdai/mainnet/address/0xcA11bde05977b3631167028862bE2a173976CA11/contracts
    324: (3908235, datetime.datetime(2023, 5, 24)),  # Zksync
    42220: (13112599, datetime.datetime(2022, 5, 21)),  # Celo https://celo.blockscout.com/tx/0xe21952e50a541d6a9129009429b4c931841f95817235b2a7de4d0904c6278afb
    2741: (284377, datetime.datetime(2025, 1, 28)),  # Abstract https://abscan.org/tx/0x99fbeee476b397360a2a8cdac20488053198520c3055b78888a52bb765cb3051
    10: (4_286_263, datetime.datetime(2022, 3, 9)),  # Optimism https://optimistic.etherscan.io/address/0xcA11bde05977b3631167028862bE2a173976CA11#code
}


class MulticallStateProblem(Exception):
    """TODO"""


class MulticallRetryable(Exception):
    """Out of gas.

    - Broken contract in a gas loop

    Try to decrease batch size.
    """


class MulticallNonRetryable(Exception):
    """Need to take a manual look these errors."""


def get_multicall_block_number(chain_id: int) -> int | None:
    """When the multicall contract was deployed for a chain."""
    entry = MUTLICALL_DEPLOYED_AT.get(chain_id, None)
    if entry:
        return entry[0]
    return None


def get_multicall_contract(
    web3: Web3,
    address: HexAddress | str | None = None,
    block_identifier: BlockNumber = None,
) -> "Contract":
    """Return a multicall smart contract instance.

    - Get `IMulticall3` compiled with Forge

    - Use `multicall3` ABI.
    """

    if address is None:
        address = MULTICALL_CHAIN_ADDRESSES.get(web3.eth.chain_id, MULTICALL_DEPLOY_ADDRESS)
        chain_id = web3.eth.chain_id
        multicall_data = MUTLICALL_DEPLOYED_AT.get(chain_id)
        # Do a block number check for archive nodes
        if multicall_data is not None and type(block_identifier) == int:
            assert multicall_data[0] < block_identifier, f"Multicall not yet deployed at {block_identifier}"

    return get_deployed_contract(web3, "multicall/IMulticall3.json", Web3.to_checksum_address(address))


def call_multicall(
    multicall_contract: Contract,
    calls: list["MulticallWrapper"],
    block_identifier: BlockIdentifier,
) -> dict[Hashable, Any]:
    """Call a multicall contract."""

    assert all(isinstance(c, MulticallWrapper) for c in calls), f"Got: {calls}"

    encoded_calls = [c.get_address_and_data() for c in calls]

    payload_size = sum(20 + len(c[1]) for c in encoded_calls)

    start = native_datetime_utc_now()

    logger.info(
        f"Performing multicall, input payload total size %d bytes on %d functions, block is {block_identifier:,}",
        payload_size,
        len(encoded_calls),
    )

    bound_func = multicall_contract.functions.tryBlockAndAggregate(
        calls=encoded_calls,
        requireSuccess=False,
    )
    _, _, calls_results = bound_func.call(block_identifier=block_identifier)

    results = {}

    assert len(calls_results) == len(calls_results)

    out_size = sum(len(o[1]) for o in calls_results)

    for call, output_tuple in zip(calls, calls_results):
        succeed, output = output_tuple
        results[call.get_key()] = call.handle(succeed, output)

    # User friendly logging
    duration = native_datetime_utc_now() - start
    logger.info("Multicall result fetch and handling took %s, output was %d bytes", duration, out_size)

    return results


def call_multicall_encoded(
    multicall_contract: Contract,
    calls: list["MulticallWrapper"],
    block_identifier: BlockIdentifier,
) -> dict[Hashable, Any]:
    """Call a multicall contract."""

    assert all(isinstance(c, MulticallWrapper) for c in calls), f"Got: {calls}"

    encoded_calls = [c.get_address_and_data() for c in calls]

    payload_size = sum(20 + len(c[1]) for c in encoded_calls)

    start = native_datetime_utc_now()

    logger.info(
        f"Performing multicall, input payload total size %d bytes on %d functions, block is {block_identifier:,}",
        payload_size,
        len(encoded_calls),
    )

    bound_func = multicall_contract.functions.tryBlockAndAggregate(
        calls=encoded_calls,
        requireSuccess=False,
    )
    _, _, calls_results = bound_func.call(block_identifier=block_identifier)

    results = {}

    assert len(calls_results) == len(calls_results)

    out_size = sum(len(o[1]) for o in calls_results)

    for call, output_tuple in zip(calls, calls_results):
        succeed, output = output_tuple
        results[call.get_key()] = call.handle(succeed, output)

    # User friendly logging
    duration = native_datetime_utc_now() - start
    logger.info("Multicall result fetch and handling took %s, output was %d bytes", duration, out_size)

    return results


def call_multicall_batched_single_thread(
    multicall_contract: Contract,
    calls: list["MulticallWrapper"],
    block_identifier: BlockIdentifier,
    batch_size=15,
) -> dict[Hashable, Any]:
    """Call Multicall contract with a payload.

    - Single threaded

    :param web3_factory:
        - Each thread will get its own web3 instance

    :param batch_size:
        Don't do more than this calls per one RPC.

    """
    result = {}
    assert len(calls) > 0
    for idx, batch in enumerate(_batcher(calls, batch_size), start=1):
        logger.info("Processing multicall batch #%d, batch size %d", idx, batch_size)
        partial_result = call_multicall(multicall_contract, batch, block_identifier)
        result.update(partial_result)
    return result


def call_multicall_debug_single_thread(
    multicall_contract: Contract,
    calls: list["MulticallWrapper"],
    block_identifier: BlockIdentifier,
):
    """Skip Multicall contract and try eth_call directly.

    - For debugging problems

    - Perform normal `eth_call`

    - Log output what calls are going out to diagnose issues
    """
    assert len(calls) > 0
    web3 = multicall_contract.w3

    results = {}

    for idx, call in enumerate(calls, start=1):
        address, data = call.get_address_and_data()

        logger.info(
            "Doing call #%d, call info %s, data len %d, args %s",
            idx,
            call,
            len(data),
            call.get_human_args(),
        )
        started = native_datetime_utc_now()

        # 0xcdca1753000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000004c4b400000000000000000000000000000000000000000000000000000000000000042833589fcd6edb6e08f4c7c32d4f71b54bda029130001f44200000000000000000000000000000000000006000bb8ca73ed1815e5915489570014e024b7ebe65de67900000000000000000000000000000000000000000000000000000000000
        if len(data) >= 196:
            logger.info("To: %s, data: %s", address, data.hex())

        try:
            output = web3.eth.call(
                {
                    "from": ZERO_ADDRESS,
                    "to": address,
                    "data": data,
                },
                block_identifier=block_identifier,
            )
            success = True
        except Exception as e:
            success = False
            output = None
            logger.error("Failed with %s", e)

        results[call.get_key()] = call.handle(success, output)

        duration = native_datetime_utc_now() - started
        logger.info("Success %s, took %s", success, duration)

    return results


def _batcher(iterable: Iterable, batch_size: int) -> Generator:
    """ "Batch data into lists of batch_size length. The last batch may be shorter.

    https://stackoverflow.com/a/8290514/2527433
    """
    iterator = iter(iterable)
    while batch := list(islice(iterator, batch_size)):
        yield batch


@dataclass(slots=True, frozen=True)
class MulticallWrapper(abc.ABC):
    """Wrap a call going through the Multicall contract.

    - Each call in the batch is represented by one instance of :py:class:`MulticallWrapper`

    - This class must be subclassed and needed :py:meth:`get_key`, :py:meth:`handle` and :py:meth:`__repr__`
    """

    #: Bound web3.py function with args in the place
    call: ContractFunction

    #: Set for extensive info logging
    debug: bool

    def __post_init__(self):
        assert isinstance(self.call, ContractFunction)
        assert self.call.args

    def __repr__(self):
        """Log output about this call"""
        raise NotImplementedError(f"Please implement in a subclass")

    @property
    def contract_address(self) -> HexAddress:
        return self.call.address

    @abstractmethod
    def get_key(self) -> Hashable:
        """Get key that will identify this call in the result dictionary"""

    @abstractmethod
    def handle(self, succeed: bool, raw_return_value: bytes) -> Any:
        """Parse the call result.

        :param succeed:
            Did we revert or not

        :param raw_return_value:
            Undecoded bytes from the Solidity function call

        :return:
            The value placed in the return dict
        """

    def get_human_id(self) -> str:
        return str(self.get_key())

    def get_address_and_data(self) -> tuple[HexAddress, bytes]:
        data = encode_function_call(
            self.call,
            self.call.args,
        )
        return self.call.address, data

    def get_human_args(self) -> str:
        """Get Solidity args as human readable string for debugging."""
        args = self.call.args

        def _humanise(a):
            if not type(a) == int:
                if hasattr(a, "hex"):
                    return a.hex()
            return str(a)

        return "(" + ", ".join(_humanise(a) for a in args) + ")"

    def multicall_callback(self, succeed: bool, raw_return_value: Any) -> Any:
        """Convert the raw Solidity function call result to a denominated token amount.

        - Multicall library callback

        :return:
            The token amount in the reserve currency we get on the market sell.

            None if this path was not supported (Solidity reverted).
        """
        if not succeed:
            # Avoid expensive logging if we do not need it
            if self.debug:
                # Print calldata so we can copy-paste it to Tenderly for symbolic debug stack trace
                address, data = self.get_address_and_data()
                logger.info("Calldata failed %s: %s", address, data)
        try:
            value = self.handle(succeed, raw_return_value)
        except Exception as e:
            logger.error(
                "Handler failed %s for return value %s",
                self.get_human_id(),
                raw_return_value,
            )
            raise e  #  0.0000673

        if self.debug:
            logger.info(
                "Succeed: %s, got handled value %s",
                self,
                self.get_human_id(),
                value,
            )

        return value


class BatchCallState(abc.ABC):
    """Allow mutlicall calls to maintain state over the multiple invocations.

    - Mostly useful for historical mutlticall read and frequency management
    """

    @abstractmethod
    def should_invoke(
        self,
        call: "EncodedCall",
        block_identifier: BlockIdentifier,
        timestamp: datetime.datetime,
    ) -> bool:
        """Check the condition if this multicall is good to go."""
        pass

    @abstractmethod
    def save(self) -> dict:
        """Persist state across multiple runs.

        :return:
            Pickleable Python object
        """
        pass

    @abstractmethod
    def load(self, data: dict):
        """Persist state across multiple runs"""
        pass


_next_call_id = 0


def _generate_call_id():
    global _next_call_id
    _next_call_id += 1
    return _next_call_id


@dataclass(slots=True, frozen=False)
class EncodedCall:
    """Multicall payload, minified implementation.

    - Designed for multiprocessing and historical reads

    - Only carry encoded data, not ABI etc. metadata

    - Contain :py:attr:`extra_data` which allows route to call results from several calls to one handler class

    Example:

    .. code-block:: python

        convert_to_shares_payload = eth_abi.encode(["uint256"], [share_probe_amount])

        share_price_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="convertToShares(uint256)")[0:4],
            function="convertToShares",
            data=convert_to_shares_payload,
            extra_data=None,
        )

    """

    #: Store ABI function for debugging purposers
    func_name: str

    #: Contract address
    address: HexAddress

    #: Call ABI-encoded payload
    data: bytes

    #: Use this to match the reader
    extra_data: dict | None

    #: First block hint when doing historical multicall reading.
    #:
    #: Skip calls for blocks that are earlier than this block number.
    #:
    first_block_number: int | None = None

    #: Running counter call id for debugging purposes
    call_id: int = field(default_factory=_generate_call_id)

    _hash: int = None

    def __hash__(self):
        """Multiprocess compatible hash.

        Needed for the workarounds when passing EncodedCall.state across multiprocess boundaries.
        """
        if not self._hash:
            # Must be multiprocess compatible
            hash_data = self.address.encode("ascii") + self.data
            self._hash = zlib.crc32(hash_data)
        return self._hash

    def __eq__(self, other):
        assert isinstance(other, EncodedCall)
        return self.address == other.address and self.data == other.data

    def get_debug_info(self) -> str:
        """Get human-readable details for debugging.

        - Punch into Tenderly simulator

        - Data contains both function signature and data payload
        """
        return f"""Address: {self.address}\nData: {self.data.hex()}"""

    def get_curl_info(self, block_number: int) -> str:
        """Get human-readable details for debugging.

        - Punch into Tenderly simulator

        - Data contains both function signature and data payload
        """
        contract_address = self.address
        data = self.data
        debug_template = f"""curl -X POST -H "Content-Type: application/json" \\
        --data '{{
          "jsonrpc": "2.0",
          "method": "eth_call",
          "params": [
            {{
              "to": "{contract_address}",
              "data": "{data.hex()}"
            }},
            "{hex(block_number)}"
          ],
          "id": 1
        }}' \\
        $JSON_RPC_URL"""
        return debug_template

    @staticmethod
    def from_contract_call(
        call: ContractFunction,
        extra_data: dict | None = None,
        first_block_number: int | None = None,
    ) -> "EncodedCall":
        """Create poller call from Web3.py Contract proxy object"""
        assert isinstance(call, ContractFunction)
        if extra_data is None:
            extra_data = {}
        assert isinstance(extra_data, dict)
        data = encode_function_call(
            call,
            call.args,
        )
        return EncodedCall(
            func_name=call.fn_name,
            address=call.address,
            data=data,
            extra_data=extra_data,
            first_block_number=first_block_number,
        )

    @staticmethod
    def from_keccak_signature(
        address: HexAddress,
        function: str,
        signature: bytes,
        data: bytes,
        extra_data: dict | None,
        first_block_number: int | None = None,
        ignore_errors: bool = False,
        state: BatchCallState | None = None,
    ) -> "EncodedCall":
        """Create poller call directly from a raw function signature"""
        assert isinstance(signature, bytes)
        assert len(signature) == 4
        assert isinstance(data, bytes)

        if extra_data is not None:
            extra_data["function"] = function

        return EncodedCall(
            func_name=function,
            address=address,
            data=signature + data,
            extra_data=extra_data,
            first_block_number=first_block_number,
        )

    def is_valid_for_block(self, block_number: BlockIdentifier) -> bool:
        if self.first_block_number is None:
            return True

        if type(block_number) == str:
            # "latest"
            return True

        assert isinstance(block_number, int)
        return self.first_block_number <= block_number

    def call(
        self,
        web3: Web3,
        block_identifier: BlockIdentifier,
        from_=ZERO_ADDRESS_STR,
        gas: int = None,
        ignore_error=False,
        silent_error=False,
        attempts: int = 3,
        retry_sleep=30.0,
    ) -> bytes:
        """Return raw results of the call.

        Example how to read:

        .. code-block:: python

            erc_7575_call = EncodedCall.from_keccak_signature(
                address=self.vault_address,
                signature=Web3.keccak(text="share()")[0:4],
                function="share",
                data=b"",
                extra_data=None,
            )

            result = erc_7575_call.call(self.web3, block_identifier="latest")
            share_token_address = convert_uint256_bytes_to_address(result)

        :param ignore_error:
            Set to True to inform middleware that it is normal for this call to fail and do not log it as a failed call, or retry it.

        :param attempts:
            Use built-in retry mechanism for flaky RPC.

            This works regardless of middleware installed.
            Set to zero to ignore.

            Cannot be used with ignore_errors.

        :param gas:
            Gas limit.

            If not given, use 15M limit except for Mantle use 99M.

        :return:
            Raw call results as bytes

        :raise ValueError:
            If the call reverts
        """

        if gas is None:
            gas = get_default_call_gas_limit(web3.eth.chain_id)

        transaction = {
            "to": self.address,
            "from": from_,
            "data": self.data.hex(),
            "gas": gas,
            "ignore_error": ignore_error,  # Hint logging middleware that we should not care about if this fails
            "silent_error": silent_error,  # Hint logging middleware that we should not care about if this fails
        }

        attempt = 0

        # Cannot use with ignore eror
        if ignore_error:
            attempts = 0

        while True:
            try:
                result = web3.eth.call(
                    transaction=transaction,
                    block_identifier=block_identifier,
                )
                return result
            except Exception as e:
                msg = f"Call failed: {str(e)}\nBlock: {block_identifier}, chain: {web3.eth.chain_id}\nTransaction data:{pformat(transaction)}"
                if is_retryable_http_exception(e, method="eth_call") and attempt < attempts:
                    attempt += 1
                    logger.warning(
                        "Retrying EncodedCall.call() %s/%s, %s",
                        attempt,
                        attempts,
                        msg,
                    )
                    time.sleep(retry_sleep)
                    continue

                raise e

    def transact(
        self,
        from_: HexAddress,
        gas_limit: int,
    ) -> dict:
        """Build a transaction payload for this call.

        Example:

        .. code-block:: python

            gas_limit = 15_000_000

            # function settleDeposit(uint256 _newTotalAssets) public virtual;
            call = EncodedCall.from_keccak_signature(
                address=vault.address,
                function="settleDeposit()",
                signature=Web3.keccak(text="settleDeposit(uint256)")[0:4],
                data=convert_uin256_to_bytes(raw_nav),
                extra_data=None,
            )
            tx_data = call.transact(
                from_=asset_manager,
                gas_limit=gas_limit,
            )
            tx_hash = web3.eth.send_transaction(tx_data)
            assert_transaction_success_with_explanation(web3, tx_hash)
        """
        return {
            "to": self.address,
            "data": self.data.hex(),
            "from": from_,
            "gas": gas_limit,
        }

    def call_as_result(
        self,
        web3: Web3,
        block_identifier: BlockIdentifier,
        from_=ZERO_ADDRESS_STR,
        gas=99_000_000,
        ignore_error=False,
    ) -> "EncodedCallResult":
        """Perform RPC call and return the result as an :py:class:`EncodedCallResult`.

        - Performs an RPC call and returns a wrapped result in an :py:class:`EncodedCallResult`.

        See :py:meth:`call` for info.
        """

        try:
            raw_result = self.call(
                web3=web3,
                block_identifier=block_identifier,
                from_=from_,
                gas=gas,
                ignore_error=ignore_error,
            )

            assert isinstance(raw_result, HexBytes), f"Expected HexBytes, got {type(raw_result)}: {raw_result.hex()}"

            return EncodedCallResult(
                call=self,
                success=True,
                result=bytes(raw_result),
                block_identifier=block_identifier,
            )
        except ValueError as e:
            # TODO: RPCs can return varying exceptoins here
            return EncodedCallResult(
                call=self,
                success=False,
                result=b"",
                block_identifier=block_identifier,
                revert_exception=e,
            )


@dataclass(slots=True, frozen=False)
class EncodedCallResult:
    """Result of an one multicall.

    Example:

    .. code-block:: python

        # File 21 of 47 : PlasmaVaultStorageLib.sol
        #     /// @custom:storage-location erc7201:io.ipor.PlasmaVaultPerformanceFeeData
        #     struct PerformanceFeeData {
        #         address feeManager;
        #         uint16 feeInPercentage;
        #     }
        data = call_by_name["getPerformanceFeeData"].result
        performance_fee = int.from_bytes(data[32:64], byteorder="big") / 10_000

    """

    call: EncodedCall
    success: bool
    result: bytes

    #: Block number
    block_identifier: BlockIdentifier

    #: Timestamp of the block (if available)
    timestamp: datetime.datetime | None = None

    #: Not available in multicalls, only through :py:meth:`EncodedCall.call_as_result`
    revert_exception: Exception | None = None

    #: Copy the state reference in stateful reading
    state: BatchCallState | None = None

    def __repr__(self):
        return f"<Call {self.call} at block {self.block_identifier}, success {self.success}, result: {self.result.hex()}, result len {len(self.result)}>"

    def __post_init__(self):
        assert isinstance(self.call, EncodedCall), f"Got: {self.call}"
        assert type(self.success) == bool, f"Got success: {self.success}"
        assert type(self.result) == bytes


@dataclass(slots=True, frozen=True)
class CombinedEncodedCallResult:
    """Historical read result of multiple multicalls.

    Return the whole block worth of calls when iterating over chain block by block.
    """

    block_number: int
    timestamp: datetime.datetime
    results: list[EncodedCallResult]


class MultiprocessMulticallReader:
    """An instance created in a subprocess to do calls.

    - Specific to a chain (connection is married with a chain, otherwise stateless)
    - Initialises the web3 connection at the start of the process
    - If you try to read using multicall when the contract is not yet deployed (see :py:func:`get_multicall_block_number`)
      then you get no results
    """

    def __init__(
        self,
        web3factory: Web3Factory | Web3,
        batch_size=40,
        backswitch_threshold=100,
        too_many_requets_sleep=60.0,
    ):
        """Create subprocess worker instance.

        :param web3factory:
            Initialise connection within the subprocess

        :param batch_size:
            How many calls we pack into the multicall.

            Manually tuned number if your RPC nodes start to crap out, as they hit their internal time limits.

        """
        if isinstance(web3factory, Web3):
            # Directly passed
            self.web3 = web3factory
        else:
            # Construct new RPC connection in every subprocess
            self.web3 = web3factory()

        name = get_provider_name(self.web3.provider)

        logger.info(
            "Initialising multiprocess multicall handler, process %s, thread %s, provider %s",
            os.getpid(),
            threading.current_thread(),
            name,
        )
        self.batch_size = batch_size

        # How many calls we have done in this subprocess
        self.calls = 0

        #: How many calls ago we switched the fallback provider.
        self.last_switch = 0

        #: Try to switch back from the fallback provider to the main provider after this many calls.
        self.backswitch_threshold = backswitch_threshold

        self.too_many_requets_sleep = too_many_requets_sleep

    def __repr__(self):
        return f"<MultiprocessMulticallReader process: {os.getpid()}, thread: {threading.current_thread()}, chain: {self.web3.eth.chain_id}>"

    def get_block_timestamp(self, block_number: int) -> datetime.datetime:
        return get_block_timestamp(self.web3, block_number)

    def get_gas_hint(self, chain_id: int, batch_calls: list[tuple[HexAddress, bytes]]) -> int | None:
        """Fix non-standard out of gas issues

        - # https://docs.alchemy.com/reference/gas-limits-for-eth_call-and-eth_estimategas
        """
        if chain_id == 5000:
            # Mantle: 1000B gas
            # Block 61298003
            # Address 0xca11bde05977b3631167028862be2a173976ca11
            return 9_999_000_000_000
        else:
            return None

    def get_batch_size(self, web3: Web3, chain_id) -> int | None:
        """Fix non-standard out of gas issues"""

        provider = web3.provider
        if isinstance(provider, FallbackProvider):
            provider = provider.get_active_provider()

        # name = get_provider_name(provider)

        if chain_id == 5000:
            # Mantle argh
            return 16
        elif chain_id == 100:
            # Gnosis chain argh
            return 16
        elif chain_id == 1:
            # Boost mainnet scan
            return 60
        else:
            return self.batch_size

    def call_multicall_with_batch_size(
        self,
        multicall_contract: Contract,
        block_identifier: BlockIdentifier,
        batch_size: int,
        encoded_calls: list[tuple[HexAddress, bytes]],
        require_multicall_result: bool,
    ) -> list[tuple[bool, bytes]]:
        """Communicate with Multicall3 contract.

        - Fail safes for ugly situations
        """
        payload_size = 0
        calls_results = []
        chain_id = self.web3.eth.chain_id
        batch_calls = []
        for i in range(0, len(encoded_calls), batch_size):
            batch_calls = encoded_calls[i : i + batch_size]
            # Calculate how many bytes we are going to use
            payload_size += sum(20 + len(c[1]) for c in batch_calls)

            # Fix Mantle out of gas
            gas = self.get_gas_hint(chain_id=chain_id, batch_calls=batch_calls)

            # https://github.com/onflow/go-ethereum/blob/18406ff59b887a1d132f46068aa0bee2a9234bd7/core/state/reader.go#L303C6-L303C25
            # https://etherscan.io/address/0xcA11bde05977b3631167028862bE2a173976CA11#code
            bound_func = multicall_contract.functions.tryBlockAndAggregate(
                calls=batch_calls,
                requireSuccess=False,
            )
            try:
                # Apply gas limit workaround
                if gas:
                    tx = {"gas": gas}
                else:
                    tx = {}

                # See make_request() in fallback.py
                tx["ignore_error"] = True

                # Perform multicall
                received_block_number, received_block_hash, batch_results = bound_func.call(tx, block_identifier=block_identifier)
            except (ValueError, ProbablyNodeHasNoBlock, HTTPError, ReadTimeout, ConnectionError, RemoteDisconnected) as e:
                debug_data = format_debug_instructions(bound_func, block_identifier=block_identifier)
                headers = get_last_headers()
                name = get_provider_name(self.web3.provider)
                if type(block_identifier) == int:
                    block_identifier = f"{block_identifier:,}"
                addresses = [t[0] for t in batch_calls]

                #
                # When
                #

                error_msg = f"Multicall failed for chain {chain_id}, block {block_identifier}, batch size: {len(batch_calls)}: {e}.\nUsing provider: {self.web3.provider.__class__}: {name}\nHTTP reply headers: {pformat(headers)}\nTo simulate:\n{debug_data}\nAddresses: {addresses}"
                parsed_error = str(e)

                for address, data in batch_calls:
                    logger.info(
                        "Failed: Multicall batch call to %s with data %s: %s",
                        address,
                        data.hex(),
                        str(e),
                    )

                if isinstance(e, HTTPError) and e.response.status_code == 429:
                    # Alchemy throttling us
                    logger.warning("Received HTTP 429: %s from %s, headers %s, sleeping %s", e, name, pformat(headers), self.too_many_requets_sleep)
                    time.sleep(self.too_many_requets_sleep)
                    raise MulticallRetryable(error_msg) from e

                # F*cking hell Ethereum nodes, what unbearable mess.
                # Need to maintain crappy retry rules and all node behaviour is totally random
                # fmt: off

                #  {'message': 'historical state 403577f4153c080830e4b964d013aa20179f9175c89b54a6d9f10188709c7662 is not available', 'code': -32000}.

                if ("out of gas" in parsed_error) or \
                   ("evm timeout" in parsed_error) or \
                   ("request timeout" in parsed_error) or \
                   ("request timed out" in parsed_error) or \
                   ("intrinsic gas too low" in parsed_error) or \
                   ("intrinsic gas too high" in parsed_error) or \
                   ("intrinsic gas too high" in parsed_error) or \
                   ("incorrect response body" in parsed_error) or \
                   ("exceeds block gas limit" in parsed_error) or \
                   ("historical state" in parsed_error) or \
                   ("state histories haven't been fully indexed yet" in parsed_error) or \
                   ("Failed to call: InvalidTransaction" in parsed_error) or \
                   isinstance(e, ProbablyNodeHasNoBlock) or \
                   isinstance(e, (ReadTimeout, RemoteDisconnected, ConnectionError)) or \
                   (isinstance(e, HTTPError) and e.response.status_code == 500):
                    raise MulticallRetryable(error_msg) from e
                # fmt: on
                else:
                    raise MulticallNonRetryable(error_msg) from e

            # Debug flag to diagnose WTF is going on Github
            # where calls randomly get empty results
            if require_multicall_result:
                for output_tuple in batch_results:
                    if output_tuple[1] == b"":
                        global _reader_instance
                        readers = _reader_instance.per_chain_readers
                        debug_str = format_debug_instructions(bound_func, block_identifier=block_identifier)
                        rpc_name = get_provider_name(multicall_contract.w3.provider)
                        last_headers = get_last_headers()
                        raise MulticallStateProblem(f"Multicall gave empty result: at block {block_identifier} at chain {self.web3.eth.chain_id}.\nDebug data is:\n{debug_str}\nRPC is: {rpc_name}\nBatch result: {batch_results}\nBatch calls: {batch_calls}\nReceived block number: {received_block_number}\nResponse headers: {pformat(last_headers)}\nLive multicall readers are: {pformat(readers)}")

            calls_results += batch_results

        return calls_results

    def process_calls(
        self,
        block_identifier: BlockIdentifier,
        calls: list[EncodedCall],
        require_multicall_result=False,
        timestamp: datetime.datetime | None = None,
    ) -> Iterable[EncodedCallResult]:
        """Work a chunk of calls in the subprocess.

        - Divide unlimited number of calls to something we think Multicall3 and RPC node can handle
        - If a single batch fail

        :param require_multicall_result:
            Headache debug flag.

        :param block_identifier:
            Block number

        :param timestamp:
            Block timestamp
        """

        assert isinstance(calls, list)
        assert all(isinstance(c, EncodedCall) for c in calls), f"Got: {calls}"

        # These calls we dropped because they are historical multicalls to later blocks
        filtered_out_calls = [c for c in calls if not c.is_valid_for_block(block_identifier)]

        # These calls with hit the RPC node
        filtered_in_calls = [c for c in calls if c.is_valid_for_block(block_identifier)]
        encoded_calls = [(Web3.to_checksum_address(c.address), c.data) for c in filtered_in_calls]

        start = native_datetime_utc_now()

        if len(filtered_out_calls) > 0:
            filtered_out_call_block = f"{filtered_out_calls[0].first_block_number:,}"
        else:
            filtered_out_call_block = "-"

        block_identifier_str = f"{block_identifier:,}" if type(block_identifier) == int else str(block_identifier)
        logger.info(
            f"Performing multicall, %d calls included, %d calls excluded, block is %s, example filtered out block number is %s",
            len(encoded_calls),
            len(filtered_out_calls),
            block_identifier_str,
            filtered_out_call_block,
        )

        if len(filtered_in_calls) == 0:
            return

        # Cannot read as multicall is not yet deployed
        if type(block_identifier) == int:
            # Historical read
            block_number = get_multicall_block_number(self.web3.eth.chain_id)
            if block_number is not None:
                if block_identifier < block_number:
                    return

        multicall_contract = get_multicall_contract(
            self.web3,
            block_identifier=block_identifier,
        )

        # If multicall payload is heavy,
        # we need to break it to smaller multicall call chunks
        # or we get RPC timeout
        chain_id = self.web3.eth.chain_id
        batch_size = self.get_batch_size(self.web3, chain_id)

        try:
            # Happy path
            calls_results = self.call_multicall_with_batch_size(
                multicall_contract,
                block_identifier=block_identifier,
                batch_size=batch_size,
                encoded_calls=encoded_calls,
                require_multicall_result=require_multicall_result,
            )
        except MulticallRetryable as e:
            # Fall back to one call per time if someone is out of gas bombing us.
            # See Mantle issues.
            # This will usually fix the issue, but it if is not resolve itself in few blocks the scan will grind snail pace and
            # the underlying contract needs to be manually blacklisted.
            block_identifier_str = f"{block_identifier:,}" if type(block_identifier) == int else str(block_identifier)
            logger.warning(f"Multicall failed (out of gas?) at chain {chain_id}, block {block_identifier_str}, batch size: {batch_size}. Falling back to one call at a time to figure out broken contract.")
            logger.info(f"Debug details: {str(e)}")  # Don't flood the terminal

            # Work around some bad apples by doing forced switch
            provider = self.web3.provider
            if isinstance(provider, FallbackProvider):
                self.last_switch = self.calls
                fallback_provider = provider
                # If we have only one fallback provider configured, try it twice
                fallback_attempts = len(provider.providers)

            else:
                fallback_provider = None
                fallback_attempts = 0

            # Set batch size to 1 and give it one more go
            if fallback_attempts > 0:
                logger.info("Attempting %d fallbacks", fallback_attempts)
                for i in range(fallback_attempts):
                    fallback_provider.switch_provider()
                    active_provider = fallback_provider.get_active_provider()
                    active_provider_name = get_provider_name(active_provider)

                    try:
                        calls_results = self.call_multicall_with_batch_size(
                            multicall_contract,
                            block_identifier=block_identifier,
                            batch_size=1,
                            encoded_calls=encoded_calls,
                            require_multicall_result=require_multicall_result,
                        )

                    except MulticallRetryable as e:
                        provider_name = get_provider_name(provider)
                        if i < (fallback_attempts - 1):
                            logger.warning(f"Attempts: {i}, max attempts: {fallback_attempts}.")
                            logger.warning(f"Multicall with batch size 1 still failed at chain {chain_id}, block {block_identifier_str}. Switching provider and retrying. Current provider: {active_provider =} ({active_provider_name}). Exception: {e}.")
                            continue

                        raise RuntimeError(
                            f"Multicall retry failed\n"
                            # Ruff piece of crap hack
                            # https://github.com/astral-sh/ruff/pull/8822
                            f"Encountered a contract that cannot be called even after dropping multicall batch size to 1 and switching providers, bailing out.\n"
                            f"Fallback attempts {i}, max attempts {fallback_attempts}.\n"
                            f"Manually figure out how to work around / change RPC providers.\n"
                            f"Original provider: {provider} ({provider_name}), fallback provider: {fallback_provider} ({active_provider_name}), chain {chain_id}, block {block_identifier_str}, batch size: 1.\n"
                            f"Exception: {e}.\n"
                        ) from e

        self.calls += 1

        # Calculate byte size of output
        out_size = sum(len(o[1]) for o in calls_results)

        # Check we are internally coherent
        assert len(filtered_in_calls) == len(calls_results), f"Calls: {len(filtered_in_calls)}, results: {len(calls_results)}"
        assert len(encoded_calls) == len(calls_results), f"Calls: {len(encoded_calls)}, results: {len(calls_results)}"

        # Build EncodedCallResult() objects out of incoming results
        for call, output_tuple in zip(filtered_in_calls, calls_results):
            yield EncodedCallResult(
                call=call,
                success=output_tuple[0],
                result=output_tuple[1],
                block_identifier=block_identifier,
                timestamp=timestamp,
            )

        # User friendly logging
        duration = native_datetime_utc_now() - start
        logger.info("Multicall result fetch and handling took %s, output was %d bytes", duration, out_size)

        # Cycle back to our main provider and hope it has recovered from the errors
        if self.last_switch:
            diff = self.calls - self.last_switch
            if diff > self.backswitch_threshold:
                # Switch back to the main provider if we have been using the fallback for too long
                provider = self.web3.provider
                if isinstance(provider, FallbackProvider):
                    if provider.currently_active_provider != 0:
                        logger.info("Switching back to the main provider at %d call after %d calls", self.calls, diff)
                        provider.reset_switch()
                self.last_switch = 0


def read_multicall_historical(
    chain_id: int,
    web3factory: Web3Factory,
    calls: Iterable[EncodedCall],
    start_block: int,
    end_block: int,
    step: int,
    max_workers=8,
    timeout=1800,
    display_progress: bool | str = True,
    progress_suffix: Callable | None = None,
    require_multicall_result=False,
) -> Iterable[CombinedEncodedCallResult]:
    """Read historical data using multiple threads in parallel for speedup.

    - Run over period of time (blocks)
    - Use multicall to harvest data from a single block at a time
    -
    - Show a progress bar using :py:mod:`tqdm`

    :param chain_id:
        Which chain we are targeting with calls.

    :param web3factory:
        The connection factory for subprocesses

    :param start_block:
        Block range to scoop

    :param end_block:
        Block range to scoop

    :param step:
        How many blocks we iterate at once

    :param timeout:
        Joblib timeout to wait for a result from an individual task

    :param progress_suffix:
        Allow caller to decorate the progress bar

    :param require_multicall_result:
        Debug parameter to crash the reader if we start to get invalid replies from Multicall3 contract.

    :param display_progress:
        Whether to display progress bar or not.

        Set to string to have a progress bar label.
    """

    assert type(start_block) == int, f"Got: {start_block}"
    assert type(end_block) == int, f"Got: {end_block}"
    assert type(step) == int, f"Got: {step}"
    assert type(chain_id) == int, f"Got: {step}"

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend="loky",
        timeout=timeout,
        max_nbytes=40 * 1024 * 1024,  # Allow passing 40 MBytes for child processes
        return_as="generator",  # TODO: Dig generator_unordered cause bugs?
    )

    iter_count = (end_block - start_block + 1) // step
    total = iter_count

    logger.info("Doing %d historical multicall tasks for blocks %d to %d with step %d", total, start_block, end_block, step)

    if display_progress:
        if type(display_progress) == str:
            desc = display_progress
        else:
            desc = f"Reading chain data w/historical multicall, {total} tasks, using {max_workers} CPUs"
        progress_bar = tqdm(
            total=total,
            desc=desc,
        )
    else:
        progress_bar = None

    calls_pickle_friendly = list(calls)

    logger.debug("Per block we need to do %d calls", len(calls_pickle_friendly))

    def _task_gen() -> Iterable[MulticallHistoricalTask]:
        for block_number in range(start_block, end_block, step):
            task = MulticallHistoricalTask(chain_id, web3factory, block_number, calls_pickle_friendly, require_multicall_result=require_multicall_result)
            logger.debug(
                "Created task for block %d with %d calls",
                block_number,
                len(calls_pickle_friendly),
            )
            yield task

    completed_task_count = 0

    for completed_task in worker_processor(delayed(_execute_multicall_subprocess)(task) for task in _task_gen()):
        completed_task_count += 1
        if progress_bar:
            progress_bar.update(1)

            if progress_suffix is not None:
                suffixes = progress_suffix()
                progress_bar.set_postfix(suffixes)

        yield completed_task

    logger.info("Completed %d historical reading tasks", completed_task_count)

    if progress_bar:
        progress_bar.close()


def read_multicall_historical_stateful(
    chain_id: int,
    web3factory: Web3Factory,
    calls: dict[EncodedCall, BatchCallState],
    start_block: int,
    end_block: int,
    step: int,
    max_workers=8,
    timeout=1800,
    display_progress: bool | str = True,
    progress_suffix: Callable | None = None,
    require_multicall_result=False,
    chunk_size=48,
) -> Iterable[CombinedEncodedCallResult]:
    """Read historical data using multicall with reading state and adaptive frequency filtering.

    - Allow adaptive frequency with read state
    - Slower loop than the dumb :py:func:`read_multicall_historical` as it has to maintain state
    - Because of state, we need to do block by block reading,
      as we need to evaluate state to see which calls are needed for which block,
      and the state depends on the result of the previous blocks

    :param chunk_size:
        We guarantee to update the reader state at least this many steps.

        24 = 24h hours per day, assuming we update state once for every day data read.

        Between chunks we blindly push data to subprocesses for speedup,
        do not attempt to hear back from the multiprocess to update the state.
    """

    assert type(start_block) == int, f"Got: {start_block}"
    assert type(end_block) == int, f"Got: {end_block}"
    assert type(step) == int, f"Got: {step}"
    assert type(chain_id) == int, f"Got: {step}"

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend="loky",
        timeout=timeout,
        max_nbytes=40 * 1024 * 1024,  # Allow passing 40 MBytes for child processes
        return_as="generator",  # TODO: Dig generator_unordered cause bugs?
    )

    iter_count = end_block - start_block + 1
    total = iter_count

    logger.info("Doing %d historical multicall block polls for blocks %d to %d with step %d", total, start_block, end_block, step)

    if display_progress:
        if type(display_progress) == str:
            desc = display_progress
        else:
            desc = f"Reading chain data w/historical multicall, {total} tasks, using {max_workers} CPUs"
        progress_bar = tqdm(
            total=total,
            desc=desc,
            unit_scale=True,
        )
    else:
        progress_bar = None

    assert isinstance(calls, dict), f"Input must be call->state dict dictionary, got {type(calls)}"
    all_calls = list(calls.keys())
    logger.info("Per block we need to do %d max calls", len(all_calls))

    assert all(s is not None for s in calls.values()), f"States missing for some calls"

    # Significant speedup by prefetcing timestamps
    timestamps = fetch_block_timestamps_multiprocess(
        chain_id=chain_id,
        web3factory=web3factory,
        start_block=start_block,
        end_block=end_block,
        step=step,
        max_workers=max_workers,
        timeout=timeout,
        display_progress=display_progress,
    )

    chunk = []

    def _flush_chunk(chunk: list[MulticallHistoricalTask]) -> Iterable[CombinedEncodedCallResult]:
        # Pass all buffered calls to sub-multiprocesses for JSON-RPC fetching
        combined_result: CombinedEncodedCallResult

        if len(chunk) == 0:
            return

        for combined_result in worker_processor(delayed(_execute_multicall_subprocess)(task) for task in chunk):
            for r in combined_result.results:
                # Retrofit states to the result objects
                assert r.timestamp, f"Got bad result: {r}"
                state = calls[r.call]
                assert state is not None
                r.state = state
            yield combined_result

    last_block = start_block
    total_accepted_calls = total_blocks = 0
    first_read = True
    for block_number in range(start_block, end_block, step):
        # Map prefetch timestamp
        timestamp = timestamps[block_number]

        # Get the list of calls that are effective for this block and the blocks in the next multicall batch.
        # Drop vaults that have peaked/dysfunctional
        if first_read:
            # Force reading of every item at the first cycle,
            # to refresh should_invoke() conditions caused
            # by broken peak_tvl read. If we get one TVL that is down to zero because of error,
            # the vault reader might stuck. By forcing the read at every program run at least once,
            # we hope to mitigate these issues.
            accepted_calls = list(calls.keys())
            first_read = False
        else:
            accepted_calls = [c for c, state in calls.items() if state.should_invoke(c, block_number, timestamp)]

        total_blocks += 1
        total_accepted_calls += len(accepted_calls)

        logger.debug(f"Compiling calls for {block_number:,}, {timestamp}, total calls {len(all_calls):,}, accepted calls {len(accepted_calls):,}")

        if len(accepted_calls) == 0:
            logger.debug("Block %d has no calls to perform, skipping", block_number)
            continue

        task = MulticallHistoricalTask(
            chain_id,
            web3factory,
            block_number,
            accepted_calls,
            timestamp=timestamp,
            require_multicall_result=require_multicall_result,
        )

        chunk.append(task)

        # Check if we are ready to process chunk blocks at a time
        if len(chunk) > chunk_size:
            if progress_bar:
                block_now = chunk[-1].block_number
                blocks_done = block_now - last_block
                last_block = block_now
                progress_bar.update(blocks_done)
                if progress_suffix is not None:
                    suffixes = progress_suffix()
                    progress_bar.set_postfix(suffixes)

            for combined_result in _flush_chunk(chunk):
                logger.debug(f"Updating states for {combined_result.timestamp} {combined_result.block_number:,}")
                yield combined_result

            chunk = []

    logger.info(
        "Total blocks %d, total accepted calls over the period: %d",
        total_blocks,
        total_accepted_calls,
    )

    # Process the remaning uneven chunk
    yield from _flush_chunk(chunk)

    if progress_bar:
        progress_bar.close()


def read_multicall_chunked(
    chain_id: int,
    web3factory: Web3Factory,
    calls: list[EncodedCall],
    block_identifier: BlockIdentifier,
    max_workers=8,
    timeout=1800,
    chunk_size: int = 40,
    progress_bar_desc: str | None = None,
    timestamped_results=True,
) -> Iterable[EncodedCallResult]:
    """Read current data using multiple processes in parallel for speedup.

    - All calls hit the same block number
    - Show a progress bar using :py:mod:`tqdm`

    Example:

    .. code-block:: python

            # Generated packed multicall for each token contract we want to query
            balance_of_signature = Web3.keccak(text="balanceOf(address)")[0:4]


            def _gen_calls(addresses: Iterable[str]) -> Iterable[EncodedCall]:
                for _token_address in addresses:
                    yield EncodedCall.from_keccak_signature(
                        address=_token_address.lower(),
                        signature=balance_of_signature,
                        data=convert_address_to_bytes32(out_address),
                        extra_data={},
                        ignore_errors=True,
                        function="balanceOf",
                    )


            web3factory = MultiProviderWeb3Factory(web3.provider.endpoint_uri, hint="fetch_erc20_balances_multicall")

            # Execute calls for all token balance reads at a specific block.
            # read_multicall_chunked() will automatically split calls to multiple chunks
            # if we are querying too many.
            results = read_multicall_chunked(
                chain_id=chain_id,
                web3factory=web3factory,
                calls=list(_gen_calls(tokens)),
                block_identifier=block_identifier,
                max_workers=max_workers,
                timestamped_results=False,
            )

            results = list(results)

            addr_to_balance = LowercaseDict()

            for result in results:
                token_address = result.call.address

                if not result.result:
                    if raise_on_error:
                        raise BalanceFetchFailed(f"Could not read token balance for ERC-20: {token_address} for address {out_address}")
                    value = None
                else:
                    raw_value = convert_int256_bytes_to_int(result.result)
                    if decimalise:
                        token = fetch_erc20_details(web3, token_address, cache=token_cache, chain_id=chain_id)
                        value = token.convert_to_decimals(raw_value)
                    else:
                        value = raw_value

                addr_to_balance[token_address] = value


    :param chain_id:
        Which EVM chain we are targeting with calls.

    :param web3factory:
        The connection factory for subprocesses

    :param calls:
        List of calls to perform against Multicall3.

    :param chunk_size:
        Max calls per one chunk sent to Multicall contract, to stay below JSON-RPC read gas limit.

    :param max_workers:
        How many parallel processes to use.

    :param timeout:
        Joblib timeout to wait for a result from an individual task.

    :param block_identifier:
        Block number to read.

        - Can be a block number or "latest" or "earliest"

    :param progress_bar_desc:
        If set, display a TQDM progress bar for the process.

    :param timestamped_results:
        Need timestamp of the block number in each result.

        Causes very slow eth_getBlock call, use only if needed.

    :return:
        Iterable of results.

        One entry per each call.

        Calls may be different order than originally given.
    """

    assert type(chain_id) == int, f"Got: {chain_id}"

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend="loky",
        timeout=timeout,
        max_nbytes=40 * 1024 * 1024,  # Allow passing 40 MBytes for child processes
        return_as="generator_unordered",
    )

    chunk_count = len(calls) // chunk_size + 1
    total = chunk_count

    logger.info("About to perform %d multicalls", len(calls))

    if progress_bar_desc:
        progress_bar = tqdm(
            total=total,
            desc=progress_bar_desc,
        )
    else:
        progress_bar = None

    def _task_gen() -> Iterable[MulticallHistoricalTask]:
        if timestamped_results:
            # Need timestamp of block number
            ts = None
        else:
            # Prefill our current time, do not care about the real timestamp
            ts = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        for i in range(0, len(calls), chunk_size):
            chunk = calls[i : i + chunk_size]
            yield MulticallHistoricalTask(chain_id, web3factory, block_identifier, chunk, timestamp=ts)

    performed_calls = success_calls = failed_calls = 0
    for completed_task in worker_processor(delayed(_execute_multicall_subprocess)(task) for task in _task_gen()):
        if progress_bar:
            progress_bar.update(1)

        yield from completed_task.results

        performed_calls += len(completed_task.results)
        success_calls += len([r for r in completed_task.results if r.success])
        failed_calls += len([r for r in completed_task.results if not r.success])

    if progress_bar:
        progress_bar.close()

    logger.info(
        "Performed %d calls, succeed: %d, failed: %d",
        performed_calls,
        success_calls,
        failed_calls,
    )


#: Store per-chain reader instances recycled in multiprocess reading
_reader_instance = threading.local()

_task_counter = 0


def _create_task_id() -> int:
    global _task_counter
    _task_counter += 1
    return _task_counter


@dataclass(slots=True, frozen=True)
class MulticallHistoricalTask:
    """Pickled task send between multicall reader loop and subprocesses.

    Send a batch of calls to a specific block.
    """

    #: Track which chain this call belongs to
    chain_id: int

    #: Used to initialise web3 connection in the subprocess
    web3factory: Web3Factory

    #: Block number to sccan
    block_number: BlockIdentifier

    #: Multicalls to perform
    calls: list[EncodedCall]

    #: Debug parameter to early abort if we get invalid replies from Multicall contract
    require_multicall_result: bool = False

    #: Fetch timestamp not given.
    #:
    #: Otherwise prefetched
    timestamp: datetime.datetime = None

    #: Running counter for task ids, for serialisation checks
    task_id: int = field(default_factory=_create_task_id)

    def __post_init__(self):
        assert callable(self.web3factory)
        assert type(self.block_number) in (int, str), f"Got: {self.block_number}"
        assert type(self.calls) == list
        assert all(isinstance(c, EncodedCall) for c in self.calls), f"Expected list of EncodedCall objects, got {self.calls}"


def _execute_multicall_subprocess(
    task: MulticallHistoricalTask,
) -> CombinedEncodedCallResult:
    """Extract raw JSON-RPC data from a node in.

    - Subprocess entrypoint
    - This is called by a joblib.Parallel
    - The subprocess is recycled between different batch jobs
    - We cache reader Web3 connections between batch jobs
    - joblib never shuts down this process
    """
    global _reader_instance

    reader: MultiprocessMulticallReader

    # Initialise web3 connection when called for the first time.
    # We will recycle the same connection instance and it is kept open
    # until shutdown.
    per_chain_readers = getattr(_reader_instance, "per_chain_readers", None)
    if per_chain_readers is None:
        per_chain_readers = _reader_instance.per_chain_readers = {}

    assert task.chain_id

    reader = per_chain_readers.get(task.chain_id)
    if reader is None:
        reader = per_chain_readers[task.chain_id] = MultiprocessMulticallReader(task.web3factory)

    # Read block timestan for this batch
    assert task.chain_id == reader.web3.eth.chain_id, f"chain_id mismatch. Wanted: {task.chain_id}, reader has: {reader.web3.eth.chain_id}"

    if task.timestamp is None:
        timestamp = reader.get_block_timestamp(task.block_number)
    else:
        timestamp = task.timestamp

    # Perform multicall to read share prices
    call_results = reader.process_calls(
        task.block_number,
        task.calls,
        require_multicall_result=task.require_multicall_result,
        timestamp=timestamp,
    )

    # Pass results back to the main process
    return CombinedEncodedCallResult(
        block_number=task.block_number,
        timestamp=timestamp,
        results=[c for c in call_results],
    )
