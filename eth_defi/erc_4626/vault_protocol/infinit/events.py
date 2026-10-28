"""Indexed Infinit deployment, manager activity and receipt-supply events.

Uses the configured Hypersync stream and cache-aware timestamp API. Verified
event definitions: https://arbiscan.io/address/0x0bb4f37f9bd8fe3a72c8acb53509ae95ca5343ab#code.
"""

import asyncio
import datetime
import logging
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import duckdb
import hypersync
import pandas as pd
from eth_abi import decode
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.infinit.constants import INFINIT_CHAIN_ID, INFINIT_MANAGER_DEPLOYMENT_BLOCK, INFINIT_USDC, InfinitProduct
from eth_defi.hypersync.hypersync_timestamp import fetch_exact_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.session import ThrottledHypersyncClient, open_hypersync_stream
from eth_defi.vault.flow_events import decode_hypersync_int

logger = logging.getLogger(__name__)
CREATED_TOPIC = "0x" + Web3.keccak(text="VaultCreated(uint256,address,address,address)").hex()
DEPOSITED_TOPIC = "0x" + Web3.keccak(text="Deposited(address,uint256,uint256)").hex()
WITHDRAWN_TOPIC = "0x" + Web3.keccak(text="Withdrawn(address,uint256,uint256,uint256)").hex()
TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex()
ZERO_TOPIC = "0x" + "0" * 64


@dataclass(slots=True, frozen=True)
class InfinitEvent:
    """An exact manager or share-supply event."""

    #: EVM block number.
    block_number: int
    #: Event order within that block.
    log_index: int
    #: Unix timestamp, resolved through the shared timestamp cache.
    timestamp: int
    #: ``created``, ``deposit``, ``withdraw``, ``mint`` or ``burn``.
    kind: str
    #: Unscaled USDC for deposits; receipt shares for withdrawals, mints and
    #: burns; zero for deployment events. Withdrawal proceeds are not stored.
    amount: int
    #: Source transaction for provenance.
    transaction_hash: str


def decode_infinit_event(log: object, product: InfinitProduct) -> InfinitEvent | None:
    """Route verified manager events to a reviewed vault identity.

    Unknown future indices are reported instead of borrowing Apex metadata.

    :param log: Hypersync log including complete topics and data.
    :param product: Reviewed product mapping.
    :return: Recognised event, or ``None`` for another product.
    """
    topics = [str(t).lower() for t in log.topics if t is not None]
    raw = bytes.fromhex(str(log.data).removeprefix("0x"))
    address = str(log.address).lower()
    if address == product.manager:
        topic = topics[0]
        if topic == CREATED_TOPIC:
            index = int(topics[1], 16)
            vault = "0x" + topics[2][-40:]
            share, asset = decode(["address", "address"], raw)
            if index != product.vault_id:
                logger.warning("Unreviewed Infinit deployment: index=%s address=%s", index, vault)
                return None
            if vault != product.address or share.lower() != product.share_token:
                msg = "Infinit factory event disagrees with reviewed vault/share mapping"
                raise ValueError(msg)
            if asset.lower() != INFINIT_USDC:
                msg = "Infinit factory collateral differs from reviewed USDC"
                raise ValueError(msg)
            kind, amount = "created", 0
        elif topic in {DEPOSITED_TOPIC, WITHDRAWN_TOPIC}:
            if int(topics[2], 16) != product.vault_id:
                logger.warning("Unreviewed Infinit manager activity: index=%s", int(topics[2], 16))
                return None
            amount = decode(["uint256"] if topic == DEPOSITED_TOPIC else ["uint256", "uint256"], raw)[0]
            kind = "deposit" if topic == DEPOSITED_TOPIC else "withdraw"
        else:
            return None
    elif address == product.share_token and topics[0] == TRANSFER_TOPIC:
        if topics[1] == ZERO_TOPIC and topics[2] != ZERO_TOPIC:
            kind = "mint"
        elif topics[2] == ZERO_TOPIC and topics[1] != ZERO_TOPIC:
            kind = "burn"
        else:
            return None
        amount = decode(["uint256"], raw)[0]
    else:
        return None
    return InfinitEvent(decode_hypersync_int(log.block_number), decode_hypersync_int(log.log_index), 0, kind, amount, str(log.transaction_hash))


