"""Refresh capacity separately from discovery, metadata and publication."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from time import monotonic

from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.denomination import convert_usd_threshold_to_denomination
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow
from eth_defi.vault.withdrawal_capacity import CAPACITY_REFRESH_INTERVAL, LOW_TVL_CAPACITY_REFRESH_INTERVAL, TINY_TVL_THRESHOLD_USD

logger = logging.getLogger(__name__)


def get_capacity_refresh_interval(row: VaultRow) -> timedelta:
    """Choose daily or weekly checks using the shared denomination converter.

    Prefer TVL from the last capacity read, falling back to metadata NAV when
    that reader did not supply TVL. Conversion uses the existing policy rates,
    not a live oracle. Unvalued or invalid TVL uses the weekly schedule.

    :param row: Catalogue metadata with an optional capacity observation.
    :return: One day at or above USD 10,000, otherwise seven days.
    """
    observation = row.get("_withdrawal_capacity_observation") or {}
    value = observation.get("current_tvl")
    if value is None:
        # A transient read failure cannot demote a previously daily vault just
        # because its older metadata NAV predates the growth in its TVL.
        if observation.get("status") == "failed" and observation.get("refresh_interval_seconds") == int(CAPACITY_REFRESH_INTERVAL.total_seconds()):
            return CAPACITY_REFRESH_INTERVAL
        value = row.get("NAV")
    if value is None or isinstance(value, bool):
        return LOW_TVL_CAPACITY_REFRESH_INTERVAL
    try:
        tvl = Decimal(str(value))
        threshold = convert_usd_threshold_to_denomination(TINY_TVL_THRESHOLD_USD, observation.get("denomination_symbol") or row.get("Denomination") or "")
    except (InvalidOperation, ValueError, TypeError):
        return LOW_TVL_CAPACITY_REFRESH_INTERVAL
    if tvl.is_finite() and tvl >= threshold:
        return CAPACITY_REFRESH_INTERVAL
    return LOW_TVL_CAPACITY_REFRESH_INTERVAL


def select_due_capacity_vaults(database: VaultDatabase, *, now: datetime, max_vaults_per_chain: int = 100) -> dict[int, list[VaultSpec]]:
    """Select bounded, oldest-due batches before any provider is constructed.

    Persisted intervals govern the deadline until the next observation, keeping
    scheduling and export freshness consistent when metadata NAV changes.
    Unscheduled rows are due now; older overdue observations have priority.

    :param database: Already loaded catalogue, unchanged by selection.
    :param now: Naive or aware UTC scheduling clock.
    :param max_vaults_per_chain: Maximum attempted rows per chain per tick.
    :return: Due vault identities grouped by chain, oldest deadline first.
    """
    assert isinstance(max_vaults_per_chain, int) and not isinstance(max_vaults_per_chain, bool) and max_vaults_per_chain > 0, "Capacity batch limit must be a positive integer"
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    due = {}
    for spec, row in database.rows.items():
        observation = row.get("_withdrawal_capacity_observation") or {}
        seconds = observation.get("refresh_interval_seconds")
        interval = timedelta(seconds=seconds) if isinstance(seconds, int) and seconds in {86400, 604800} else get_capacity_refresh_interval(row)
        deadline = now
        stamp = observation.get("checked_at")
        if stamp:
            try:
                checked_at = datetime.fromisoformat(stamp)
                if checked_at.tzinfo is not None:
                    checked_at = checked_at.astimezone(UTC).replace(tzinfo=None)
            except (ValueError, TypeError, OverflowError):
                checked_at = None
            if checked_at is not None and checked_at <= now:
                deadline = checked_at + interval
                if deadline > now:
                    continue
        due.setdefault(spec.chain_id, []).append((deadline, spec))
    selected = {}
    for chain_id, entries in due.items():
        entries.sort(key=lambda entry: (entry[0], entry[1].vault_address))
        selected[chain_id] = [spec for _, spec in entries[:max_vaults_per_chain]]
        logger.info("Withdrawal capacity queue: chain=%s due=%d selected=%d deferred=%d", chain_id, len(entries), len(selected[chain_id]), max(0, len(entries) - max_vaults_per_chain))
    return selected


def refresh_withdrawal_capacity(web3: Web3, database_path: Path, *, max_workers: int = 4, now: datetime | None = None, database: VaultDatabase | None = None, targets: list[VaultSpec] | None = None, max_vaults_per_chain: int = 100) -> int:
    """Refresh a bounded same-chain observation batch under the writer lock.

    The coordinator supplies its once-loaded database and selected identities.
    Standalone callers get the same due filtering and batch limit. One numeric
    block and a shared read cache serve the batch. Failures produce explicit
    unknowns without replacing other metadata. Idle callers never upload.

    :param web3: Chain provider with the scanner's retry configuration.
    :param database_path: Existing metadata pickle, atomically replaced once.
    :param max_workers: Bounded concurrent adapter readers.
    :param now: Naive or aware UTC scheduling clock for deterministic tests.
    :param database: Optional catalogue shared across chain batches by the coordinator.
    :param targets: Preselected due identities for this provider's chain.
    :param max_vaults_per_chain: Batch limit for standalone selection.
    :return: Number of attempted observations persisted.
    """
    if database is None:
        if not database_path.exists():
            return 0
        database = VaultDatabase.read(database_path)
    now = now or native_datetime_utc_now()
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    started = monotonic()
    chain_id = web3.eth.chain_id
    if targets is None:
        targets = select_due_capacity_vaults(database, now=now, max_vaults_per_chain=max_vaults_per_chain).get(chain_id, [])
    assert all(spec.chain_id == chain_id for spec in targets), "Capacity provider does not match selected chain"
    if not targets:
        return 0

    head = web3.eth.get_block("latest")
    block = web3.eth.get_block(max(0, head["number"] - 3))
    web3._withdrawal_capacity_read_context = ({}, Lock())

    def read(spec: VaultSpec) -> tuple[VaultSpec, dict]:
        """Read one adapter without letting optional capacity break the batch."""
        row = database.rows[spec]
        vault = None
        try:
            vault = create_vault_instance(web3, spec.vault_address, features=row.get("features"), default_block_identifier=block["number"])
            data = vault.fetch_withdrawal_capacity_observation(block["number"]) if vault is not None else {"status": "unsupported", "reason": "No supported constructed vault"}
            if not isinstance(data, dict):
                message = "Capacity reader must return an observation dictionary"
                raise TypeError(message)
        except Exception as error:
            # This optional stage reports a failed observation and continues;
            # never persist provider exception text that may contain an RPC URL.
            logger.warning("Withdrawal capacity reader failed for %s (%s)", spec, type(error).__name__)
            data = {"status": "failed", "reason": f"Withdrawal capacity read failed ({type(error).__name__})"}
        data = dict(data)
        data.update(block_number=block["number"], block_hash=block["hash"].hex(), observed_at=datetime.fromtimestamp(block["timestamp"], UTC).isoformat(), chain_id=chain_id, vault_address=spec.vault_address, checked_at=now.replace(tzinfo=None).isoformat())
        data["structural_warning"] = data.get("structural_warning") is True or getattr(vault, "withdrawal_capacity_structural_warning", False) is True or ERC4626Feature.forty_acres_like in (row.get("features") or ())
        data.pop("capacity_warning_source", None)
        interval_row = row if data.get("status") == "failed" else {**row, "_withdrawal_capacity_observation": data}
        data["refresh_interval_seconds"] = int(get_capacity_refresh_interval(interval_row).total_seconds())
        return spec, data

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(tqdm(pool.map(read, targets), total=len(targets), desc=f"Withdrawal capacity chain {chain_id}"))
    finally:
        distinct_reads = len(web3._withdrawal_capacity_read_context[0])
        del web3._withdrawal_capacity_read_context
    for spec, data in results:
        database.rows[spec]["_withdrawal_capacity_observation"] = data
    database.write(database_path)
    logger.info("Withdrawal capacity refresh: chain=%s observations=%d distinct_contract_reads=%d elapsed=%.2fs", chain_id, len(results), distinct_reads, monotonic() - started)
    return len(results)
