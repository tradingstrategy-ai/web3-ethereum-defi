#!/usr/bin/env python3
"""Backfill all Enzyme Blue and Onyx vaults into the shared vault pipeline.

This targeted migration discovers Enzyme Blue vaults from the official
Dispatcher event and Enzyme Onyx vaults from the Base
``SharesFactory.ProxyDeployed`` factory event. It does not restart generic
whole-chain lead discovery. Instead, it upserts only Enzyme leads and metadata
and rewrites only the selected Enzyme raw and cleaned price histories.

The script needs archive-capable RPC connections for each selected chain and
``HYPERSYNC_API_KEY`` for factory-event discovery and timestamp caching. The
dense per-chain timestamp caches at ``~/.tradingstrategy/block-timestamp/``
must be retained or restored before a full history rewrite.

Usage::

    source .local-test.env && poetry run python scripts/enzyme/backfill-history.py

Environment variables:

- ``DRY_RUN``: print the discovered migration plan without writing, default ``false``.
- ``ENZYME_SCAN_PRICES``: scan historical prices and TVL, default ``true``.
- ``ENZYME_CLEAN_PRICES``: replace selected cleaned histories, default ``true``.
- ``ENZYME_REWRITE_TARGETED``: rewrite every selected vault from deployment,
  default ``false``. Vaults without price rows always start from their factory
  creation block.
- ``ENZYME_DISCOVERY_START_BLOCK``: optional factory-event scan start block.
- ``START_BLOCK`` / ``END_BLOCK``: optional inclusive price scan bounds.
- ``FREQUENCY``: historical sampling frequency, ``1h`` or ``1d``, default ``1d``.
- ``MAX_WORKERS``: historical multicall worker count, default ``8``.
- ``ENZYME_METADATA_BATCH_SIZE``: resumable current-metadata batch size, default ``128``.
- ``ENZYME_CHECKPOINT_PATH``: durable migration checkpoint path, defaulting to
  ``enzyme-backfill-history-state.json`` beside the vault database.
- ``PIPELINE_LOCK_TIMEOUT``: seconds to wait for the shared scanner writer
  lock, default ``60``.
- ``VAULT_DB_PATH``, ``UNCLEANED_PRICE_DATABASE``, ``CLEANED_PRICE_DATABASE``,
  ``READER_STATE_DATABASE``: optional pipeline state paths.
"""

import asyncio
import datetime
import itertools
import json
import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import hypersync
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from atomicwrites import atomic_write
from eth_typing import HexAddress
from filelock import Timeout as FileLockTimeout
from hypersync import BlockField, LogField
from joblib import Parallel, delayed
from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.enzyme.blue_discovery import EnzymeBlueVaultFactoryCandidate, create_enzyme_blue_factory_candidate, fetch_enzyme_blue_dispatchers_for_chain, fetch_enzyme_blue_first_block, fetch_enzyme_blue_vault_deployed_event_topic, is_enzyme_blue_factory_log
from eth_defi.enzyme.offchain_metadata import ONYX_PUBLIC_DESCRIPTION_UNAVAILABLE, create_enzyme_vault_link
from eth_defi.enzyme.onyx_discovery import ENZYME_BASE_CHAIN_ID, EnzymeDepositHandlerUpdate, EnzymeVaultFactoryCandidate, create_enzyme_factory_candidate, decode_enzyme_deposit_handler_event, fetch_enzyme_deposit_handler_event_topics, fetch_enzyme_shares_deployed_event_topic, fetch_enzyme_shares_factories_for_chain, is_enzyme_factory_log, reconstruct_active_enzyme_deposit_handlers
from eth_defi.enzyme.onyx_permission import fetch_onyx_current_deposit_permissions
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import ERC4262VaultDetection, PotentialVaultMatch, create_enzyme_blue_factory_detection, create_enzyme_blue_potential_vault_match, create_enzyme_factory_detection, create_enzyme_potential_vault_match
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess
from eth_defi.hypersync.hypersync_timestamp import is_hypersync_retryable_runtime_error
from eth_defi.hypersync.session import open_hypersync_stream
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

#: Base block where the official Enzyme Onyx deployment became live.
#: https://github.com/enzymefinance/onyx-sdk/blob/main/packages/environment/src/deployments/base.ts
ENZYME_BASE_DEPLOYMENT_BLOCK = 35_306_000

EnzymeFactoryCandidate = EnzymeVaultFactoryCandidate | EnzymeBlueVaultFactoryCandidate

CHECKPOINT_VERSION = 1

#: Increment when current metadata semantics change and existing rows need one
#: new adapter read even when a resumable checkpoint retains the same head.
ENZYME_CURRENT_METADATA_VERSION = 2

#: Increment only when Onyx deposit-handler classification changes. Keeping
#: this separate from the general metadata version avoids rereading thousands
#: of unaffected Blue vaults for an Onyx-only migration.
ENZYME_ONYX_PERMISSION_VERSION = 1

#: Increment only when the explicit public Onyx-description policy changes.
#: This refreshes legacy generic copy without rereading unaffected Blue rows.
ENZYME_ONYX_DESCRIPTION_VERSION = 1

#: The migration scans these chains independently exactly once.  Blue discovery
#: uses one persistent Dispatcher event stream per chain; Base remains the
#: separate Onyx SharesFactory integration.
ENZYME_MIGRATION_CHAINS = {
    1: "JSON_RPC_ETHEREUM",
    137: "JSON_RPC_POLYGON",
    8453: "JSON_RPC_BASE",
    42161: "JSON_RPC_ARBITRUM",
}


