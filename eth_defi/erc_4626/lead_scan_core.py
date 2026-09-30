"""Core logic for scanning the vault leads.

- Does not scan historical prices, but only discovers vaults
"""

import datetime
import decimal
import hashlib
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_classifier_signature
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import LeadScanReport
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess
from eth_defi.hypersync.hypersync_timestamp import get_hypersync_block_height
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.token import TokenDiskCache
from eth_defi.vault.rpc_batch import fetch_metadata_snapshots
from eth_defi.vault.rpc_scan_state import is_metadata_due, load_rpc_scan_state, record_metadata_failure, rpc_optimisations_enabled, save_rpc_scan_state
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)


def display_vaults_table(
    df: pd.DataFrame,
    nav_threshold: Decimal = Decimal(1.1),
    max_entries: int | None = None,
) -> None:
    """Diplay scanned vault leads in the terminal

    - Only used for local diagnostics
    - See :py:func:`eth_defi.erc_4626.scan.create_vault_scan_record` for rows
    """

    # Format DataFrame output for terminal
    # df["First seen"] = df["First seen"].dt.strftime("%Y-%b-%d")

    if len(df) == 0:
        logger.info("No data")
        return

    df = df.copy()

    # Skip trash entries,
    # or pass env var for debug
    if not os.environ.get("PRINT_ALL_VAULTS"):
        df = df[df["NAV"] > 1_000]

    # Remove zero entries
    df = df.loc[df["NAV"] >= nav_threshold]

    # Keep operational logs compact and focused on vault identity and size.
    df = df[["Name", "Protocol", "Share token", "NAV"]]

    if max_entries is not None:
        df = df.head(max_entries)

    # Round dust to zero, drop to 4 decimals
    def round_below_epsilon(x, epsilon=Decimal("0.1"), round_factor=Decimal("0.001")):
        if isinstance(x, Decimal):
            # Eliminate dust
            x = Decimal("0") if abs(x) < epsilon else x

            float_x = float(x)

            # Get rid of numbers with too many digits
            if float_x >= 1e12:  # Trillions
                return f"{float_x / 1e12:.1f}T"
            elif float_x >= 1e9:  # Billions
                return f"{float_x / 1e9:.1f}G"
            elif float_x >= 1e6:  # Millions
                return f"{float_x / 1e6:.1f}M"
            elif float_x >= 1e3:  # Millions
                return f"{float_x / 1e6:.1f}K"
            else:
                try:
                    x = x.quantize(round_factor)
                except decimal.InvalidOperation:
                    logger.warning("Cannot quantise: %s", x)

        return x  # Not decimal

    # Apply the function to all elements in the DataFrame
    df = df.apply(lambda col: col.map(round_below_epsilon))

    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 200):
        logger.info("Vault scan results:\n%s", df.to_string(index=False))


