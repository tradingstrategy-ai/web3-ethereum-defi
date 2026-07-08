"""Analyse Lagoon deposit and redemption queues from historical events.

This script replays Lagoon ERC-7540 request and settlement events for one or
more vaults and reports queue sizes before each settlement.

Usage:

.. code-block:: shell

    source .local-test.env
    poetry run python scripts/lagoon/analyse-vault-queues.py

Optional environment variables:

``VAULT_SPECS``
    Comma-separated vault specs in ``chain_id:address`` form. Defaults to Hub
    Capital USDC vault on Ethereum and Angmar Capital on Arbitrum.

``START_BLOCK``
    Global inclusive start block. If unset, known defaults are used for Hub and
    Angmar, otherwise scanning starts from block 0.

``END_BLOCK``
    Inclusive end block. Defaults to latest block for each chain.

``LOG_CHUNK_SIZE``
    Initial block range for ``eth_getLogs`` calls. Defaults to 50,000 blocks.

``OUTPUT_DIR``
    If set, write per-vault settlement CSV files to this directory.

``USE_HYPERSYNC``
    Use Hypersync for log reads when ``HYPERSYNC_API_KEY`` is available.
    Defaults to true. Set to ``false`` to force JSON-RPC ``eth_getLogs``.
"""

import csv
import datetime
import logging
import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from eth_typing import HexAddress
from hexbytes import HexBytes
from tabulate import tabulate
from web3 import Web3
from web3.contract.contract import ContractEvent
from web3.datastructures import AttributeDict

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.timestamp import get_block_timestamp
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flow_events import IndexedVaultFlowLog, fetch_vault_flow_logs_hypersync


logger = logging.getLogger(__name__)


#: Hub Capital USDC vault and Angmar Capital, from the xchain2 backtest issue.
DEFAULT_VAULT_SPECS = (
    "1:0xca790385506b790554571cbc9da73f0130cdcfd5",
    "42161:0x1723cb57af58efb35a013870c90fcc3d60174a4e",
)

#: First observed price-history blocks for the default vaults.
#:
#: These keep the default scan small while still covering known vault history in
#: Trading Strategy data. Set ``START_BLOCK=0`` to scan from genesis.
DEFAULT_START_BLOCKS: dict[tuple[int, str], int] = {
    (1, "0xca790385506b790554571cbc9da73f0130cdcfd5"): 23_317_667,
    (42161, "0x1723cb57af58efb35a013870c90fcc3d60174a4e"): 393_742_721,
}


@dataclass(slots=True, frozen=True)
class LagoonQueueVaultSpec:
    """Vault to analyse."""

    #: EVM chain id.
    chain_id: int

    #: Lagoon vault contract address.
    address: HexAddress


@dataclass(slots=True, frozen=True)
class QueueEvent:
    """Decoded Lagoon queue event."""

    #: Event timestamp.
    timestamp: datetime.datetime

    #: Event block number.
    block_number: int

    #: Transaction hash.
    tx_hash: HexBytes

    #: Log index inside the block.
    log_index: int

    #: Event kind.
    event_name: str

    #: Raw decoded event arguments.
    args: dict


@dataclass(slots=True, frozen=True)
class SettlementRow:
    """One Lagoon settlement transaction with reconstructed queue sizes."""

    #: Settlement timestamp.
    timestamp: datetime.datetime

    #: Settlement block number.
    block_number: int

    #: Transaction hash.
    tx_hash: HexBytes

    #: Lagoon deposit epoch id, if emitted.
    deposit_epoch_id: int | None

    #: Lagoon redeem epoch id, if emitted.
    redeem_epoch_id: int | None

    #: Deposits waiting immediately before this settlement, in underlying units.
    deposit_queue_before: Decimal

    #: Deposit assets settled in this transaction, in underlying units.
    deposit_settled: Decimal

    #: Shares minted for deposits in this transaction.
    shares_minted: Decimal

    #: Minimum deposit queue wait in days for assets settled in this transaction.
    deposit_wait_days_min: Decimal | None

    #: Maximum deposit queue wait in days for assets settled in this transaction.
    deposit_wait_days_max: Decimal | None

    #: Amount-weighted average deposit queue wait in days for this transaction.
    deposit_wait_days_average: Decimal | None

    #: Redemption shares waiting immediately before this settlement.
    redemption_queue_shares_before: Decimal

    #: Estimated redemption queue value before settlement, in underlying units.
    redemption_queue_assets_before: Decimal | None

    #: Redemption assets settled in this transaction, in underlying units.
    redemption_settled: Decimal

    #: Redemption shares burned in this transaction.
    shares_burned: Decimal

    #: Minimum redemption queue wait in days for shares settled in this transaction.
    redemption_wait_days_min: Decimal | None

    #: Maximum redemption queue wait in days for shares settled in this transaction.
    redemption_wait_days_max: Decimal | None

    #: Share-weighted average redemption queue wait in days for this transaction.
    redemption_wait_days_average: Decimal | None

    #: Share price implied by this settlement event.
    settlement_share_price: Decimal | None

    #: Deposit queue left after this settlement, in underlying units.
    deposit_queue_after: Decimal

    #: Redemption queue left after this settlement, in shares.
    redemption_queue_shares_after: Decimal


