"""Multicall3 contract helpers.

- Read :ref:`multicall3-tutorial` for tutorial
- Perform several smart contract calls in one RPC request using `Multicall <https://www.multicall3.com/>`__ contract
- Increase smart contract call throughput using Multicall smart contract
- Further increase call throughput using multiprocessing with :py:class:`joblib.Parallel`
- Do fast historical reads several blocks with :py:func:`read_multicall_historical`

For usage see :py:func:`read_multicall_chunked` and `read_multicall_historical_stateful` functions.

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
from collections import Counter, defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from http.client import RemoteDisconnected
from itertools import islice
from pathlib import Path
from pprint import pformat
from typing import TYPE_CHECKING, Any, Callable, Final, Generator, Hashable, Iterable, Iterator, TypeAlias

from eth_typing import BlockIdentifier, BlockNumber, HexAddress
from hexbytes import HexBytes
from joblib import Parallel, delayed
from requests import HTTPError
from requests.exceptions import ConnectionError, ReadTimeout
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import ZERO_ADDRESS, ZERO_ADDRESS_STR, encode_function_call, format_debug_instructions, get_deployed_contract
from eth_defi.chain import get_default_call_gas_limit
from eth_defi.compat import native_datetime_utc_now
from eth_defi.event_reader.fast_json_rpc import get_last_headers
from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess_auto_backend
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.middleware import ProbablyNodeHasNoBlock, is_retryable_http_exception
from eth_defi.provider.fallback import ChainIdMismatch, FallbackProvider, FallbackRetryConfiguration, get_fallback_provider
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.provider.named import get_provider_name
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.timestamp import get_block_timestamp
from eth_defi.vault.risk import BROKEN_VAULT_CONTRACTS

if TYPE_CHECKING:
    from hypersync import HypersyncClient

logger = logging.getLogger(__name__)

#: ``(target_address, function_arguments)``: the contract to call and its
#: positional ABI arguments, before encoding the Multicall payload.
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
# Key: chain ID; value: (deployment_block_number, deployment_timestamp).
# Timestamps are naive UTC; these boundaries keep historical batches from
# calling Multicall3 before its bytecode existed on the selected chain.
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


def plan_multicall_batches(
    encoded_calls: list[tuple[HexAddress, bytes]],
    batch_size: int,
    greylist_batch_size: int = 1,
    greylist: frozenset[HexAddress] = frozenset(),
) -> Iterator[tuple[bool, list[int]]]:
    """Plan normal requests before contract-isolated greylisted requests.

    The shared reader uses positions rather than addresses as result keys so
    duplicate inputs retain identity. Reviewed targets never share a physical
    request with robust targets or another greylisted contract. The caller
    supplies a policy for the selected chain; no chain-specific list is imported.

    :param encoded_calls: Ordered ``(target_address, encoded_calldata)`` tuples.
    :param batch_size: Positive normal maximum encoded subcalls per request.
    :param greylist:
        Caller-selected target addresses for this chain, matched case-insensitively.
        Defaults to empty: readers never import or select chain-specific policies.
    :param greylist_batch_size: Positive isolated maximum subcalls; default one.
    :return: ``(greylisted, input_indexes)`` pairs in execution order. The boolean
        identifies the lane; indexes map outputs back to the original input.
    """
    assert batch_size > 0 and greylist_batch_size > 0
    greylist = frozenset(HexAddress(address.lower()) for address in greylist)
    regular: list[int] = []
    isolated: dict[HexAddress, list[int]] = defaultdict(list)
    for index, (address, _data) in enumerate(encoded_calls):
        if address.lower() in greylist:
            isolated[HexAddress(address.lower())].append(index)
        else:
            regular.append(index)
    for offset in range(0, len(regular), batch_size):
        yield False, regular[offset : offset + batch_size]
    for indexes in isolated.values():
        for offset in range(0, len(indexes), greylist_batch_size):
            yield True, indexes[offset : offset + greylist_batch_size]


HISTORICAL_STATE_UNAVAILABLE_MESSAGE_CLUES: Final[frozenset[str]] = frozenset(
    {
        "missing trie node",
        "metadata is not found",
        "layer stale",
    }
)
"""RPC error-message fragments indicating unavailable historical state.

