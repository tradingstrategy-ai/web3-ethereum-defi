"""Scan ERC-4626 vaults across all supported chains.

Multi-chain vault scanning pipeline with retry logic, native protocol
support (Hypercore, GRVT, Lighter), looped scheduling, and
post-processing.  Extracted from the
``scripts/erc-4626/scan-vaults-all-chains.py`` CLI wrapper.
"""

import datetime
import json
import logging
import logging.handlers
import os
import pickle
import re
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from atomicwrites import atomic_write
from filelock import Timeout as FileLockTimeout

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.grvt.constants import GRVT_CHAIN_ID
from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.grvt.daily_metrics import run_daily_scan as grvt_run_daily_scan
from eth_defi.grvt.vault_data_export import merge_into_vault_database as grvt_merge_vault_db
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.daily_metrics import run_daily_scan as hyperliquid_run_daily_scan
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_data_export import merge_into_vault_database as hyperliquid_merge_vault_db
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.daily_metrics import run_daily_scan as lighter_run_daily_scan
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault_data_export import merge_into_vault_database as lighter_merge_vault_db
from eth_defi.hibachi.constants import HIBACHI_DAILY_METRICS_DATABASE
from eth_defi.hibachi.daily_metrics import run_daily_scan as hibachi_run_daily_scan
from eth_defi.hibachi.vault_data_export import merge_into_vault_database as hibachi_merge_vault_db
from eth_defi.provider.broken_provider import verify_archive_node
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.post_processing import run_post_processing, validate_top_vaults_config
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, get_pipeline_data_dir

#: How many days of backups to keep
BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "7"))

logger = logging.getLogger(__name__)


def parse_duration(s: str) -> datetime.timedelta:
    """Parse a human-friendly duration string into a timedelta.

    Supported formats: ``0h``, ``4h``, ``24h``, ``1d``, ``7d``.
    A value of ``0h`` or ``0d`` produces a zero timedelta (always due).

    :param s:
        Duration string, e.g. ``"4h"`` or ``"1d"``.
    :return:
        Corresponding timedelta.
    :raises ValueError:
        If the string cannot be parsed.
    """
    m = re.fullmatch(r"(\d+)\s*(h|d)", s.strip())
    if not m:
        raise ValueError(f"Cannot parse duration: {s!r}  (expected e.g. '4h' or '1d')")
    value = int(m.group(1))
    unit = m.group(2)
    if unit == "h":
        return datetime.timedelta(hours=value)
    return datetime.timedelta(days=value)


def parse_scan_cycles(cycles_str: str) -> dict[str, datetime.timedelta]:
    """Parse the ``SCAN_CYCLES`` environment variable.

    :param cycles_str:
        Comma-separated ``name=interval`` pairs, e.g.
        ``"Hypercore=4h,GRVT=4h,Lighter=4h"``.
    :return:
        Mapping of item name to cycle interval.
    """
    result = {}
    if not cycles_str or not cycles_str.strip():
        return result
    for pair in cycles_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Invalid SCAN_CYCLES entry (expected name=interval): {pair!r}")
        name, interval_str = pair.split("=", 1)
        result[name.strip()] = parse_duration(interval_str.strip())
    return result


def format_duration(td: datetime.timedelta) -> str:
    """Format a timedelta as a human-friendly duration string.

    Inverse of :py:func:`parse_duration`.

    :param td:
        Timedelta to format.
    :return:
        E.g. ``"4h"``, ``"24h"``, ``"1d"``, ``"7d"``.
    """
    total_hours = td.total_seconds() / 3600
    if total_hours >= 24 and total_hours % 24 == 0:
        return f"{int(total_hours // 24)}d"
    return f"{int(total_hours)}h"


def load_cycle_state(path: Path) -> dict[str, str]:
    """Load the cycle state JSON from disc.

    :param path:
        Path to the JSON file.
    :return:
        Mapping of item name to ISO-formatted last-completed timestamp.
        Empty dict if the file does not exist.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read cycle state from %s: %s", path, e)
        return {}


def save_cycle_state(state: dict[str, str], path: Path) -> None:
    """Atomically write the cycle state JSON.

    Uses :py:func:`atomicwrites.atomic_write` for flush, fsync,
    atomic rename, and directory sync.

    :param state:
        Mapping of item name to ISO-formatted last-completed timestamp.
    :param path:
        Destination JSON file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="w", overwrite=True) as f:
        json.dump(state, f, indent=2, sort_keys=True)


def get_due_items(
    chain_configs: list,
    native_protocols: list[str],
    cycle_overrides: dict[str, datetime.timedelta],
    default_cycle: datetime.timedelta,
    state: dict[str, str],
    tolerance: datetime.timedelta = datetime.timedelta(0),
) -> tuple[list, list[str]]:
    """Determine which chains and protocols are due for scanning.

    An item is due when either:
    - It has never been scanned (not in *state*)
    - ``now - last_completed >= cycle_interval - tolerance``

    The *tolerance* argument prevents scheduler drift on fixed-interval
    loops: because state save time lags tick start time by the scan
    duration, a rigid ``>= cycle`` check always falls a few seconds short
    of the 4h mark and slips by one whole tick (e.g. a "4h" cycle turns
    into 5h when ``LOOP_INTERVAL_SECONDS=3600``). Passing ``tolerance =
    loop_interval / 2`` fires the scan at whichever tick is closest to
    the target time, eliminating drift.

    On a fresh state file every item is due on the first tick.
    EVM chains without a configured RPC URL will be skipped at scan time.

    :param chain_configs:
        List of :py:class:`ChainConfig` objects.
    :param native_protocols:
        List of native protocol names (e.g. ``["Hypercore", "GRVT", "Lighter"]``).
    :param cycle_overrides:
        Per-item cycle intervals from ``SCAN_CYCLES``.
    :param default_cycle:
        Default cycle for items not in *cycle_overrides*.
    :param state:
        Last-completed timestamps from :py:func:`load_cycle_state`.
    :param tolerance:
        Allowed slack when checking if the cycle has elapsed. Defaults
        to zero (strict comparison). The loop sets this to half the
        tick interval so scans fire at the nearest tick rather than
        drifting by one tick each cycle.
    :return:
        Tuple of ``(due_chains, due_protocols)``.
    """
    now = native_datetime_utc_now()
    due_chains = []
    due_protocols = []
    threshold = lambda cycle: cycle - tolerance  # noqa: E731

    for chain in chain_configs:
        cycle = cycle_overrides.get(chain.name, default_cycle)
        last_str = state.get(chain.name)
        if last_str is None or (now - datetime.datetime.fromisoformat(last_str)) >= threshold(cycle):
            due_chains.append(chain)

    for proto in native_protocols:
        cycle = cycle_overrides.get(proto, default_cycle)
        last_str = state.get(proto)
        if last_str is None or (now - datetime.datetime.fromisoformat(last_str)) >= threshold(cycle):
            due_protocols.append(proto)

    return due_chains, due_protocols


@dataclass
class ChainConfig:
    """Configuration for scanning a single chain"""

    #: Chain name (e.g., "Ethereum")
    name: str

    #: Environment variable name for RPC URL (e.g., "JSON_RPC_ETHEREUM")
    env_var: str

    #: Whether to scan vaults (False only for Unichain)
    scan_vaults: bool


@dataclass
class ChainResult:
    """Result of scanning a single chain"""

    #: Chain name
    name: str

    #: Status: "pending", "running", "success", "failed", "skipped"
    status: str

    #: Whether vault scan succeeded
    vault_scan_ok: bool | None = None

    #: Whether price scan succeeded
    price_scan_ok: bool | None = None

    #: First block scanned
    start_block: int | None = None

    #: Last block scanned
    end_block: int | None = None

    #: Total vault count
    vault_count: int | None = None

    #: Number of new vaults discovered
    new_vaults: int | None = None

    #: Number of price rows written
    price_rows: int | None = None

    #: Error message if failed
    error: str | None = None

    #: Full traceback string if failed
    traceback_str: str | None = None

    #: Scan duration in seconds
    duration: float | None = None

    #: Retry attempt number (0 for first attempt)
    retry_attempt: int = 0

    #: Cycle interval for this item (e.g. "4h", "24h")
    cycle_interval: str | None = None

    #: Hours remaining until this item is next due (for "not due" items)
    next_due_in_hours: float | None = None


