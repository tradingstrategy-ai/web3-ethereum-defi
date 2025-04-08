"""Multicall contract helpers.

- Perform several smart contract calls in one RPC request using `Multicall <https://www.multicall3.com/>`__ contract
- Increase call througput using Multicall smart contract
- Further increase call throughput using multiprocessing and

.. warning::

    See Multicall `private key leak hack warning <https://github.com/mds1/multicall>`__.

"""
import abc
import datetime
import logging
import os
import threading
from abc import abstractmethod
from dataclasses import dataclass
from itertools import islice
from pprint import pformat
from typing import TypeAlias, Iterable, Generator, Hashable, Any, Final, Callable

from fontTools.unicodedata import block
from tqdm_loggable.auto import tqdm

from eth_typing import HexAddress, BlockIdentifier, BlockNumber
from joblib import Parallel, delayed
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract, ZERO_ADDRESS, encode_function_call, ZERO_ADDRESS_STR, format_debug_instructions
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.timestamp import get_block_timestamp

logger = logging.getLogger(__name__)

#: Address, arguments tuples
CallData: TypeAlias = tuple[str | HexAddress, tuple]

#: Multicall3 address
MULTICALL_DEPLOY_ADDRESS: Final[str] = "0xca11bde05977b3631167028862be2a173976ca11"

# The muticall small contract seems unable to fetch token balances at blocks preceding
# the block when it was deployed on a chain. We can thus only use multicall for recent
# enough blocks.
MUTLICALL_DEPLOYED_AT: Final[dict[int, tuple[BlockNumber, datetime.datetime]]] = {
    # values: (block_number, blok_timestamp)
    1: (14_353_601, datetime.datetime(2022, 3, 9, 16, 17, 56)),
    56: (15_921_452, datetime.datetime(2022, 3, 9, 23, 17, 54)),  # BSC
    137: (25_770_160, datetime.datetime(2022, 3, 9, 15, 58, 11)),  # Poly
    43114: (11_907_934, datetime.datetime(2022, 3, 9, 23, 11, 52)),  # Ava
    42161: (7_654_707, datetime.datetime(2022, 3, 9, 16, 5, 28)),  # Arbitrum
}


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
        address = MULTICALL_DEPLOY_ADDRESS
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

    start = datetime.datetime.utcnow()

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
    duration = datetime.datetime.utcnow() - start
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

    start = datetime.datetime.utcnow()

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
    duration = datetime.datetime.utcnow() - start
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
        started = datetime.datetime.utcnow()

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

        duration = datetime.datetime.utcnow() - started
        logger.info("Success %s, took %s", success, duration)

    return results


def _batcher(iterable: Iterable, batch_size: int) -> Generator:
    """"Batch data into lists of batch_size length. The last batch may be shorter.

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
            self.call.args
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
            raise e #  0.0000673

        if self.debug:
            logger.info(
            "Succeed: %s, got handled value %s",
                self,
                self.get_human_id(),
                value,
            )

        return value



@dataclass(slots=True, frozen=True)
class EncodedCall:
    """Multicall payload, minified implementation.

    - Designed for multiprocessing and historical reads

    - Only carry encoded data, not ABI etc. metadata

    - Contain :py:attr:`extra_data` which allows route to call results from several calls to one handler class

    Example:

    .. code-block:: python

        convert_to_shares_payload = eth_abi.encode(['uint256'], [share_probe_amount])

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

    def get_debug_info(self) -> str:
        """Get human-readable details for debugging.

        - Punch into Tenderly simulator

        - Data contains both function signature and data payload
        """
        return f"""Address: {self.address}\nData: {self.data.hex()}"""

    @staticmethod
    def from_contract_call(
        call: ContractFunction,
        extra_data: dict,
        first_block_number: int | None = None,
    ) -> "EncodedCall":
        """Create poller call from Web3.py Contract proxy object"""
        assert isinstance(call, ContractFunction)
        assert isinstance(extra_data, dict)
        data = encode_function_call(
            call,
            call.args
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
    ) -> "EncodedCall":
        """Create poller call directly from a raw function signature"""
        assert isinstance(signature,  bytes)
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
        web3: Web3, block_identifier: BlockIdentifier,
        from_=ZERO_ADDRESS_STR,
        gas=75_000_000,
    ) -> bytes:
        """Return raw results of the call.

        Example:

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

        :return:
            Raw call results as bytes

        :raise ValueError:
            If the call reverts
        """
        transaction = {
            "to": self.address,
            "from": from_,
            "data": self.data.hex(),
            "gas": gas,
        }
        try:
            return web3.eth.call(
                transaction=transaction,
                block_identifier=block_identifier,
            )
        except Exception as e:
            raise ValueError(f"Call failed: {str(e)}\nBlock: {block_identifier}\nTransaction data:{pformat(transaction)}") from e