These are provider implementation and retention errors, not Solidity reverts
from the target vault. A node may serve the requested chain head correctly but
still be unable to execute an ``eth_call`` at the requested historical block.
"""


def is_historical_state_unavailable_error(error: str | Exception) -> bool:
    """Identify an RPC response which cannot supply requested historical state.

    Historical Multicall uses ``eth_call`` with a block identifier. The call
    requires the RPC node to retain both the block and the associated state trie.
    Many endpoints advertised as archive-capable retain this data incompletely,
    route some historical requests to a pruned replica, or temporarily lose the
    rollup metadata needed to reconstruct a historical call. Repeating the same
    request against that endpoint normally cannot repair the problem; reducing
    the Multicall batch size cannot repair it either.

    The recognised messages describe the observed provider behaviours:

    - Geth-compatible nodes return ``missing trie node ... state ... is not
      available`` when the historical state trie was pruned.
    - dRPC has returned ``metadata is not found, <block>`` when its Arbitrum
      backend lacked rollup metadata for the requested state.
    - Alchemy has returned ``layer stale`` when its Arbitrum historical layer
      could not serve the requested block.

    A concrete production case occurred during the Enzyme historical backfill
    on 2026-08-20. At Arbitrum block ``368,237,833``, the configured
    ``arb-mainnet.g.alchemy.com`` endpoint returned JSON-RPC ``-32000`` with
    ``{"message": "layer stale"}``. Earlier attempts in the same run saw
    Goldsky return ``missing trie node`` and dRPC return
    ``metadata is not found``. All mean that the *RPC node*, rather than the
    Enzyme vault, is missing historical data.

    :py:class:`MultiprocessMulticallReader` automatically rotates the failed
    block through every configured fallback endpoint once, including wrapping
    from the final endpoint back to the first. Callers should catch
    :py:class:`MulticallHistoricalDataUnavailable` only after that complete
    round is exhausted, preserve their durable checkpoint, and obtain an
    archive-complete provider instead of silently creating a gap in the time
    series.

    :param error:
        Provider exception, multicall exception, or raw JSON-RPC error message.

    :return:
        ``True`` if the error means the selected node cannot provide the
        requested historical state and another provider may recover the scan.
    """

    message = str(error).lower()
    return any(clue in message for clue in HISTORICAL_STATE_UNAVAILABLE_MESSAGE_CLUES)


def is_multicall_gas_error(error: BaseException) -> bool:
    """Recognise the documented provider gas symptoms through transport wrappers.

    Only gas rejection warrants the isolated lane's reduced retry budget and
    optional unavailable result. Timeouts, rate limits and consensus failures
    retain normal transport recovery even when the target reads HyperCore.

    :param error: Transport exception or wrapper carrying the provider cause.
    :return: Whether the provider reports gas rejection, including Alchemy's
        ``BasicOutOfGas`` spelling. This does not identify the responsible target.
    """
    message = str(error.__cause__ or error).lower()
    return "out of gas" in message or "basicoutofgas" in message


class MulticallStateProblem(Exception):
    """Multicall returned structurally invalid data despite a successful RPC call.

    This exception is raised when a Multicall response is empty where the caller
    required a result. It differs from
    :py:class:`MulticallHistoricalDataUnavailable`: the provider did answer the
    historical request, but the returned response cannot be safely interpreted.
    Inspect the attached debug data, contract call and block before retrying.
    """


class MulticallRetryable(Exception):
    """Transient Multicall transport or payload failure.

    This exception is used for failures that can plausibly recover by retrying
    the *same historical block* with a smaller batch size, a different fallback
    endpoint, or after a short provider outage. Typical examples are HTTP
    timeouts, connection failures, request-rate errors and out-of-gas Multicall
    payloads caused by an expensive target contract.

    It deliberately does not represent missing historical state. For errors
    such as ``missing trie node``, ``metadata is not found`` and ``layer stale``,
    :py:class:`MulticallHistoricalDataUnavailable` is raised instead. Retrying
    those errors with a smaller batch wastes RPC capacity because the selected
    node lacks the data regardless of payload size.

    Callers may retry this error locally. When using a multi-provider RPC
    configuration, normal fallback rotation is also appropriate.
    """

    def __init__(self, message: str, status_code: int | None = None, headers: dict | None = None, completed_results: list[tuple[bool, bytes]] | None = None) -> None:
        """Retain transport diagnostics and the completed physical-request prefix.

        Reduced-batch recovery can fail after earlier fragments succeeded.
        The retry coordinator needs their outputs to resume at the first unread
        input instead of charging the provider again for completed work.

        :param message: Failure description from the transport wrapper.
        :param status_code: HTTP status when available, including rate limits.
        :param headers: Response headers used by provider retry policy.
        :param completed_results: Ordered ``(success, return_data)`` prefix.
        :return: None.
        """
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers
        #: Ordered ``(success, return_data)`` from physical chunks completed
        #: before this failure. Retries resume after these inputs, not at zero.
        self.completed_results = completed_results or []


class MulticallRetryExhausted(MulticallRetryable, RuntimeError):
    """Bounded physical-batch recovery failed; inspect the chained final cause."""


class MulticallNonRetryable(Exception):
    """Multicall failure which cannot be recovered by its generic retry loop.

    This base class covers malformed requests, deterministic contract failures
    and other errors for which reducing the Multicall batch size is not useful.
    Most instances need application-specific investigation. Historical
    node-retention failures are represented by the more specific
    :py:class:`MulticallHistoricalDataUnavailable` subclass, so a historical
    scanner can distinguish an unavailable archive endpoint from a bad vault
    call and rotate to another provider.
    """


class MulticallHistoricalDataUnavailable(MulticallNonRetryable):
    """The selected RPC node cannot execute a Multicall at a historical block.

    A node can return this exception even when it serves the latest block and
    ordinary contract calls correctly. It indicates that required historical
    state has been pruned, is missing from the provider's rollup metadata store,
    or is temporarily stale. The Multicall request itself remains valid.

    The exception is intentionally a :py:class:`MulticallNonRetryable` for the
    current provider: the internal Multicall retry loop must not repeatedly
    halve the batch and reissue a request for state the endpoint cannot supply.
    It is recoverable at the *caller* level. Catch this exception around a
    checkpointed historical scan, rotate a different archive RPC URL to the
    first position in the :py:class:`~eth_defi.provider.multi_provider.MultiProviderWeb3Factory`
    configuration, and rerun the affected chain. Preserve existing historical
    parquet rows until a replacement scan completes successfully.

    The error classification includes Geth's ``missing trie node``, dRPC's
    ``metadata is not found`` and Alchemy's ``layer stale`` messages. In the
    Enzyme Arbitrum backfill on 2026-08-20, Alchemy returned ``layer stale``
    (JSON-RPC ``-32000``) at block ``368,237,833``. This is a documented
    example of an RPC archive-data gap, not a vault-level failure.

    :param message:
        Complete Multicall diagnostic message, including chain, block, active
        provider and the original JSON-RPC response.

    :param status_code:
        HTTP status code when available.

    :param headers:
        Last provider response headers when available, retained for diagnosis.
    """

    def __init__(self, message: str, status_code: int | None = None, headers: dict | None = None, completed_results: list[tuple[bool, bytes]] | None = None) -> None:
        """Carry completed outputs across historical-state provider rotation.

        Archive failover must preserve successful earlier fragments while
        requesting only the unread suffix at the original historical block.
        An exhausted rotation remains a hard error rather than a contract revert.

        :param message: Provider's unavailable-state diagnosis.
        :param status_code: HTTP status when present.
        :param headers: Last response headers retained for diagnosis.
        :param completed_results: Ordered ``(success, return_data)`` prefix.
        :return: None.
        """
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers
        #: Ordered (success, return_data) prefix already read before the archive gap.
        self.completed_results = completed_results or []


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
        raise NotImplementedError("Please implement in a subclass")

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
        retry_exceptions: set[type[Exception]] | None = None,
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
            Mark an expected call failure, such as probing an optional Solidity
            method. This does not suppress the exception; it disables retries
            unless ``retry_exceptions`` explicitly allows one.

        :param attempts:
            Use built-in retry mechanism for flaky RPC.

            This works regardless of middleware installed.
            Set to zero to ignore.

            With :class:`FallbackProvider`, this caps its retries after the
            initial request instead of adding an outer retry loop.

        :param retry_exceptions:
            Transient exception classes to retry when ``ignore_error`` is set.
            Other exceptions, including Solidity reverts, are raised after one
            attempt. With :class:`FallbackProvider`, the allow-list also lets
            the provider switch endpoint for the selected failure.

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

        retry_exceptions = tuple(retry_exceptions or ())
        assert not retry_exceptions or ignore_error, "retry_exceptions requires ignore_error=True"
        fallback_provider = None
        if getattr(web3, "provider", None) is not None:
            try:
                fallback_provider = get_fallback_provider(web3)
            except AssertionError:
                pass
        fallback_retries = bool(retry_exceptions) and isinstance(fallback_provider, FallbackProvider)
        fallback_retry_attempts = attempts

        transaction = {
            "to": self.address,
            "from": from_,
            "data": self.data.hex(),
            "gas": gas,
            "ignore_error": ignore_error,  # Hint logging middleware that we should not care about if this fails
            "silent_error": silent_error,  # Hint logging middleware that we should not care about if this fails
        }
        attempt = 0

        if ignore_error and not retry_exceptions:
            attempts = 0
        elif fallback_retries:
            # FallbackProvider performs the selected retry and endpoint rotation.
            # Avoid repeating its entire backoff cycle in this outer loop.
            attempts = 0

        while True:
            try:
                context_token = None
                if fallback_retries:
                    context_token = fallback_provider.retry_configuration_context.set(
                        FallbackRetryConfiguration(
                            retry_exceptions=retry_exceptions,
                            retries=fallback_retry_attempts,
                            sleep=retry_sleep,
                        )
                    )
                try:
                    result = web3.eth.call(
                        transaction=transaction,
                        block_identifier=block_identifier,
                    )
                finally:
                    if context_token is not None:
                        fallback_provider.retry_configuration_context.reset(context_token)
                return result
            except Exception as e:
                msg = f"Call failed: {str(e)}\nBlock: {block_identifier}, chain: {web3.eth.chain_id}\nTransaction data:{pformat(transaction)}"
                if ignore_error:
                    retryable = isinstance(e, retry_exceptions)
                else:
                    retryable = is_retryable_http_exception(e, method="eth_call")

                if retryable and attempt < attempts:
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
        gas=15_000_000,
        ignore_error=False,
    ) -> "EncodedCallResult":
        """Perform RPC call and return the result as an :py:class:`EncodedCallResult`.

        - Performs an RPC call and returns a wrapped result in an :py:class:`EncodedCallResult`.

        See :py:meth:`call` for info.

        :param gas_limit:
            eth_call RPC gas limit.

            Set to 15M by default, assume to be safe on every chain.
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

    #: Provider-level unavailability, distinct from a served Solidity revert.
    #: Historical consumers must reject the entire affected vault observation
    #: before decoding or advancing state. Admission callers keep it unverified.
    unavailable_error: str | None = None

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

    #: Physical JSON-RPC calls made by this subprocess task.
    rpc_request_stats: RPCRequestStats | None = None


#: F**k EVM
WTF_RETRY_EXCEPTIONS_MESSAGE_CLUES = {
    m.lower()
    for m in (
        # On HyperEVM (chain 999) this is usually not a real EVM out-of-gas.
        # Vaults that read HyperCore through the read precompiles
        # (0x...0801 spot balance, 0x...0805 delegator summary, 0x...0809 L1
        # block number) get a punitive gas figure attributed to them by
        # goldsky and dRPC nodes, so a normal 40-call batch is rejected up
        # front with -32003 "out of gas: gas required exceeds: <cap>" for any
        # cap we send, while Alchemy executes the same batch in ~117k gas.
        # See docs/README-hyperevm-hypercore-read-gas.md and PR #1536.
        "out of gas",
        "evm timeout",
        "request timeout",
        "request timed out",
        "intrinsic gas too low",
        "intrinsic gas too high",
        "intrinsic gas too high",
        "incorrect response body",
        "exceeds block gas limit",
        "historical state",
        "state histories haven't been fully indexed yet",
        "Failed to call: InvalidTransaction",
        "failsafe timeout policy exceeded",
        # dRPC out of credit?
        "API key is not allowed to access blockchain",
        # eRPC consensus mode requires multiple upstream RPC providers to return
        # identical responses. When providers disagree (e.g. Hyperliquid nodes with
        # inconsistent HyperCore-oracle state or divergent revert serialisation),
        # eRPC returns this error. Classified retryable, but on HyperEVM the
        # disagreement is intermittent and pool-driven, so simply retrying the same
        # consensus endpoint may never converge — see the goldsky->Alchemy failover
        # in resolve_hyperevm_consensus_failover() and the full analysis in
        # docs/README-hyperevm-goldsky-failure.md. Keep this literal in sync with
        # ERPC_CONSENSUS_DISAGREEMENT_CLUE (defined below; cannot reference it here
        # as this set is built before it).
        "not enough agreement among responses",
    )
}