@dataclass(slots=True, frozen=True)
class QueueStats:
    """Summary statistics for a queue series."""

    #: Minimum value.
    min: Decimal | None

    #: Maximum value.
    max: Decimal | None

    #: Arithmetic average.
    average: Decimal | None


@dataclass(slots=True)
class QueueLot:
    """One FIFO request lot waiting for settlement."""

    #: Request timestamp.
    timestamp: datetime.datetime

    #: Raw remaining amount in the lot.
    raw_amount: int


@dataclass(slots=True, frozen=True)
class QueueWaitEstimate:
    """FIFO queue wait estimate for one settlement."""

    #: Minimum wait in days.
    min_days: Decimal | None

    #: Maximum wait in days.
    max_days: Decimal | None

    #: Raw-amount weighted average wait in days.
    average_days: Decimal | None


def parse_vault_specs(raw: str | None) -> list[LagoonQueueVaultSpec]:
    """Parse ``VAULT_SPECS`` environment variable.

    :param raw:
        Comma-separated ``chain_id:address`` entries.

    :return:
        Vault specs.
    """
    specs = raw.split(",") if raw else list(DEFAULT_VAULT_SPECS)
    result: list[LagoonQueueVaultSpec] = []
    for spec in specs:
        chain_raw, address = spec.strip().split(":", 1)
        result.append(
            LagoonQueueVaultSpec(
                chain_id=int(chain_raw),
                address=HexAddress(Web3.to_checksum_address(address)),
            )
        )
    return result


def decimal_or_none(raw: int | None, decimals: int) -> Decimal | None:
    """Convert a raw token amount to decimal units."""
    if raw is None:
        return None
    return Decimal(raw) / Decimal(10**decimals)


def decimal_amount(raw: int, decimals: int) -> Decimal:
    """Convert a raw token amount to decimal units."""
    return Decimal(raw) / Decimal(10**decimals)


def format_decimal(value: Decimal | None, precision: int = 2) -> str:
    """Format decimal value for tables."""
    if value is None:
        return "-"
    return f"{value:,.{precision}f}"


def format_hash(tx_hash: HexBytes) -> str:
    """Shorten transaction hash for display."""
    value = tx_hash.hex()
    return f"{value[:10]}...{value[-8:]}"


def get_event_topic(event: ContractEvent) -> str:
    """Get event topic as lowercase hex."""
    return normalise_topic(get_topic_signature_from_event(event))


def normalise_topic(topic: str) -> str:
    """Normalise an EVM event topic to ``0x``-prefixed lowercase hex."""
    topic = topic.lower()
    if not topic.startswith("0x"):
        topic = "0x" + topic
    return topic


def get_available_events(vault: LagoonVault) -> dict[str, ContractEvent]:
    """Get Lagoon events needed for queue reconstruction.

    :param vault:
        Lagoon vault instance.

    :return:
        Mapping from event name to Web3 event class.
    """
    events = vault.vault_contract.events
    result: dict[str, ContractEvent] = {
        "SettleDeposit": events.SettleDeposit,
        "SettleRedeem": events.SettleRedeem,
        "TotalAssetsUpdated": events.TotalAssetsUpdated,
    }
    if hasattr(events, "DepositRequest"):
        result["DepositRequest"] = events.DepositRequest
    if hasattr(events, "RedeemRequest"):
        result["RedeemRequest"] = events.RedeemRequest
    if hasattr(events, "Referral"):
        # Legacy Lagoon deposit request fallback. Modern vaults normally emit
        # DepositRequest, but including Referral makes the script useful for old
        # deployments as well.
        result["Referral"] = events.Referral
    return result


