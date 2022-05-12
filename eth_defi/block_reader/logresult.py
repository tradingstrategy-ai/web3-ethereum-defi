"""Output of the log reader"""
from typing import TypedDict, List

from eth_typing import HexAddress
from web3.contract import ContractEvent


class LogContext:
    """A helper you can pass around for the events.

    Subclass this and add your own data / methods.
    """
    pass


class LogResult(TypedDict):
    """A dictionary of the event look up.

    The values are untranslated hex strings for the maximum speed.

    See also

    - https://docs.alchemy.com/alchemy/guides/eth_getlogs
    """

    context: LogContext

    #: Contract event matches for this raw log
    event: ContractEvent

    #: Smart contract address
    address: str

    #: Block where the event was
    blockHash: str

    #: Block number as hex string
    blockNumber: str

    #: UNIX timestamp of the block number.
    #: Synthesized by us.
    timestamp: int

    #: Transaction where the event occred
    transactionHash: str

    #: Log index as a hex number
    logIndex: str

    #: Topics in this receipt
    topics: List[str]

    #: Block reorg helper
    removed: bool

    #: Data related to the event
    data: str