#: Chain id of Hyperliquid HyperEVM.
#:
#: Used to gate the goldsky eRPC consensus failover special case in the multicall
#: retry loop. See :py:func:`resolve_hyperevm_consensus_failover` and the full
#: write-up in ``docs/README-hyperevm-goldsky-failure.md``.
HYPEREVM_CHAIN_ID: Final[int] = 999


#: Marker string eRPC returns when its upstream nodes disagree in consensus mode.
#:
#: This is goldsky's eRPC ``-32603`` "not enough agreement among responses" error.
#: It is *not* a transient per-request glitch — on HyperEVM the upstream node pool
#: intermittently disagrees on `eth_call` results (live HyperCore oracle reads, and
#: revert serialisation past the ~128-block execution window), so retrying the same
#: consensus endpoint cannot resolve it. See ``docs/README-hyperevm-goldsky-failure.md``.
ERPC_CONSENSUS_DISAGREEMENT_CLUE: Final[str] = "not enough agreement among responses"


def resolve_hyperevm_consensus_failover(
    chain_id: int,
    provider: Any,
    exception: Exception,
) -> str | None:
    """Decide whether a failed HyperEVM multicall should fail over to a single node.

    On HyperEVM (chain 999) the scan's primary provider is goldsky's eRPC endpoint
    running in *consensus mode*: it fans each ``eth_call`` to several upstream nodes
    and only returns a result when enough of them agree byte-for-byte. For some
    vaults the upstreams intermittently disagree and eRPC returns
    :py:data:`ERPC_CONSENSUS_DISAGREEMENT_CLUE`. Retrying or randomly cycling back
    onto the same consensus endpoint is futile; a single (non-consensus) node such
    as Alchemy returns a usable answer immediately.

    This helper detects that exact situation — HyperEVM chain id, a consensus
    disagreement error, and a provider mix that contains *both* a goldsky and an
    Alchemy endpoint — and returns the provider host substring to pin retries to.

    See ``docs/README-hyperevm-goldsky-failure.md`` for the full failure analysis,
    the nodes involved, and the on-chain evidence.

    :param chain_id:
        Chain id of the multicall being retried.

    :param provider:
        The active web3 provider. Only :py:class:`FallbackProvider` mixes are
        eligible (we need an alternative single node to fail over to).

    :param exception:
        The :py:class:`MulticallRetryable` (or its cause) raised by the failed call.

    :return:
        Lower-case provider host substring (``"alchemy"``) to pin retries to, or
        ``None`` if this is not the HyperEVM goldsky consensus failure mode.
    """
    if chain_id != HYPEREVM_CHAIN_ID:
        return None

    if not isinstance(provider, FallbackProvider):
        return None

    if ERPC_CONSENSUS_DISAGREEMENT_CLUE not in str(exception).lower():
        return None

    # Only fail over when the mix actually contains the two relevant providers:
    # goldsky (the consensus endpoint that fails) and Alchemy (the single node we
    # pin to). If the mix is different, fall back to the normal random switch.
    names = [get_provider_name(p).lower() for p in provider.providers]
    has_goldsky = any("goldsky" in n for n in names)
    has_alchemy = any("alchemy" in n for n in names)
    if has_goldsky and has_alchemy:
        return "alchemy"

    return None


def pin_fallback_provider_by_host(fallback_provider: FallbackProvider, host_substring: str) -> bool:
    """Pin a :py:class:`FallbackProvider` to the provider whose host matches a substring.

    Unlike :py:meth:`FallbackProvider.switch_provider` (which cycles or randomises),
    this deterministically selects a specific upstream — used to force HyperEVM
    multicall retries onto the Alchemy single node, bypassing goldsky's eRPC
    consensus endpoint. See ``docs/README-hyperevm-goldsky-failure.md``.

    The switch goes through :py:meth:`FallbackProvider.switch_to_provider_index`, so
    the pinned provider is chain-id verified and rolled back if it is misconfigured
    or routing to the wrong chain — we never silently read from a bad endpoint.

    :param fallback_provider:
        The fallback provider to repoint.

    :param host_substring:
        Lower-case substring matched against :py:func:`get_provider_name` output.

    :return:
        ``True`` if a matching provider was found and successfully selected,
        ``False`` if no provider matched or the match failed chain-id verification
        (in which case the caller should resume normal provider switching).
    """
    host_substring = host_substring.lower()
    for idx, candidate in enumerate(fallback_provider.providers):
        if host_substring in get_provider_name(candidate).lower():
            if idx == fallback_provider.currently_active_provider:
                # Already pinned to this provider; it was chain-id verified when we
                # first switched to it, so there is nothing to do.
                return True
            try:
                fallback_provider.switch_to_provider_index(
                    idx,
                    log_level=logging.WARNING,
                    cause="HyperEVM goldsky eRPC consensus failover",
                )
            except ChainIdMismatch as e:
                # The pinned provider failed chain-id verification and was already
                # rolled back; let the caller fall back to normal switching.
                logger.warning(
                    "HyperEVM consensus failover to %r failed chain-id verification: %s; resuming normal provider switching",
                    host_substring,
                    e,
                )
                return False
            return True
    return False


