"""GMX onchain event reader.

- GMX uses a special contract called EventEmitter to emit logs
- GMX has its own topic structure on the top of Solidity's topic structure
- Here we have utilities to lift off this data directly onchain using HyperSync

See

- `EventEmitter source <https://github.com/gmx-io/gmx-synthetics/blob/e9c918135065001d44f24a2a329226cf62c55284/contracts/event/EventEmitter.sol>`__
- `EventUtils for packing data into the logs <https://github.com/gmx-io/gmx-synthetics/blob/e9c918135065001d44f24a2a329226cf62c55284/contracts/event/EventUtils.sol>`__

"""
import enum

import hypersync
from hypersync import HypersyncClient, ClientConfig
from hypersync import BlockField, LogField

from eth_defi.chain import get_chain_name
from eth_defi.gmx.constants import GMX_EVENT_EMITTER_ADDRESS
from eth_defi.gmx.onchain.trade import HexAddress, HexBytes
from eth_defi.gmx.utils import create_hash_string

class EventLogType(enum.Enum):
    """See EventEmitter.sol"""

    EventLog = "EventLog"
    EventLog1 = "EventLog1"
    EventLog2 = "EventLog2"

    def get_hash(self) -> str:
        # https://www.codeslaw.app/contracts/arbitrum/0xc8ee91a54287db53897056e12d9819156d3822fb
        # https://www.codeslaw.app/contracts/arbitrum/0xc8ee91a54287db53897056e12d9819156d3822fb?tab=abi
        match self:
            case EventLogType.EventLog:
                return "0xc666579c261c0b272eeac102561fd381be4c18912e9bff98fafff43046dc3410"
            case EventLogType.EventLog1:
                return "0x23a65b039a8c7150257d1536d872dc2bc30ee565c7043c6971591708e13e8ca8"
            case EventLogType.EventLog2:
                return "0xd56ea9fb3c84ad093426d6d349a86fa45043ae6bf0083a61bb2be8dc9d2d3701
            case _:
                raise ValueError(f"Unknown EventLogType: {self}")


def get_gmx_event_hash(event_name: str) -> str:
    assert type(event_name) == str
    return create_hash_string(event_name).hex()


def create_gmx_query(
    start_block: int,
    end_block: int,
    event_emitter_address: HexAddress,
    log_type_hash: HexBytes,
    event_name_hash: HexBytes,
) -> hypersync.Query:

    assert type(start_block) == int and start_block >= 0
    assert type(end_block) == int and end_block >= start_block
    assert type(event_emitter_address) == str and event_emitter_address.startswith("0x") and len(event_emitter_address) == 42
    assert isinstance(log_type_hash, HexBytes)
    assert isinstance(event_name_hash, HexBytes)

    # https://github.com/enviodev/30-hypersync-examples

    # [['0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7'], ['0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db']]
    log_selections = [
        hypersync.LogSelection(
            address=[event_emitter_address],  # USDC contract
            topics=[log_type_hash.hex(), event_name_hash.hex(0)],
        )
    ]

    # The query to run
    query = hypersync.Query(
        # start from block 0 and go to the end of the chain (we don't specify a toBlock).
        from_block=start_block,
        to_block=end_block,
        # The logs we want. We will also automatically get transactions and blocks relating to these logs (the query implicitly joins them).
        logs=log_selections,
        # Select the fields we are interested in, notice topics are selected as topic0,1,2,3
        field_selection=hypersync.FieldSelection(
            block=[
                BlockField.NUMBER,
                BlockField.TIMESTAMP,
            ],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.ADDRESS,
                LogField.TRANSACTION_HASH,
                LogField.TOPIC0,
            ],
        ),
    )
    return query


async def query_gmx_events_async(
    client: HypersyncClient,
    gmx_event_name: str,
    log_type:EventLogType,
    start_block: int,
    end_block: int,
    timeout: float = 30,
):
    """Query GMX events emitted by EventEmitter from HyperSync client."""

    assert isinstance(client, HypersyncClient), f"Expected HypersyncClient, got {type(client)}"
    assert type(gmx_event_name) == str, f"Expected str, got {type(gmx_event_name)}"
    assert isinstance(log_type, EventLogType), f"Expected EventLogType, got {type(log_type)}"

    chain_id = await client.get_chain_id()

    event_emitter_address = GMX_EVENT_EMITTER_ADDRESS[get_chain_name(chain_id)]

    log_type_hash = log_type.get_hash()
    event_name_hash = get_gmx_event_hash(gmx_event_name)

    query = create_gmx_query(
        start_block=start_block,
        end_block=end_block,
        event_emitter_address=event_emitter_address,
        log_type_hash=log_type_hash,
        event_name_hash=event_name_hash,
    )