def build_chain_configs() -> list[ChainConfig]:
    """Build list of chain configurations.

    Returns chains in the same order as scan-vaults-all-chains.sh
    """
    return [
        ChainConfig("Megaeth", "JSON_RPC_MEGAETH", True),
        ChainConfig("Sonic", "JSON_RPC_SONIC", True),
        ChainConfig("Monad", "JSON_RPC_MONAD", True),
        ChainConfig("Hyperliquid", "JSON_RPC_HYPERLIQUID", True),
        ChainConfig("Base", "JSON_RPC_BASE", True),
        ChainConfig("Arbitrum", "JSON_RPC_ARBITRUM", True),
        ChainConfig("Ethereum", "JSON_RPC_ETHEREUM", True),
        ChainConfig("Linea", "JSON_RPC_LINEA", True),
        ChainConfig("Gnosis", "JSON_RPC_GNOSIS", True),
        ChainConfig("Zora", "JSON_RPC_ZORA", True),
        ChainConfig("Polygon", "JSON_RPC_POLYGON", True),
        ChainConfig("Avalanche", "JSON_RPC_AVALANCHE", True),
        ChainConfig("Berachain", "JSON_RPC_BERACHAIN", True),
        ChainConfig("Unichain", "JSON_RPC_UNICHAIN", False),  # Prices only
        ChainConfig("Hemi", "JSON_RPC_HEMI", True),
        ChainConfig("Plasma", "JSON_RPC_PLASMA", True),
        ChainConfig("Binance", "JSON_RPC_BINANCE", True),
        ChainConfig("Mantle", "JSON_RPC_MANTLE", True),
        ChainConfig("Katana", "JSON_RPC_KATANA", True),
        ChainConfig("Ink", "JSON_RPC_INK", True),
        ChainConfig("Blast", "JSON_RPC_BLAST", True),
        ChainConfig("Soneium", "JSON_RPC_SONEIUM", True),
        ChainConfig("Optimism", "JSON_RPC_OPTIMISM", True),
    ]


def scan_vaults_for_chain(rpc_url: str, max_workers: int, vault_db_path: Path = DEFAULT_VAULT_DATABASE) -> tuple[bool, dict]:
    """Scan vaults for a single chain by calling scan_leads() directly.

    :param rpc_url: RPC URL for the chain
    :param max_workers: Number of parallel workers
    :param vault_db_path: Path to the vault database pickle
    :return: Tuple of (success, metrics_dict)
    """
    try:
        report = scan_leads(
            json_rpc_urls=rpc_url,
            vault_db_file=vault_db_path,
            max_workers=max_workers,
            backend="auto",
            hypersync_api_key=os.environ.get("HYPERSYNC_API_KEY"),
            printer=lambda msg: None,  # Suppress output to keep logs clean
        )

        return True, {
            "start_block": report.start_block,
            "end_block": report.end_block,
            "vault_count": len(report.rows),
            "new_vaults": report.new_leads,
        }

    except Exception as e:
        logger.exception("Vault scan failed")
        return False, {"error": str(e), "traceback": traceback.format_exc()}


def scan_prices_for_chain(
    rpc_url: str,
    max_workers: int,
    frequency: str,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    uncleaned_price_path: Path = DEFAULT_UNCLEANED_PRICE_DATABASE,
    reader_state_path: Path = DEFAULT_READER_STATE_DATABASE,
) -> tuple[bool, dict]:
    """Scan historical prices for a single chain.

    :param rpc_url: RPC URL for the chain
    :param max_workers: Number of parallel workers
    :param frequency: Scan frequency ("1h" or "1d")
    :param vault_db_path: Path to the vault database pickle
    :param uncleaned_price_path: Path to the uncleaned price parquet
    :param reader_state_path: Path to the reader state pickle
    :return: Tuple of (success, metrics_dict)
    """
    try:
        # Setup Web3 connection
        web3 = create_multi_provider_web3(rpc_url)
        web3factory = MultiProviderWeb3Factory(rpc_url, retries=5)
        token_cache = TokenDiskCache()
        chain_id = web3.eth.chain_id

        # Load vault database
        if not vault_db_path.exists():
            logger.warning("Vault database does not exist, skipping price scan")
            return True, {"rows_written": 0}

        vault_db = pickle.load(vault_db_path.open("rb"))

        # Load reader states
        reader_states = {}
        if reader_state_path.exists():
            reader_states = pickle.load(reader_state_path.open("rb"))

        # Filter vaults for this chain
        chain_vaults = [v for v in vault_db.rows.values() if v["_detection_data"].chain == chain_id]

        if len(chain_vaults) == 0:
            logger.info("No vaults on chain %d, skipping price scan", chain_id)
            return True, {"rows_written": 0}

        # Create vault instances with filtering
        vaults = []
        min_deposit_threshold = 5

        for row in chain_vaults:
            detection = row["_detection_data"]

            # Skip vaults with low activity (but keep hardcoded protocol vaults)
            if detection.deposit_count < min_deposit_threshold and detection.address.lower() not in HARDCODED_PROTOCOLS:
                continue

            vault = create_vault_instance(web3, detection.address, detection.features, token_cache=token_cache)
            if vault:
                vault.first_seen_at_block = detection.first_seen_at_block
                vaults.append(vault)

        if len(vaults) == 0:
            logger.info("No vaults to scan on chain %d after filtering", chain_id)
            return True, {"rows_written": 0}

        # Configure HyperSync
        hypersync_config = configure_hypersync_from_env(web3)

        # Scan historical prices
        result = scan_historical_prices_to_parquet(
            output_fname=uncleaned_price_path,
            web3=web3,
            web3factory=web3factory,
            vaults=vaults,
            start_block=None,
            end_block=web3.eth.block_number,
            max_workers=max_workers,
            chunk_size=32,
            token_cache=token_cache,
            frequency=frequency,
            reader_states=reader_states,
            hypersync_client=hypersync_config.hypersync_client,
        )

        # Save reader states atomically to avoid corruption on interruption
        if result["reader_states"]:
            with atomic_write(str(reader_state_path), mode="wb", overwrite=True) as f:
                pickle.dump(result["reader_states"], f)

        return True, {
            "rows_written": result["rows_written"],
            "start_block": result["start_block"],
            "end_block": result["end_block"],
        }

    except Exception as e:
        logger.exception("Price scan failed")
        return False, {"error": str(e), "traceback": traceback.format_exc()}


def scan_chain(
    config: ChainConfig,
    scan_prices: bool,
    max_workers: int,
    frequency: str,
    retry_attempt: int,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    uncleaned_price_path: Path = DEFAULT_UNCLEANED_PRICE_DATABASE,
    reader_state_path: Path = DEFAULT_READER_STATE_DATABASE,
) -> ChainResult:
    """Scan a single chain (vaults and optionally prices).

    :param config: Chain configuration
    :param scan_prices: Whether to scan prices
    :param max_workers: Number of parallel workers
    :param frequency: Scan frequency
    :param retry_attempt: Retry attempt number (0 for first)
    :param vault_db_path: Path to the vault database pickle
    :param uncleaned_price_path: Path to the uncleaned price parquet
    :param reader_state_path: Path to the reader state pickle
    :return: Scan result
    """
    result = ChainResult(name=config.name, status="running", retry_attempt=retry_attempt)

    # Check if RPC URL is configured
    rpc_url = os.environ.get(config.env_var)
    if not rpc_url:
        logger.warning("%s: SKIPPED - %s not configured", config.name, config.env_var)
        result.status = "skipped"
        result.error = f"{config.env_var} not set"
        return result

    logger.info("%s: Starting scan (retry %d)", config.name, retry_attempt)
    start_time = time.time()

    # Verify RPC providers and filter out broken ones
    try:
        rpc_url, latest_block = verify_archive_node(rpc_url, config.name)
        logger.info("%s: RPC archive node verification passed, latest block %s", config.name, f"{latest_block:,}")
    except RuntimeError as e:
        logger.error("%s: All archive node providers failed: %s", config.name, e)
        result.status = "failed"
        result.error = str(e)
        result.duration = time.time() - start_time
        return result

    # Scan vaults
    if config.scan_vaults:
        vault_success, vault_metrics = scan_vaults_for_chain(rpc_url, max_workers, vault_db_path=vault_db_path)
        result.vault_scan_ok = vault_success

        if vault_success:
            result.start_block = vault_metrics.get("start_block")
            result.end_block = vault_metrics.get("end_block")
            result.vault_count = vault_metrics.get("vault_count")
            result.new_vaults = vault_metrics.get("new_vaults")
        else:
            result.error = vault_metrics.get("error", "Unknown error")
            result.traceback_str = vault_metrics.get("traceback")

    # Scan prices
    if scan_prices:
        price_success, price_metrics = scan_prices_for_chain(
            rpc_url,
            max_workers,
            frequency,
            vault_db_path=vault_db_path,
            uncleaned_price_path=uncleaned_price_path,
            reader_state_path=reader_state_path,
        )
        result.price_scan_ok = price_success

        if price_success:
            result.price_rows = price_metrics.get("rows_written")
            # Update block range if not set by vault scan
            if result.start_block is None:
                result.start_block = price_metrics.get("start_block")
            if result.end_block is None:
                result.end_block = price_metrics.get("end_block")
        else:
            price_error = price_metrics.get("error", "Unknown error")
            price_tb = price_metrics.get("traceback")
            if result.error:
                result.error += "; " + price_error
                if price_tb:
                    result.traceback_str = (result.traceback_str or "") + "\n" + price_tb
            else:
                result.error = price_error
                result.traceback_str = price_tb

    # Calculate duration
    result.duration = time.time() - start_time

    # Determine overall status
    vault_ok = result.vault_scan_ok if config.scan_vaults else True
    price_ok = result.price_scan_ok if scan_prices else True

    if vault_ok and price_ok:
        result.status = "success"
    else:
        result.status = "failed"

    return result


