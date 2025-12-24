"""Event reader output types and decoding."""

from typing import TypedDict, List, Optional

from eth_utils import hexstr_if_str, to_bytes
from web3._utils.events import get_event_data
from web3.contract.contract import ContractEvent
from web3.types import EventData


class LogContext:
    """An abstract context class you can pass around for the log results.

    Subclass this and add your own data / methods.

    See `scripts/read-uniswap-v2-pairs-and-swaps.py` for an example.
    """


class LogResult(TypedDict):
    """One emitted Solidity event.

    - Type mappings for a raw Python :py:class:`dict` object

    - Designed for high performance at the cost of readability and usability

    - The values are untranslated hex strings to maximize the reading speed of events

    - See :py:func:`decode_event` how to turn to ABI converted data

    - See :py:mod:`eth_defi.event_reader.reader` for more information

    Example data (PancakeSwap swap):

    .. code-block:: text

        {
            'address': '0xc91cd2b9c9aafe494cf3ccc8bee7795deb17231a',
            'blockHash': '0x3bc60abea8fca30516f48b0374542b9c8fa554061c8802d7bcd4211fffbf6caf',
            'blockNumber': 30237147,
            'chunk_id': 30237147,
            'context': None,
            'data': '0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000de90d34e1f2e65c0000000000000000000000000000000000000000000018e627902bfb974416f90000000000000000000000000000000000000000000000000000000000000000',
            'event': <class 'web3._utils.datatypes.Swap'>,
            'logIndex': '0x3',
            'removed': False,
            'timestamp': 1690184818,
            'topics': ['0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822',
                    '0x00000000000000000000000064c425b6fa04873ea460cda049b340d79cf859d7',
                    '0x000000000000000000000000ad1fedfb04377c4b849cef6ef9627bca41955fa0'],
            'transactionHash': '0xf2287653559f01d8afba9ae00386d453b731699b784851f7a8504d41dee7503b',
            'transactionIndex': '0x1'
        }

    """

    #: User passed context for the event reader
    context: LogContext

    #: Contract event matches for this raw log
    #:
    #: To use web3.py helpers to decode this log.
    #:
    #: This event instance is just a class reference and does
    #: not contain any bound data.
    #:
    event: ContractEvent

    #: Smart contract address
    address: str

    #: Block where the event was
    blockHash: str

    #: Block number as hex string
    blockNumber: int

    #: UNIX timestamp of the block number.
    #: Synthesized by block reader code, not present in the receipt.
    #: May be None if timestamp fetching is disabled for the speed reasons.
    timestamp: Optional[int]

    #: Transaction where the event occred
    transactionHash: str

    #: Log index as a hex number
    logIndex: str

    #: Topics in this receipt.
    #: `topics[0]` is always the event signature.
    #:
    #: TODO: Whether these are strings or HexBytes depends on the EVM backend and Web3 version.
    #: Resolve this so that results are normalised to one type.
    #:
    #: See :py:mod:`eth_defi.reader.conversion` how to get Python values out of this.
    #:
    topics: List[str]

    #: Block reorg helper
    removed: bool

    #: Data related to the event
    #:
    #: As raw hex dump from the JSON-RPC.
    #:
    #: See :py:func:`eth_defi.reader.conversion.decode_data` to split to args.
    #:
    data: str


def decode_log(evt: LogResult) -> EventData:
    """Decodes a single raw log result using the attached ABI.

    See :py:class:`LogResult` for more information.

    Example usage:

    .. code-block:: python

        # Decode a Swap event from PancakeSwap
        decoded = decode_log(evt)

    Example output - see decoded 'args':

    .. code-block: python

        {
            'args': {
                'sender': '0x0000000000016a723d0d576Df7DC79EC149ac760',
                'to': '0x0000000000016a723d0d576Df7DC79EC149ac760',
                'amount0In': 0,
                'amount1In': 160311000000000000,
                'amount0Out': 52141086389997638430846,
                'amount1Out': 0
             },
            'event': 'Swap',
            'logIndex': '0x3',
            'transactionIndex': '0x0',
            'transactionHash': '0x4a64c93bae322d7992612559976feac55cedb10f5cb14bdd86e2f21189bdce48',
            'address': '0xa9da8ab754b8d0ca39de269309c747d6cb28c97c',
            'blockHash': '0x64c3f40f92a8ef1a7333041494877d7282752363f30b3bf3c4820fd4a23cc595',
            'blockNumber': 30237924
        }


    :param evt:
        A raw log event from the event reader.

    :return:
        Human-readable dict.
    """

    evt = evt.copy()

    # Web3.py ABI expects these as HexBytes,
    # so we just mangle in place
    evt["topics"] = [hexstr_if_str(to_bytes, t) for t in evt["topics"]]

    event = evt["event"]
    abi = event._get_event_abi()
    data = get_event_data(event.w3.codec, abi, evt)
    return data
