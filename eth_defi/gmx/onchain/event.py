"""GMX onchain event reader.

- GMX uses a special contract called EventEmitter to emit logs
- GMX has its own topic structure on the top of Solidity's topic structure
- Here we have utilities to lift off this data directly onchain using HyperSync

See

- `EventEmitter source <https://github.com/gmx-io/gmx-synthetics/blob/e9c918135065001d44f24a2a329226cf62c55284/contracts/event/EventEmitter.sol>`__
- `EventUtils for packing data into the logs <https://github.com/gmx-io/gmx-synthetics/blob/e9c918135065001d44f24a2a329226cf62c55284/contracts/event/EventUtils.sol>`__

"""
import asyncio
import enum
import logging
from dataclasses import dataclass
from typing import Iterable

import hypersync
from hypersync import HypersyncClient, ClientConfig
from hypersync import BlockField, LogField, Log

from tqdm_loggable.auto import tqdm


from eth_defi.chain import get_chain_name
from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.gmx.constants import GMX_EVENT_EMITTER_ADDRESS
from eth_defi.gmx.onchain.trade import HexAddress, HexBytes
from eth_defi.gmx.utils import create_hash_string
from eth_defi.utils import from_unix_timestamp


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GMXEvent:
    """Wrap raw HyperSync log to something with better DevEx"""
    block: BlockHeader
    log: Log


class EventLogType(enum.Enum):
    """See EventEmitter.sol"""

    EventLog = "EventLog"
    EventLog1 = "EventLog1"
    EventLog2 = "EventLog2"

    # Eventlog2: https://arbiscan.io/tx/0x4ac7a74da910b2834eb3a90712eb4cf1efb31e6e3a23bfa0938682480a000af9
    # EventLog1: https://arbiscan.io/tx/0xf98073aacd3cf22a8106035be40b0e96caadddb8c58346fc4fb90164f9ce5151
    # 0x468a25a7ba624ceea6e540ad6f49171b52495b648417ae91bca21676d8a24dc5
    # 0xf884901fabe4018defc05734811ffbea40165f97728a16c8b964ced8119e21c1
    # https://dashboard.tenderly.co/miohtama/test-project/tx/0x4ac7a74da910b2834eb3a90712eb4cf1efb31e6e3a23bfa0938682480a000af9/logs
    def get_hash(self) -> HexBytes:
        #
        # https://www.codeslaw.app/contracts/arbitrum/0xc8ee91a54287db53897056e12d9819156d3822fb
        # https://www.codeslaw.app/contracts/arbitrum/0xc8ee91a54287db53897056e12d9819156d3822fb?tab=abi
        match self:
            case EventLogType.EventLog:
                raise NotImplementedError()
                # s = ""
            case EventLogType.EventLog1:
                s = "137a44067c8961cd7e1d876f4754a5a3a75989b4552f1843fc69c3b372def160"
            case EventLogType.EventLog2:
                s = "0x468a25a7ba624ceea6e540ad6f49171b52495b648417ae91bca21676d8a24dc5"
            case _:
                raise ValueError(f"Unknown EventLogType: {self}")

        return HexBytes(bytes.fromhex(s))


def get_gmx_event_hash(event_name: str) -> HexBytes:
    assert type(event_name) == str
    return create_hash_string(event_name)


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
            # address=[event_emitter_address],  # USDC contract
            # topics=[["0x" + log_type_hash.hex(), "0x" + event_name_hash.hex()]],
            # topics=[["0x" + log_type_hash.hex(), "0x" + event_name_hash.hex()]],
            topics=[["0x" + log_type_hash.hex()]],
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
                BlockField.HASH,
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
    display_progress=True,
) -> Iterable[GMXEvent]:
    """Query GMX events emitted by EventEmitter from HyperSync client."""

    assert isinstance(client, HypersyncClient), f"Expected HypersyncClient, got {type(client)}"
    assert type(gmx_event_name) == str, f"Expected str, got {type(gmx_event_name)}"
    assert isinstance(log_type, EventLogType), f"Expected EventLogType, got {type(log_type)}"

    chain_id = await client.get_chain_id()
    chain_name = get_chain_name(chain_id)

    event_emitter_address = GMX_EVENT_EMITTER_ADDRESS[chain_name.lower()]

    log_type_hash = log_type.get_hash()
    event_name_hash = get_gmx_event_hash(gmx_event_name)

    if display_progress:
        progress_bar = tqdm(
            total=end_block - start_block,
            desc=f"Scanning HyperSync even chain {chain_name}",
        )
    else:
        progress_bar = None

    query = create_gmx_query(
        start_block=start_block,
        end_block=end_block,
        event_emitter_address=event_emitter_address,
        log_type_hash=log_type_hash,
        event_name_hash=event_name_hash,
    )

    logger.info(f"Starting HyperSync stream {start_block:,} to {end_block:,}, event {gmx_event_name}, chain {chain_name}, emitter is {event_emitter_address}")

    # start the stream
    receiver = await client.stream(query, hypersync.StreamConfig())

    # Progress bar state
    last_block = end_block
    event_count = 0
    timestamp = None

    while True:
        try:
            res = await asyncio.wait_for(receiver.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("HyperSync receiver timed out")
            break  # or handle as appropriate

        # exit if the stream finished
        if res is None:
            break

        current_block = res.next_block

        if res.data.logs:

            block_lookup = {b.number: b for b in res.data.blocks}

            for log in res.data.logs:
                timestamp = int(block_lookup[log.block_number].timestamp, 16)
                yield GMXEvent(
                    block=BlockHeader(
                        block_number=log.block_number,
                        block_hash=block_lookup[log.block_number].hash,
                        timestamp=timestamp,
                    ),
                    log=log,
                )
                event_count += 1

        last_synced = res.archive_height

        if progress_bar is not None:
            progress_bar.update(current_block - last_block)
            last_block = current_block

            # Add extra data to the progress bar
            if timestamp is not None:
                progress_bar.set_postfix(
                    {
                        "At": from_unix_timestamp(timestamp),
                        "Events": f"{event_count:,}",
                    }
                )

    logger.info(f"HyperSync sees {last_synced} as the last block")

    if progress_bar is not None:
        progress_bar.close()


def query_gmx_events(
    client: HypersyncClient,
    gmx_event_name: str,
    log_type :EventLogType,
    start_block: int,
    end_block: int,
    timeout: float = 30,
    display_progress=True,
) -> list[GMXEvent]:
    """Sync version.

    - See :py:func:`query_gmx_events_async` for documentation.
    - Cannot do iterable because of colored functions
    """
    logger.info("Go")
    async def _wrapped():
        _iter = query_gmx_events_async(
            client=client,
            gmx_event_name=gmx_event_name,
            log_type=log_type,
            start_block=start_block,
            end_block=end_block,
            timeout=timeout,
            display_progress=display_progress,
        )
        return [e async for e in _iter]

    return asyncio.run(_wrapped())