def _run_hypercore_scan(
    name: str,
    scan_fn,
    scan_kwargs: dict,
    vault_db_path: Path,
) -> ChainResult:
    """Shared orchestration for Hypercore scan functions.

    Steps:

    1. Run the scan (``run_daily_scan`` or ``run_high_freq_scan``).
    2. Optionally pull the latest manual review decisions from the
       Hyperliquid review Google Sheet via
       :py:func:`~eth_defi.hyperliquid.vault_review_sync.fetch_vault_review_statuses`.
       Failure is logged as a warning and downgraded to ``None`` so the
       merge step carries forward whatever ``_manual_review_status`` is
       already stored in the pickle — an outage never wipes reviews.
    3. Merge the Hyperliquid metadata + (possibly ``None``) review
       statuses into the ``VaultDatabase`` pickle.
    4. Optionally push the refreshed vault metadata back into the same
       Google Sheet via
       :py:func:`~eth_defi.hyperliquid.vault_review_sync.sync_vault_review_sheet`.
       Skipped when Step 2 failed (same sheet is presumed unreachable)
       and wrapped in its own ``try/except`` so push-side failures
       (rate limit, sheet locked, quota) do not abort the scan.

    The review sync only runs when both ``GS_SHEET_URL`` and
    ``GS_SERVICE_ACCOUNT_FILE`` are set (``GS_WORKSHEET_NAME`` is
    optional — it defaults to ``"Hyperliquid vault review"``). When
    either required variable is unset, steps 2 and 4 are no-ops and
    the scan behaves exactly like before. This mirrors the behaviour
    of ``scripts/hyperliquid/daily-vault-metrics.py`` so both the
    standalone script and the docker pipeline share the same contract.

    :param name:
        Label for logging (e.g. ``"Hypercore"`` or ``"Hypercore HF"``).
    :param scan_fn:
        The scan function to call (``run_daily_scan`` or ``run_high_freq_scan``).
    :param scan_kwargs:
        Keyword arguments passed to *scan_fn*.
    :param vault_db_path:
        Path to the VaultDatabase pickle.
    :return:
        Scan result with vault count and duration.
    """
    from eth_defi.hyperliquid.vault_data_export import merge_into_vault_database

    result = ChainResult(name="Hypercore", status="running")
    start_time = time.time()

    # Review-sheet configuration is opt-in: all three env vars must be
    # set, otherwise we behave exactly like before the review feature
    # existed. ``GS_WORKSHEET_NAME`` defaults to the human-review tab
    # name used by ``scripts/hyperliquid/daily-vault-metrics.py``.
    gs_sheet_url = os.environ.get("GS_SHEET_URL", "").strip()
    gs_service_account_file = os.environ.get("GS_SERVICE_ACCOUNT_FILE", "").strip()
    gs_worksheet_name = os.environ.get("GS_WORKSHEET_NAME", "Hyperliquid vault review").strip()
    review_sync_enabled = bool(gs_sheet_url and gs_service_account_file)

    try:
        db = scan_fn(**scan_kwargs)
        try:
            result.vault_count = db.get_vault_count()
            result.vault_scan_ok = True

            # Step 2: fetch current manual review decisions from the sheet
            # before the merge so they can be persisted to the pickle.
            review_statuses = None
            sheet_fetch_failed = False
            if review_sync_enabled:
                from eth_defi.hyperliquid.vault_review_sync import fetch_vault_review_statuses  # noqa: PLC0415

                try:
                    review_statuses = fetch_vault_review_statuses(
                        sheet_url=gs_sheet_url,
                        worksheet_name=gs_worksheet_name,
                        service_account_file=Path(gs_service_account_file).expanduser(),
                    )
                    reviewed_count = sum(1 for status in review_statuses.values() if status is not None)
                    logger.info(
                        "%s: fetched %d manual review decisions from Google Sheet (%d rows total)",
                        name,
                        reviewed_count,
                        len(review_statuses),
                    )
                except Exception as exc:
                    logger.warning(
                        "%s: failed to fetch manual review statuses from Google Sheet: %s — merge will carry forward existing pickle values and the post-merge push will be skipped",
                        name,
                        exc,
                    )
                    sheet_fetch_failed = True

            # Step 3: merge metadata + reviews into the pickle.
            merge_into_vault_database(db, vault_db_path, review_statuses=review_statuses)
            # Price merge happens in post-processing
            result.price_scan_ok = True

            # Step 4: push the refreshed metadata back to the sheet so
            # the reviewer sees fresh TVL/APR/follower numbers.
            if review_sync_enabled:
                if sheet_fetch_failed:
                    logger.info(
                        "%s: skipping Google Sheet push (Step 2 fetch already failed; sheet is presumed unreachable)",
                        name,
                    )
                else:
                    _push_hypercore_review_sheet(
                        name=name,
                        db=db,
                        sheet_url=gs_sheet_url,
                        worksheet_name=gs_worksheet_name,
                        service_account_file=Path(gs_service_account_file).expanduser(),
                    )
        finally:
            db.close()
        result.status = "success"
    except Exception as e:
        logger.exception("%s scan failed", name)
        result.status = "failed"
        result.error = str(e)
        result.traceback_str = traceback.format_exc()

    result.duration = time.time() - start_time
    return result


