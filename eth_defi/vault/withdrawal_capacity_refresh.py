"""Refresh only the capacity observation namespace of existing catalogue rows."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

from requests.exceptions import RequestException
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3RPCError

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.fallback import ExtraValueError, ProbablyNodeHasNoBlock
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)


def refresh_withdrawal_capacity(web3: Web3, database_path: Path, *, interval: timedelta = timedelta(hours=1), max_workers: int = 4, now: datetime | None = None, tolerance: timedelta = timedelta(0)) -> int:
    """Refresh due same-chain rows without rediscovering vaults or changing prices.

    Called under the scanner's normal writer lock before its discovery-cache
    shortcut. Provider failures affect this optional observation, not membership.
    :param web3: Verified chain provider with the scanner's retry configuration.
    :param database_path: Existing catalogue pickle, atomically replaced on success.
    :param interval: Minimum time between observations, independent of discovery.
    :param max_workers: Bounded RPC concurrency.
    :param now: UTC clock override for scheduler tests.
    :return: Number of refreshed observations.
    """
    now = now or datetime.now(UTC)
    if interval == timedelta(hours=1):
        interval = timedelta(seconds=int(os.environ.get("WITHDRAWAL_CAPACITY_REFRESH_SECONDS", "3600")))
    if interval <= timedelta(0):
        message = "Withdrawal capacity refresh interval must be positive"
        raise ValueError(message)
    if not database_path.exists():
        return 0
    database = VaultDatabase.read(database_path)
    started = monotonic()
    chain_id = web3.eth.chain_id
    head = web3.eth.get_block("latest")
    block = web3.eth.get_block(max(0, head["number"] - 3))
    web3._withdrawal_capacity_read_context = ({}, Lock())
    targets = []
    for spec, row in database.rows.items():
        if spec.chain_id != chain_id:
            continue
        old = row.get("_withdrawal_capacity_observation", {})
        stamp = old.get("checked_at")
        if stamp:
            try:
                if timedelta(0) <= now - datetime.fromisoformat(stamp) < interval - min(tolerance, interval / 2):
                    continue
            except (ValueError, TypeError):
                pass
        targets.append((spec, row))

    def read(item: tuple[Any, dict]) -> tuple[Any, dict]:
        spec, row = item
        vault = None
        try:
            vault = create_vault_instance(web3, spec.vault_address, features=row.get("features"), default_block_identifier=block["number"])
            data = vault.fetch_withdrawal_capacity_observation(block["number"]) if vault is not None else {"status": "unsupported", "reason": "No supported constructed vault"}
            data.update(block_hash=block["hash"].hex(), observed_at=datetime.fromtimestamp(block["timestamp"], UTC).isoformat())
        except (RequestException, Web3RPCError, ExtraValueError, ProbablyNodeHasNoBlock) as error:
            logger.warning("Withdrawal capacity RPC failed for %s (%s)", spec, type(error).__name__)
            data = {"status": "failed", "reason": "Withdrawal capacity provider read failed"}
        except (NotImplementedError, ContractLogicError, BadFunctionCallOutput) as error:
            data = {"status": "unsupported", "reason": f"Withdrawal capacity reader unavailable ({type(error).__name__})"}
        except Exception as error:
            logger.error("Withdrawal capacity reader failed for %s (%s)", spec, type(error).__name__)
            data = {"status": "failed", "reason": f"Withdrawal capacity measurement failed ({type(error).__name__})"}
        data["structural_warning"] = data.get("structural_warning", False) or getattr(vault, "withdrawal_capacity_structural_warning", False) or ERC4626Feature.forty_acres_like in (row.get("features") or set())
        data.pop("capacity_warning_source", None)
        data.setdefault("block_number", block["number"])
        data.setdefault("chain_id", chain_id)
        data["checked_at"] = now.isoformat()
        return spec, data

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(tqdm(pool.map(read, targets), total=len(targets), desc=f"Withdrawal capacity chain {chain_id}"))
    finally:
        distinct_reads = len(web3._withdrawal_capacity_read_context[0])
        del web3._withdrawal_capacity_read_context
    for spec, data in results:
        database.rows[spec]["_withdrawal_capacity_observation"] = data
    if results:
        database.write(database_path)
    logger.info("Withdrawal capacity refresh: chain=%s observations=%d distinct_contract_reads=%d elapsed=%.2fs", chain_id, len(results), distinct_reads, monotonic() - started)
    return len(results)
