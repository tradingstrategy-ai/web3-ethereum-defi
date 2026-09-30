"""Rollback-compatible sidecars for scanner retry and probe scheduling.

Critical reader, metadata and cycle-state schemas remain unchanged. These
versioned JSON files can be ignored by an older release without losing history.
"""

import datetime
import json
import logging
import pickle
from decimal import Decimal
from pathlib import Path

from atomicwrites import atomic_write
from eth_typing import HexAddress
from requests.exceptions import ConnectionError, HTTPError, Timeout
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, ProviderConnectionError, TimeExhausted, Web3RPCError

from eth_defi.compat import native_datetime_utc_now
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.env import rpc_optimisations_enabled as rpc_optimisations_enabled
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.rpcdb import normalise_rpc_error

logger = logging.getLogger(__name__)


def load_rpc_scan_state(path: Path) -> dict:
    """Load a versioned sidecar, never silently discard a damaged file.

    A missing sidecar is normal on initial deployment or rollback.

    :param path: Sidecar path under the mounted pipeline directory.
    :return: Mutable entry mapping, empty only when the file is absent.
    """
    if not path.exists():
        return {}
    document = json.loads(path.read_text())
    if document.get("version") != 1 or not isinstance(document.get("entries"), dict):
        raise ValueError(f"Unsupported scanner sidecar schema: {path}")
    return document["entries"]


