"""In-house filter implementation.

Wrap low-level JSON-RPC filters with more manageable Python code.
"""

from dataclasses import dataclass
from typing import Dict, Optional, List, Type

from eth_bloom import BloomFilter
from web3.contract import ContractEvent


@dataclass
class Filter:
    """Internal filter used to match events.

    A helper class to deal with `eth_getLogs` JSON API.

    This can be used with

    - Historical events: :py:mod:`eth_defi.event_reader.reader`

    - Live events: :py:mod:`eth_defi.event_reader.websocket`
    """

    #: Preconstructed topic hash -> Event mapping
    topics: Dict[str, ContractEvent]

    #: Bloom filter to match block headers
    #: TODO: Currently unsupported
    bloom: Optional[BloomFilter]

    #: Get events from a single contract only.
    #:
    #: For multiple contracts give a list of addresses.
    contract_address: Optional[str | List[str]] = None

    @staticmethod
    def create_filter(address: Optional[str | List[str]], event_types: List[Type[ContractEvent]]) -> "Filter":

        topics = {event_type.build_filter().topics[0]: event_type for event_type in event_types}

        filter = Filter(
            contract_address=address,
            bloom=None,
            topics=topics,
        )

        return filter