def fetch_logs_chunked(
    web3: Web3,
    address: HexAddress,
    topics: list[str],
    start_block: int,
    end_block: int,
    chunk_size: int,
) -> Iterator[AttributeDict]:
    """Fetch logs using chunked ``eth_getLogs`` calls.

    The function recursively halves a chunk on provider-side range errors.

    :param web3:
        Web3 connection.

    :param address:
        Contract address.

    :param topics:
        Event topic0 values to scan.

    :param start_block:
        Inclusive start block.

    :param end_block:
        Inclusive end block.

    :param chunk_size:
        Initial chunk size.

    :return:
        Decoded JSON-RPC log objects.
    """
    assert start_block <= end_block, f"Bad block range: {start_block:,} - {end_block:,}"
    current = start_block
    while current <= end_block:
        to_block = min(current + chunk_size - 1, end_block)
        yield from _fetch_logs_range(web3, address, topics, current, to_block, chunk_size)
        current = to_block + 1


def _fetch_logs_range(
    web3: Web3,
    address: HexAddress,
    topics: list[str],
    start_block: int,
    end_block: int,
    chunk_size: int,
) -> Iterator[AttributeDict]:
    """Fetch one log range, recursively shrinking on JSON-RPC range errors."""
    params = {
        "fromBlock": start_block,
        "toBlock": end_block,
        "address": Web3.to_checksum_address(address),
        "topics": [topics],
    }
    try:
        logs = web3.eth.get_logs(params)
        logger.info("Fetched %d logs from blocks %d - %d", len(logs), start_block, end_block)
        yield from logs
    except ValueError as e:
        if start_block == end_block or chunk_size <= 1:
            raise
        midpoint = (start_block + end_block) // 2
        logger.warning(
            "eth_getLogs failed for blocks %d - %d (%s), splitting range",
            start_block,
            end_block,
            e,
        )
        yield from _fetch_logs_range(web3, address, topics, start_block, midpoint, max(1, chunk_size // 2))
        yield from _fetch_logs_range(web3, address, topics, midpoint + 1, end_block, max(1, chunk_size // 2))


def use_hypersync() -> bool:
    """Check whether event logs should be fetched using Hypersync.

    Hypersync keeps full-history scans practical. JSON-RPC is still available
    as a fallback for environments without a Hypersync API key.

    :return:
        True if Hypersync should be used.
    """
    requested = os.environ.get("USE_HYPERSYNC", "true").lower() not in {"0", "false", "no"}
    return requested and bool(os.environ.get("HYPERSYNC_API_KEY"))


def create_hypersync_client(web3: Web3):
    """Create a Hypersync client for a chain.

    :param web3:
        Web3 connection used to resolve the chain id.

    :return:
        Configured Hypersync client.
    """
    import hypersync

    hypersync_url = get_hypersync_server(web3)
    return hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url=hypersync_url,
            api_token=os.environ["HYPERSYNC_API_KEY"],
        )
    )


def indexed_log_to_web3_log(log: IndexedVaultFlowLog) -> AttributeDict:
    """Convert a Hypersync log object to Web3.py event decoder input.

    :param log:
        Raw indexed log returned by Hypersync.

    :return:
        Web3.py-compatible log object.
    """
    return AttributeDict(
        {
            "address": Web3.to_checksum_address(log.address),
            "topics": [HexBytes(topic) for topic in log.topics if topic is not None],
            "data": HexBytes(log.data),
            "blockNumber": log.block_number,
            "transactionHash": HexBytes(log.transaction_hash),
            "transactionIndex": 0,
            "blockHash": HexBytes(b"\x00" * 32),
            "logIndex": log.log_index,
            "removed": False,
        }
    )


def decode_queue_events(
    vault: LagoonVault,
    start_block: int,
    end_block: int,
    chunk_size: int,
) -> list[QueueEvent]:
    """Fetch and decode all Lagoon queue-related events.

    :param vault:
        Lagoon vault instance.

    :param start_block:
        Inclusive start block.

    :param end_block:
        Inclusive end block.

    :param chunk_size:
        Initial getLogs chunk size.

    :return:
        Queue events sorted in chain order.
    """
    event_by_name = get_available_events(vault)
    event_by_topic = {get_event_topic(event): (name, event) for name, event in event_by_name.items()}
    timestamp_cache: dict[int, datetime.datetime] = {}
    decoded: list[QueueEvent] = []

    if use_hypersync():
        hypersync_client = create_hypersync_client(vault.web3)
        logs = fetch_vault_flow_logs_hypersync(
            hypersync_client=hypersync_client,
            vault_address=vault.address,
            topic0_list=list(event_by_topic.keys()),
            start_block=start_block,
            end_block=end_block,
        )
        logger.info("Fetched %d logs using Hypersync from blocks %d - %d", len(logs), start_block, end_block)

        for log in logs:
            topic = normalise_topic(log.topics[0] or "")
            name, event = event_by_topic[topic]
            event_data = event().process_log(indexed_log_to_web3_log(log))
            block_number = log.block_number
            timestamp = log.block_timestamp
            if timestamp is None:
                timestamp = timestamp_cache.get(block_number)
                if timestamp is None:
                    timestamp = get_block_timestamp(vault.web3, block_number)
                    timestamp_cache[block_number] = timestamp
            decoded.append(
                QueueEvent(
                    timestamp=timestamp,
                    block_number=block_number,
                    tx_hash=HexBytes(log.transaction_hash),
                    log_index=log.log_index,
                    event_name=name,
                    args=dict(event_data["args"]),
                )
            )

    else:
        logs = fetch_logs_chunked(
            web3=vault.web3,
            address=vault.address,
            topics=list(event_by_topic.keys()),
            start_block=start_block,
            end_block=end_block,
            chunk_size=chunk_size,
        )

        for log in logs:
            topic = normalise_topic(log["topics"][0].hex())
            name, event = event_by_topic[topic]
            event_data = event().process_log(log)
            block_number = int(log["blockNumber"])
            timestamp = timestamp_cache.get(block_number)
            if timestamp is None:
                timestamp = get_block_timestamp(vault.web3, block_number)
                timestamp_cache[block_number] = timestamp
            decoded.append(
                QueueEvent(
                    timestamp=timestamp,
                    block_number=block_number,
                    tx_hash=HexBytes(log["transactionHash"]),
                    log_index=int(log["logIndex"]),
                    event_name=name,
                    args=dict(event_data["args"]),
                )
            )

    decoded.sort(key=lambda e: (e.block_number, e.log_index))
    return decoded


def get_settlement_share_price(
    deposit_args: dict | None,
    redeem_args: dict | None,
    underlying_decimals: int,
    share_decimals: int,
) -> Decimal | None:
    """Calculate share price from settlement event arguments."""
    args = deposit_args or redeem_args
    if not args:
        return None
    raw_total_assets = args.get("totalAssets")
    raw_total_supply = args.get("totalSupply")
    total_assets = decimal_or_none(raw_total_assets, underlying_decimals)
    total_supply = decimal_or_none(raw_total_supply, share_decimals)
    if total_assets is None or total_supply is None or total_supply == 0:
        return None
    return total_assets / total_supply


def calculate_wait_days(request_timestamp: datetime.datetime, settlement_timestamp: datetime.datetime) -> Decimal:
    """Calculate elapsed queue wait in days.

    :param request_timestamp:
        Timestamp when a request entered the queue.

    :param settlement_timestamp:
        Timestamp when the queued amount was settled.

    :return:
        Elapsed time in days.
    """
    seconds = Decimal((settlement_timestamp - request_timestamp).total_seconds())
    return seconds / Decimal(60 * 60 * 24)


def settle_fifo_queue_lots(queue: deque[QueueLot], raw_amount: int, settlement_timestamp: datetime.datetime) -> QueueWaitEstimate:
    """Estimate settlement wait time by consuming queued lots FIFO.

    Lagoon settlement events are aggregate events. They do not state which
    individual request ids were settled. FIFO matching gives a deterministic
    estimate that is suitable for backtest cycle modelling.

    :param queue:
        Pending request lots.

    :param raw_amount:
        Raw amount consumed by this settlement. Deposits use raw underlying
        units, redemptions use raw share units.

    :param settlement_timestamp:
        Settlement timestamp.

    :return:
        Min/max/weighted-average wait estimate for the settled amount.
    """
    if raw_amount <= 0:
        return QueueWaitEstimate(min_days=None, max_days=None, average_days=None)

    remaining = raw_amount
    matched = 0
    weighted_days = Decimal(0)
    min_days: Decimal | None = None
    max_days: Decimal | None = None

    while remaining > 0 and queue:
        lot = queue[0]
        matched_raw_amount = min(remaining, lot.raw_amount)
        wait_days = calculate_wait_days(lot.timestamp, settlement_timestamp)
        min_days = wait_days if min_days is None else min(min_days, wait_days)
        max_days = wait_days if max_days is None else max(max_days, wait_days)
        weighted_days += Decimal(matched_raw_amount) * wait_days
        matched += matched_raw_amount
        remaining -= matched_raw_amount
        lot.raw_amount -= matched_raw_amount
        if lot.raw_amount == 0:
            queue.popleft()

    if matched == 0:
        return QueueWaitEstimate(min_days=None, max_days=None, average_days=None)

    return QueueWaitEstimate(
        min_days=min_days,
        max_days=max_days,
        average_days=weighted_days / Decimal(matched),
    )


def analyse_queue_events(
    events: Iterable[QueueEvent],
    underlying_decimals: int,
    share_decimals: int,
) -> list[SettlementRow]:
    """Replay Lagoon request and settlement events.

    Queue state is exact in raw underlying assets for deposits and exact in raw
    shares for redemptions. Redemption underlying value is estimated at the
    settlement transaction's implied share price.

    :param events:
        Chain-ordered Lagoon events.

    :param underlying_decimals:
        Underlying token decimals.

    :param share_decimals:
        Share token decimals.

    :return:
        Per-settlement queue rows.
    """
    events = list(events)
    rows: list[SettlementRow] = []
    deposit_queue_raw = 0
    redemption_queue_raw_shares = 0
    deposit_queue_lots: deque[QueueLot] = deque()
    redemption_queue_lots: deque[QueueLot] = deque()
    pending_settlement_events: list[QueueEvent] = []
    deposit_request_keys = {(event.tx_hash, event.args.get("requestId")) for event in events if event.event_name == "DepositRequest"}

    def flush_settlement_group() -> None:
        nonlocal deposit_queue_raw
        nonlocal redemption_queue_raw_shares
        nonlocal pending_settlement_events

        if not pending_settlement_events:
            return

        deposit_event = next((e for e in pending_settlement_events if e.event_name == "SettleDeposit"), None)
        redeem_event = next((e for e in pending_settlement_events if e.event_name == "SettleRedeem"), None)
        marker = pending_settlement_events[0]

        deposit_args = deposit_event.args if deposit_event else None
        redeem_args = redeem_event.args if redeem_event else None
        if deposit_args is None and redeem_args is None:
            # ``TotalAssetsUpdated`` can be emitted by a valuation-only
            # transaction before a later settlement. It is useful diagnostic
            # context, but it is not itself a queue-clearing settlement event.
            pending_settlement_events = []
            return

        share_price = get_settlement_share_price(deposit_args, redeem_args, underlying_decimals, share_decimals)

        raw_deposit_settled = int(deposit_args.get("assetsDeposited", 0)) if deposit_args else 0
        raw_shares_minted = int(deposit_args.get("sharesMinted", 0)) if deposit_args else 0
        raw_redeem_settled = int(redeem_args.get("assetsWithdrawed", 0)) if redeem_args else 0
        raw_shares_burned = int(redeem_args.get("sharesBurned", 0)) if redeem_args else 0

        deposit_queue_before = decimal_amount(deposit_queue_raw, underlying_decimals)
        redemption_queue_shares_before = decimal_amount(redemption_queue_raw_shares, share_decimals)
        redemption_queue_assets_before = redemption_queue_shares_before * share_price if share_price is not None else None
        deposit_wait = settle_fifo_queue_lots(deposit_queue_lots, raw_deposit_settled, marker.timestamp)
        redemption_wait = settle_fifo_queue_lots(redemption_queue_lots, raw_shares_burned, marker.timestamp)

        deposit_queue_raw = max(0, deposit_queue_raw - raw_deposit_settled)
        redemption_queue_raw_shares = max(0, redemption_queue_raw_shares - raw_shares_burned)

        rows.append(
            SettlementRow(
                timestamp=marker.timestamp,
                block_number=marker.block_number,
                tx_hash=marker.tx_hash,
                deposit_epoch_id=deposit_args.get("epochId") if deposit_args else None,
                redeem_epoch_id=redeem_args.get("epochId") if redeem_args else None,
                deposit_queue_before=deposit_queue_before,
                deposit_settled=decimal_amount(raw_deposit_settled, underlying_decimals),
                shares_minted=decimal_amount(raw_shares_minted, share_decimals),
                deposit_wait_days_min=deposit_wait.min_days,
                deposit_wait_days_max=deposit_wait.max_days,
                deposit_wait_days_average=deposit_wait.average_days,
                redemption_queue_shares_before=redemption_queue_shares_before,
                redemption_queue_assets_before=redemption_queue_assets_before,
                redemption_settled=decimal_amount(raw_redeem_settled, underlying_decimals),
                shares_burned=decimal_amount(raw_shares_burned, share_decimals),
                redemption_wait_days_min=redemption_wait.min_days,
                redemption_wait_days_max=redemption_wait.max_days,
                redemption_wait_days_average=redemption_wait.average_days,
                settlement_share_price=share_price,
                deposit_queue_after=decimal_amount(deposit_queue_raw, underlying_decimals),
                redemption_queue_shares_after=decimal_amount(redemption_queue_raw_shares, share_decimals),
            )
        )
        pending_settlement_events = []

    current_settlement_tx: HexBytes | None = None

    for event in events:
        is_settlement = event.event_name in {"SettleDeposit", "SettleRedeem", "TotalAssetsUpdated"}
        if is_settlement:
            if current_settlement_tx is not None and event.tx_hash != current_settlement_tx:
                flush_settlement_group()
            current_settlement_tx = event.tx_hash
            pending_settlement_events.append(event)
            continue

        flush_settlement_group()
        current_settlement_tx = None

        if event.event_name == "DepositRequest":
            raw_assets = int(event.args["assets"])
            deposit_queue_raw += raw_assets
            deposit_queue_lots.append(QueueLot(timestamp=event.timestamp, raw_amount=raw_assets))
        elif event.event_name == "Referral":
            # Legacy fallback. Modern Lagoon deposits emit DepositRequest, and
            # double-counting is prevented by skipping matching DepositRequest
            # events from the same transaction and request id.
            if (event.tx_hash, event.args.get("requestId")) in deposit_request_keys:
                continue
            raw_assets = int(event.args["assets"])
            deposit_queue_raw += raw_assets
            deposit_queue_lots.append(QueueLot(timestamp=event.timestamp, raw_amount=raw_assets))
        elif event.event_name == "RedeemRequest":
            raw_shares = int(event.args["shares"])
            redemption_queue_raw_shares += raw_shares
            redemption_queue_lots.append(QueueLot(timestamp=event.timestamp, raw_amount=raw_shares))

    flush_settlement_group()
    return rows


def calculate_stats(values: Iterable[Decimal | None]) -> QueueStats:
    """Calculate min/max/average for non-null values."""
    clean_values = [v for v in values if v is not None]
    if not clean_values:
        return QueueStats(min=None, max=None, average=None)
    return QueueStats(
        min=min(clean_values),
        max=max(clean_values),
        average=sum(clean_values, Decimal(0)) / Decimal(len(clean_values)),
    )


def calculate_weighted_wait_stats(
    rows: Iterable[SettlementRow],
    min_getter: Callable[[SettlementRow], Decimal | None],
    max_getter: Callable[[SettlementRow], Decimal | None],
    average_getter: Callable[[SettlementRow], Decimal | None],
    weight_getter: Callable[[SettlementRow], Decimal],
) -> QueueStats:
    """Calculate weighted wait-day summary statistics.

    :param rows:
        Settlement rows to scan.

    :param min_getter:
        Callable returning a row minimum wait.

    :param max_getter:
        Callable returning a row maximum wait.

    :param average_getter:
        Callable returning a row weighted average wait.

    :param weight_getter:
        Callable returning the row weight, in assets or shares.

    :return:
        Min/max/weighted-average wait days.
    """
    rows = list(rows)
    min_values = [min_getter(row) for row in rows if min_getter(row) is not None]
    max_values = [max_getter(row) for row in rows if max_getter(row) is not None]
    weighted_sum = Decimal(0)
    total_weight = Decimal(0)
    for row in rows:
        average = average_getter(row)
        weight = weight_getter(row)
        if average is None or weight <= 0:
            continue
        weighted_sum += average * weight
        total_weight += weight

    return QueueStats(
        min=min(min_values) if min_values else None,
        max=max(max_values) if max_values else None,
        average=weighted_sum / total_weight if total_weight else None,
    )


def settlement_row_to_dict(row: SettlementRow, underlying_symbol: str) -> dict[str, str]:
    """Convert a settlement row for display or CSV."""

    def optional_decimal_to_str(value: Decimal | None) -> str:
        """Format optional decimal without treating zero as missing."""
        return "" if value is None else str(value)

    return {
        "Timestamp": row.timestamp.isoformat(sep=" "),
        "Block": f"{row.block_number}",
        "Tx": row.tx_hash.hex(),
        "Deposit epoch": str(row.deposit_epoch_id or ""),
        "Redeem epoch": str(row.redeem_epoch_id or ""),
        f"Deposit queue before ({underlying_symbol})": str(row.deposit_queue_before),
        f"Deposit settled ({underlying_symbol})": str(row.deposit_settled),
        "Shares minted": str(row.shares_minted),
        "Deposit wait min (days)": optional_decimal_to_str(row.deposit_wait_days_min),
        "Deposit wait max (days)": optional_decimal_to_str(row.deposit_wait_days_max),
        "Deposit wait average (days)": optional_decimal_to_str(row.deposit_wait_days_average),
        "Redemption queue before (shares)": str(row.redemption_queue_shares_before),
        f"Redemption queue before est. ({underlying_symbol})": optional_decimal_to_str(row.redemption_queue_assets_before),
        f"Redemption settled ({underlying_symbol})": str(row.redemption_settled),
        "Shares burned": str(row.shares_burned),
        "Redemption wait min (days)": optional_decimal_to_str(row.redemption_wait_days_min),
        "Redemption wait max (days)": optional_decimal_to_str(row.redemption_wait_days_max),
        "Redemption wait average (days)": optional_decimal_to_str(row.redemption_wait_days_average),
        "Settlement share price": optional_decimal_to_str(row.settlement_share_price),
        f"Deposit queue after ({underlying_symbol})": str(row.deposit_queue_after),
        "Redemption queue after (shares)": str(row.redemption_queue_shares_after),
    }


def display_settlements(rows: list[SettlementRow], underlying_symbol: str) -> None:
    """Print settlement table."""
    table_rows = [
        {
            "Time": row.timestamp.strftime("%Y-%m-%d %H:%M"),
            "Block": f"{row.block_number:,}",
            "Tx": format_hash(row.tx_hash),
            "Dep. queue": format_decimal(row.deposit_queue_before),
            "Dep. settled": format_decimal(row.deposit_settled),
            "Dep. wait d": format_decimal(row.deposit_wait_days_average),
            "Red. queue shares": format_decimal(row.redemption_queue_shares_before, 6),
            "Red. queue est.": format_decimal(row.redemption_queue_assets_before),
            "Red. settled": format_decimal(row.redemption_settled),
            "Red. wait d": format_decimal(row.redemption_wait_days_average),
            "Share price": format_decimal(row.settlement_share_price, 6),
            "Dep. after": format_decimal(row.deposit_queue_after),
            "Red. shares after": format_decimal(row.redemption_queue_shares_after, 6),
        }
        for row in rows
    ]
    print(tabulate(table_rows, headers="keys", tablefmt="simple"))
    print(f"Amounts are in {underlying_symbol} unless otherwise noted.")


def display_stats(rows: list[SettlementRow], underlying_symbol: str) -> None:
    """Print queue summary statistics."""
    deposit_stats = calculate_stats(row.deposit_queue_before for row in rows)
    redemption_share_stats = calculate_stats(row.redemption_queue_shares_before for row in rows)
    redemption_asset_stats = calculate_stats(row.redemption_queue_assets_before for row in rows)
    deposit_wait_stats = calculate_weighted_wait_stats(
        rows,
        min_getter=lambda row: row.deposit_wait_days_min,
        max_getter=lambda row: row.deposit_wait_days_max,
        average_getter=lambda row: row.deposit_wait_days_average,
        weight_getter=lambda row: row.deposit_settled,
    )
    redemption_wait_stats = calculate_weighted_wait_stats(
        rows,
        min_getter=lambda row: row.redemption_wait_days_min,
        max_getter=lambda row: row.redemption_wait_days_max,
        average_getter=lambda row: row.redemption_wait_days_average,
        weight_getter=lambda row: row.shares_burned,
    )

    table_rows = [
        {
            "Queue": "Deposit wait (days)",
            "Min": format_decimal(deposit_wait_stats.min),
            "Max": format_decimal(deposit_wait_stats.max),
            "Average": format_decimal(deposit_wait_stats.average),
        },
        {
            "Queue": "Redemption wait (days)",
            "Min": format_decimal(redemption_wait_stats.min),
            "Max": format_decimal(redemption_wait_stats.max),
            "Average": format_decimal(redemption_wait_stats.average),
        },
        {
            "Queue": f"Deposits ({underlying_symbol})",
            "Min": format_decimal(deposit_stats.min),
            "Max": format_decimal(deposit_stats.max),
            "Average": format_decimal(deposit_stats.average),
        },
        {
            "Queue": "Redemptions (shares)",
            "Min": format_decimal(redemption_share_stats.min, 6),
            "Max": format_decimal(redemption_share_stats.max, 6),
            "Average": format_decimal(redemption_share_stats.average, 6),
        },
        {
            "Queue": f"Redemptions est. ({underlying_symbol})",
            "Min": format_decimal(redemption_asset_stats.min),
            "Max": format_decimal(redemption_asset_stats.max),
            "Average": format_decimal(redemption_asset_stats.average),
        },
    ]
    print(tabulate(table_rows, headers="keys", tablefmt="simple"))


def write_csv(rows: list[SettlementRow], vault: LagoonVault, output_dir: Path) -> Path:
    """Write settlement rows to CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"lagoon-queues-{vault.chain_id}-{vault.address.lower()}.csv"
    path = output_dir / filename
    underlying_symbol = vault.denomination_token.symbol
    with path.open("wt", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(settlement_row_to_dict(rows[0], underlying_symbol).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(settlement_row_to_dict(row, underlying_symbol))
    return path


def analyse_vault(spec: LagoonQueueVaultSpec, start_block: int | None, end_block: int | None, chunk_size: int, output_dir: Path | None) -> None:
    """Analyse one Lagoon vault."""
    json_rpc_url = read_json_rpc_url(spec.chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    assert web3.eth.chain_id == spec.chain_id, f"RPC chain mismatch: got {web3.eth.chain_id}, expected {spec.chain_id}"

    vault = LagoonVault(web3, VaultSpec(chain_id=spec.chain_id, vault_address=spec.address))
    actual_start_block = start_block
    if actual_start_block is None:
        actual_start_block = DEFAULT_START_BLOCKS.get((spec.chain_id, spec.address.lower()), 0)
    actual_end_block = end_block if end_block is not None else web3.eth.block_number

    print()
    print("=" * 100)
    print(f"Vault: {vault.name}")
    print(f"Protocol: {vault.get_protocol_name()}")
    print(f"Chain: {get_chain_name(spec.chain_id)} ({spec.chain_id})")
    print(f"Address: {vault.address}")
    print(f"Blocks: {actual_start_block:,} - {actual_end_block:,}")
    print(f"Underlying: {vault.denomination_token.symbol} ({vault.denomination_token.decimals} decimals)")
    print(f"Share token: {vault.share_token.symbol} ({vault.share_token.decimals} decimals)")

    events = decode_queue_events(vault, actual_start_block, actual_end_block, chunk_size)
    print(f"Decoded {len(events):,} queue/settlement events")

    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.event_name] = event_counts.get(event.event_name, 0) + 1
    print(tabulate([{"Event": k, "Count": v} for k, v in sorted(event_counts.items())], headers="keys", tablefmt="simple"))

    rows = analyse_queue_events(
        events,
        underlying_decimals=vault.denomination_token.decimals,
        share_decimals=vault.share_token.decimals,
    )

    print()
    print(f"Settlement events: {len(rows):,}")
    if not rows:
        return

    display_settlements(rows, vault.denomination_token.symbol)
    print()
    display_stats(rows, vault.denomination_token.symbol)

    if output_dir:
        path = write_csv(rows, vault, output_dir)
        print(f"Wrote {path}")


def main() -> None:
    """Script entry point."""
    setup_console_logging(default_log_level="INFO")

    specs = parse_vault_specs(os.environ.get("VAULT_SPECS"))
    start_block = int(os.environ["START_BLOCK"]) if os.environ.get("START_BLOCK") else None
    end_block = int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else None
    chunk_size = int(os.environ.get("LOG_CHUNK_SIZE", "50000"))
    output_dir = Path(os.environ["OUTPUT_DIR"]) if os.environ.get("OUTPUT_DIR") else None

    for spec in specs:
        analyse_vault(spec, start_block, end_block, chunk_size, output_dir)


if __name__ == "__main__":
    main()