def scan_leads(
    json_rpc_urls: str,
    vault_db_file: Path,
    max_workers: int = 16,
    start_block: int | None = None,
    end_block: int | None = None,
    printer: Callable[[str], None] = print,
    backend: Literal["auto", "hypersync", "rpc"] = "auto",
    max_getlogs_range: int | None = None,
    hypersync_api_key: str | None = None,
    hypersync_concurrency: int | None = None,
    max_display_entries: int | None = None,
    rpc_request_stats: RPCRequestStats | None = None,
    web3: Web3 | None = None,
    force_metadata_refresh: bool = False,
    force_classification_refresh: bool = False,
) -> LeadScanReport:
    """Core loop to discover new vaults on a chain.

    Resume indexed Hypersync events from the durable chain cursor, then refresh
    due classification and metadata independently. JSON-RPC is used for contract
    state, never event discovery. See :mod:`eth_defi.erc_4626.hypersync_discovery`.

    :param json_rpc_urls: Environment-supplied, space-separated fallback URLs.
    :param vault_db_file: Shared metadata pickle under the pipeline writer lock.
    :param max_workers: Maximum threaded metadata/feature-probe concurrency.
    :param start_block: Optional discovery start; defaults to the chain cursor.
    :param end_block: Optional inclusive discovery end; defaults to the RPC head.
    :param printer: Callback for human-readable scan progress.
    :param backend: ``auto`` or ``hypersync``; legacy ``rpc`` is rejected.
    :param max_getlogs_range: Deprecated compatibility argument, ignored.
    :param hypersync_api_key: Supplied Hypersync API key, or environment default.
    :return: Discovery report containing cumulative leads and classifications.

    :param force_classification_refresh:
        Bypass cached feature observations for an explicit discovery refresh.

    :param force_metadata_refresh:
        Bypass metadata deadlines for an explicit operator refresh.

    :param hypersync_concurrency:
        Number of concurrent Hypersync stream requests.
        ``None`` falls back to the ``HYPERSYNC_CONCURRENCY`` env var,
        then to the Hypersync server default.
    :param max_display_entries:
        Maximum number of vaults included in the diagnostic results table.
        ``None`` displays all matching vaults.
    :param rpc_request_stats:
        Optional phase accumulator for physical JSON-RPC request accounting.
    :param web3:
        Optional phase-owned Web3 connection. Supplying it lets an outer
        scanner establish the chain id before entering exception-handled work.
    """

    if backend not in {"auto", "hypersync"}:
        raise ValueError("Vault discovery requires Hypersync; SCAN_BACKEND must be auto or hypersync")
    if max_getlogs_range is not None:
        logger.warning("max_getlogs_range is ignored: event discovery uses Hypersync")

    force_classification_refresh = force_classification_refresh or os.environ.get("FORCE_CLASSIFICATION_REFRESH", "false").lower() == "true"
    force_metadata_refresh = force_metadata_refresh or os.environ.get("FORCE_METADATA_REFRESH", "false").lower() == "true" or os.environ.get("FORCE_CLASSIFICATION_REFRESH", "false").lower() == "true"
    assert isinstance(vault_db_file, Path)

    web3 = web3 or create_multi_provider_web3(json_rpc_urls, rpc_request_stats=rpc_request_stats)
    chain_id = web3.eth.chain_id

    # The parent process verified provider chain IDs above. Subprocess workers
    # rebuild Web3 from this factory; skip per-worker re-verification so we do not
    # storm the primary provider with eth_chainId probes and trip HTTP 429. Seed
    # the verified chain ID so worker-side provider switchover still rejects an
    # endpoint that mis-routes to the wrong chain.
    web3factory = MultiProviderWeb3Factory(json_rpc_urls, retries=5, skip_verification=True, expected_chain_id=chain_id, rpc_request_stats=rpc_request_stats)

    name = get_chain_name(chain_id)
    rpcs = get_provider_name(web3.provider)

    hypersync_config = configure_hypersync_from_env(web3, hypersync_api_key=hypersync_api_key, concurrency=hypersync_concurrency)
    printer(f"Scanning EVM vaults on chain {web3.eth.chain_id}: {name}, using rpcs: {rpcs}, using event backend hypersync, HyperSync: {hypersync_config.hypersync_url or '<not avail>'}, and {max_workers} workers")

    if not vault_db_file.exists():
        logger.info("Starting vault lead scan, created new database at %s", vault_db_file)
        existing_db = VaultDatabase()
    else:
        logger.info("Starting vault lead scan, using database at %s", vault_db_file)
        existing_db = VaultDatabase.read(vault_db_file)
        assert type(existing_db) == VaultDatabase, f"Got: {type(existing_db)}: {existing_db}"

    last_scanned_block = existing_db.last_scanned_block.get(chain_id)

    if start_block is None:
        start_block = existing_db.get_chain_start_block(web3.eth.chain_id)

    current_state = end_block is None
    if end_block is None:
        end_block = web3.eth.block_number
    else:
        assert type(end_block) == int

    if hypersync_config.hypersync_client:
        # Create a scanner that uses web3, HyperSync and subprocesses
        vault_discover = HypersyncVaultDiscover(
            web3,
            web3factory,
            hypersync_config.hypersync_client,
            max_workers=max_workers,
        )

        if not end_block:
            end_block = get_hypersync_block_height(hypersync_config.hypersync_client)

    else:
        raise RuntimeError("Vault lead discovery requires HyperSync; JSON-RPC event fallback is disabled")

    vault_discover.current_state = current_state
    existing_leads = existing_db.get_existing_leads_by_chain(chain_id)
    vault_discover.seed_existing_leads(existing_leads)

    classification_path = vault_db_file.parent / f"rpc-classification-{chain_id}.json"
    classification_entries = load_rpc_scan_state(classification_path)
    now = native_datetime_utc_now()
    classifier_version = create_vault_classifier_signature()
    changed_classifiers = sum(entry.get("classifier_version") != classifier_version for entry in classification_entries.values())
    if changed_classifiers or force_classification_refresh:
        logger.info("Classification invalidation on chain %d: version-changed=%d, forced=%s, current signature=%s", chain_id, changed_classifiers, force_classification_refresh, classifier_version)
    if rpc_optimisations_enabled() and not force_classification_refresh:
        for spec, row in existing_db.rows.items():
            if spec.chain_id != chain_id:
                continue
            detection = row["_detection_data"]
            address = detection.address.lower()
            entry = classification_entries.get(address)
            if entry is not None:
                if entry["classifier_version"] != classifier_version or now >= datetime.datetime.fromisoformat(entry["expires_at"]):
                    continue
                features = {ERC4626Feature[name] for name in entry["features"]}
                checked_at = datetime.datetime.fromisoformat(entry["checked_at"])
            else:
                # Legacy classification expires at its original weekly boundary.
                # Do not extend an unversioned old observation during migration.
                if detection.updated_at is None or now - detection.updated_at >= datetime.timedelta(days=7):
                    continue
                features, checked_at = detection.features, detection.updated_at
            vault_discover.cached_features[address] = (features, checked_at)
        for address, entry in classification_entries.items():
            if entry["classifier_version"] == classifier_version and now < datetime.datetime.fromisoformat(entry["expires_at"]):
                vault_discover.cached_features[address] = ({ERC4626Feature[name] for name in entry["features"]}, datetime.datetime.fromisoformat(entry["checked_at"]))

    def persist_discovered_leads(report: LeadScanReport) -> None:
        """Commit complete event coverage before classification/metadata reads.

        Every unresolved candidate remains in the cumulative lead catalogue.
        Existing metadata and its independent completion state remain intact.

        :param report: Fully fetched event range with cumulative leads.
        :return: None.
        """
        minimum = max(start_block, last_scanned_block or 0)
        if report.end_block <= minimum:
            raise RuntimeError(f"Incomplete discovery range on chain {chain_id}: {report.end_block} <= {minimum}")
        existing_db.update_leads_and_rows(chain_id, report.end_block, report.leads, {})
        existing_db.write(vault_db_file)

    vault_discover.on_leads_discovered = persist_discovered_leads

    printer(f"Chain: {name}: scan range {start_block:,} - {end_block:,}")

    # Perform vault discovery and categorisation,
    # so we get information which address contains which kind of a vault
    report = vault_discover.scan_vaults(start_block, end_block)
    end_block = report.end_block
    minimum_end_block = max(start_block, last_scanned_block or 0)
    if end_block <= minimum_end_block:
        message = f"Vault lead discovery did not advance past its scan range for chain {chain_id}: minimum={minimum_end_block}, received={end_block}"
        raise RuntimeError(message)
    existing_db.update_leads_and_rows(chain_id, end_block, report.leads, {})
    vault_detections = list(report.detections.values())
    for detection in vault_detections:
        address = detection.address.lower()
        if address in vault_discover.cached_features:
            continue
        days = 7 if ERC4626Feature.broken in detection.features else 28
        jitter = int(hashlib.sha256(address.encode()).hexdigest()[:4], 16) % 24
        classification_entries[address] = {"features": sorted(feature.name for feature in detection.features), "checked_at": detection.updated_at.isoformat(), "expires_at": (detection.updated_at + datetime.timedelta(days=days, hours=jitter)).isoformat(), "classifier_version": classifier_version}
    save_rpc_scan_state(classification_path, classification_entries)

    # Keep metadata requests on threads so phase accounting is shared.
    worker_processor = Parallel(n_jobs=max_workers, backend="threading", return_as="generator")
    logger.info("Extracting remaining vault metadata for %d vaults", len(vault_detections))

    desc = f"Extracting vault metadata using {max_workers} workers"
    pending_path = vault_db_file.parent / f"rpc-pending-metadata-{chain_id}.json"
    pending = load_rpc_scan_state(pending_path)
    metadata_path = vault_db_file.parent / f"rpc-metadata-{chain_id}.json"
    metadata_entries = load_rpc_scan_state(metadata_path)
    if rpc_optimisations_enabled():
        vault_detections = [d for d in vault_detections if force_metadata_refresh or (d.address.lower() in pending and now >= datetime.datetime.fromisoformat(pending[d.address.lower()]["next_attempt_at"])) or (d.address.lower() not in pending and is_metadata_due(metadata_entries.get(d.address.lower()), sorted(feature.name for feature in d.features), classifier_version, now))]
    # Refresh cheap cumulative activity/classification on every discovered row,
    # independently of the expensive metadata deadline.
    for detection in report.detections.values():
        previous = existing_db.rows.get(detection.get_spec())
        if previous is not None:
            previous["_detection_data"] = detection

    for detection in vault_detections:
        pending[detection.address.lower()] = {**pending.get(detection.address.lower(), {}), "next_attempt_at": now.isoformat(), "features": sorted(feature.name for feature in detection.features)}
    save_rpc_scan_state(pending_path, pending)
    if rpc_request_stats is not None:
        rpc_request_stats.operation = "metadata_inputs"
    snapshots = fetch_metadata_snapshots(vault_detections, web3factory, end_block, max_workers) if rpc_optimisations_enabled() else {}
    token_addresses = {d.address for d in vault_detections if d.address.lower() in snapshots}
    token_addresses.update("0x" + value[-20:].hex() for snapshot in snapshots.values() for key, value in snapshot.items() if key in {"asset", "share"} and isinstance(value, bytes) and any(value))
    if token_addresses:
        token_cache = TokenDiskCache()
        try:
            token_cache.load_token_details_with_multicall(chain_id, web3factory, sorted(token_addresses), block_identifier=end_block, max_workers=max_workers, display_progress=True)
        finally:
            token_cache.close()
    rows = []
    for row in tqdm(worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, end_block, metadata_snapshot=snapshots.get(d.address.lower()), current_state=current_state) for d in vault_detections), total=len(vault_detections), desc=desc):
        detection = row["_detection_data"]
        address = detection.address.lower()
        broken = str(row.get("Name", "")).startswith("<broken")
        if broken:
            record_metadata_failure(pending, metadata_entries, address, sorted(feature.name for feature in detection.features), classifier_version, now, row.get("_rpc_failure_category", "internal"))
            previous = existing_db.rows.get(detection.get_spec())
            if previous is not None:
                row = {**previous, "_detection_data": detection}
        else:
            _record_metadata_success(row, existing_db.rows.get(detection.get_spec(), {}), pending, metadata_entries, end_block, classifier_version, now)
        rows.append(row)
        existing_db.update_leads_and_rows(chain_id, end_block, {}, {detection.get_spec(): row})
        if len(rows) % 100 == 0:
            existing_db.write(vault_db_file)
            save_rpc_scan_state(pending_path, pending)
            save_rpc_scan_state(metadata_path, metadata_entries)
    existing_db.write(vault_db_file)
    save_rpc_scan_state(pending_path, pending)
    save_rpc_scan_state(metadata_path, metadata_entries)

    printer(f"Total {len(rows)} vaults detected")

    if rows:
        df = pd.DataFrame(rows)
        # Parquet cannot export the raw Python objects,
        # so we remove columns that are marked Python-internal only
        df = df.drop(columns=[col for col in df.columns if col.startswith("_")])
        df = df.sort_values("First seen")
    else:
        df = pd.DataFrame()

    #
    # Save raw data rows
    #

    # output_fname = Path(f"{output_folder}/chain-{chain}-vaults.parquet")
    # parquet_df = df.copy()
    # parquet_df = parquet_df.fillna(pd.NA)  # fillna replaces None and NaN with pd.NA
    # # Avoid funny number issues
    # # pyarrow.lib.ArrowInvalid: ('Decimal precision out of range [1, 76]: 90', 'Conversion failed for column NAV with type object')
    # parquet_df["Mgmt fee"] = pd.to_numeric(parquet_df["Mgmt fee"], errors="coerce").astype("float64")
    # parquet_df["Perf fee"] = pd.to_numeric(parquet_df["Perf fee"], errors="coerce").astype("float64")
    # parquet_df["Shares"] = pd.to_numeric(parquet_df["Shares"], errors="coerce").astype("float64")
    # parquet_df["NAV"] = pd.to_numeric(parquet_df["NAV"], errors="coerce").astype("float64")
    # print(f"Saving raw data to {output_fname}")
    # parquet_df.to_parquet(output_fname)

    #
    # Save machine-readable output
    #

    # Save dict -> data mapping with raw data to be read in notebooks and such.
    # This will preserve raw vault detection objects.
    # Keyed by (chain id, address)
    data_dict = {r["_detection_data"].get_spec(): r for r in rows}
    report.rows = data_dict

    if rows:
        display_vaults_table(df, max_entries=max_display_entries)

    printer(f"Saving vault pickled database to {vault_db_file}")
    # Merge new results
    existing_db.update_leads_and_rows(
        chain_id=chain_id,
        last_scanned_block=end_block,
        leads=report.leads,
        rows=data_dict,
    )
    existing_db.write(vault_db_file)
    printer(f"Chain: {name}: {len(report.leads)} leads, {len(report.detections)} detections, {len(report.rows)} metadata rows")
    printer(f"Vault database has {existing_db.get_lead_count()} entries")
    printer(f"Total: {len(rows)} vaults detected, last block is now {report.end_block:,}")

    return report