class MultiprocessMulticallReader:
    """Reusable worker-local connection and retry policy for Multicall reads.

    Historical scans use process workers; chunked consumers can use threads.
    Each joblib worker creates its own instance for a chain/provider setup, so
    mutable connection and accounting state are not shared between workers.
    Calls before Multicall deployment yield no results; see
    :py:func:`get_multicall_block_number` when selecting a historical range.
    """

    def __init__(
        self,
        web3factory: Web3Factory | Web3,
        batch_size: int = 40,
        backswitch_threshold: int = 100,
        rate_limit_sleep: float = 61.0,
        rpc_request_stats: RPCRequestStats | None = None,
        greylist_batch_size: int = 1,
        greylist: frozenset[HexAddress] = frozenset(),
    ) -> None:
        """Create a reader inside its owning thread or process.

        The factory keeps connections local while allowing the phase to provide
        request accounting from the very first provider-verification request.

        :param web3factory:
            Connection factory, or an already worker-owned Web3 connection.

        :param batch_size:
            How many calls we pack into the multicall.

            Existing chain limits may reduce this for provider constraints.

        :param greylist:
            Caller-selected targets for this chain, matched case-insensitively.
            Defaults to empty; the entrypoint selects chain policy.
        :param greylist_batch_size:
            Positive maximum encoded subcalls per isolated target request,
            default one. Applies only to the supplied greylist.
            Normal calls keep their configured batch size; no environment
            setting or additional provider connection is needed.
        :param backswitch_threshold: Completed tasks before retrying the primary.
        :param rate_limit_sleep: Seconds to wait after an HTTP 429 response.
        :param rpc_request_stats: Physical request accumulator for this worker.
        :return: None.
        """
        if isinstance(web3factory, Web3):
            # Directly passed
            self.web3 = web3factory
        elif isinstance(web3factory, MultiProviderWeb3Factory):
            # Account for provider verification requests made while the
            # process-local cached Web3 is first constructed.
            self.web3 = web3factory(rpc_request_stats=rpc_request_stats)
        else:
            # Construct new RPC connection in every subprocess
            self.web3 = web3factory()

        self.chain_id = self.web3.eth.chain_id
        name = get_provider_name(self.web3.provider)

        logger.info(
            "Initialising multiprocess multicall handler, process %s, thread %s, provider %s",
            os.getpid(),
            threading.current_thread(),
            name,
        )
        assert batch_size > 0 and greylist_batch_size > 0
        self.greylist_batch_size = greylist_batch_size
        # Snapshot caller policy: worker reuse must never follow a mutable list
        # or implicitly acquire another chain's address-specific exceptions.
        self.greylist = frozenset(HexAddress(address.lower()) for address in greylist)
        self.batch_size = batch_size

        # How many calls we have done in this subprocess
        self.calls = 0

        #: How many calls ago we switched the fallback provider.
        self.last_switch = 0

        #: Try to switch back from the fallback provider to the main provider after this many calls.
        self.backswitch_threshold = backswitch_threshold

        self.rate_limit_sleep = rate_limit_sleep

    def __repr__(self) -> str:
        return f"<MultiprocessMulticallReader process: {os.getpid()}, thread: {threading.current_thread()}, chain: {self.chain_id}>"

    def fetch_block_timestamp(self, block_number: int) -> datetime.datetime:
        """Fetch one timestamp for callers without a supplied Hypersync timestamp.

        The worker fallback is for isolated reads. Historical scans normally
        prefetch timestamps through the dense cache and pass them into tasks.

        :param block_number: Exact source block for the fallback RPC read.
        :return: Naive UTC timestamp from the block header.
        """
        return get_block_timestamp(self.web3, block_number)

    def get_gas_hint(self, chain_id: int) -> int | None:
        """Choose an explicit gas allowance for chains with unusual execution limits.

        Mantle requires a larger allowance than the provider's default for
        aggregate reads. See the `provider gas-limit documentation
        <https://docs.alchemy.com/reference/gas-limits-for-eth_call-and-eth_estimategas>`__.

        :param chain_id: Already verified chain ID; this performs no network read.
        :return: Explicit transaction gas, or None to use the provider default.
        """

        if chain_id == 5000:
            # Allowance established for Mantle aggregate reads at block 61298003.
            # Address 0xca11bde05977b3631167028862be2a173976ca11
            return 9_999_000_000_000
        return None

    def get_batch_size(self, chain_id: int) -> int:
        """Apply normal-lane chain limits without shrinking unrelated greylist work.

        Mantle and Gnosis retain the existing conservative limit for provider
        execution constraints. All other chains use the configured normal limit;
        reviewed HyperEVM targets have a separate planner limit.

        :param chain_id: Already verified chain ID; this performs no network read.
        :return: Positive maximum encoded subcalls per normal request.
        """

        return 16 if chain_id in (5000, 100) else self.batch_size

    def fetch_multicall_with_batch_size(
        self,
        multicall_contract: Contract,
        block_identifier: BlockIdentifier,
        batch_size: int,
        encoded_calls: list[tuple[HexAddress, bytes]],
        require_multicall_result: bool,
    ) -> list[tuple[bool, bytes]]:
        """Fetch ordered subcall outputs, retaining completed chunks on failure.

        Recovery may reduce a logical request into several physical requests.
        Failed transport exceptions carry the completed prefix so the coordinator
        can resume without replaying paid work. Served subcall reverts remain
        ordinary outputs, as required by `Multicall3's aggregate interface
        <https://github.com/mds1/multicall>`__.

        :param multicall_contract: Bound Multicall3 contract used for execution.
        :param block_identifier: Source block for all calls in this request.
        :param batch_size: Maximum encoded subcalls per physical request.
        :param encoded_calls:
            Ordered ``(target_address, encoded_calldata)`` tuples. Addresses
            identify the target contracts; calldata includes each ABI selector
            and encoded arguments as bytes.
        :param require_multicall_result: Whether to reject empty return bytes from any subcall.
        :return:
            Ordered ``(success, return_data)`` tuples, one per input subcall.
            success is a boolean; return_data is raw bytes to decode on success
            or inspect as revert data on failure. Order is preserved across chunks.
        """
        calls_results = []
        chain_id = self.chain_id

        for i in range(0, len(encoded_calls), batch_size):
            batch_calls = encoded_calls[i : i + batch_size]
            # Fix Mantle out of gas
            gas = self.get_gas_hint(chain_id)

            for address, data in batch_calls:
                assert address.lower() not in BROKEN_VAULT_CONTRACTS, f"Contract {address} is broken, cannot call multicall on it."

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

                if hasattr(e, "response") and e.response is not None:
                    status_code = e.response.status_code
                else:
                    status_code = None

                parsed_error = str(e)

                displayed_addresses = list(set(addresses))

                error_msg = (
                    f"Multicall failed for chain {chain_id}\n"
                    # Ruff
                    f"Block {block_identifier}, batch size: {len(batch_calls)}: {e}.\n"
                    f"Using provider: {self.web3.provider.__class__}: {name}\n"
                    f"Exception: {e.__class__}: {parsed_error} \n"
                    f"HTTP status code: {status_code.__class__}: {status_code}\n"
                    f"HTTP reply headers: {pformat(headers)}\n"
                    f"To simulate:\n"
                    f"{debug_data}\n"
                    f"Addresses: {displayed_addresses[0:15]}... total {len(displayed_addresses)}\n"
                )

                isolated_gas = all(address.lower() in self.greylist for address, _data in batch_calls) and is_multicall_gas_error(e)
                if isolated_gas:
                    logger.warning("Greylisted Multicall gas rejection: chain=%d block=%s provider=%s target=%s selectors=%s", chain_id, block_identifier, name, batch_calls[0][0], [data[:4].hex() for _address, data in batch_calls])
                else:
                    # A rejected aggregate does not identify which member caused
                    # it. Keep retry logs concise and retain full replay details
                    # in the exception/debug output for a final failure analysis.
                    logger.warning("Rejected Multicall batch: chain=%d block=%s provider=%s subcalls=%d targets=%s error=%s", chain_id, block_identifier, name, len(batch_calls), sorted(set(addresses))[:8], parsed_error)
                    logger.debug("Multicall replay diagnostics: %s", error_msg)

                # Check for upstream RPC being broken issues
                parsed_error = parsed_error.lower()
                if is_historical_state_unavailable_error(parsed_error):
                    raise MulticallHistoricalDataUnavailable(error_msg, status_code=status_code, headers=headers, completed_results=calls_results) from e
                wtf_error = is_multicall_gas_error(e) or any(clue in parsed_error for clue in WTF_RETRY_EXCEPTIONS_MESSAGE_CLUES)

                if wtf_error or isinstance(e, ProbablyNodeHasNoBlock) or isinstance(e, (ReadTimeout, RemoteDisconnected, ConnectionError)) or (isinstance(e, HTTPError) and e.response.status_code >= 400):
                    raise MulticallRetryable(error_msg, status_code=status_code, headers=headers, completed_results=calls_results) from e
                else:
                    raise MulticallNonRetryable(error_msg) from e

            if len(batch_results) != len(batch_calls):
                raise MulticallStateProblem("Multicall response length does not match the requested physical batch")

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
                        raise MulticallStateProblem(f"Multicall gave empty result: at block {block_identifier} at chain {self.chain_id}.\nDebug data is:\n{debug_str}\nRPC is: {rpc_name}\nBatch result: {batch_results}\nBatch calls: {batch_calls}\nReceived block number: {received_block_number}\nResponse headers: {pformat(last_headers)}\nLive multicall readers are: {pformat(readers)}")

            calls_results += batch_results

        return calls_results

    def fetch_multicall_from_alternate_archives(
        self,
        *,
        block_identifier: BlockIdentifier,
        batch_size: int,
        encoded_calls: list[tuple[HexAddress, bytes]],
        require_multicall_result: bool,
        error: MulticallHistoricalDataUnavailable,
    ) -> list[tuple[bool, bytes]]:
        """Retry one failed historical block through every other configured RPC.

        A :py:class:`MulticallHistoricalDataUnavailable` means that the current
        endpoint cannot execute ``eth_call`` at ``block_identifier``. It is not
        a payload-size or target-contract problem, so reducing the batch size or
        retrying the same endpoint does not help. Instead, this method performs
        exactly one deterministic provider-rotation round for this *one block*.

        The endpoint that produced ``error`` has already been tried. Each other
        member of the active :py:class:`FallbackProvider` is selected once in
        cyclic order, retaining the original Multicall batch and call set. Thus,
        with three endpoints where endpoint 3 failed, the retry order is
        endpoint 1 followed by endpoint 2. A failure on endpoint 1 tries
        endpoints 2 and 3. This avoids discarding an entire historical scan just
        because a single provider has a state-retention gap at one block.

        The Enzyme Arbitrum backfill exposed why this is needed: at block
        ``421,460,233`` on 2026-08-20, the final configured Alchemy endpoint
        returned JSON-RPC ``-32000`` / ``layer stale``. Before this recovery
        path, a final-provider error stopped the full chain attempt without
        revisiting the first two endpoints for that failed block. This method
        ensures the caller gives those endpoints one opportunity to serve it.

        A successful retry leaves its provider active for subsequent blocks.
        If every endpoint returns missing historical data, the final specialised
        exception is re-raised to the outer checkpointed scan. The caller should
        preserve existing parquet data and obtain an archive-complete RPC
        provider before retrying. Never convert unavailable historical state
        into a zero-valued result.

        :param block_identifier:
            Historical block whose Multicall request failed.

        :param batch_size:
            Original Multicall batch size. It is deliberately not reduced for
            archive-state errors.

        :param encoded_calls:
            Exact ``(target_address, encoded_calldata)`` tuples which failed at
            this block, preserving their order for result-to-call attribution.

        :param require_multicall_result:
            Whether to reject an empty Multicall result, passed through unchanged.

        :param error:
            Historical-data exception raised by the endpoint that has already
            been tried.

        :return:
            ``(success, return_data)`` tuples from one of the other endpoints,
            in input order. success describes each target subcall, so a served
            batch may still contain contract reverts; return_data is raw bytes.

        :raises MulticallHistoricalDataUnavailable:
            If all configured endpoints fail this block with unavailable
            historical state, or the reader has no alternate fallback endpoint.
        """

        provider = self.web3.provider
        if not isinstance(provider, FallbackProvider) or len(provider.providers) < 2:
            raise error

        failed_provider_index = provider.currently_active_provider
        last_error = error
        completed = list(error.completed_results)
        remaining = encoded_calls[len(completed) :]
        attempted_provider_names = [get_provider_name(provider.get_active_provider())]

        for rotation_offset in range(1, len(provider.providers)):
            provider_index = (failed_provider_index + rotation_offset) % len(provider.providers)
            try:
                provider.switch_to_provider_index(
                    provider_index,
                    log_level=logging.WARNING,
                    cause=f"Historical state unavailable at block {block_identifier}: {last_error}",
                )
            except ChainIdMismatch as switch_error:
                logger.warning(
                    "Could not rotate historical multicall at chain %d, block %s to provider %d: %s",
                    self.chain_id,
                    block_identifier,
                    provider_index,
                    switch_error,
                )
                continue

            active_provider = provider.get_active_provider()
            active_provider_name = get_provider_name(active_provider)
            attempted_provider_names.append(active_provider_name)
            multicall_contract = get_multicall_contract(self.web3, block_identifier=block_identifier)

            try:
                recovered = self.fetch_multicall_with_batch_size(
                    multicall_contract,
                    block_identifier=block_identifier,
                    batch_size=batch_size,
                    encoded_calls=remaining,
                    require_multicall_result=require_multicall_result,
                )
                return completed + recovered
            except MulticallRetryable as retry_error:
                # Alternate archives can serve state yet reject the gas payload.
                # Let bounded transport recovery continue with only unread calls.
                retry_error.completed_results = completed + retry_error.completed_results
                raise
            except MulticallHistoricalDataUnavailable as retry_error:
                completed.extend(retry_error.completed_results)
                remaining = remaining[len(retry_error.completed_results) :]
                retry_error.completed_results = list(completed)
                last_error = retry_error
                logger.warning(
                    "Historical multicall state unavailable at chain %d, block %s from provider %s; continuing provider rotation (%d/%d)",
                    self.chain_id,
                    block_identifier,
                    active_provider_name,
                    rotation_offset + 1,
                    len(provider.providers),
                )

        logger.warning(
            "Historical multicall provider rotation exhausted at chain %d, block %s; attempted providers: %s",
            self.chain_id,
            block_identifier,
            attempted_provider_names,
        )
        raise last_error

    def fetch_multicall_batch_with_retries(
        self,
        multicall_contract: Contract,
        block_identifier: BlockIdentifier,
        batch_size: int,
        encoded_calls: list[tuple[HexAddress, bytes]],
        require_multicall_result: bool,
        min_fallback_retries: int,
        greylisted: bool = False,
    ) -> list[tuple[bool, bytes]]:
        """Recover one physical batch without replaying preceding successes.

        The planner owns the lane and payload; recovery keeps existing archive
        rotation and consensus handling. Reduced retries carry completed outputs
        forward so a later failing fragment cannot replay an earlier success.
        Greylisted gas failures have a smaller bounded recovery budget; other
        transient failures retain normal backoff and retry limits.

        :param multicall_contract: Worker-owned Multicall3 binding.
        :param block_identifier: Exact source block; never replaced with latest.
        :param batch_size: Maximum encoded subcalls in this lane.
        :param encoded_calls: Ordered ``(target_address, encoded_calldata)`` inputs.
        :param require_multicall_result: Strict empty-response validation.
        :param min_fallback_retries: Minimum transport recovery budget. Only
            isolated gas failures use the smaller alternate-provider cap.
        :param greylisted: Whether this request belongs to the isolated lane.
        :return: Ordered ``(success, return_data)`` subcall results.
        """
        assert min_fallback_retries > 0
        chain_id = self.chain_id

        def fetch_with_archive_rotation(limit: int, pending: list[tuple[HexAddress, bytes]]) -> list[tuple[bool, bytes]]:
            """Serve an unread suffix, rotating archive gaps before gas recovery.

            Initial and reduced attempts share this rule: missing historical
            state is independent of payload size. A different provider can then
            fail on gas, in which case its completed prefix reaches the outer
            retry coordinator through the original transport exception.

            :param limit: Physical request limit for this attempt.
            :param pending: Ordered ``(target_address, encoded_calldata)`` suffix.
            :return: Ordered ``(success, return_data)`` outputs for that suffix.
            """
            try:
                return self.fetch_multicall_with_batch_size(multicall_contract, block_identifier, limit, pending, require_multicall_result)
            except MulticallHistoricalDataUnavailable as error:
                return self.fetch_multicall_from_alternate_archives(
                    block_identifier=block_identifier,
                    batch_size=limit,
                    encoded_calls=pending,
                    require_multicall_result=require_multicall_result,
                    error=error,
                )

        try:
            return fetch_with_archive_rotation(batch_size, encoded_calls)
        except MulticallRetryable as error:
            last_error = error

        completed = list(last_error.completed_results)
        remaining = encoded_calls[len(completed) :]
        provider = self.web3.provider
        fallback = provider if isinstance(provider, FallbackProvider) else None
        normal_retries = max(min_fallback_retries, len(fallback.providers) + 2) if fallback else min_fallback_retries
        gas_retries = min(2, len(fallback.providers) - 1) if fallback else 0
        reduced_size = max(batch_size // 3, 1)
        if not greylisted:
            self.last_switch = self.calls
        for attempt in range(normal_retries):
            # Generic transport failures still receive the established bounded
            # backoff. Isolated deterministic gas rejections rotate immediately;
            # waiting cannot change the provider's precompile accounting.
            gas_failure = is_multicall_gas_error(last_error)
            # Evaluate the current cause: a timeout can recover on a provider
            # that then rejects gas, or a gas rejection can become a timeout.
            # A sole provider receives no repeated isolated gas probe; genuine
            # transient outages keep the standard retry/backoff budget.
            retry_limit = gas_retries if greylisted and gas_failure else normal_retries
            if attempt >= retry_limit:
                break
            if not (greylisted and gas_failure):
                time.sleep(self.rate_limit_sleep if last_error.status_code == 429 else 2.0 * (attempt + 1))
            if fallback:
                consensus_host = resolve_hyperevm_consensus_failover(chain_id, fallback, last_error)
                if not (consensus_host and pin_fallback_provider_by_host(fallback, consensus_host)):
                    fallback.switch_provider(log_level=logging.WARNING, randomise=not greylisted, cause="Retry failed physical Multicall batch")
            logger.warning("Retrying Multicall lane=%s chain=%d block=%s attempt=%d/%d remaining_subcalls=%d limit=%d provider=%s", "greylist" if greylisted else "regular", chain_id, block_identifier, attempt + 1, retry_limit, len(remaining), reduced_size, get_provider_name(provider))
            try:
                recovered = fetch_with_archive_rotation(reduced_size, remaining)
                # Returning is the success exit. Previously the fallback loop
                # replayed already successful calls on all remaining providers.
                return completed + recovered
            except MulticallRetryable as error:
                # A reduced retry can itself contain multiple physical chunks.
                # Resume after its completed prefix to avoid replaying successful
                # pieces when a later fragment needs another provider or split.
                completed.extend(error.completed_results)
                remaining = remaining[len(error.completed_results) :]
                last_error = error
                reduced_size = max(reduced_size // 2, 1)
        raise MulticallRetryExhausted("Multicall physical-batch retries exhausted", status_code=last_error.status_code) from last_error

    def process_calls(
        self,
        block_identifier: BlockIdentifier,
        calls: list[EncodedCall],
        require_multicall_result: bool = False,
        timestamp: datetime.datetime | None = None,
        min_fallback_retries: int = 5,
        allow_greylist_unavailable: bool = False,
    ) -> Iterator[EncodedCallResult]:
        """Fetch normal calls before isolated calls and restore original result order.

        Chunked consumers and historical workers use this same routing policy.
        Completed physical batches are never replayed because a later target
        fails. Transport failures remain strict unless the caller can preserve
        unavailable observations separately from served Solidity reverts.

        :param require_multicall_result:
            Reject empty return bytes, for diagnostics requiring complete replies.

        :param calls:
            Ordered encoded inputs; duplicate targets retain separate positions.

        :param block_identifier:
            Source block or Web3 block tag; never substituted during recovery.

        :param timestamp:
            Optional prefetched naive UTC source timestamp.

        :param min_fallback_retries:
            Normal minimum bounded retries; isolated gas failures use at most two.
        :param allow_greylist_unavailable:
            Opt in only for consumers that distinguish provider unavailability
            and preserve saved source data. Metadata, feature and token-cache
            callers keep strict errors rather than recording absent methods.
        :return: Results in original input order with exact block attribution.
        """

        assert isinstance(calls, list)
        assert all(isinstance(c, EncodedCall) for c in calls), f"Got: {calls}"

        for c in calls:
            assert c.address.lower() not in BROKEN_VAULT_CONTRACTS, f"Contract {c.address} is blacklisted due to known multicall issues. Remove it from the call list."

        # First-seen bounds prevent historical calls to contracts not deployed
        # yet. Partition once so result positions match only eligible inputs.
        filtered_in_calls = []
        filtered_out_calls = []
        for call in calls:
            (filtered_in_calls if call.is_valid_for_block(block_identifier) else filtered_out_calls).append(call)
        encoded_calls = [(Web3.to_checksum_address(c.address), c.data) for c in filtered_in_calls]

        start = native_datetime_utc_now()

        if len(filtered_out_calls) > 0:
            filtered_out_call_block = f"{filtered_out_calls[0].first_block_number:,}"
        else:
            filtered_out_call_block = "-"

        block_identifier_str = f"{block_identifier:,}" if type(block_identifier) == int else str(block_identifier)
        logger.info(
            "Performing multicall, %d calls included, %d calls excluded, block is %s, example filtered out block number is %s",
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
            block_number = get_multicall_block_number(self.chain_id)
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
        chain_id = self.chain_id
        batch_size = self.get_batch_size(chain_id)

        # Key: original eligible input index. Value: (success, raw return bytes).
        outputs: dict[int, tuple[bool, bytes]] = {}
        unavailable: dict[int, str] = {}
        provider = self.web3.provider
        initial_provider = None
        try:
            for greylisted, indexes in plan_multicall_batches(encoded_calls, batch_size, self.greylist_batch_size, self.greylist):
                batch_calls = [encoded_calls[index] for index in indexes]
                limit = self.greylist_batch_size if greylisted else batch_size
                # Small-lane failover must not steer subsequent robust tasks to an
                # expensive backup endpoint merely because one target reads Core.
                provider = self.web3.provider
                if greylisted and initial_provider is None and isinstance(provider, FallbackProvider):
                    initial_provider = provider.currently_active_provider
                logger.info("Multicall batch lane=%s chain=%d block=%s subcalls=%d limit=%d", "greylist" if greylisted else "regular", chain_id, block_identifier, len(indexes), limit)
                # Provider instrumentation remains authoritative: this scope labels
                # actual attempts, including retries and provider verification, without
                # multiplying totals or mutating another thread's operation label.
                stats = getattr(provider, "rpc_request_stats", None)
                scope = stats.operation_scope(stats.operation + "_greylist") if greylisted and stats is not None else nullcontext()
                with scope:
                    try:
                        batch_outputs = self.fetch_multicall_batch_with_retries(
                            multicall_contract,
                            block_identifier,
                            limit,
                            batch_calls,
                            require_multicall_result,
                            min_fallback_retries,
                            greylisted,
                        )
                    except MulticallRetryExhausted as error:
                        # Only documented target-local gas failures can become missing
                        # observations. Rate limits, archive gaps and malformed replies
                        # remain hard errors so outages cannot silently damage coverage.
                        if not allow_greylist_unavailable or not greylisted or not is_multicall_gas_error(error):
                            raise
                        reason = "Greylisted Multicall gas accounting unavailable"
                        logger.warning("%s: chain=%d block=%s target=%s selectors=%s", reason, chain_id, block_identifier, batch_calls[0][0], [data[:4].hex() for _address, data in batch_calls])
                        for index in indexes:
                            unavailable[index] = reason
                        batch_outputs = [(False, b"")] * len(indexes)
                assert len(batch_outputs) == len(indexes)
                outputs.update(zip(indexes, batch_outputs))
        finally:
            # Restore once for the whole isolated lane. Its subcalls can reuse
            # a working backup instead of paying failed primary probes repeatedly.
            # A verification failure leaves the healthy backup active and must
            # not mask successfully read data or the original scan exception.
            if initial_provider is not None and provider.currently_active_provider != initial_provider:
                stats = getattr(provider, "rpc_request_stats", None)
                scope = stats.operation_scope(stats.operation + "_greylist") if stats is not None else nullcontext()
                with scope:
                    try:
                        provider.switch_to_provider_index(initial_provider, cause="Restore regular Multicall provider after isolated greylist lane")
                    except ChainIdMismatch:
                        logger.warning("Could not restore prior normal-lane Multicall provider after isolated lane; retaining verified fallback")
        calls_results = [outputs[index] for index in range(len(encoded_calls))]

        self.calls += 1

        # Calculate byte size of output
        out_size = sum(len(o[1]) for o in calls_results)

        # Check we are internally coherent
        assert len(filtered_in_calls) == len(calls_results), f"Calls: {len(filtered_in_calls)}, results: {len(calls_results)}"
        assert len(encoded_calls) == len(calls_results), f"Calls: {len(encoded_calls)}, results: {len(calls_results)}"

        # Build EncodedCallResult() objects out of incoming results
        for index, (call, output_tuple) in enumerate(zip(filtered_in_calls, calls_results)):
            yield EncodedCallResult(
                call=call,
                success=output_tuple[0],
                result=output_tuple[1],
                unavailable_error=unavailable.get(index),
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
    max_workers: int = 8,
    timeout: float = 1800,
    display_progress: bool | str = True,
    progress_suffix: Callable | None = None,
    require_multicall_result: bool = False,
    hypersync_client: "HypersyncClient | None" = None,
    timestamp_cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
    rpc_request_stats: RPCRequestStats | None = None,
    greylist_batch_size: int = 1,
    allow_greylist_unavailable: bool = False,
    greylist: frozenset[HexAddress] = frozenset(),
) -> Iterator[CombinedEncodedCallResult]:
    """Fetch stateless historical samples with process-local Multicall workers.

    Each sampled block becomes an ordered joblib task. Connections are reused
    inside workers; timestamps can be supplied from the shared Hypersync cache
    to avoid one header RPC per task. Isolation follows the caller-supplied target list
    without altering the requested source block.

    :param greylist:
        Caller-selected target addresses for this chain, matched case-insensitively.
        Defaults to empty: readers never import or select chain-specific policies.
    :param greylist_batch_size:
        Positive maximum encoded subcalls per supplied greylisted target request,
        default one. Normal targets retain standard batching. This counts
        subcalls rather than vaults and never combines different isolated targets.

    :param allow_greylist_unavailable:
        Opt in to explicit unavailable results only when the consumer preserves
        old source observations. Defaults to strict transport errors. The vault
        price exporter opts in; arbitrary historical callers remain strict.

    :param chain_id:
        Which chain we are targeting with calls.

    :param calls: Encoded contract calls reused at each sampled block.
    :param max_workers: Maximum joblib process workers, default eight.
    :param timestamp_cache_file: Dense per-chain Hypersync timestamp cache folder.
    :param rpc_request_stats: Optional accumulator for physical worker attempts.

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

    :param hypersync_client:
        Optional HyperSync client used to fetch the sampled block timestamps
        through the shared cache. This keeps timestamp reads out of the
        archive JSON-RPC workers.
    :return: Combined subcall results in sampled block order.
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

    timestamps = None
    if hypersync_client is not None:
        # Prefetch timestamps once through the cache-aware HyperSync reader.
        #
        # Do not let each multicall worker make its own
        # ``eth_getBlockByNumber`` call when a HyperSync client is available.
        # Callers without HyperSync retain the existing inline RPC timestamp
        # lookup behaviour.
        timestamps = fetch_block_timestamps_multiprocess_auto_backend(
            chain_id=chain_id,
            web3factory=web3factory,
            start_block=start_block,
            end_block=end_block,
            step=step,
            max_workers=max_workers,
            timeout=timeout,
            display_progress=display_progress,
            hypersync_client=hypersync_client,
            cache_path=timestamp_cache_file,
            rpc_request_stats=rpc_request_stats,
        )

        timestamp_end_block = timestamps.get_last_block()
        if timestamp_end_block < end_block:
            logger.warning("Clipping end block by timestamps cache end block %d < %d", timestamp_end_block, end_block)
            # ``end_block`` is exclusive in the task range below.
            end_block = timestamp_end_block + 1

    def _task_gen() -> Iterable[MulticallHistoricalTask]:
        for block_number in range(start_block, end_block, step):
            task = MulticallHistoricalTask(
                chain_id,
                web3factory,
                block_number,
                calls_pickle_friendly,
                timestamp=timestamps[block_number] if timestamps is not None else None,
                require_multicall_result=require_multicall_result,
                greylist_batch_size=greylist_batch_size,
                greylist=greylist,
                allow_greylist_unavailable=allow_greylist_unavailable,
                collect_rpc_request_stats=rpc_request_stats is not None,
            )
            logger.debug(
                "Created task for block %d with %d calls",
                block_number,
                len(calls_pickle_friendly),
            )
            yield task

    completed_task_count = 0

    try:
        for completed_task in worker_processor(delayed(_execute_multicall_in_worker)(task) for task in _task_gen()):
            if rpc_request_stats is not None and completed_task.rpc_request_stats is not None:
                rpc_request_stats.merge(completed_task.rpc_request_stats)
            completed_task_count += 1
            if progress_bar:
                progress_bar.update(1)

                if progress_suffix is not None:
                    suffixes = progress_suffix()
                    progress_bar.set_postfix(suffixes)

            yield completed_task

        logger.info("Completed %d historical reading tasks", completed_task_count)
    finally:
        if progress_bar:
            progress_bar.close()

        timestamp_cache_close = getattr(timestamps, "close", None)
        if callable(timestamp_cache_close):
            timestamp_cache_close()


def read_multicall_historical_stateful(
    chain_id: int,
    web3factory: Web3Factory,
    calls: dict[EncodedCall, BatchCallState],
    start_block: int,
    end_block: int,
    step: int,
    max_workers: int = 8,
    timeout: float = 1800,
    display_progress: bool | str = True,
    progress_suffix: Callable | None = None,
    require_multicall_result: bool = False,
    chunk_size: int = 48,
    hypersync_client: "HypersyncClient | None" = None,
    timestamp_cache_file: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
    rpc_request_stats: RPCRequestStats | None = None,
    greylist_batch_size: int = 1,
    allow_greylist_unavailable: bool = False,
    greylist: frozenset[HexAddress] = frozenset(),
) -> Iterator[CombinedEncodedCallResult]:
    """Fetch historical samples with adaptive scheduling and bounded state feedback.

    Each call's reader state chooses its sampling frequency. Process workers
    handle a chunk of selected blocks, then results reach the caller before the
    next chunk is scheduled. This balances parallel reads against the need to
    update TVL, errors and polling frequency from completed source observations.

    :param greylist:
        Caller-selected target addresses for this chain, matched case-insensitively.
        Defaults to empty: readers never import or select chain-specific policies.
    :param greylist_batch_size:
        Positive maximum encoded subcalls per supplied greylisted target request,
        default one. Normal targets retain standard batching. This counts
        subcalls rather than vaults and never combines different isolated targets.

    :param allow_greylist_unavailable:
        Opt in only for consumers preserving unavailable source observations.
        Generic historical callers retain strict transport errors by default;
        the vault price exporter explicitly enables deferral.

    :param chain_id: Already verified target chain ID.
    :param web3factory: Worker-local provider connection factory.
    :param calls: Encoded call to adaptive reader-state mapping.
    :param start_block: Inclusive first sampled block.
    :param end_block: Exclusive scan boundary.
    :param step: Positive block interval between samples.
    :param max_workers: Maximum joblib process workers, default eight.
    :param timeout: Seconds before an individual joblib task times out.
    :param display_progress: Whether to show progress, or a custom label.
    :param progress_suffix: Optional progress metrics callback.
    :param require_multicall_result: Reject empty return data for diagnostics.
    :param hypersync_client: Hypersync client for cached sampled timestamps.
    :param timestamp_cache_file: Dense per-chain timestamp cache folder.
    :param rpc_request_stats: Optional physical request accumulator.
    :return: Combined samples with their adaptive states reattached.

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

    assert all(s is not None for s in calls.values()), "States missing for some calls"

    # Significant speedup by prefetcing timestamps
    timestamps = fetch_block_timestamps_multiprocess_auto_backend(
        chain_id=chain_id,
        web3factory=web3factory,
        start_block=start_block,
        end_block=end_block,
        step=step,
        max_workers=max_workers,
        timeout=timeout,
        display_progress=display_progress,
        hypersync_client=hypersync_client,
        cache_path=timestamp_cache_file,
        rpc_request_stats=rpc_request_stats,
    )

    chunk = []

    def _flush_chunk(chunk: list[MulticallHistoricalTask]) -> Iterable[CombinedEncodedCallResult]:
        # Pass all buffered calls to sub-multiprocesses for JSON-RPC fetching
        combined_result: CombinedEncodedCallResult

        if len(chunk) == 0:
            return

        for combined_result in worker_processor(delayed(_execute_multicall_in_worker)(task) for task in chunk):
            if rpc_request_stats is not None and combined_result.rpc_request_stats is not None:
                rpc_request_stats.merge(combined_result.rpc_request_stats)
            for r in combined_result.results:
                # Retrofit states to the result objects
                assert r.timestamp, f"Got bad result: {r}"
                state = calls[r.call]
                assert state is not None
                r.state = state
            yield combined_result

    last_block = start_block
    total_accepted_calls = total_blocks = 0
    timestamp_end_block = timestamps.get_last_block()
    if timestamp_end_block < end_block:
        logger.warning("Clipping end block by timestamps cache end block %d < %d", timestamp_end_block, end_block)
        # +1 because end_block is used in range(start, end_block, step) which is exclusive
        end_block = timestamp_end_block + 1

    logger.info("Starting the main historical state read loop from block %d to %d", start_block, end_block)

    metrics = {}
    for block_number in range(start_block, end_block, step):
        # Map prefetch timestamp
        timestamp = timestamps[block_number]

        accepted_calls = [c for c, state in calls.items() if state.should_invoke(c, block_number, timestamp)]

        total_blocks += 1
        total_accepted_calls += len(accepted_calls)

        # These counts describe scheduled subcall states, not distinct vaults:
        # several methods can share one adaptive reader state.
        metrics = Counter()
        for state in calls.values():
            freq_type = state.vault_poll_frequency or "unknown"
            if state.unsupported_token:
                metrics["unsupported_token"] += 1
            metrics[freq_type] += 1
            metrics["total"] += 1

        logger.debug("Scheduling block=%d timestamp=%s available_subcalls=%d accepted_subcalls=%d call_state_metrics=%s", block_number, timestamp, len(all_calls), len(accepted_calls), dict(metrics))

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
            greylist_batch_size=greylist_batch_size,
            greylist=greylist,
            allow_greylist_unavailable=allow_greylist_unavailable,
            collect_rpc_request_stats=rpc_request_stats is not None,
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
                logger.debug("Updating states for timestamp=%s block=%d", combined_result.timestamp, combined_result.block_number)
                yield combined_result

            chunk = []

    logger.info(
        "Total blocks %d, total accepted calls over the period: %d",
        total_blocks,
        total_accepted_calls,
    )

    logger.info("Last scheduled subcall-state counts: %s", dict(metrics))

    # Process the remaning uneven chunk
    yield from _flush_chunk(chunk)

    if progress_bar:
        progress_bar.close()

    timestamp_cache_close = getattr(timestamps, "close", None)
    if callable(timestamp_cache_close):
        timestamp_cache_close()


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
    backend="loky",
    rpc_request_stats: RPCRequestStats | None = None,
    refresh_current_block: bool = False,
    greylist_batch_size: int = 1,
    greylist: frozenset[HexAddress] = frozenset(),
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


    :param greylist:
        Caller-selected target addresses for this chain, matched case-insensitively.
        Defaults to empty: readers never import or select chain-specific policies.
    :param greylist_batch_size:
        Positive maximum encoded subcalls per supplied greylisted target request,
        default one. Normal targets retain standard batching. This counts
        subcalls rather than vaults and never combines different isolated targets.

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

    :param backend:
        Joblib backend to use.

        Either "loky" or "threading".

    :param rpc_request_stats:
        Optional accumulator receiving physical JSON-RPC calls from either
        worker backend.

    :return:
        Iterable of results.

        One entry per each call.

        Calls may be different order than originally given.
    """

    assert type(chain_id) == int, f"Got: {chain_id}"

    if rpc_request_stats is None:
        rpc_request_stats = getattr(web3factory, "rpc_request_stats", None)

    if max_workers == 1:
        timeout = None  # No timeout for single process

    worker_processor = Parallel(
        n_jobs=max_workers,
        backend=backend,
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
            yield MulticallHistoricalTask(
                chain_id,
                web3factory,
                block_identifier,
                chunk,
                timestamp=ts,
                greylist_batch_size=greylist_batch_size,
                greylist=greylist,
                collect_rpc_request_stats=backend == "loky" and rpc_request_stats is not None,
                rpc_request_stats=rpc_request_stats if backend == "threading" else None,
                rpc_operation=getattr(rpc_request_stats, "operation", None),
                refresh_current_block=refresh_current_block,
            )

    performed_calls = success_calls = failed_calls = 0
    for completed_task in worker_processor(delayed(_execute_multicall_in_worker)(task) for task in _task_gen()):
        if backend == "loky" and rpc_request_stats is not None and completed_task.rpc_request_stats is not None:
            rpc_request_stats.merge(completed_task.rpc_request_stats)
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
    """Dispatch contract reads and source provenance to a joblib worker.

    Threaded workers can share counters; process workers return detached task
    counters for merging. Keeping the operation label as a scalar avoids
    transferring accumulated parent history with every historical batch.
    """

    #: Track which chain this call belongs to
    chain_id: int

    #: Factory creates the connection inside its owning worker.
    web3factory: Web3Factory

    #: Requested source block; historical tasks must preserve it.
    block_number: BlockIdentifier

    #: Multicalls to perform
    calls: list[EncodedCall]

    #: Debug parameter to early abort if we get invalid replies from Multicall contract
    require_multicall_result: bool = False

    #: Fetch timestamp not given.
    #:
    #: Otherwise prefetched
    timestamp: datetime.datetime | None = None

    #: Running counter for task ids, for serialisation checks
    task_id: int = field(default_factory=_create_task_id)

    #: Return a task-local counter to the parent when running under ``loky``.
    collect_rpc_request_stats: bool = False

    #: Shared parent counter when running under the threading backend.
    rpc_request_stats: RPCRequestStats | None = None

    #: Explicit phase-operation label copied into subprocess counters.
    rpc_operation: str | None = None

    #: Only preservation-aware historical price consumers accept missing RPC data.
    #: Chunked feature and token-cache probes keep this false to avoid cache damage.
    allow_greylist_unavailable: bool = False

    #: Encoded subcall limit for each caller-selected greylisted target; default one.
    #: Carried in task payloads so workers do not silently reuse another limit.
    greylist_batch_size: int = 1

    #: Immutable caller-selected targets for this chain; empty means normal batching.
    #: Included in worker identity so policy changes cannot reuse stale routing.
    greylist: frozenset[HexAddress] = frozenset()

    #: Refresh a safe numeric head per batch for current-state feature probes.
    refresh_current_block: bool = False

    def __post_init__(self) -> None:
        assert callable(self.web3factory)
        assert type(self.block_number) in (int, str), f"Got: {self.block_number}"
        assert type(self.calls) == list
        assert all(isinstance(c, EncodedCall) for c in self.calls), f"Expected list of EncodedCall objects, got {self.calls}"


def _execute_multicall_in_worker(
    task: MulticallHistoricalTask,
) -> CombinedEncodedCallResult:
    """Execute one Multicall task using worker-local reusable resources.

    The chunked and historical readers dispatch here through joblib, using
    either threads or processes. Worker-local connections amortise provider
    setup without sharing mutable provider sessions between concurrent tasks.
    Separate task counters are returned to process callers for one parent-side
    merge; threaded callers can instead share a phase accumulator directly.

    :param task: Calls, requested block, timestamp and accounting policy.
    :return: Raw call results with their actual source block and optional stats.
    """
    global _reader_instance

    reader: MultiprocessMulticallReader

    # A worker may serve multiple chains and later runs with different RPC
    # endpoints. Chain ID alone would reuse the wrong provider after failover
    # configuration changes, so the connection cache also includes the factory.
    per_chain_readers = getattr(_reader_instance, "per_chain_readers", None)
    if per_chain_readers is None:
        per_chain_readers = _reader_instance.per_chain_readers = {}

    assert task.chain_id

    if task.collect_rpc_request_stats:
        # Copy only the operation label into a fresh task counter. Returning the
        # parent's accumulated history from each process would multiply totals
        # when completed tasks are merged back into the phase accumulator.
        source_stats = task.rpc_request_stats if task.rpc_request_stats is not None else getattr(task.web3factory, "rpc_request_stats", None)
        task_rpc_request_stats = RPCRequestStats(operation=task.rpc_operation or getattr(source_stats, "operation", "historical_multicall"))
    else:
        task_rpc_request_stats = task.rpc_request_stats if task.rpc_request_stats is not None else getattr(task.web3factory, "rpc_request_stats", None)

    # Key: (chain ID, provider configuration/factory identity, isolated limit,
    # lower-case greylisted targets). A reused worker must retain neither a
    # previous limit nor a different caller's address-specific routing policy.
    greylist = frozenset(HexAddress(address.lower()) for address in task.greylist)
    reader_key = (task.chain_id, getattr(task.web3factory, "rpc_url", task.web3factory), task.greylist_batch_size, greylist)
    reader = per_chain_readers.get(reader_key)
    if reader is None:
        reader = per_chain_readers[reader_key] = MultiprocessMulticallReader(
            task.web3factory,
            rpc_request_stats=task_rpc_request_stats,
            greylist_batch_size=task.greylist_batch_size,
            greylist=greylist,
        )

    set_rpc_request_stats = getattr(reader.web3, "set_rpc_request_stats", None)
    if callable(set_rpc_request_stats):
        set_rpc_request_stats(task_rpc_request_stats)

    try:
        # Read block timestamp for this batch
        # Connection construction and provider switches already verify the chain.
        # Re-checking the immutable identity must not charge every sampled task.
        assert task.chain_id == reader.chain_id, f"chain_id mismatch. Wanted: {task.chain_id}, reader has: {reader.chain_id}"

        # Live HyperCore feature probes need a recent executable state block.
        # Historical callers must keep their requested block: refreshing those
        # would silently attach current values to historical price observations.
        block_number = max(1, reader.web3.eth.block_number - 10) if task.refresh_current_block else task.block_number

        if task.timestamp is None:
            timestamp = reader.fetch_block_timestamp(block_number)
        else:
            timestamp = task.timestamp

        # Perform multicall to read share prices
        call_results = reader.process_calls(
            block_number,
            task.calls,
            require_multicall_result=task.require_multicall_result,
            timestamp=timestamp,
            allow_greylist_unavailable=task.allow_greylist_unavailable,
        )

        # Pass results back to the main process
        return CombinedEncodedCallResult(
            block_number=block_number,
            timestamp=timestamp,
            results=[c for c in call_results],
            rpc_request_stats=task_rpc_request_stats if task.collect_rpc_request_stats else None,
        )
    finally:
        # The provider survives this task. Detach counters on every exit so a
        # later task cannot write attempts into this already returned result.
        if callable(set_rpc_request_stats):
            set_rpc_request_stats(None)