@dataclass(slots=True)
class EnzymeChainRun:
    """Keep the fixed discovery scope for one resumable chain migration.

    The checkpoint fixes its end block and discovered candidates before
    metadata or price writes begin. Keeping these fields together avoids
    accidentally mixing one chain's RPC connection, candidate set, or
    historical boundary with another chain.

    """

    #: Connected Web3 instance for the selected chain.
    web3: Web3

    #: Configured multi-provider RPC URL string.
    json_rpc_url: str

    #: Fixed inclusive migration end block.
    end_block: int

    #: Factory-discovered Blue and/or Onyx vaults.
    candidates: list[EnzymeFactoryCandidate]

    #: Event-reconstructed current handler membership for every Onyx vault.
    active_onyx_deposit_handlers: dict[HexAddress, tuple[HexAddress, ...]]


@dataclass(slots=True)
class EnzymeChainDiscovery:
    """One complete per-chain event discovery result.

    Factory deployments and mutable Onyx handler membership are collected by
    one Hypersync stream.  The result is checkpointed before current metadata
    reads, so an interrupted run never rescans the chain merely to recover the
    non-enumerable Onyx handler set.

    """

    #: Factory-confirmed Blue and Onyx vaults.
    candidates: list[EnzymeFactoryCandidate]

    #: Current handler membership through the same fixed discovery end block.
    active_onyx_deposit_handlers: dict[HexAddress, tuple[HexAddress, ...]]


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name: Environment variable name.
    :param default: Value when the variable is unset.
    :return: Parsed boolean value.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_optional_int_env(name: str) -> int | None:
    """Parse an optional integer environment variable.

    :param name: Environment variable name.
    :return: Integer value, or ``None`` if unset.
    """

    value = os.environ.get(name, "").strip()
    return int(value) if value else None


def parse_path_env(name: str, default: Path) -> Path:
    """Resolve a filesystem path from an environment variable.

    :param name: Environment variable name.
    :param default: Default pipeline path.
    :return: Expanded selected path.
    """

    return Path(os.environ.get(name, str(default))).expanduser()


def read_checkpoint(path: Path) -> dict:
    """Load incomplete progress or initialise a new migration.

    A completed run starts fresh on a later invocation so new vault deployments
    are discovered. An incomplete run retains its fixed discovery heads and
    candidates, preventing duplicate factory scans after a failure.

    :param path: Checkpoint JSON path.
    :return: Valid serialisable checkpoint state.
    """

    if not path.exists():
        return {"version": CHECKPOINT_VERSION, "chains": {}, "cleaned": False}
    with path.open() as inp:
        checkpoint = json.load(inp)
    if checkpoint.get("version") != CHECKPOINT_VERSION or checkpoint.get("complete"):
        return {"version": CHECKPOINT_VERSION, "chains": {}, "cleaned": False}
    return checkpoint


