"""Event reader output."""

from typing import TypedDict, List, Optional

from web3.contract import ContractEvent


class LogContext:
    """A helper you can pass around for the log results.

    Subclass this and add your own data / methods.

    See `scripts/read-uniswap-v2-pairs-and-swaps.py` for an example.
    """

    pass


class LogResult(TypedDict):
    """A dictionary of the event look up.

    The values are untranslated hex strings for the maximum speed.

    See also

    - https://docs.alchemy.com/alchemy/guides/eth_getlogs

    """

    #: User passed context
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
    #: Synthesized by block reader code, not present in the receipt.
    #: May be None if timestamp fetching is disabled for the speed reasons.
    timestamp: Optional[int]

    #: Transaction where the event occred
    transactionHash: str

    #: Log index as a hex number
    logIndex: str

    #: Topics in this receipt.
    #: `topics[0]` is always the event signature.
    topics: List[str]

    #: Block reorg helper
    removed: bool

    #: Data related to the event
    data: str