def _push_hypercore_review_sheet(
    name: str,
    db,
    sheet_url: str,
    worksheet_name: str,
    service_account_file: Path,
) -> None:
    """Push fresh Hyperliquid vault metadata to the review Google Sheet.

    Wrapped in its own ``try/except`` so a push-side failure (write-side
    rate limit, sheet temporarily locked for editing, quota exhaustion,
    etc.) is logged as a warning instead of aborting the whole scan.
    The pickle is already durable at this point and the next run will
    retry the push.

    :param name:
        Logging label (e.g. ``"Hypercore"`` or ``"Hypercore HF"``).
    :param db:
        The Hyperliquid metrics database that already holds fresh
        metadata from the just-completed scan. Must expose
        ``get_all_vault_metadata()`` returning a DataFrame with
        ``name``, ``vault_address``, ``apr``, ``tvl``, and
        ``follower_count`` columns (both daily and HF databases do).
    :param sheet_url:
        Google Sheets URL.
    :param worksheet_name:
        Worksheet tab name.
    :param service_account_file:
        Path to the service account JSON key.
    """
    import math  # noqa: PLC0415

    from eth_defi.hyperliquid.vault_review_sync import VaultReviewRow, sync_vault_review_sheet  # noqa: PLC0415

    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)

    def _optional_int(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        return int(value)

    try:
        metadata_df = db.get_all_vault_metadata()
        review_rows = [
            VaultReviewRow(
                name=str(row["name"]),
                address=str(row["vault_address"]).lower(),
                apy_1m=_optional_float(row["apr"]),
                tvl=_optional_float(row["tvl"]),
                followers=_optional_int(row["follower_count"]),
                review_status=None,
            )
            for _, row in metadata_df.iterrows()
        ]
        sync_vault_review_sheet(
            rows=review_rows,
            sheet_url=sheet_url,
            worksheet_name=worksheet_name,
            service_account_file=service_account_file,
        )
        logger.info("%s: pushed %d vault rows to Google Sheet", name, len(review_rows))
    except Exception as exc:
        logger.warning(
            "%s: failed to push fresh metrics to Google Sheet: %s — the pickle is still up to date and the next run will retry",
            name,
            exc,
        )


def scan_hypercore_fn(
    max_workers: int,
    db_path: Path | None = None,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
) -> ChainResult:
    """Scan Hyperliquid native (Hypercore) vaults via REST API.

    :param max_workers:
        Number of parallel workers for fetching vault details.
    :param db_path:
        Path to the Hyperliquid DuckDB file.  ``None`` uses the default.
    :param vault_db_path:
        Path to the vault database pickle.
    """
    from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE

    session = create_hyperliquid_session(requests_per_second=2.75)
    return _run_hypercore_scan(
        name="Hypercore",
        scan_fn=hyperliquid_run_daily_scan,
        scan_kwargs=dict(
            session=session,
            db_path=db_path or HYPERLIQUID_DAILY_METRICS_DATABASE,
            max_workers=max_workers,
        ),
        vault_db_path=vault_db_path,
    )


def scan_hypercore_hf_fn(
    max_workers: int,
    db_path: Path | None = None,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    scan_interval: datetime.timedelta | None = None,
) -> ChainResult:
    """Scan Hyperliquid native vaults at high frequency with proxy support.

    :param max_workers:
        Number of parallel workers.
    :param db_path:
        Override for the HF DuckDB path.
    :param vault_db_path:
        Path to the VaultDatabase pickle.
    :param scan_interval:
        Override scan interval (default from constants).
    """
    from eth_defi.event_reader.webshare import load_proxy_rotator
    from eth_defi.hyperliquid.constants import HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE
    from eth_defi.hyperliquid.high_freq_metrics import run_high_freq_scan

    rotator = None
    try:
        rotator = load_proxy_rotator()
    except Exception:
        logger.debug("Proxy rotator not available, proceeding without proxies")

    session = create_hyperliquid_session(
        requests_per_second=1.0,
        rotator=rotator,
    )

    kwargs = dict(
        session=session,
        db_path=db_path or HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE,
        max_workers=max_workers,
    )
    if scan_interval is not None:
        kwargs["scan_interval"] = scan_interval

    return _run_hypercore_scan(
        name="Hypercore HF",
        scan_fn=run_high_freq_scan,
        scan_kwargs=kwargs,
        vault_db_path=vault_db_path,
    )


def scan_grvt_fn(
    db_path: Path | None = None,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
) -> ChainResult:
    """Scan GRVT native vaults via public endpoints.

    Runs the GRVT daily metrics pipeline: discovers vaults from the
    strategies page, fetches share price history from the public market
    data API, stores in DuckDB, and merges into the shared ERC-4626
    pipeline files (VaultDatabase pickle + cleaned Parquet).

    No authentication required.

    :param db_path:
        Path to the GRVT DuckDB file.  ``None`` uses the default.
    :param vault_db_path:
        Path to the vault database pickle.
    :return:
        Scan result with vault count and duration.
    """
    from eth_defi.grvt.constants import GRVT_DAILY_METRICS_DATABASE

    if db_path is None:
        db_path = GRVT_DAILY_METRICS_DATABASE

    result = ChainResult(name="GRVT", status="running")
    start_time = time.time()

    try:
        db = grvt_run_daily_scan(
            db_path=db_path,
        )

        try:
            vault_count = db.get_vault_count()
            result.vault_count = vault_count
            result.vault_scan_ok = True

            grvt_merge_vault_db(db, vault_db_path)
            # Price merge happens in post-processing after generate_cleaned_vault_datasets()
            # to avoid being overwritten by the EVM price cleaning step
            result.price_scan_ok = True
        finally:
            db.close()

        result.status = "success"

    except Exception as e:
        logger.exception("GRVT scan failed")
        result.status = "failed"
        result.error = str(e)
        result.traceback_str = traceback.format_exc()

    result.duration = time.time() - start_time
    return result


def scan_lighter_fn(
    max_workers: int,
    db_path: Path | None = None,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
) -> ChainResult:
    """Scan Lighter native pools via public endpoints.

    Runs the Lighter daily metrics pipeline: discovers pools from the
    public API, fetches share price history, stores in DuckDB, and
    merges into the shared ERC-4626 pipeline files (VaultDatabase pickle
    + cleaned Parquet).

    No authentication required.

    :param max_workers:
        Number of parallel workers for fetching pool details.
    :param db_path:
        Path to the Lighter DuckDB file.  ``None`` uses the default.
    :param vault_db_path:
        Path to the vault database pickle.
    :return:
        Scan result with vault count and duration.
    """
    from eth_defi.lighter.constants import LIGHTER_DAILY_METRICS_DATABASE

    if db_path is None:
        db_path = LIGHTER_DAILY_METRICS_DATABASE

    result = ChainResult(name="Lighter", status="running")
    start_time = time.time()

    try:
        session = create_lighter_session()

        db = lighter_run_daily_scan(
            session=session,
            db_path=db_path,
            max_workers=max_workers,
        )

        try:
            vault_count = db.get_vault_count()
            result.vault_count = vault_count
            result.vault_scan_ok = True

            lighter_merge_vault_db(db, vault_db_path)
            result.price_scan_ok = True
        finally:
            db.close()

        result.status = "success"

    except Exception as e:
        logger.exception("Lighter scan failed")
        result.status = "failed"
        result.error = str(e)
        result.traceback_str = traceback.format_exc()

    result.duration = time.time() - start_time
    return result


def scan_hibachi_fn(
    db_path: Path | None = None,
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
) -> ChainResult:
    """Scan Hibachi native vaults via public endpoints.

    Runs the Hibachi daily metrics pipeline: fetches vault metadata
    and share price history from the public data API, stores in DuckDB,
    and merges into the shared VaultDatabase pickle.

    No authentication required.

    :param db_path:
        Path to the Hibachi DuckDB file.  ``None`` uses the default.
    :param vault_db_path:
        Path to the vault database pickle.
    :return:
        Scan result with vault count and duration.
    """
    from eth_defi.hibachi.constants import HIBACHI_DAILY_METRICS_DATABASE

    if db_path is None:
        db_path = HIBACHI_DAILY_METRICS_DATABASE

    result = ChainResult(name="Hibachi", status="running")
    start_time = time.time()

    try:
        db = hibachi_run_daily_scan(
            db_path=db_path,
        )

        try:
            vault_count = db.get_vault_count()
            result.vault_count = vault_count
            result.vault_scan_ok = True

            hibachi_merge_vault_db(db, vault_db_path)
            result.price_scan_ok = True
        finally:
            db.close()

        result.status = "success"

    except Exception as e:
        logger.exception("Hibachi scan failed")
        result.status = "failed"
        result.error = str(e)
        result.traceback_str = traceback.format_exc()

    result.duration = time.time() - start_time
    return result


def _load_last_timestamps(uncleaned_price_path: Path | None = None) -> dict[str, str]:
    """Load the last data timestamp per chain from the uncleaned parquet.

    Reads only the ``chain`` and ``timestamp`` columns for efficiency.

    :param uncleaned_price_path:
        Path to the uncleaned parquet.  ``None`` uses the default.
    :return:
        Mapping of chain name to formatted date string (YYYY-MM-DD HH:MM).
    """
    from eth_defi.chain import get_chain_name

    path = uncleaned_price_path or DEFAULT_UNCLEANED_PRICE_DATABASE
    if not path.exists():
        return {}

    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path, columns=["chain", "timestamp"])
        if table.num_rows == 0:
            return {}

        df = table.to_pandas()
        last_ts = df.groupby("chain")["timestamp"].max()
        result = {}
        for chain_id, ts in last_ts.items():
            try:
                name = get_chain_name(int(chain_id))
            except Exception:
                name = str(chain_id)
            # Use lowercase keys so dashboard lookup is case-insensitive
            result[name.lower()] = ts.strftime("%Y-%m-%d %H:%M")
        return result
    except Exception as e:
        logger.warning("Could not read last timestamps from parquet: %s", e)
        return {}