def write_checkpoint(path: Path, checkpoint: dict) -> None:
    """Atomically save progress after a durable metadata or price write.

    :param path: Checkpoint JSON path.
    :param checkpoint: Complete JSON-serialisable migration state.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True, encoding="utf-8") as out:
        json.dump(checkpoint, out, indent=2, sort_keys=True)


def serialise_candidate(candidate: EnzymeFactoryCandidate) -> dict:
    """Convert a factory candidate to durable JSON."""

    common = {
        "chain": candidate.chain,
        "address": candidate.address,
        "created_block": candidate.created_block,
        "created_at": candidate.created_at.isoformat(),
        "transaction_hash": candidate.transaction_hash,
        "log_index": candidate.log_index,
    }
    if isinstance(candidate, EnzymeBlueVaultFactoryCandidate):
        return common | {
            "protocol": "blue",
            "dispatcher_address": candidate.dispatcher_address,
            "fund_deployer": candidate.fund_deployer,
            "vault_accessor": candidate.vault_accessor,
            "fund_name": candidate.fund_name,
        }
    return common | {"protocol": "onyx", "factory_address": candidate.factory_address}


def deserialise_candidate(data: dict) -> EnzymeFactoryCandidate:
    """Restore a factory candidate from durable JSON."""

    common = {
        "chain": int(data["chain"]),
        "address": HexAddress(data["address"]),
        "created_block": int(data["created_block"]),
        "created_at": datetime.datetime.fromisoformat(data["created_at"]),
        "transaction_hash": data["transaction_hash"],
        "log_index": int(data["log_index"]),
    }
    if data["protocol"] == "blue":
        return EnzymeBlueVaultFactoryCandidate(
            **common,
            dispatcher_address=HexAddress(data["dispatcher_address"]),
            fund_deployer=HexAddress(data["fund_deployer"]),
            vault_accessor=HexAddress(data["vault_accessor"]),
            fund_name=data["fund_name"],
        )
    return EnzymeVaultFactoryCandidate(**common, factory_address=HexAddress(data["factory_address"]))


def resolve_frequency() -> Literal["1h", "1d"]:
    """Read the requested historical sampling frequency.

    :return: Validated hourly or daily scan frequency.
    :raises ValueError: If ``FREQUENCY`` is unsupported.
    """

    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency not in {"1h", "1d"}:
        message = f"Unsupported FREQUENCY: {frequency}"
        raise ValueError(message)
    return cast(Literal["1h", "1d"], frequency)


def create_factory_query(chain_id: int, start_block: int, end_block: int) -> hypersync.Query:
    """Build one per-chain Hypersync query for factories and Onyx handlers.

    Factory events and the two distinctive Onyx Shares handler-membership
    topics are streamed together. Handler events are filtered against
    factory-confirmed Shares addresses after the stream completes. This keeps
    discovery to one historical scan per chain without trusting arbitrary
    contracts that emit a copied event signature.

    :param chain_id: Target EVM chain id.
    :param start_block: Inclusive first chain block.
    :param end_block: Inclusive final chain block.
    :return: Factory-event query with log block and timestamp fields.
    """

    return hypersync.Query(
        from_block=start_block,
        to_block=end_block,
        logs=[
            *(
                [
                    hypersync.LogSelection(
                        address=fetch_enzyme_shares_factories_for_chain(chain_id),
                        topics=[[fetch_enzyme_shares_deployed_event_topic()]],
                    )
                ]
                if fetch_enzyme_shares_factories_for_chain(chain_id)
                else []
            ),
            *(
                [
                    hypersync.LogSelection(
                        topics=[list(fetch_enzyme_deposit_handler_event_topics())],
                    )
                ]
                if chain_id == ENZYME_BASE_CHAIN_ID
                else []
            ),
            *(
                [
                    hypersync.LogSelection(
                        address=fetch_enzyme_blue_dispatchers_for_chain(chain_id),
                        topics=[[fetch_enzyme_blue_vault_deployed_event_topic()]],
                    )
                ]
                if fetch_enzyme_blue_dispatchers_for_chain(chain_id)
                else []
            ),
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[LogField.BLOCK_NUMBER, LogField.LOG_INDEX, LogField.ADDRESS, LogField.TRANSACTION_HASH, LogField.TOPIC0, LogField.TOPIC1, LogField.TOPIC2, LogField.TOPIC3, LogField.DATA],
        ),
    )


async def fetch_enzyme_chain_discovery_async(web3: Web3, start_block: int, end_block: int) -> EnzymeChainDiscovery:
    """Fetch factory candidates and Onyx handler events in one stream.

    :param web3: Connected Web3 instance for a selected Enzyme chain.
    :param start_block: Inclusive factory-event scan start block.
    :param end_block: Inclusive factory-event scan end block.
    :return:
        Deduplicated candidates and active Onyx handler membership.
    :raises RuntimeError: If a Hypersync client is not configured.
    """

    config = configure_hypersync_from_env(web3)
    if config.hypersync_client is None:
        message = "Enzyme factory discovery requires Hypersync; set SCAN_BACKEND=auto or hypersync and HYPERSYNC_API_KEY"
        raise RuntimeError(message)

    chain_id = web3.eth.chain_id
    receiver = await open_hypersync_stream(config.hypersync_client, create_factory_query(chain_id, start_block, end_block))
    candidates: dict[VaultSpec, EnzymeFactoryCandidate] = {}
    handler_updates: list[EnzymeDepositHandlerUpdate] = []
    handler_topics = set(fetch_enzyme_deposit_handler_event_topics())
    while response := await receiver.recv():
        block_timestamps = {block.number: native_datetime_utc_fromtimestamp(int(block.timestamp, 16) if isinstance(block.timestamp, str) else block.timestamp) for block in response.data.blocks}
        for log in response.data.logs:
            if is_enzyme_factory_log(chain_id, log.address, log.topics[0]):
                timestamp = block_timestamps.get(log.block_number)
                if timestamp is None:
                    raise RuntimeError(f"Hypersync response omitted timestamp for Enzyme factory log block {log.block_number}")
                candidate = create_enzyme_factory_candidate(web3, chain_id, log, timestamp)
            elif is_enzyme_blue_factory_log(chain_id, log.address, log.topics[0]):
                timestamp = block_timestamps.get(log.block_number)
                if timestamp is None:
                    raise RuntimeError(f"Hypersync response omitted timestamp for Enzyme factory log block {log.block_number}")
                candidate = create_enzyme_blue_factory_candidate(web3, chain_id, log, timestamp)
            elif chain_id == ENZYME_BASE_CHAIN_ID and log.topics[0] in handler_topics:
                handler_updates.append(decode_enzyme_deposit_handler_event(log))
                continue
            else:
                continue
            candidates[VaultSpec(chain_id, candidate.address.lower())] = candidate

    sorted_candidates = sorted(candidates.values(), key=lambda candidate: (candidate.created_block, candidate.address.lower()))
    onyx_addresses = {HexAddress(candidate.address.lower()) for candidate in sorted_candidates if isinstance(candidate, EnzymeVaultFactoryCandidate)}
    active_handlers = reconstruct_active_enzyme_deposit_handlers(onyx_addresses, handler_updates)
    return EnzymeChainDiscovery(sorted_candidates, active_handlers)


def fetch_enzyme_chain_discovery(
    web3: Web3,
    start_block: int,
    end_block: int,
    attempts: int = 3,
    retry_sleep: float = 30.0,
) -> EnzymeChainDiscovery:
    """Synchronously fetch one complete Enzyme chain discovery state.

    Hypersync deliberately exposes transient server rate limits and near-head
    pagination failures to Python with no Rust-side retries. Retry the complete
    per-chain stream here because candidates are persisted only after the
    stream finishes, making each attempt atomic from the checkpoint's point of
    view. A completed chain is never scanned again when the migration resumes.

    :param web3: Connected Web3 instance for a selected Enzyme chain.
    :param start_block: Inclusive factory-event scan start block.
    :param end_block: Inclusive factory-event scan end block.
    :param attempts: Maximum complete stream attempts for retryable Hypersync
        failures.
    :param retry_sleep: Seconds to wait between retryable stream attempts.
    :return:
        Canonical candidates and current Onyx handler membership.
    :raises RuntimeError: If all retry attempts fail or Hypersync returns a
        non-recoverable error.
    """

    assert attempts >= 1
    for attempt in range(1, attempts + 1):
        try:
            return asyncio.run(fetch_enzyme_chain_discovery_async(web3, start_block, end_block))
        except RuntimeError as e:
            if not is_hypersync_retryable_runtime_error(e) or attempt == attempts:
                raise
            logger.warning(
                "Retryable Hypersync factory discovery failure on chain %d; retrying complete stream in %.1f seconds (%d/%d): %s",
                web3.eth.chain_id,
                retry_sleep,
                attempt,
                attempts,
                e,
            )
            time.sleep(retry_sleep)

    message = "Unreachable Enzyme Hypersync retry state"
    raise AssertionError(message)


def create_enzyme_lead(candidate: EnzymeFactoryCandidate) -> PotentialVaultMatch:
    """Create a persisted factory-backed lead for one Enzyme vault.

    :param candidate: Factory-discovered Enzyme vault.
    :return: Lead eligible for non-ERC-4626 price scanning.
    """

    if isinstance(candidate, EnzymeBlueVaultFactoryCandidate):
        return create_enzyme_blue_potential_vault_match(candidate)
    return create_enzyme_potential_vault_match(candidate)


def create_enzyme_detection(
    candidate: EnzymeFactoryCandidate,
    current_deposit_permissions: dict[HexAddress, VaultDepositPermission] | None = None,
) -> ERC4262VaultDetection:
    """Create a direct detection with any indexed Onyx permission snapshot.

    :param candidate:
        Factory-confirmed Blue or Onyx vault.
    :param current_deposit_permissions:
        Lower-case Onyx Shares addresses mapped to a fixed-block result.
    :return:
        Detection ready for the shared metadata reader.
    """

    if isinstance(candidate, EnzymeBlueVaultFactoryCandidate):
        return create_enzyme_blue_factory_detection(candidate)
    permission = (current_deposit_permissions or {}).get(HexAddress(candidate.address.lower()))
    return create_enzyme_factory_detection(candidate, permission.value if permission is not None else None)


def read_vault_database(path: Path) -> VaultDatabase:
    """Load the existing vault database without affecting unrelated entries.

    :param path: Metadata database path.
    :return: Existing or empty vault database.
    """

    return VaultDatabase.read(path) if path.exists() else VaultDatabase()


def read_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Load persisted reader state.

    :param path: Reader-state pickle path.
    :return: Existing state mapping, or an empty mapping.
    """

    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Persist reader state atomically.

    :param path: Reader-state pickle path.
    :param states: Complete post-scan state mapping.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def fetch_latest_existing_price_blocks(price_path: Path, chain_id: int, addresses: set[str]) -> dict[str, int]:
    """Read the latest raw-price block for each selected Enzyme vault.

    :param price_path: Uncleaned historical price parquet path.
    :param addresses: Lower-case Enzyme Shares or Blue VaultProxy addresses.
    :return: Mapping from address to its latest stored block on the chain.
    """

    if not price_path.exists() or not addresses:
        return {}

    table = pq.read_table(price_path, columns=["chain", "address", "block_number"])
    filtered = table.filter(
        pc.and_(
            pc.equal(table["chain"], chain_id),
            pc.is_in(table["address"], value_set=pa.array(sorted(addresses))),
        )
    )
    latest_blocks: dict[str, int] = {}
    for address, block_number in zip(filtered["address"].to_pylist(), filtered["block_number"].to_pylist(), strict=True):
        latest_blocks[address] = max(latest_blocks.get(address, 0), block_number)
    return latest_blocks