def _record_metadata_success(row: dict[str, Any], previous: dict[str, Any], pending: dict, observations: dict, block: int, classifier_version: str, now: datetime.datetime) -> None:
    """Complete a metadata candidate and retain unavailable economic fields.

    Both initial discovery and queue recovery use the same provenance rules.
    Retained liquidity/utilisation values are explicitly marked stale in the
    observation sidecar rather than presented as newly read values.

    :param row: Successful metadata record, mutated to retain missing economics.
    :param previous: Last stored metadata record, or an empty mapping.
    :param pending: Mutable pending candidate queue.
    :param observations: Mutable metadata observation catalogue.
    :param block: Numeric source block for the new metadata read.
    :param classifier_version: Signature used for this classification batch.
    :param now: Naive UTC observation time.
    :return: None.
    """
    detection = row["_detection_data"]
    address = detection.address.lower()
    pending.pop(address, None)
    features = sorted(feature.name for feature in detection.features)
    same_protocol = observations.get(address, {}).get("features") == features
    entry = {
        "checked_at": now.isoformat(),
        "next_attempt_at": (now + datetime.timedelta(days=7)).isoformat(),
        "source_block": block,
        "features": features,
        "classifier_version": classifier_version,
    }
    for key in ("_available_liquidity", "_utilisation"):
        if same_protocol and key in row.get("_lending_fields_unavailable", []) and row.get(key) is None and previous.get(key) is not None:
            row[key] = previous[key]
            entry.setdefault("economics_stale", []).append(key)
    observations[address] = entry