def print_dashboard(results: dict[str, ChainResult], display_order: list[str] | None = None, uncleaned_price_path: Path | None = None) -> None:
    """Print console dashboard showing scan progress.

    :param results: Dictionary mapping chain name to result
    :param display_order: Optional list of chain names specifying display order
    :param uncleaned_price_path: Path to the uncleaned parquet for timestamps
    """
    # Load last data timestamps per chain from the uncleaned parquet
    last_timestamps = _load_last_timestamps(uncleaned_price_path)

    # Clear screen (simple approach)
    print("\n" * 3)

    lines = []
    lines.append("=" * 123)
    lines.append(" " * 40 + "Chain Scan Progress")
    lines.append("=" * 123)
    lines.append(f"{'Chain':<15} {'Status':<10} {'Cycle':<8} {'Vaults':<8} {'New':<6} {'Blocks':<22} {'Duration':<10} {'Retry':<5} {'Last data':<18}")
    lines.append("-" * 123)

    # Use display_order if provided, otherwise use dict order
    if display_order:
        ordered_results = [results[name] for name in display_order if name in results]
    else:
        ordered_results = list(results.values())

    for result in ordered_results:
        # Format fields
        status = result.status
        cycle = result.cycle_interval or "-"
        vaults = f"{result.vault_count:,}" if result.vault_count is not None else "-"
        new = f"{result.new_vaults}" if result.new_vaults is not None else "-"

        if result.start_block is not None and result.end_block is not None:
            blocks = f"{result.start_block:,} -> {result.end_block:,}"
        else:
            blocks = "-"

        duration = f"{result.duration:.1f}s" if result.duration is not None else "-"
        retry = str(result.retry_attempt)
        last_data = last_timestamps.get(result.name.lower(), "-")

        line = f"{result.name:<15} {status:<10} {cycle:<8} {vaults:<8} {new:<6} {blocks:<22} {duration:<10} {retry:<5} {last_data:<18}"
        if result.status == "not due" and result.next_due_in_hours is not None:
            line += f"  due in {result.next_due_in_hours:.1f}h"
        elif result.status == "failed" and result.error:
            # Truncate long error messages to fit the dashboard
            error_msg = result.error[:40]
            line += f"  {error_msg}"
        lines.append(line)

    # Summary
    lines.append("-" * 123)
    success_count = sum(1 for r in results.values() if r.status == "success")
    failed_count = sum(1 for r in results.values() if r.status == "failed")
    pending_count = sum(1 for r in results.values() if r.status == "pending")
    running_count = sum(1 for r in results.values() if r.status == "running")
    skipped_count = sum(1 for r in results.values() if r.status == "skipped")
    disabled_count = sum(1 for r in results.values() if r.status == "disabled")
    not_due_count = sum(1 for r in results.values() if r.status == "not due")

    summary = f"Summary: {success_count} success, {failed_count} failed, {pending_count} pending, {running_count} running, {skipped_count} skipped"
    if not_due_count:
        summary += f", {not_due_count} not due"
    if disabled_count:
        summary += f", {disabled_count} disabled"
    lines.append(summary)
    lines.append("=" * 123)

    # Print to console and log at info level
    dashboard = "\n".join(lines)
    print(dashboard)
    logger.info(dashboard)

    # Print full error messages below the dashboard
    failed_results = [r for r in ordered_results if r.status == "failed" and r.error]
    for r in failed_results:
        logger.error("%s: %s", r.name, r.error)