def fetch_vault_price_start_block(
    candidate: EnzymeFactoryCandidate,
    latest_blocks: dict[str, int],
    reader_states: dict[VaultSpec, dict],
    *,
    rewrite_targeted: bool,
) -> int:
    """Resolve the first missing price block for one Enzyme vault.

    :param candidate: Target vault.
    :param latest_blocks: Existing latest price blocks by vault address.
    :param reader_states: Persisted historical-reader states by vault.
    :param rewrite_targeted: Whether to rewrite the target from creation.
    :return: Inclusive first block requiring a historical read.
    """

    explicit_start_block = parse_optional_int_env("START_BLOCK")
    if explicit_start_block is not None:
        return explicit_start_block
    address = candidate.address.lower()
    if rewrite_targeted:
        return candidate.created_block
    state = reader_states.get(VaultSpec(candidate.chain, address))
    if state and (last_block := state.get("last_block")):
        # Historical rows are change-compressed.  A last row from weeks ago
        # can still represent a poll at the head, so it must not trigger a
        # full re-read on every migration execution.
        return int(last_block) + 1
    if address not in latest_blocks:
        return candidate.created_block
    return latest_blocks[address] + 1


def build_vaults(web3: Web3, candidates: list[EnzymeFactoryCandidate], token_cache: TokenDiskCache) -> list[VaultBase]:
    """Construct direct ``VaultBase`` adapters for selected Enzyme vaults.

    :param web3: Connected Web3 instance for the candidates' chain.
    :param candidates: Factory-discovered vaults.
    :param token_cache: Shared token metadata cache.
    :return: Enzyme vault adapters annotated with their deployment blocks.
    """

    vaults = []
    for candidate in candidates:
        feature = ERC4626Feature.enzyme_blue_like if isinstance(candidate, EnzymeBlueVaultFactoryCandidate) else ERC4626Feature.enzyme_onyx_like
        vault = create_vault_instance(web3, candidate.address, {feature}, token_cache=token_cache)
        if vault is None:
            message = f"Could not create Enzyme vault adapter for {candidate.address}"
            raise RuntimeError(message)
        vault.first_seen_at_block = candidate.created_block
        vaults.append(vault)
    return vaults