def fetch_pending_vault_metadata(web3: Web3, json_rpc_urls: str, vault_db_file: Path, max_workers: int, rpc_request_stats: RPCRequestStats) -> int:
    """Resume due candidate metadata without repeating event discovery.

    Pending leads already exist durably in the legacy catalogue before its
    event cursor advances. Successful rows are committed in bounded batches;
    unsupported candidates retain previous metadata and a finite retry time.

    :param web3: Phase-owned, verified chain connection.
    :param json_rpc_urls: Environment-supplied fallback configuration.
    :param vault_db_file: Shared metadata pickle under the writer lock.
    :param max_workers: Maximum metadata thread concurrency.
    :param rpc_request_stats: Parent physical-attempt accumulator.
    :return: Remaining pending candidate count, including not-yet-due entries.
    """
    chain_id = web3.eth.chain_id
    path = vault_db_file.parent / f"rpc-pending-metadata-{chain_id}.json"
    pending = load_rpc_scan_state(path)
    now = native_datetime_utc_now()
    due = {address: entry for address, entry in pending.items() if now >= datetime.datetime.fromisoformat(entry["next_attempt_at"])}
    if not due:
        return len(pending)
    database = VaultDatabase.read(vault_db_file)
    leads = {address.lower(): lead for address, lead in database.get_existing_leads_by_chain(chain_id).items()}
    detections = []
    for address, entry in due.items():
        lead = leads.get(address)
        if lead is None:
            logger.warning("Discarding orphaned metadata sidecar after state restore: %s-%s", chain_id, address)
            pending.pop(address, None)
            continue
        detections.append(ERC4262VaultDetection(chain=chain_id, address=lead.address, first_seen_at_block=lead.first_seen_at_block, first_seen_at=lead.first_seen_at, features={ERC4626Feature[name] for name in entry["features"]}, updated_at=now, deposit_count=lead.deposit_count, redeem_count=lead.withdrawal_count))
    save_rpc_scan_state(path, pending)
    if not detections:
        return len(pending)
    factory = MultiProviderWeb3Factory(json_rpc_urls, retries=5, skip_verification=True, expected_chain_id=chain_id, rpc_request_stats=rpc_request_stats)
    rpc_request_stats.operation = "metadata_inputs"
    block = web3.eth.block_number
    snapshots = fetch_metadata_snapshots(detections, factory, block, max_workers) if rpc_optimisations_enabled() else {}
    metadata_path = vault_db_file.parent / f"rpc-metadata-{chain_id}.json"
    metadata_entries = load_rpc_scan_state(metadata_path)
    classifier_version = create_vault_classifier_signature()
    rows = Parallel(n_jobs=max_workers, backend="threading", return_as="generator")(delayed(create_vault_scan_record_subprocess)(factory, detection, block, metadata_snapshot=snapshots.get(detection.address.lower()), current_state=True) for detection in detections)
    for index, row in enumerate(tqdm(rows, total=len(detections), desc="Resuming candidate metadata"), 1):
        address = row["_detection_data"].address.lower()
        if str(row.get("Name", "")).startswith("<broken"):
            detection = row["_detection_data"]
            record_metadata_failure(pending, metadata_entries, address, sorted(feature.name for feature in detection.features), classifier_version, now, row.get("_rpc_failure_category", "internal"))
        else:
            detection = row["_detection_data"]
            _record_metadata_success(row, database.rows.get(detection.get_spec(), {}), pending, metadata_entries, block, classifier_version, now)
            database.update_leads_and_rows(chain_id, database.last_scanned_block[chain_id], {}, {detection.get_spec(): row})
        if index % 100 == 0:
            database.write(vault_db_file)
            save_rpc_scan_state(path, pending)
            save_rpc_scan_state(metadata_path, metadata_entries)
    database.write(vault_db_file)
    save_rpc_scan_state(path, pending)
    save_rpc_scan_state(metadata_path, metadata_entries)
    logger.info("Chain %d metadata candidates remaining: %d", chain_id, len(pending))
    return len(pending)
