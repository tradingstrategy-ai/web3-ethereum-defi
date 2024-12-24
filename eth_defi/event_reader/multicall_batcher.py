"""Multicall helpers.

- Perform several smart contract calls in one RPC request using `Multicall <https://www.multicall3.com/>`__ contract

- A wrapper around `Multicall library by Bantg <https://github.com/banteg/multicall.py>`__

- Batching and multiprocessing reworked to use threads

.. warning::

    See Multicall `private key leak hack warning <https://github.com/mds1/multicall>`__.
"""
import abc
import datetime
import logging
from abc import abstractmethod
from dataclasses import dataclass
from itertools import islice
from typing import TypeAlias, Iterable, Generator, Hashable, Any, Final

from eth_typing import HexAddress, BlockIdentifier, BlockNumber
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract, ZERO_ADDRESS, encode_function_call

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
    137: (25_770_160, datetime.datetime(2022, 3, 9, 15, 58, 11)),  # Pooly
    43114: (11_907_934, datetime.datetime(2022, 3, 9, 23, 11, 52)),  # Ava
    42161: (7_654_707, datetime.datetime(2022, 3, 9, 16, 5, 28)),  # Arbitrum
}


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
        if multicall_data is not None:
            assert multicall_data[0] < block_identifier, f"Multicall not yet deployed at {block_identifier}"

    return get_deployed_contract(web3, "multicall/IMulticall3.json", address)


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