def scan_price_history(  # noqa: PLR0917 - explicit operational arguments make this production mutation auditable.
    web3: Web3,
    json_rpc_url: str,
    token_cache: TokenDiskCache,
    reader_states: dict[VaultSpec, dict],
    candidates: list[EnzymeFactoryCandidate],
    price_path: Path,
    end_block: int,
    frequency: Literal["1h", "1d"],
    max_workers: int,
    *,
    rewrite_targeted: bool,
) -> dict | None:
    """Backfill one address-scoped batch of Enzyme historical prices and TVL.

    :param web3: Connected Web3 instance for the candidate chain.
    :param json_rpc_url: Archive RPC configuration for worker connections.
    :param token_cache: Shared token metadata cache.
    :param reader_states: Persisted state that is updated in place.
    :param candidates: Selected Enzyme factory candidates.
    :param price_path: Uncleaned historical price parquet path.
    :param end_block: Inclusive final historical block.
    :param frequency: Hourly or daily sampling frequency.
    :param max_workers: Threaded historical multicall worker count.
    :param rewrite_targeted: Whether to rebuild all selected histories.
    :return: Scan result, or ``None`` when every target is already caught up.
    """

    addresses = {candidate.address.lower() for candidate in candidates}
    latest_blocks = fetch_latest_existing_price_blocks(price_path, candidates[0].chain, addresses)
    selected_candidates = [candidate for candidate in candidates if fetch_vault_price_start_block(candidate, latest_blocks, reader_states, rewrite_targeted=rewrite_targeted) <= end_block]
    if not selected_candidates:
        logger.info("All %d Enzyme vaults are caught up at chain %d block %d", len(candidates), candidates[0].chain, end_block)
        return None

    start_block = min(fetch_vault_price_start_block(candidate, latest_blocks, reader_states, rewrite_targeted=rewrite_targeted) for candidate in selected_candidates)
    target_specs = {VaultSpec(candidate.chain, candidate.address.lower()) for candidate in selected_candidates}
    scan_reader_states = {spec: state for spec, state in reader_states.items() if spec not in target_specs}
    config = configure_hypersync_from_env(web3)

    # Keep the complete chain target set in one reader invocation. Its Enzyme
    # historical readers emit share-price and supply calls that the shared
    # scanner combines into Multicall3 at each sampled block. The reader itself
    # rotates only the failed block through fallback RPC endpoints, avoiding an
    # expensive and redundant whole-chain retry.
    result = scan_historical_prices_to_parquet(
        output_fname=price_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
        vaults=build_vaults(web3, selected_candidates, token_cache),
        token_cache=token_cache,
        start_block=start_block,
        end_block=end_block,
        max_workers=max_workers,
        frequency=frequency,
        reader_states=scan_reader_states,
        hypersync_client=config.hypersync_client,
        vault_addresses={candidate.address.lower() for candidate in selected_candidates},
    )
    reader_states.clear()
    reader_states.update(result["reader_states"])
    logger.info("Enzyme historical scan result:\n%s", pformat_scan_result(result))
    return result


def has_complete_current_metadata(vault_db: VaultDatabase, candidate: EnzymeFactoryCandidate) -> bool:
    """Check whether a persisted Enzyme row satisfies the migration contract.

    Both Enzyme architectures require a conclusive current permission result.
    Blue reads its PolicyManager; Onyx uses the chain-level active handler
    index and batched current handler configuration. Name, symbol, denomination
    token and appropriate architecture-specific descriptions are mandatory
    catalogue identity fields. Blue must have both descriptions; Onyx must have
    no short description and the explicit public unavailable marker. Current
    NAV, share supply, and fees remain best-effort values: deprecated or
    invalid vault configurations can no longer execute their valuation or
    release extension calls, and repeatedly retrying them cannot recover those
    values.

    :param vault_db: Existing vault metadata database.
    :param candidate: Factory-discovered Enzyme vault.
    :return: ``True`` when all required current catalogue fields are present.
    """

    row = vault_db.rows.get(VaultSpec(candidate.chain, candidate.address))
    if row is None or not row.get("Name") or (row.get("Name") or "").startswith("<broken"):
        return False
    if not row.get("Symbol") or not row.get("_denomination_token"):
        return False
    if isinstance(candidate, EnzymeVaultFactoryCandidate):
        if row.get("_short_description") is not None or row.get("_description") != ONYX_PUBLIC_DESCRIPTION_UNAVAILABLE:
            return False
    elif not row.get("_short_description") or not row.get("_description"):
        return False
    try:
        permission = VaultDepositPermission(row.get("_deposit_permission", VaultDepositPermission.unknown.value))
    except (TypeError, ValueError):
        permission = VaultDepositPermission.unknown
    return permission is not VaultDepositPermission.unknown


def should_refresh_metadata(vault_db: VaultDatabase, candidate: EnzymeFactoryCandidate, end_block: int) -> bool:
    """Determine whether this Enzyme vault needs a metadata read.

    Every attempted row records the fixed discovery end block. This prevents a
    permanently unreadable or inconclusive contract from being called again
    after an interruption at the same checkpoint, while a later completed run
    with a newer end block can retry it.

    :param vault_db: Existing vault metadata database.
    :param candidate: Factory-discovered Enzyme vault.
    :param end_block: Fixed current-state block for this migration attempt.
    :return: ``True`` for missing, broken, or explicitly refreshed rows.
    """

    if parse_bool_env("ENZYME_REFRESH_EXISTING_METADATA", default=False):
        return True
    row = vault_db.rows.get(VaultSpec(candidate.chain, candidate.address))
    if row is not None and row.get("_enzyme_metadata_version") != ENZYME_CURRENT_METADATA_VERSION:
        return True
    if isinstance(candidate, EnzymeVaultFactoryCandidate) and row is not None and row.get("_enzyme_onyx_permission_version") != ENZYME_ONYX_PERMISSION_VERSION:
        return True
    if isinstance(candidate, EnzymeVaultFactoryCandidate) and row is not None and row.get("_enzyme_onyx_description_version") != ENZYME_ONYX_DESCRIPTION_VERSION:
        return True
    if row is not None and int(row.get("_enzyme_metadata_checked_block") or 0) >= end_block:
        return False
    return not has_complete_current_metadata(vault_db, candidate)