def save_rpc_scan_state(path: Path, entries: dict) -> None:
    """Atomically publish sidecar progress under the caller's pipeline lock.

    Parent directories are created but historical state is never replaced.

    :param path: Destination sidecar path.
    :param entries: JSON-compatible scheduling/capability mapping.
    :return: None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True) as output:
        json.dump({"version": 1, "entries": entries}, output, sort_keys=True)


def classify_rpc_scan_failure(error: BaseException) -> str:
    """Separate retryable transport failures from deterministic code errors.

    Contract reverts do not become whole-chain retry storms. Adapter version
    failures are handled per candidate before they reach this boundary.

    :param error: Original phase exception.
    :return: ``transient`` or ``internal``.
    """
    if isinstance(error, HTTPError):
        return "transient" if error.response is not None and (error.response.status_code == 429 or error.response.status_code >= 500) else "internal"
    if isinstance(error, (ConnectionError, Timeout, ProviderConnectionError, TimeExhausted, ProbablyNodeHasNoBlock)):
        return "transient"
    if isinstance(error, RuntimeError) and str(error).startswith("Monad provider cannot read state at requested end block"):
        return "transient"
    if isinstance(error, RuntimeError) and str(error).startswith("Out of multicall retries"):
        return classify_rpc_scan_failure(error.__cause__) if error.__cause__ is not None else "transient"
    if isinstance(error, (Web3RPCError, ExtraValueError)):
        code, message = normalise_rpc_error(error)
        if code == "-32090":
            return "transient"
        message = message.lower()
        if any(marker in message for marker in ("rate limit", "too many requests", "temporarily unavailable", "timeout", "timed out", "connection reset", "upstream unavailable", "header not found", "upstream does not have the requested block yet", "not enough agreement")):
            return "transient"
    return "internal"


def record_chain_backoff(path: Path, chain: str, category: str | None, now: datetime.datetime | None = None, retention_seconds: float | None = None) -> None:
    """Persist next-attempt timing independently of successful scan timestamps.

    Monad's observed state-retention margin can cap additional retry delay.
    A successful chain removes its backoff. Lock deferrals never call this.

    :param path: New backoff sidecar, never the legacy cycle-state JSON.
    :param chain: Scheduler chain name.
    :param category: Failure category, or None after success.
    :param now: Naive UTC attempt time.
    :param retention_seconds: Observed remaining state window, when available.
    :return: None.
    """
    now = now or native_datetime_utc_now()
    entries = load_rpc_scan_state(path)
    if category is None:
        entries.pop(chain, None)
    else:
        count = entries.get(chain, {}).get("consecutive_failures", 0) + 1
        maximum = 8 if chain in {"Ethereum", "Base", "Arbitrum"} else 24
        delay = min(2 ** min(count - 1, 5), maximum) * 3600
        if retention_seconds is not None:
            delay = min(delay, max(60, retention_seconds / 2))
        entries[chain] = {"last_attempt_at": now.isoformat(), "category": category, "consecutive_failures": count, "next_retry_at": (now + datetime.timedelta(seconds=delay)).isoformat()}
        logger.warning("Chain %s deferred until %s (%s, failure %d)", chain, entries[chain]["next_retry_at"], category, count)
    save_rpc_scan_state(path, entries)


def is_probe_due(entry: dict | None, now: datetime.datetime, qualified: bool) -> bool:
    """Decide admission probing before constructing an adapter.

    Previously meaningful vaults continue their ordinary historical scanning.
    A probe cache never overrides authoritative reader qualification.

    :param entry: Prior probe outcome or None for a new candidate.
    :param now: Naive UTC scheduling time.
    :param qualified: Eligibility from authoritative verified reader history.
    :return: Whether a current admission probe is needed.
    """
    return not qualified and (not entry or now >= datetime.datetime.fromisoformat(entry["next_probe_at"]))


def record_probe_result(entries: dict, address: HexAddress, now: datetime.datetime, first_seen_at: datetime.datetime, tvl_usd: str | None, error: str | None) -> None:
    """Store a bounded probe schedule and raw observation outcome.

    No qualification flag is persisted. Errors have a finite retry deadline;
    previously tiny mature vaults are checked at least weekly.

    :param entries: Mutable sidecar entries keyed by vault address.
    :param address: Lower-case vault address.
    :param now: Naive UTC observation/attempt time.
    :param first_seen_at: Deployment/first activity time from detection data.
    :param tvl_usd: Verified USD observation as decimal text, or None.
    :param error: Explicit unavailable outcome or None after success.
    :return: None.
    """
    days = 1 if error or now - first_seen_at < datetime.timedelta(days=14) else 7
    delay = datetime.timedelta(days=days)
    if tvl_usd is not None and Decimal(tvl_usd) >= Decimal(750):
        delay = datetime.timedelta(hours=8)
    entries[address] = {"attempted_at": now.isoformat(), "tvl_usd": tvl_usd, "error": error, "next_probe_at": (now + delay).isoformat()}


def save_reader_publication_journal(journal_path: Path, temporary_prices: Path, output_prices: Path, states: dict) -> None:
    """Durably prepare reader progress before replacing the price file.

    Rename preserves the temporary file's inode, size and nanosecond mtime.
    These identify the exact local publication without hashing a large Parquet.
    An interrupted pre-publication receipt cannot match the previous file.

    :param journal_path: New sidecar beside the critical reader-state pickle.
    :param temporary_prices: Verified and fsynced Parquet about to be renamed.
    :param output_prices: Final Parquet path.
    :param states: Reader state already serialised using the legacy schema.
    :return: None.
    """
    status = temporary_prices.stat()
    receipt = {"version": 1, "output": str(output_prices.resolve()), "identity": (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns), "reader_states": states}
    with atomic_write(str(journal_path), mode="wb", overwrite=True) as output:
        pickle.dump(receipt, output)


def fetch_reader_publication_progress(journal_path: Path, output_prices: Path) -> dict:
    """Recover committed local progress when legacy state persistence failed.

    The journal is usable only if the published file matches its exact local
    identity. A receipt from an unpublished or replaced output yields no state.
    Invalid receipts fail loudly rather than discard critical history.

    :param journal_path: Optional publication sidecar from the upgraded writer.
    :param output_prices: Current price Parquet.
    :return: Legacy-shaped reader state ready to merge monotonically.
    """
    if not journal_path.exists():
        return {}
    with journal_path.open("rb") as source:
        receipt = pickle.load(source)
    if receipt.get("version") != 1 or not isinstance(receipt.get("reader_states"), dict):
        raise ValueError(f"Invalid reader publication journal: {journal_path}")
    if not output_prices.exists() or receipt["output"] != str(output_prices.resolve()):
        return {}
    status = output_prices.stat()
    if receipt["identity"] != (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns):
        return {}
    logger.warning("Recovering committed reader progress from %s", journal_path)
    return receipt["reader_states"]


def is_metadata_due(entry: dict | None, features: list[str], classifier_version: str, now: datetime.datetime, force: bool = False) -> bool:
    """Refresh metadata when protocol identity changes or a deadline arrives.

    Missing provenance is refreshed once. Explicit operator refreshes bypass
    the sidecar without altering the event discovery cursor.

    :param entry: Last metadata observation or negative result.
    :param features: Current sorted classification feature names.
    :param classifier_version: Current classification implementation hash.
    :param now: Naive UTC scheduling time.
    :param force: Explicit operator refresh.
    :return: Whether adapter metadata must be read.
    """
    return force or not entry or entry.get("features") != features or entry.get("classifier_version") != classifier_version or now >= datetime.datetime.fromisoformat(entry.get("next_attempt_at", entry["checked_at"]))


def record_metadata_failure(pending: dict, observations: dict, address: HexAddress, features: list[str], classifier_version: str, now: datetime.datetime, category: str) -> None:
    """Bound candidate retries and move persistent negatives out of the queue.

    Unsupported versions and deterministic contract failures are checked weekly.
    Transient failures receive up to three daily attempts before weekly checks.
    New classification or explicit force still makes negatives immediately due.

    :param pending: Mutable resumable candidate queue.
    :param observations: Mutable metadata status catalogue.
    :param address: Lower-case vault address.
    :param features: Sorted current protocol features.
    :param classifier_version: Current feature implementation hash.
    :param now: Naive UTC failure time.
    :param category: Redacted failure category, never provider error text.
    :return: None.
    """
    count = pending.get(address, {}).get("failure_count", 0) + 1
    if category != "transient" or count >= 3:
        pending.pop(address, None)
        observations[address] = {"checked_at": now.isoformat(), "next_attempt_at": (now + datetime.timedelta(days=7)).isoformat(), "status": "unavailable", "category": category, "features": features, "classifier_version": classifier_version}
    else:
        pending[address] = {"next_attempt_at": (now + datetime.timedelta(days=1)).isoformat(), "features": features, "failure_count": count, "category": category}


def is_contract_read_failure(error: BaseException) -> bool:
    """Recognise expected contract failures without hiding programmer errors.

    Providers vary between Solidity revert errors and plain JSON-RPC VM errors.
    Only known contract-execution markers qualify for per-candidate deferral.

    :param error: Original constructor/reader exception.
    :return: Whether the error describes unavailable contract execution/data.
    """
    if isinstance(error, (ContractLogicError, BadFunctionCallOutput)):
        return True
    if isinstance(error, (Web3RPCError, ExtraValueError)):
        _code, message = normalise_rpc_error(error)
        return any(marker in message.lower() for marker in ("execution reverted", "invalid opcode", "out of gas", "vm execution error"))
    # Missing node state is a provider outcome, not a deterministic contract revert.
    return False


def fetch_remaining_state_budget(boundary: dict, committed_block: int, block_seconds: float, now: datetime.datetime) -> float:
    """Estimate time before unread state is evicted from the measured window.

    A fresh capability observation does not reset the age of unscanned history.
    The estimate subtracts both head-to-committed-block lag and observation age.

    :param boundary: Actual bounded provider-state observation.
    :param committed_block: Last durably committed chain reader block.
    :param block_seconds: Configured approximate block interval.
    :param now: Naive UTC scheduling time.
    :return: Remaining seconds, possibly negative to expose an existing gap.
    """
    age = max(0, (now - datetime.datetime.fromisoformat(boundary["checked_at"])).total_seconds())
    unread = max(0, boundary["head_block"] - committed_block) * block_seconds
    return boundary["retention_seconds"] - age - unread