@dataclass(slots=True, frozen=True)
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

    - Initialises the web3 connection at the start of the process
    - If you try to read using multicall when the contract is not yet deployed (see :py:func:`get_multicall_block_number`)
      then you get no results
    """

    def __init__(self, web3factory: Web3Factory | Web3, chunk_size=24):
        """Create subprocess worker instance.

        :param web3factory:
            Initialise connection within the subprocess

        :param chunk_size:
            How many calls we pack into the multicall.

            Manually tuned number if your RPC nodes start to crap out, as they hit their internal time limits.

        """
        logger.info(
            "Initialising multiprocess multicall handler, process %s, thread %s",
            os.getpid(),
            threading.current_thread(),
        )
        if isinstance(web3factory, Web3):
            # Directly passed
            self.web3 = web3factory
        else:
            # Construct new RPC connection in every subprocess
            self.web3 = web3factory()

        self.chunk_size = chunk_size

    def get_block_timestamp(self, block_number: int) -> datetime.datetime:
        return get_block_timestamp(self.web3, block_number)

    def process_calls(
        self,
        block_identifier: BlockIdentifier,
        calls: list[EncodedCall],
    ) -> Iterable[EncodedCallResult]:
        """Work a chunk of calls in the subprocess."""

        assert isinstance(calls, list)
        assert all(isinstance(c, EncodedCall) for c in calls), f"Got: {calls}"

        # These calls we dropped because they are historical multicalls to later blocks
        filtered_out_calls = [c for c in calls if not c.is_valid_for_block(block_identifier)]

        # These calls with hit the RPC node
        filtered_in_calls = [c for c in calls if c.is_valid_for_block(block_identifier)]
        encoded_calls = [(Web3.to_checksum_address(c.address), c.data) for c in filtered_in_calls]

        start = datetime.datetime.utcnow()

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
        calls_results = []
        payload_size = 0
        for i in range(0, len(encoded_calls), self.chunk_size):
            batch_calls = encoded_calls[i : i + self.chunk_size]
            # Calculate how many bytes we are going to use
            payload_size += sum(20 + len(c[1]) for c in batch_calls)

            # "empty reader set" -32000 error
            # WTF
            # https://github.com/onflow/go-ethereum/blob/18406ff59b887a1d132f46068aa0bee2a9234bd7/core/state/reader.go#L303C6-L303C25
            bound_func = multicall_contract.functions.tryBlockAndAggregate(
                calls=batch_calls,
                requireSuccess=False,
            )
            try:
                _, _, batch_results = bound_func.call(block_identifier=block_identifier)
            except ValueError as e:
                debug_data = format_debug_instructions(bound_func)
                raise ValueError(f"Multicall failed. To simulate:\n{debug_data}") from e
            calls_results += batch_results

        # Calculate byte size of output
        out_size = sum(len(o[1]) for o in calls_results)

        assert len(filtered_in_calls) == len(calls_results), f"Calls: {len(filtered_in_calls)}, results: {len(calls_results)}"
        assert len(encoded_calls) == len(calls_results), f"Calls: {len(encoded_calls)}, results: {len(calls_results)}"

        for call, output_tuple in zip(filtered_in_calls, calls_results):
            yield EncodedCallResult(
                call=call,
                success=output_tuple[0],
                result=output_tuple[1],
            )

        # User friendly logging
        duration = datetime.datetime.utcnow() - start
        logger.info("Multicall result fetch and handling took %s, input was %d bytes, output was %d bytes", duration, payload_size, out_size)


def read_multicall_historical(
    web3factory: Web3Factory,
    calls: Iterable[EncodedCall],
    start_block: int,
    end_block: int,
    step: int,
    max_workers=8,
    timeout=1800,
    display_progress: bool | str = True,
    progress_suffix: Callable | None = None,
) -> Iterable[CombinedEncodedCallResult]:
    """Read historical data using multiple threads in parallel for speedup.

    - Show a progress bar using :py:mod:`tqdm`

    :param display_progress:
        Whether to display progress bar or not.

        Set to string to have a progress bar label.
    """

    assert type(start_block) == int, f"Got: {start_block}"
    assert type(end_block) == int, f"Got: {end_block}"
    assert type(step) == int, f"Got: {step}"

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend="loky",
        timeout=timeout,
        max_nbytes=40*1024*1024,  # Allow passing 40 MBytes for child processes
        return_as="generator",  # TODO: Dig generator_unordered cause bugs?
    )

    iter_count = (end_block - start_block + 1) // step
    total = iter_count

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

    logger.info("Per block we need to do %d calls", len(calls_pickle_friendly))

    def _task_gen() -> Iterable[MulticallHistoricalTask]:
        for block_number in range(start_block, end_block, step):
            task = MulticallHistoricalTask(web3factory, block_number, calls_pickle_friendly)
            logger.debug(
                "Created task for block %d with %d calls",
                block_number,
                len(calls_pickle_friendly),
            )
            yield task

    for completed_task in worker_processor(delayed(_execute_multicall_subprocess)(task) for task in _task_gen()):
        if progress_bar:
            progress_bar.update(1)

            if progress_suffix is not None:
                suffixes = progress_suffix()
                progress_bar.set_postfix(suffixes)

        yield completed_task

    if progress_bar:
        progress_bar.close()


def read_multicall_chunked(
    web3factory: Web3Factory,
    calls: list[EncodedCall],
    block_identifier: BlockIdentifier,
    max_workers=8,
    timeout=1800,
    chunk_size: int=40,
    progress_bar_desc: str | None = None,
) -> Iterable[EncodedCallResult]:
    """Read current data using multiple processes in parallel for speedup.

    - All calls hit the same block number
    - Show a progress bar using :py:mod:`tqdm`

    :param chunk_size:
        Max calls per one chunk sent to Multicall contract, to stay below JSON-RPC read gas limit.

    :param total:
        Estimated total number of calls for the progress bar.

    :param progress_bar_template:
        If set, display a TQDM progress bar for the process.
    """

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend="loky",
        timeout=timeout,
        max_nbytes=40*1024*1024,  # Allow passing 40 MBytes for child processes
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
        for i in range(0, len(calls), chunk_size):
            chunk = calls[i:i + chunk_size]
            yield MulticallHistoricalTask(web3factory, block_identifier, chunk)

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



_reader_instance = threading.local()


@dataclass(slots=True, frozen=True)
class MulticallHistoricalTask:
    """Pickled task send between multicall reader loop and subprocesses."""

    #: Used to initialise web3 connection in the subprocess
    web3factory: Web3Factory

    #: Block number to sccan
    block_number: BlockIdentifier

    # Multicalls to perform
    calls: list[EncodedCall]

    def __post_init__(self):
        assert callable(self.web3factory)
        assert type(self.block_number) in (int, str), f"Got: {self.block_number}"
        assert type(self.calls) == list
        assert all(isinstance(c, EncodedCall) for c in self.calls), f"Expected list of EncodedCall objects, got {self.calls}"


def _execute_multicall_subprocess(
    task: MulticallHistoricalTask,
) -> CombinedEncodedCallResult:
    """Extract raw JSON-RPC data from a node in a multiprocess"""
    global _reader_instance

    reader: MultiprocessMulticallReader

    # Initialise web3 connection when called for the first time
    if getattr(_reader_instance, "reader", None) is None:
        reader = _reader_instance.reader = MultiprocessMulticallReader(task.web3factory)
    else:
        reader = _reader_instance.reader

    timestamp = reader.get_block_timestamp(task.block_number)

    # Perform multicall to read share prices
    call_results = reader.process_calls(
        task.block_number,
        task.calls,
    )
    # Pass results back to the main process
    return CombinedEncodedCallResult(
        block_number=task.block_number,
        timestamp=timestamp,
        results=[c for c in call_results],
    )