def update_enzyme_vault_links(vault_db: VaultDatabase, candidates: Iterable[EnzymeFactoryCandidate]) -> int:
    """Replace generic persisted Enzyme links without any network reads.

    A vault-detail URL depends only on the factory-proven chain and canonical
    share-token address. Updating it locally avoids a full metadata refresh—and
    its fee, token and policy RPC calls—solely to repair presentation data.
    Missing rows are left for the normal metadata creation path.

    :param vault_db: Existing vault metadata database to update in memory.
    :param candidates: Factory-proven Blue and Onyx vaults.
    :return: Number of existing rows whose link changed.
    """

    updated = 0
    for candidate in candidates:
        spec = VaultSpec(candidate.chain, candidate.address)
        row = vault_db.rows.get(spec)
        if row is None:
            continue
        expected_link = create_enzyme_vault_link(candidate.chain, candidate.address)
        if row.get("Link") == expected_link:
            continue
        row = row.copy()
        row["Link"] = expected_link
        vault_db.rows[spec] = row
        updated += 1
    return updated


def fetch_enzyme_metadata_records(
    json_rpc_url: str,
    end_block: int,
    candidates: list[EnzymeFactoryCandidate],
    max_workers: int,
    current_deposit_permissions: dict[HexAddress, VaultDepositPermission] | None = None,
) -> list[tuple[EnzymeFactoryCandidate, dict]]:
    """Fetch current Enzyme metadata concurrently without redundant RPC setup.

    Share-token metadata is first warmed using the shared multicall reader.
    Every worker then keeps one Web3 connection and token cache for its
    lifetime. Variable-length fee components remain current-state adapter
    reads. This keeps the initial 4,000+ vault import bounded without changing
    the single factory-event stream per chain.

    :param json_rpc_url:
        Archive-capable multi-provider RPC configuration for one chain.
    :param end_block:
        Stable head block selected before any metadata reads begin.
    :param candidates:
        Factory-confirmed Enzyme vault candidates requiring metadata.
    :param max_workers:
        Number of threaded metadata workers.
    :param current_deposit_permissions:
        Fixed-block Onyx permissions calculated from the chain-level handler
        index. Blue continues to read its PolicyManager directly.
    :return:
        Candidate and scanner row pairs in factory-event order.
    """

    if not candidates:
        return []

    chain_id = candidates[0].chain
    assert all(candidate.chain == chain_id for candidate in candidates), "Metadata batch must contain one chain"
    web3factory = MultiProviderWeb3Factory(
        json_rpc_url,
        retries=5,
        skip_verification=True,
        expected_chain_id=chain_id,
    )
    token_cache = TokenDiskCache()
    token_cache.load_token_details_with_multicall(
        chain_id,
        web3factory,
        [candidate.address for candidate in candidates],
        display_progress=f"Warming Enzyme share tokens on chain {chain_id}",
        max_workers=max_workers,
        block_identifier=end_block,
    )
    detections = [create_enzyme_detection(candidate, current_deposit_permissions) for candidate in candidates]
    rows = Parallel(n_jobs=max_workers, backend="threading")(delayed(create_vault_scan_record_subprocess)(web3factory, detection, end_block) for detection in tqdm(detections, desc=f"Reading Enzyme metadata on chain {chain_id}"))
    return list(zip(candidates, rows, strict=True))


def _resolve_discovery_start_block(chain_id: int) -> int:
    """Resolve a safe factory-history start block for one Enzyme chain."""

    if start_block := parse_optional_int_env("ENZYME_DISCOVERY_START_BLOCK"):
        return start_block
    start_blocks = []
    if chain_id == ENZYME_BASE_CHAIN_ID:
        start_blocks.append(ENZYME_BASE_DEPLOYMENT_BLOCK)
    if blue_start_block := fetch_enzyme_blue_first_block(chain_id):
        start_blocks.append(blue_start_block)
    if not start_blocks:
        raise ValueError(f"No reviewed Enzyme discovery deployment for chain {chain_id}")
    return min(start_blocks)


def fetch_end_block(web3: Web3) -> int:
    """Fetch the optional chain-specific migration end block."""

    return parse_optional_int_env(f"ENZYME_END_BLOCK_{web3.eth.chain_id}") or web3.eth.block_number


def fetch_run_onyx_permissions(run: EnzymeChainRun, max_workers: int) -> dict[HexAddress, VaultDepositPermission]:
    """Read all current Onyx permissions for one fixed chain run.

    The active handler mapping was produced by the run's only historical event
    stream. All handler interfaces are now inspected in one Multicall pass at
    that same end block.

    :param run:
        Fixed chain discovery scope and RPC configuration.
    :param max_workers:
        Threaded Multicall worker count.
    :return:
        Current deposit permission keyed by Shares address.
    """

    if not run.active_onyx_deposit_handlers:
        return {}
    web3factory = MultiProviderWeb3Factory(
        run.json_rpc_url,
        retries=5,
        skip_verification=True,
        expected_chain_id=run.web3.eth.chain_id,
    )
    return fetch_onyx_current_deposit_permissions(
        chain_id=run.web3.eth.chain_id,
        web3factory=web3factory,
        active_handlers=run.active_onyx_deposit_handlers,
        block_identifier=run.end_block,
        max_workers=max_workers,
    )