def backup_pipeline_files(backup_files: list[Path] | None = None, backup_dir: Path | None = None):
    """Back up critical pipeline files before scanning.

    Creates daily backups of the uncleaned parquet, reader state, and vault
    database in ``~/.tradingstrategy/backups/YYYY-MM-DD/``. Only one backup
    per calendar day is kept (the first run of the day wins). Backups older
    than :py:data:`BACKUP_RETENTION_DAYS` are purged.

    :param backup_files:
        List of files to back up.  ``None`` uses default production paths.
    :param backup_dir:
        Directory for daily backup subdirectories.  ``None`` uses
        ``~/.tradingstrategy/backups``.
    """
    import shutil

    from eth_defi.grvt.constants import GRVT_DAILY_METRICS_DATABASE
    from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE
    from eth_defi.lighter.constants import LIGHTER_DAILY_METRICS_DATABASE

    if backup_files is None:
        backup_files = [
            DEFAULT_UNCLEANED_PRICE_DATABASE,
            DEFAULT_READER_STATE_DATABASE,
            DEFAULT_VAULT_DATABASE,
            HYPERLIQUID_DAILY_METRICS_DATABASE,
            GRVT_DAILY_METRICS_DATABASE,
            LIGHTER_DAILY_METRICS_DATABASE,
            HIBACHI_DAILY_METRICS_DATABASE,
        ]

    if backup_dir is None:
        backup_dir = Path("~/.tradingstrategy/backups").expanduser()

    today = datetime.date.today().isoformat()
    daily_dir = backup_dir / today

    if daily_dir.exists():
        logger.info("Backup already exists for today: %s", daily_dir)
    else:
        daily_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for src in backup_files:
            if src.exists():
                dst = daily_dir / src.name
                shutil.copy2(src, dst)
                size_mb = dst.stat().st_size / (1024 * 1024)
                logger.info("Backed up %s (%.1f MB)", dst, size_mb)
                copied += 1
        if copied == 0:
            logger.warning("No pipeline files found to back up")
        else:
            logger.info("Backed up %d files to %s", copied, daily_dir)

    # Purge old backups
    if backup_dir.exists():
        cutoff = datetime.date.today() - datetime.timedelta(days=BACKUP_RETENTION_DAYS)
        for entry in sorted(backup_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                entry_date = datetime.date.fromisoformat(entry.name)
            except ValueError:
                continue
            if entry_date < cutoff:
                shutil.rmtree(entry)
                logger.info("Purged old backup: %s", entry)


def run_scan_tick(
    chains: list[ChainConfig],
    active_protocols: list[str],
    scan_prices: bool,
    scan_hypercore: bool,
    scan_grvt: bool,
    scan_lighter: bool,
    scan_hibachi: bool,
    max_workers: int,
    frequency: str,
    retry_count: int,
    skip_post_processing: bool,
    skip_cleaning: bool,
    skip_top_vaults: bool,
    skip_sparklines: bool,
    skip_metadata: bool,
    skip_data: bool,
    skip_samples: bool,
    vault_db_path: Path,
    uncleaned_price_path: Path,
    reader_state_path: Path,
    hyperliquid_db_path: Path,
    hyperliquid_hf_db_path: Path,
    grvt_db_path: Path,
    lighter_db_path: Path,
    hibachi_db_path: Path,
    bkp_files: list[Path],
    bkp_dir: Path,
    cleaned_price_path: Path | None = None,
    excluded_chains: list[str] | None = None,
    hypercore_mode: str = "daily",
    not_due_items: dict[str, float] | None = None,
    cycle_intervals: dict[str, str] | None = None,
    on_item_success: Callable[[str], None] | None = None,
) -> dict[str, ChainResult]:
    """Execute one scan tick: EVM chains + native protocols + post-processing.

    Returns a tick-local results dict containing only the items scanned
    during this tick.

    :param on_item_success:
        Optional callback invoked after each successful chain or protocol
        data fetch.  Used to persist cycle state incrementally so that
        an interrupted scan does not re-fetch already-completed items on
        restart.  Not related to post-processing — post-processing always
        runs after all data fetches complete.
    """
    # Back up critical pipeline files before any scanning
    backup_pipeline_files(backup_files=bkp_files, backup_dir=bkp_dir)

    results: dict[str, ChainResult] = {}
    _ci = cycle_intervals or {}

    # Initialise results for all items in this tick
    for c in chains:
        results[c.name] = ChainResult(name=c.name, status="pending", retry_attempt=0, cycle_interval=_ci.get(c.name))
    for proto in active_protocols:
        results[proto] = ChainResult(name=proto, status="pending", cycle_interval=_ci.get(proto))

    # Show items not due in this cycle (dict maps name -> hours remaining)
    for name, hours_left in (not_due_items or {}).items():
        results[name] = ChainResult(name=name, status="not due", cycle_interval=_ci.get(name), next_due_in_hours=hours_left)

    # Show excluded chains as "disabled" on the dashboard
    for name in excluded_chains or []:
        results[name] = ChainResult(name=name, status="disabled", cycle_interval=_ci.get(name))

    display_order = [c.name for c in chains] + active_protocols + list((not_due_items or {}).keys()) + (excluded_chains or [])
    print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)

    # First pass - scan EVM chains
    if chains:
        logger.info("Scanning %d EVM chains", len(chains))
    for chain in chains:
        try:
            results[chain.name] = scan_chain(
                chain,
                scan_prices,
                max_workers,
                frequency,
                0,
                vault_db_path=vault_db_path,
                uncleaned_price_path=uncleaned_price_path,
                reader_state_path=reader_state_path,
            )
        except Exception as e:
            logger.exception("Chain %s crashed with unhandled exception", chain.name)
            results[chain.name] = ChainResult(
                name=chain.name,
                status="failed",
                error=str(e),
                traceback_str=traceback.format_exc(),
                retry_attempt=0,
            )

        r = results[chain.name]
        if r.status == "success":
            logger.info(
                "%s: SUCCESS - blocks %s-%s, %d vaults (%d new), %d price rows",
                chain.name,
                r.start_block or "?",
                r.end_block or "?",
                r.vault_count or 0,
                r.new_vaults or 0,
                r.price_rows or 0,
            )
            # Save cycle state for data fetching progress — not related to post-processing
            if on_item_success:
                on_item_success(chain.name)
        elif r.status == "failed":
            logger.error("%s: FAILED - %s", chain.name, r.error)
        elif r.status == "skipped":
            logger.warning("%s: SKIPPED - %s", chain.name, r.error)
        print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)

    # Native protocol scans
    if scan_hypercore and "Hypercore" in active_protocols:
        if hypercore_mode == "high_freq":
            logger.info("Scanning Hypercore (Hyperliquid HF mode)")
            try:
                results["Hypercore"] = scan_hypercore_hf_fn(max_workers, db_path=hyperliquid_hf_db_path, vault_db_path=vault_db_path)
            except Exception as e:
                logger.exception("Hypercore HF scan crashed with unhandled exception")
                results["Hypercore"] = ChainResult(name="Hypercore", status="failed", error=str(e), traceback_str=traceback.format_exc())
        else:
            logger.info("Scanning Hypercore (Hyperliquid native vaults)")
            try:
                results["Hypercore"] = scan_hypercore_fn(max_workers, db_path=hyperliquid_db_path, vault_db_path=vault_db_path)
            except Exception as e:
                logger.exception("Hypercore scan crashed with unhandled exception")
                results["Hypercore"] = ChainResult(name="Hypercore", status="failed", error=str(e), traceback_str=traceback.format_exc())
        r = results["Hypercore"]
        if r.status == "success":
            logger.info("Hypercore: SUCCESS - %d vaults", r.vault_count or 0)
            # Save cycle state for data fetching progress — not related to post-processing
            if on_item_success:
                on_item_success("Hypercore")
        elif r.status == "failed":
            logger.error("Hypercore: FAILED - %s", r.error)
        print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)

    if scan_grvt and "GRVT" in active_protocols:
        logger.info("Scanning GRVT (native vaults)")
        try:
            results["GRVT"] = scan_grvt_fn(db_path=grvt_db_path, vault_db_path=vault_db_path)
        except Exception as e:
            logger.exception("GRVT scan crashed with unhandled exception")
            results["GRVT"] = ChainResult(name="GRVT", status="failed", error=str(e), traceback_str=traceback.format_exc())
        r = results["GRVT"]
        if r.status == "success":
            logger.info("GRVT: SUCCESS - %d vaults", r.vault_count or 0)
            # Save cycle state for data fetching progress — not related to post-processing
            if on_item_success:
                on_item_success("GRVT")
        elif r.status == "failed":
            logger.error("GRVT: FAILED - %s", r.error)
        print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)

    if scan_lighter and "Lighter" in active_protocols:
        logger.info("Scanning Lighter (native pools)")
        try:
            results["Lighter"] = scan_lighter_fn(max_workers, db_path=lighter_db_path, vault_db_path=vault_db_path)
        except Exception as e:
            logger.exception("Lighter scan crashed with unhandled exception")
            results["Lighter"] = ChainResult(name="Lighter", status="failed", error=str(e), traceback_str=traceback.format_exc())
        r = results["Lighter"]
        if r.status == "success":
            logger.info("Lighter: SUCCESS - %d pools", r.vault_count or 0)
            # Save cycle state for data fetching progress — not related to post-processing
            if on_item_success:
                on_item_success("Lighter")
        elif r.status == "failed":
            logger.error("Lighter: FAILED - %s", r.error)
        print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)

    if scan_hibachi and "Hibachi" in active_protocols:
        logger.info("Scanning Hibachi (native vaults)")
        try:
            results["Hibachi"] = scan_hibachi_fn(db_path=hibachi_db_path, vault_db_path=vault_db_path)
        except Exception as e:
            logger.exception("Hibachi scan crashed with unhandled exception")
            results["Hibachi"] = ChainResult(name="Hibachi", status="failed", error=str(e), traceback_str=traceback.format_exc())
        r = results["Hibachi"]
        if r.status == "success":
            logger.info("Hibachi: SUCCESS - %d vaults", r.vault_count or 0)
            if on_item_success:
                on_item_success("Hibachi")
        elif r.status == "failed":
            logger.error("Hibachi: FAILED - %s", r.error)
        print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)

    # Retry passes - retry failed EVM chains (native protocols are not retried)
    evm_chain_names = {c.name for c in chains}
    for attempt in range(1, retry_count + 1):
        failed_chain_names = [name for name, r in results.items() if r.status == "failed" and name in evm_chain_names]
        if not failed_chain_names:
            logger.info("No failed chains to retry")
            break

        logger.info("Retry attempt %d: retrying %d failed chains", attempt, len(failed_chain_names))
        for chain_name in failed_chain_names:
            chain = next(c for c in chains if c.name == chain_name)
            try:
                result = scan_chain(
                    chain,
                    scan_prices,
                    max_workers,
                    frequency,
                    attempt,
                    vault_db_path=vault_db_path,
                    uncleaned_price_path=uncleaned_price_path,
                    reader_state_path=reader_state_path,
                )
            except Exception as e:
                logger.exception("Chain %s crashed with unhandled exception (retry %d)", chain.name, attempt)
                result = ChainResult(name=chain.name, status="failed", error=str(e), traceback_str=traceback.format_exc(), retry_attempt=attempt)
            results[chain.name] = result
            if result.status == "success":
                logger.info("%s (retry %d): SUCCESS - blocks %s-%s, %d vaults (%d new)", chain.name, attempt, result.start_block or "?", result.end_block or "?", result.vault_count or 0, result.new_vaults or 0)
                # Save cycle state for data fetching progress — not related to post-processing
                if on_item_success:
                    on_item_success(chain.name)
            else:
                logger.error("%s (retry %d): FAILED - %s", chain.name, attempt, result.error)
            print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)

    # Post-processing
    if skip_post_processing:
        logger.info("Skipping post-processing (SKIP_POST_PROCESSING=true)")
    else:
        logger.info("All scans complete, starting post-processing")
        post_results = run_post_processing(
            scan_hypercore=scan_hypercore,
            scan_grvt=scan_grvt,
            scan_lighter=scan_lighter,
            scan_hibachi=scan_hibachi,
            skip_cleaning=skip_cleaning,
            skip_top_vaults=skip_top_vaults,
            skip_sparklines=skip_sparklines,
            skip_metadata=skip_metadata,
            skip_data=skip_data,
            skip_samples=skip_samples,
            uncleaned_parquet_path=uncleaned_price_path,
            hyperliquid_db_path=hyperliquid_db_path,
            hyperliquid_hf_db_path=hyperliquid_hf_db_path,
            grvt_db_path=grvt_db_path,
            lighter_db_path=lighter_db_path,
            hibachi_db_path=hibachi_db_path,
            vault_db_path=vault_db_path,
            cleaned_path=cleaned_price_path,
        )
        for step, success in post_results.items():
            logger.info("Post-processing %s: %s", step, "SUCCESS" if success else "FAILED")

    # Final summary
    success_count = sum(1 for r in results.values() if r.status == "success")
    failed_count = sum(1 for r in results.values() if r.status == "failed")
    skipped_count = sum(1 for r in results.values() if r.status == "skipped")

    logger.debug("=" * 80)
    logger.info("Final summary: %d success, %d failed, %d skipped", success_count, failed_count, skipped_count)
    if failed_count > 0:
        logger.warning("Failed chains:")
        for name, r in results.items():
            if r.status == "failed":
                logger.warning("  - %s: %s", name, r.error)
    logger.info("Scan complete at %s", native_datetime_utc_now().isoformat())

    # Print full tracebacks for failed chains
    failed_results = [r for r in results.values() if r.status == "failed" and r.traceback_str]
    if failed_results:
        print("\n" + "=" * 100)
        print(" " * 30 + "Full tracebacks for failed chains")
        print("=" * 100)
        for r in failed_results:
            print(f"\n--- {r.name} (retry {r.retry_attempt}) ---")
            print(r.traceback_str)
        print("=" * 100)

    print_dashboard(results, display_order, uncleaned_price_path=uncleaned_price_path)
    return results


