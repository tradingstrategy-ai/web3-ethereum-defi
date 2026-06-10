"""CCTP V2 on-chain event reading.

Engine-agnostic scanning of ``DepositForBurn`` events, e.g. to find
cross-chain transfers initiated by a Safe so their delivery can be verified.

Events are wrapped into the slotted :class:`CCTPDepositForBurn` dataclass so
the underlying event reading engine can change without touching callers.
The current engines are:

- **HyperSync** (preferred) — server-side filtered streaming, scales to
  full-history scans.
- **Chunked** ``eth_getLogs`` fallback — used when the ``hypersync`` package
  is not installed, the chain has no HyperSync server (e.g. Anvil forks), or
  the HyperSync query fails. JSON-RPC providers cap the block range per
  request, so the fallback adaptively shrinks its chunk size on range errors.

Example::

    from eth_defi.cctp.events import fetch_deposit_for_burn_events

    burns = fetch_deposit_for_burn_events(
        web3,
        depositor=safe_address,
        start_block=web3.eth.block_number - 1_000_000,
    )
    for burn in burns:
        print(f"{burn.amount} raw USDC -> domain {burn.destination_domain}, tx {burn.transaction_hash}")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.transfer import get_token_messenger_v2

try:
    import hypersync
    from hypersync import BlockField, LogField

    from eth_defi.hypersync.server import get_hypersync_server
    from eth_defi.hypersync.session import open_hypersync_stream
except ImportError:
    hypersync = None

logger = logging.getLogger(__name__)


#: keccak256 topic0 of the CCTP V2 ``DepositForBurn`` event:
#:
#: ``DepositForBurn(address indexed burnToken, uint256 amount, address indexed depositor,
#: bytes32 mintRecipient, uint32 destinationDomain, bytes32 destinationTokenMessenger,
#: bytes32 destinationCaller, uint256 maxFee, uint32 indexed minFinalityThreshold, bytes hookData)``
#:
#: Note the bundled ``TokenMessengerV2.json`` ABI carries the stale V1 event
#: signature whose topic hash matches nothing emitted by the deployed V2
#: contracts, so the topic is pinned here. Verified against production
#: ``depositForBurn()`` transactions.
DEPOSIT_FOR_BURN_EVENT_TOPIC0 = "0x0c8c1cbdc5190613ebd485511d4e2812cfa45eecb79d845893331fedad5130a5"

#: ABI types of the non-indexed ``DepositForBurn`` data fields, in order.
_DEPOSIT_FOR_BURN_DATA_TYPES = [
    "uint256",  # amount
    "bytes32",  # mintRecipient
    "uint32",  # destinationDomain
    "bytes32",  # destinationTokenMessenger
    "bytes32",  # destinationCaller
    "uint256",  # maxFee
    "bytes",  # hookData
]

#: Minimum chunk size for the eth_getLogs fallback before giving up shrinking.
_MIN_GET_LOGS_CHUNK = 1_000


@dataclass(slots=True)
class CCTPDepositForBurn:
    """One decoded CCTP V2 ``DepositForBurn`` event.

    Engine-agnostic representation — produced identically by the HyperSync
    and ``eth_getLogs`` reading paths.
    """

    #: EVM chain id of the source chain where the burn happened
    chain_id: int

    #: Block number of the burn transaction
    block_number: int

    #: Burn transaction hash, 0x-prefixed hex
    transaction_hash: str

    #: Log index within the block
    log_index: int

    #: Token that was burned (USDC address on the source chain)
    burn_token: HexAddress

    #: Address whose tokens were burned (e.g. the vault Safe)
    depositor: HexAddress

    #: Burned amount in raw token units
    amount: int

    #: Address that will receive the mint on the destination chain
    mint_recipient: HexAddress

    #: CCTP domain id of the destination chain
    destination_domain: int

    #: Maximum fast-transfer fee, in raw token units (0 for standard transfers)
    max_fee: int

    #: Finality threshold requested for attestation (2000 = standard transfer)
    min_finality_threshold: int


def _topic_to_address(topic: str | bytes) -> HexAddress:
    """Extract an address from a 32-byte topic value."""
    raw = topic.hex() if isinstance(topic, bytes) else topic
    return Web3.to_checksum_address("0x" + raw.removeprefix("0x")[-40:])


def _decode_deposit_for_burn(chain_id: int, log: dict) -> CCTPDepositForBurn:
    """Decode a web3-style log dict into :class:`CCTPDepositForBurn`.

    :param log:
        Log with ``topics``, ``data``, ``blockNumber``, ``transactionHash``
        and ``logIndex`` keys. Topics and data may be hex strings (HyperSync)
        or bytes (web3.py).
    """
    topics = log["topics"]
    assert len(topics) == 4, f"DepositForBurn expects 4 topics, got {len(topics)}"

    data = log["data"]
    if isinstance(data, str):
        data = bytes.fromhex(data.removeprefix("0x"))
    amount, mint_recipient, destination_domain, _dest_token_messenger, _dest_caller, max_fee, _hook_data = abi_decode(
        _DEPOSIT_FOR_BURN_DATA_TYPES,
        data,
    )

    min_finality_topic = topics[3]
    if isinstance(min_finality_topic, bytes):
        min_finality_threshold = int.from_bytes(min_finality_topic, "big")
    else:
        min_finality_threshold = int(min_finality_topic, 16)

    tx_hash = log["transactionHash"]
    if isinstance(tx_hash, bytes):
        tx_hash = tx_hash.hex()
    tx_hash = "0x" + tx_hash.removeprefix("0x")

    return CCTPDepositForBurn(
        chain_id=chain_id,
        block_number=int(log["blockNumber"]),
        transaction_hash=tx_hash,
        log_index=int(log["logIndex"]),
        burn_token=_topic_to_address(topics[1]),
        depositor=_topic_to_address(topics[2]),
        amount=int(amount),
        mint_recipient=_topic_to_address(mint_recipient),
        destination_domain=int(destination_domain),
        max_fee=int(max_fee),
        min_finality_threshold=min_finality_threshold,
    )


def _depositor_topic(depositor: HexAddress) -> str:
    """Build the indexed depositor topic filter value."""
    return "0x" + depositor.removeprefix("0x").lower().rjust(64, "0")


async def _fetch_events_hypersync_async(
    client,
    chain_id: int,
    token_messenger_address: HexAddress,
    depositor: HexAddress,
    start_block: int,
    end_block: int | None,
    recv_timeout: float = 90.0,
) -> list[CCTPDepositForBurn]:
    """Read DepositForBurn events using HyperSync streaming."""
    query = hypersync.Query(
        from_block=start_block,
        # HyperSync to_block is exclusive
        to_block=end_block + 1 if end_block is not None else None,
        logs=[
            hypersync.LogSelection(
                address=[token_messenger_address.lower()],
                topics=[
                    [DEPOSIT_FOR_BURN_EVENT_TOPIC0],
                    [],
                    [_depositor_topic(depositor)],
                ],
            )
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.LOG_INDEX,
                LogField.TRANSACTION_HASH,
                LogField.TOPIC0,
                LogField.TOPIC1,
                LogField.TOPIC2,
                LogField.TOPIC3,
                LogField.DATA,
            ],
        ),
    )

    receiver = await open_hypersync_stream(client, query)
    events: list[CCTPDepositForBurn] = []
    while True:
        res = await asyncio.wait_for(receiver.recv(), timeout=recv_timeout)
        if res is None:
            break
        for log in res.data.logs or []:
            events.append(
                _decode_deposit_for_burn(
                    chain_id,
                    {
                        "topics": log.topics,
                        "data": log.data or "0x",
                        "blockNumber": log.block_number,
                        "transactionHash": log.transaction_hash,
                        "logIndex": log.log_index,
                    },
                )
            )
    return events


def _fetch_events_hypersync(
    web3: Web3,
    chain_id: int,
    token_messenger_address: HexAddress,
    depositor: HexAddress,
    start_block: int,
    end_block: int | None,
    hypersync_api_key: str | None,
) -> list[CCTPDepositForBurn] | None:
    """HyperSync engine. Returns ``None`` when HyperSync cannot be used for this chain."""
    if hypersync is None:
        logger.info("hypersync package not installed — falling back to eth_getLogs for CCTP burn scan")
        return None

    server = get_hypersync_server(web3, allow_missing=True)
    if server is None:
        logger.info("No HyperSync server for chain %d — falling back to eth_getLogs for CCTP burn scan", chain_id)
        return None

    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=server, bearer_token=hypersync_api_key))
    try:
        return asyncio.run(
            _fetch_events_hypersync_async(
                client,
                chain_id,
                token_messenger_address,
                depositor,
                start_block,
                end_block,
            )
        )
    except Exception as e:
        logger.warning("HyperSync CCTP burn scan failed on chain %d (%s) — falling back to eth_getLogs", chain_id, e)
        return None


def _fetch_events_get_logs(
    web3: Web3,
    chain_id: int,
    token_messenger_address: HexAddress,
    depositor: HexAddress,
    start_block: int,
    end_block: int,
) -> list[CCTPDepositForBurn]:
    """Chunked ``eth_getLogs`` fallback engine.

    Starts with the full range and quarters the chunk size whenever the
    provider rejects the request (providers cap the allowed block range,
    e.g. dRPC rejects ~1M-block requests with "exceeded max allowed range").
    """
    events: list[CCTPDepositForBurn] = []
    chunk = max(end_block - start_block + 1, 1)
    start = start_block
    while start <= end_block:
        end = min(start + chunk - 1, end_block)
        try:
            logs = web3.eth.get_logs(
                {
                    "address": token_messenger_address,
                    "fromBlock": start,
                    "toBlock": end,
                    "topics": [
                        DEPOSIT_FOR_BURN_EVENT_TOPIC0,
                        None,
                        _depositor_topic(depositor),
                    ],
                }
            )
        except Exception as e:
            if chunk <= _MIN_GET_LOGS_CHUNK:
                raise
            chunk //= 4
            logger.info(
                "eth_getLogs range %d-%d rejected on chain %d (%s) — retrying with %d-block chunks",
                start,
                end,
                chain_id,
                e,
                chunk,
            )
            continue
        events.extend(_decode_deposit_for_burn(chain_id, log) for log in logs)
        start = end + 1
    return events


def fetch_deposit_for_burn_events(
    web3: Web3,
    depositor: HexAddress,
    start_block: int,
    end_block: int | None = None,
    hypersync_api_key: str | None = None,
) -> list[CCTPDepositForBurn]:
    """Scan CCTP V2 ``DepositForBurn`` events sent by one depositor.

    Engine-agnostic: prefers HyperSync (server-side filtered streaming,
    scales to full-history scans) and falls back to chunked ``eth_getLogs``
    when HyperSync is unavailable for the chain (e.g. Anvil forks) or fails.
    Callers only ever see :class:`CCTPDepositForBurn` dataclasses, so the
    underlying engine can change without breaking them.

    :param web3:
        Web3 connection to the source chain.

    :param depositor:
        Only return burns whose tokens were debited from this address
        (e.g. a vault Safe). Matched via the indexed ``depositor`` topic.

    :param start_block:
        First block to scan.

    :param end_block:
        Last block to scan, inclusive. Defaults to the latest block.

    :param hypersync_api_key:
        Optional HyperSync bearer token. Without it HyperSync may apply
        anonymous rate limits.

    :return:
        Decoded events sorted by ``(block_number, log_index)``.
    """
    chain_id = web3.eth.chain_id
    token_messenger = get_token_messenger_v2(web3)

    if end_block is None:
        end_block = web3.eth.block_number

    logger.info(
        "Scanning DepositForBurn events: chain %d, depositor %s, blocks %d-%d",
        chain_id,
        depositor,
        start_block,
        end_block,
    )

    events = _fetch_events_hypersync(
        web3,
        chain_id,
        token_messenger.address,
        depositor,
        start_block,
        end_block,
        hypersync_api_key,
    )
    if events is None:
        events = _fetch_events_get_logs(
            web3,
            chain_id,
            token_messenger.address,
            depositor,
            start_block,
            end_block,
        )

    events.sort(key=lambda e: (e.block_number, e.log_index))
    logger.info("Found %d DepositForBurn event(s) for %s on chain %d", len(events), depositor, chain_id)
    return events