def _run_migration() -> None:  # noqa: PLR0914 - linear migration steps favour operational review.
    """Discover, add, and backfill all supported Enzyme Onyx and Blue vaults."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/enzyme-backfill-history.log"))
    dry_run = parse_bool_env("DRY_RUN", default=False)
    scan_prices = parse_bool_env("ENZYME_SCAN_PRICES", default=True)
    clean_prices = parse_bool_env("ENZYME_CLEAN_PRICES", default=True)
    rewrite_targeted = parse_bool_env("ENZYME_REWRITE_TARGETED", default=False)
    frequency = resolve_frequency()
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    metadata_batch_size = int(os.environ.get("ENZYME_METADATA_BATCH_SIZE", "128"))
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    price_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    checkpoint_path = parse_path_env("ENZYME_CHECKPOINT_PATH", vault_db_path.with_name("enzyme-backfill-history-state.json"))
    checkpoint = read_checkpoint(checkpoint_path)

    chain_runs: list[EnzymeChainRun] = []
    for expected_chain_id, environment_name in ENZYME_MIGRATION_CHAINS.items():
        json_rpc_url = os.environ.get(environment_name)
        if not json_rpc_url:
            raise RuntimeError(f"{environment_name} must be configured for the Enzyme migration")
        web3 = create_multi_provider_web3(json_rpc_url)
        if web3.eth.chain_id != expected_chain_id:
            raise ValueError(f"{environment_name} points to chain {web3.eth.chain_id}, expected {expected_chain_id}")
        chain_checkpoint = checkpoint["chains"].get(str(expected_chain_id))
        if chain_checkpoint is not None:
            end_block = int(chain_checkpoint["end_block"])
            candidates = [deserialise_candidate(item) for item in chain_checkpoint["candidates"]]
            if expected_chain_id == ENZYME_BASE_CHAIN_ID and "active_onyx_deposit_handlers" not in chain_checkpoint:
                # Checkpoint version 1 predates the additive Onyx handler
                # index. Reconstruct only Base at its existing fixed end block;
                # preserve completed historical-price state for every chain.
                discovery = fetch_enzyme_chain_discovery(web3, _resolve_discovery_start_block(expected_chain_id), end_block)
                candidates = discovery.candidates
                active_onyx_deposit_handlers = discovery.active_onyx_deposit_handlers
                chain_checkpoint["candidates"] = [serialise_candidate(candidate) for candidate in candidates]
                chain_checkpoint["active_onyx_deposit_handlers"] = {shares_address: list(handlers) for shares_address, handlers in active_onyx_deposit_handlers.items()}
                if not dry_run:
                    write_checkpoint(checkpoint_path, checkpoint)
            else:
                active_onyx_deposit_handlers = {HexAddress(shares_address): tuple(HexAddress(handler) for handler in handlers) for shares_address, handlers in chain_checkpoint.get("active_onyx_deposit_handlers", {}).items()}
            logger.info("Resuming %d Enzyme candidates on chain %d at fixed block %d", len(candidates), expected_chain_id, end_block)
        else:
            end_block = fetch_end_block(web3)
            discovery = fetch_enzyme_chain_discovery(web3, _resolve_discovery_start_block(expected_chain_id), end_block)
            candidates = discovery.candidates
            active_onyx_deposit_handlers = discovery.active_onyx_deposit_handlers
            if not candidates:
                raise RuntimeError(f"No Enzyme factory events found on chain {expected_chain_id}; refusing to write an incomplete migration")
            checkpoint["chains"][str(expected_chain_id)] = {
                "end_block": end_block,
                "candidates": [serialise_candidate(candidate) for candidate in candidates],
                "active_onyx_deposit_handlers": {shares_address: list(handlers) for shares_address, handlers in active_onyx_deposit_handlers.items()},
                "prices_complete": False,
            }
            if not dry_run:
                write_checkpoint(checkpoint_path, checkpoint)
        chain_runs.append(EnzymeChainRun(web3, json_rpc_url, end_block, candidates, active_onyx_deposit_handlers))

    print("Enzyme backfill plan")
    print(tabulate([{"chain": run.web3.eth.chain_id, "vaults": len(run.candidates), "first_block": min(candidate.created_block for candidate in run.candidates), "last_block": run.end_block} for run in chain_runs], headers="keys", tablefmt="github"))
    print(f"Vault DB: {vault_db_path}")
    print(f"Price DB: {price_path}")
    print(f"Cleaned price DB: {cleaned_price_path}")
    print(f"Reader states: {reader_state_path}")
    print(f"Checkpoint: {checkpoint_path}")
    print(f"Dry run: {dry_run}")

    if dry_run:
        return

    vault_db = read_vault_database(vault_db_path)
    leads_added = metadata_upserted = links_updated = 0
    all_candidates = [candidate for run in chain_runs for candidate in run.candidates]
    for run in chain_runs:
        current_deposit_permissions = fetch_run_onyx_permissions(run, max_workers)
        if current_deposit_permissions:
            logger.info(
                "Resolved %d Enzyme Onyx permissions on chain %d: whitelisted=%d, permissionless=%d, unknown=%d",
                len(current_deposit_permissions),
                run.web3.eth.chain_id,
                sum(permission is VaultDepositPermission.whitelisted for permission in current_deposit_permissions.values()),
                sum(permission is VaultDepositPermission.permissionless for permission in current_deposit_permissions.values()),
                sum(permission is VaultDepositPermission.unknown for permission in current_deposit_permissions.values()),
            )
        for candidate in run.candidates:
            spec = VaultSpec(candidate.chain, candidate.address)
            if spec not in vault_db.leads:
                vault_db.leads[spec] = create_enzyme_lead(candidate)
                leads_added += 1
            if isinstance(candidate, EnzymeVaultFactoryCandidate):
                lead = vault_db.leads[spec]
                lead.enzyme_factory_candidate = candidate
                lead.enzyme_active_deposit_handlers = run.active_onyx_deposit_handlers[HexAddress(candidate.address.lower())]
        links_updated += update_enzyme_vault_links(vault_db, run.candidates)
        metadata_candidates = [candidate for candidate in run.candidates if should_refresh_metadata(vault_db, candidate, run.end_block)]
        for metadata_batch in itertools.batched(metadata_candidates, metadata_batch_size):
            for candidate, record in fetch_enzyme_metadata_records(
                run.json_rpc_url,
                run.end_block,
                list(metadata_batch),
                max_workers,
                current_deposit_permissions,
            ):
                # Record even inconclusive or broken reads at this checkpoint so
                # an interrupted migration does not repeat the same failed RPC
                # calls. A later completed run selects a newer end block and
                # retries these rows automatically.
                record["_enzyme_metadata_checked_block"] = run.end_block
                record["_enzyme_metadata_version"] = ENZYME_CURRENT_METADATA_VERSION
                if isinstance(candidate, EnzymeVaultFactoryCandidate):
                    record["_enzyme_onyx_permission_version"] = ENZYME_ONYX_PERMISSION_VERSION
                    record["_enzyme_onyx_description_version"] = ENZYME_ONYX_DESCRIPTION_VERSION
                vault_db.rows[VaultSpec(candidate.chain, candidate.address)] = record
                metadata_upserted += 1
            vault_db_path.parent.mkdir(parents=True, exist_ok=True)
            vault_db.write(vault_db_path)
            write_checkpoint(checkpoint_path, checkpoint)
        if not metadata_candidates:
            vault_db_path.parent.mkdir(parents=True, exist_ok=True)
            vault_db.write(vault_db_path)
            write_checkpoint(checkpoint_path, checkpoint)

    token_cache = TokenDiskCache()
    scan_results: list[dict] = []
    if scan_prices:
        reader_states = read_reader_states(reader_state_path)
        for run in chain_runs:
            chain_checkpoint = checkpoint["chains"][str(run.web3.eth.chain_id)]
            if chain_checkpoint["prices_complete"]:
                logger.info("Skipping completed Enzyme price history on chain %d", run.web3.eth.chain_id)
                continue
            scan_result = scan_price_history(
                web3=run.web3,
                json_rpc_url=run.json_rpc_url,
                token_cache=token_cache,
                reader_states=reader_states,
                candidates=run.candidates,
                price_path=price_path,
                end_block=run.end_block,
                frequency=frequency,
                max_workers=max_workers,
                rewrite_targeted=rewrite_targeted,
            )
            if scan_result is not None:
                scan_results.append(scan_result)
            write_reader_states(reader_state_path, reader_states)
            chain_checkpoint["prices_complete"] = True
            write_checkpoint(checkpoint_path, checkpoint)
        if clean_prices and not checkpoint["cleaned"] and all(state["prices_complete"] for state in checkpoint["chains"].values()):
            cleaned_rows = replace_cleaned_vault_histories(
                {VaultSpec(candidate.chain, candidate.address).as_string_id() for candidate in all_candidates},
                vault_db_path=vault_db_path,
                raw_price_df_path=price_path,
                cleaned_price_df_path=cleaned_price_path,
                logger=logger.info,
                require_all_cleaned=False,
            )
            logger.info("Replaced %d cleaned Enzyme price rows", cleaned_rows)
            checkpoint["cleaned"] = True
            write_checkpoint(checkpoint_path, checkpoint)

    token_cache.commit()
    incomplete_metadata = [candidate for candidate in all_candidates if not has_complete_current_metadata(vault_db, candidate)]
    if incomplete_metadata:
        logger.warning(
            "%d Enzyme metadata rows remain incomplete after current-state reads; first addresses: %s",
            len(incomplete_metadata),
            ", ".join(candidate.address for candidate in incomplete_metadata[:20]),
        )
    checkpoint["complete"] = not scan_prices or (all(state["prices_complete"] for state in checkpoint["chains"].values()) and (not clean_prices or checkpoint["cleaned"]))
    write_checkpoint(checkpoint_path, checkpoint)
    print("Enzyme backfill summary")
    print(tabulate([{"vaults": len(all_candidates), "leads_added": leads_added, "links_updated": links_updated, "metadata_upserted": metadata_upserted, "metadata_incomplete": len(incomplete_metadata), "chains_price_scanned": len(scan_results)}], headers="keys", tablefmt="github"))


def main() -> None:
    """Run the Enzyme migration under the shared scanner writer lock.

    The metadata database is a whole-object pickle. Holding the same pipeline
    lock as the looped scanner from the initial read through the final write
    prevents either process from silently replacing updates made by the other.

    :return: None after the migration completes.
    :raises filelock.Timeout: If another scanner retains the writer lock past
        ``PIPELINE_LOCK_TIMEOUT``.
    """

    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    pipeline_lock_path = vault_db_path.parent.resolve() / "scan-pipeline"
    lock_timeout = float(os.environ.get("PIPELINE_LOCK_TIMEOUT", "60"))
    with wait_other_writers(pipeline_lock_path, timeout=lock_timeout):
        _run_migration()


if __name__ == "__main__":
    try:
        main()
    except FileLockTimeout:
        logger.error("Vault scan pipeline is locked by another scanner; stop it or retry after it finishes")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        sys.exit(130)