def main():
    """Main execution function.

    Supports two modes:

    - **Single-run** (default, ``LOOP_INTERVAL_SECONDS=0``): scans everything
      once and exits, identical to historical behaviour.
    - **Looped** (``LOOP_INTERVAL_SECONDS > 0``): ticks every *N* seconds,
      checking per-item cycle intervals to decide what is due.
    """
    # Setup logging with daily log rotation
    # - stdout: defaults to WARNING to keep container logs clean
    # - log file: always INFO for full diagnostics
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "warning"),
    )

    log_file = log_dir / "scan-all-chains.log"
    rotating_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    rotating_handler.setLevel(logging.INFO)
    rotating_handler.setFormatter(logging.Formatter("%(asctime)s %(name)-44s [%(threadName)s] %(message)s", "%Y-%m-%d %H:%M:%S"))

    root = logging.getLogger()
    root.addHandler(rotating_handler)
    # Root logger level must be low enough for INFO messages to reach
    # the file handler; console handler level gates stdout independently.
    root.setLevel(min(root.level, logging.INFO))

    # Read configuration from environment
    retry_count = int(os.environ.get("RETRY_COUNT", "1"))
    scan_prices = os.environ.get("SCAN_PRICES", "false").lower() == "true"
    scan_hypercore = os.environ.get("SCAN_HYPERCORE", "false").lower() == "true"
    scan_grvt = os.environ.get("SCAN_GRVT", "false").lower() == "true"
    scan_lighter = os.environ.get("SCAN_LIGHTER", "false").lower() == "true"
    scan_hibachi = os.environ.get("SCAN_HIBACHI", "false").lower() == "true"
    force_rescan = os.environ.get("FORCE_RESCAN", "false").lower() == "true"
    max_workers = int(os.environ.get("MAX_WORKERS", "50"))
    frequency = os.environ.get("FREQUENCY", "1h")
    skip_post_processing = os.environ.get("SKIP_POST_PROCESSING", "false").lower() == "true"
    skip_cleaning = os.environ.get("SKIP_CLEANING", "false").lower() == "true"
    skip_top_vaults = os.environ.get("SKIP_TOP_VAULTS", "false").lower() == "true"
    skip_sparklines = os.environ.get("SKIP_SPARKLINES", "false").lower() == "true"
    skip_metadata = os.environ.get("SKIP_METADATA", "false").lower() == "true"
    skip_data = os.environ.get("SKIP_DATA", "false").lower() == "true"
    skip_samples = os.environ.get("SKIP_SAMPLES", "false").lower() == "true"

    # Fail-fast: refuse to start the scan loop if the top-vaults R2 upload
    # is not configured. Discovering at the end of a multi-hour scan that
    # the final upload step cannot run is unacceptable ops risk — crash
    # immediately with a clear error so the operator fixes the env file.
    # The SKIP_TOP_VAULTS=true escape hatch covers the "I know what I'm
    # doing" case (e.g. SKIP_POST_PROCESSING=true debug runs).
    if not skip_post_processing:
        validate_top_vaults_config(skip_top_vaults=skip_top_vaults)

    loop_interval = int(os.environ.get("LOOP_INTERVAL_SECONDS", "0"))
    max_cycles = int(os.environ.get("MAX_CYCLES", "0"))
    looped_mode = loop_interval > 0

    if looped_mode:
        cycle_overrides = parse_scan_cycles(os.environ.get("SCAN_CYCLES", ""))
        default_cycle = parse_duration(os.environ.get("DEFAULT_CYCLE", "24h"))
        logger.info("Looped mode: tick every %ds, cycles=%s, default=%s", loop_interval, cycle_overrides, default_cycle)
        if max_cycles > 0:
            logger.info("MAX_CYCLES=%d — will exit after %d cycles", max_cycles, max_cycles)

    # Compute all paths from the pipeline data directory
    data_dir = get_pipeline_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_db_path = data_dir / "vault-metadata-db.pickle"
    uncleaned_price_path = data_dir / "vault-prices-1h.parquet"
    cleaned_price_path = data_dir / "cleaned-vault-prices-1h.parquet"
    reader_state_path = data_dir / "vault-reader-state-1h.pickle"
    cycle_state_path = data_dir / "scan-cycle-state.json"
    pipeline_lock_path = data_dir / "scan-pipeline"
    backup_dir = data_dir / "backups"
    lighter_db_path = data_dir / "lighter-pools.duckdb"
    hibachi_db_path = data_dir / "hibachi-vaults.duckdb"
    hypercore_mode = os.environ.get("HYPERCORE_MODE", "daily").strip().lower()
    hyperliquid_db_path = data_dir / "hyperliquid-vaults.duckdb"
    hyperliquid_hf_db_path = data_dir / "hyperliquid-vaults-hf.duckdb"
    grvt_db_path = data_dir / "grvt-vaults.duckdb"

    bkp_files = [uncleaned_price_path, reader_state_path, vault_db_path, hyperliquid_db_path, hyperliquid_hf_db_path, grvt_db_path, lighter_db_path, hibachi_db_path]

    # Test mode - filter chains if TEST_CHAINS is set
    disable_chains_str = os.environ.get("DISABLE_CHAINS")
    test_chains_str = os.environ.get("TEST_CHAINS")
    if test_chains_str:
        test_chain_names = {name.strip() for name in test_chains_str.split(",")}
        logger.info("TEST MODE: Will only scan chains: %s", ", ".join(sorted(test_chain_names)))
    else:
        test_chain_names = None

    logger.debug("=" * 80)
    logger.info("Starting multi-chain vault scan")
    logger.info("SCAN_PRICES: %s, SCAN_HYPERCORE: %s, SCAN_GRVT: %s, SCAN_LIGHTER: %s, SCAN_HIBACHI: %s, RETRY_COUNT: %d, MAX_WORKERS: %d, FREQUENCY: %s", scan_prices, scan_hypercore, scan_grvt, scan_lighter, scan_hibachi, retry_count, max_workers, frequency)
    logger.info("PIPELINE_DATA_DIR: %s", data_dir)
    if skip_post_processing:
        logger.info("SKIP_POST_PROCESSING: true")
    if test_chain_names:
        logger.info("TEST_CHAINS: %s", ", ".join(sorted(test_chain_names)))
    if disable_chains_str:
        logger.info("DISABLE_CHAINS: %s", disable_chains_str)
    if force_rescan:
        logger.info("FORCE_RESCAN: true")
    logger.debug("=" * 80)

    # Build chain configurations
    all_chains = build_chain_configs()
    chain_by_name = {c.name: c for c in all_chains}

    # Reorder and filter chains if CHAIN_ORDER is set
    chain_order_str = os.environ.get("CHAIN_ORDER")
    skipped_by_order = []
    if chain_order_str:
        chain_order = [name.strip() for name in chain_order_str.split(",")]
        reordered_chains = []
        for name in chain_order:
            if name in chain_by_name:
                reordered_chains.append(chain_by_name[name])
            else:
                logger.warning("Unknown chain in CHAIN_ORDER: %s", name)
        specified_names = set(chain_order)
        for chain in all_chains:
            if chain.name not in specified_names:
                skipped_by_order.append(chain)
        all_chains = reordered_chains

    # Disable specific chains
    disabled_chains = []
    if disable_chains_str:
        disable_chain_names = {name.strip() for name in disable_chains_str.split(",")}
        disabled_chains = [c for c in all_chains if c.name in disable_chain_names]
        all_chains = [c for c in all_chains if c.name not in disable_chain_names]

    # Filter chains if in test mode
    if test_chain_names:
        if test_chain_names == {"none"}:
            # TEST_CHAINS=none means "no EVM chains at all"
            chains = []
        else:
            chains = [c for c in all_chains if c.name in test_chain_names]
            if not chains:
                logger.error("No matching chains found for TEST_CHAINS=%s", test_chains_str)
                sys.exit(1)
    else:
        chains = all_chains

    # Log chains excluded by CHAIN_ORDER or DISABLE_CHAINS
    if skipped_by_order:
        logger.info("Skipped by CHAIN_ORDER: %s", ", ".join(c.name for c in skipped_by_order))
    if disabled_chains:
        logger.info("Disabled by DISABLE_CHAINS: %s", ", ".join(c.name for c in disabled_chains))

    # Build list of active native protocols
    all_protocols = []
    if scan_hypercore:
        all_protocols.append("Hypercore")
    if scan_grvt:
        all_protocols.append("GRVT")
    if scan_lighter:
        all_protocols.append("Lighter")
    if scan_hibachi:
        all_protocols.append("Hibachi")

    # Pre-compute human-readable cycle intervals for all items
    if looped_mode:
        cycle_intervals = {}
        for c in chains:
            cycle_intervals[c.name] = format_duration(cycle_overrides.get(c.name, default_cycle))
        for proto in all_protocols:
            label = format_duration(cycle_overrides.get(proto, default_cycle))
            if proto == "Hypercore":
                label += " HF" if hypercore_mode == "high_freq" else " Daily"
            cycle_intervals[proto] = label
    else:
        cycle_intervals = None

    # Shared kwargs for run_scan_tick
    tick_kwargs = dict(
        scan_prices=scan_prices,
        scan_hypercore=scan_hypercore,
        scan_grvt=scan_grvt,
        scan_lighter=scan_lighter,
        scan_hibachi=scan_hibachi,
        max_workers=max_workers,
        frequency=frequency,
        retry_count=retry_count,
        skip_post_processing=skip_post_processing,
        skip_cleaning=skip_cleaning,
        skip_top_vaults=skip_top_vaults,
        skip_sparklines=skip_sparklines,
        skip_metadata=skip_metadata,
        skip_data=skip_data,
        skip_samples=skip_samples,
        vault_db_path=vault_db_path,
        uncleaned_price_path=uncleaned_price_path,
        reader_state_path=reader_state_path,
        hyperliquid_db_path=hyperliquid_db_path,
        hyperliquid_hf_db_path=hyperliquid_hf_db_path,
        grvt_db_path=grvt_db_path,
        lighter_db_path=lighter_db_path,
        hibachi_db_path=hibachi_db_path,
        bkp_files=bkp_files,
        bkp_dir=backup_dir,
        cleaned_price_path=cleaned_price_path,
        excluded_chains=[c.name for c in skipped_by_order + disabled_chains],
        hypercore_mode=hypercore_mode,
    )

    # Clear cycle state on disc so the first tick rescans everything.
    # Subsequent cycles use normal cycle logic because incremental saves
    # repopulate the state as each item succeeds.
    if force_rescan:
        logger.info("FORCE_RESCAN: true — clearing cycle state for first cycle")
        save_cycle_state({}, cycle_state_path)

    # Half-tick tolerance eliminates scheduler drift on fixed-interval loops.
    # Without tolerance, a rigid ``>= cycle`` check always falls a few seconds
    # short of the nominal cycle mark (state save time lags tick start time by
    # the scan duration) and every check slips by one whole tick, turning a
    # "4h" cycle into 5h when the loop interval is 1h.
    schedule_tolerance = datetime.timedelta(seconds=loop_interval) / 2 if looped_mode else datetime.timedelta(0)

    cycle = 0
    while True:
        cycle += 1

        tick_start = time.monotonic()

        try:
            with wait_other_writers(pipeline_lock_path, timeout=60):
                if looped_mode:
                    state = load_cycle_state(cycle_state_path)
                    # Always resume from persisted cycle state, including cycle 1.
                    # Cycle state is written incrementally after every successful
                    # item (see ``_save_item`` below), so items completed before a
                    # crash keep their fresh timestamps and are skipped, while
                    # items that had not been scanned yet are missing from state
                    # and become due. This gives automatic crash recovery without
                    # re-scanning everything. If everything in state is fresh
                    # (e.g. the container restarted shortly after completing a
                    # full cycle) the loop will correctly sleep until the next
                    # item is due.
                    due_chains, due_protocols = get_due_items(
                        chains,
                        all_protocols,
                        cycle_overrides,
                        default_cycle,
                        state,
                        tolerance=schedule_tolerance,
                    )

                    if due_chains or due_protocols:
                        # Compute items not due in this cycle with hours remaining
                        now = native_datetime_utc_now()
                        due_chain_names = {c.name for c in due_chains}
                        due_protocol_set = set(due_protocols)
                        not_due_names = [c.name for c in chains if c.name not in due_chain_names] + [p for p in all_protocols if p not in due_protocol_set]
                        not_due_items = {}
                        for name in not_due_names:
                            cycle_td = cycle_overrides.get(name, default_cycle)
                            last_str = state.get(name)
                            if last_str is not None:
                                elapsed = now - datetime.datetime.fromisoformat(last_str)
                                # Mirror the tolerance used in get_due_items so
                                # the displayed "due in" matches when the scan
                                # will actually fire.
                                remaining = (cycle_td - schedule_tolerance - elapsed).total_seconds() / 3600
                                not_due_items[name] = max(0.0, remaining)
                            else:
                                not_due_items[name] = 0.0

                        logger.info("Cycle %d: %d chains, %d protocols due", cycle, len(due_chains), len(due_protocols))

                        # Persist cycle state after each successful data fetch so that
                        # an interrupted scan skips already-completed items on restart.
                        # This only tracks data fetching progress, not post-processing.
                        def _save_item(name: str) -> None:
                            state[name] = native_datetime_utc_now().isoformat()
                            save_cycle_state(state, cycle_state_path)

                        tick_results = run_scan_tick(
                            chains=due_chains,
                            active_protocols=due_protocols,
                            not_due_items=not_due_items,
                            cycle_intervals=cycle_intervals,
                            on_item_success=_save_item,
                            **tick_kwargs,
                        )
                    else:
                        logger.info("Cycle %d: nothing due, sleeping", cycle)
                else:
                    # Single-run: scan everything once, ignore cycle state
                    tick_results = run_scan_tick(
                        chains=chains,
                        active_protocols=all_protocols,
                        **tick_kwargs,
                    )

        except FileLockTimeout:
            if looped_mode:
                logger.warning("Cycle %d: pipeline locked by another process, skipping", cycle)
            else:
                logger.error("Pipeline locked by another process, exiting")
                sys.exit(1)

        if loop_interval <= 0:
            break
        if max_cycles > 0 and cycle >= max_cycles:
            logger.info("Reached MAX_CYCLES=%d, exiting", max_cycles)
            break

        tick_duration = time.monotonic() - tick_start
        sleep_seconds = max(0, loop_interval - tick_duration)
        logger.info(
            "Cycle %d finished in %.1f min, next cycle in %.1f min",
            cycle,
            tick_duration / 60,
            sleep_seconds / 60,
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    # In single-run mode, exit with appropriate code
    if not looped_mode:
        success_count = sum(1 for r in tick_results.values() if r.status == "success")
        failed_count = sum(1 for r in tick_results.values() if r.status == "failed")
        if success_count == 0 and failed_count > 0:
            sys.exit(1)