async def _fetch_chunk(client: ThrottledHypersyncClient, product: InfinitProduct, start: int, end: int) -> list[InfinitEvent]:
    """Read a complete bounded event range through the configured stream.

    :param client: Configured Hypersync client.
    :param product: Reviewed product.
    :param start: Inclusive block boundary.
    :param end: Exclusive block boundary.
    :return: Decoded events awaiting cached timestamps.
    """
    query = hypersync.Query(
        from_block=start,
        to_block=end,
        logs=[
            hypersync.LogSelection(address=[product.manager], topics=[[CREATED_TOPIC, DEPOSITED_TOPIC, WITHDRAWN_TOPIC]]),
            hypersync.LogSelection(address=[product.share_token], topics=[[TRANSFER_TOPIC], [ZERO_TOPIC]]),
            hypersync.LogSelection(address=[product.share_token], topics=[[TRANSFER_TOPIC], [], [ZERO_TOPIC]]),
        ],
        field_selection=hypersync.FieldSelection(
            log=[
                hypersync.LogField.BLOCK_NUMBER,
                hypersync.LogField.LOG_INDEX,
                hypersync.LogField.ADDRESS,
                hypersync.LogField.TRANSACTION_HASH,
                hypersync.LogField.TOPIC0,
                hypersync.LogField.TOPIC1,
                hypersync.LogField.TOPIC2,
                hypersync.LogField.TOPIC3,
                hypersync.LogField.DATA,
            ],
        ),
    )
    receiver = await open_hypersync_stream(client, query)
    events = []
    reached = start
    try:
        while True:
            response = await asyncio.wait_for(receiver.recv(), timeout=55)
            if response is None:
                break
            reached = response.next_block
            events.extend(event for log in response.data.logs or [] if (event := decode_infinit_event(log, product)) is not None)
            logger.info("Infinit events: scanned to block %s, %d records", reached, len(events))
        if reached < end:
            raise RuntimeError(f"Incomplete Infinit Hypersync range: {reached} < {end}")
    finally:
        receiver.close()
        await asyncio.sleep(0.05)
    return events


def fetch_infinit_events(client: ThrottledHypersyncClient, product: InfinitProduct, end_block: int, timestamp_cache_path: Path, start_block: int = INFINIT_MANAGER_DEPLOYMENT_BLOCK) -> Iterator[InfinitEvent]:
    """Fetch indexed events with exact cached block timestamps.

    A finite chunk timeout and progress logging bound every source read.

    :param client: Configured Hypersync client.
    :param product: Reviewed product.
    :param end_block: Exclusive safe source boundary.
    :param timestamp_cache_path: Shared per-chain timestamp-cache directory.
    :param start_block: Inclusive source boundary.
    :return: Canonically ordered deployment, activity and supply events.
    """
    events = {}
    with asyncio.Runner() as runner:
        for start in range(start_block, end_block, 10_000_000):
            for event in runner.run(_fetch_chunk(client, product, start, min(start + 10_000_000, end_block))):
                events[event.block_number, event.log_index] = event
    if not events:
        return
    # Snapshot only required cached headers. The standard cache opener can
    # migrate legacy schemas and write misses, so it must never open the
    # operator's cache during a read-only maintenance rehearsal.
    with tempfile.TemporaryDirectory(prefix="infinit-event-timestamps-") as directory:
        cache_path = Path(directory)
        existing = timestamp_cache_path / f"{INFINIT_CHAIN_ID}-timestamps.duckdb"
        if existing.exists():
            con = duckdb.connect(str(existing), read_only=True)
            try:
                con.register("requested", pd.DataFrame({"block_number": sorted({e.block_number for e in events.values()})}))
                headers = con.execute("SELECT b.block_number,b.timestamp FROM block_timestamps b JOIN requested r USING(block_number)").fetchdf()
            finally:
                con.close()
            target = duckdb.connect(str(cache_path / existing.name))
            try:
                target.register("headers", headers)
                target.execute("CREATE TABLE block_timestamps AS SELECT * FROM headers")
            finally:
                target.close()
        stamps = fetch_exact_block_timestamps_using_hypersync_cached(client=client, chain_id=INFINIT_CHAIN_ID, block_numbers=sorted({e.block_number for e in events.values()}), cache_path=cache_path, display_progress=False)
        try:
            for event in sorted(events.values(), key=lambda e: (e.block_number, e.log_index)):
                timestamp = int(stamps[event.block_number].replace(tzinfo=datetime.UTC).timestamp())
                yield replace(event, timestamp=timestamp)
        finally:
            stamps.close()
