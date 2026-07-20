"""Backfill reviewed Sygnum FILQ metadata and Chainlink bundle NAV history.

The shared Chainlink DataFeedsCache emits every accepted FILQ bundle as a
``BundleReportUpdated`` event. Hypersync reads these reports without JSON-RPC
log-range queries; archive RPC state supplies the FILQ token supply at each
report block. Only the two reviewed FILQ identifiers are replaced in shared
raw and cleaned price files.

Run with ``source .local-test.env && PROTOCOLS=sygnum poetry run python scripts/backfill-tokenised-funds.py``.
Set ``DRY_RUN=true`` to inspect the address-scoped plan without writing.
``VAULT_DB_PATH``, price database paths, ``START_BLOCK``, ``END_BLOCK`` and
``MAX_WORKERS`` may be overridden for controlled runs.
"""

# The CLI entry point needs all configured paths, both products and scan state.
# ruff: noqa: PLR0914

import logging
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from joblib import Parallel, delayed
from tabulate import tabulate

from eth_defi.chainlink.bundle_aggregator import ChainlinkBundleReport, fetch_chainlink_bundle_reports_hypersync
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.hypersync_timestamp import get_hypersync_block_height
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets, replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_BUNDLE_DATA_ID, FILQ_A_BUNDLE_FIRST_SEEN_AT_BLOCK, FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_BUNDLE_AGGREGATOR_ADDRESS, FILQ_D_BUNDLE_DATA_ID, FILQ_D_BUNDLE_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, SYGNUM_ETHEREUM_CHAIN_ID
from eth_defi.tokenised_fund.sygnum.vault import SygnumVault
from eth_defi.tokenised_fund.usyc.backfill import parse_path_env, read_reader_states, write_reader_states
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Read a conventional Boolean environment variable.

    :param name: Environment-variable name.
    :param default: Value used when the variable is absent.
    :return: Parsed Boolean value.
    """

    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def create_detection(address: str = FILQ_A_ETHEREUM_ADDRESS, first_seen_at_block: int = FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, first_seen_at=FILQ_A_ETHEREUM_FIRST_SEEN_AT) -> ERC4262VaultDetection:
    """Create a reviewed FILQ share-class detection.

    :return: Hardcoded non-event-derived FILQ detection.
    """

    return ERC4262VaultDetection(chain=SYGNUM_ETHEREUM_CHAIN_ID, address=address, first_seen_at_block=first_seen_at_block, first_seen_at=first_seen_at, features={ERC4626Feature.sygnum_like}, updated_at=native_datetime_utc_now(), deposit_count=0, redeem_count=0)


def create_lead(address: str = FILQ_A_ETHEREUM_ADDRESS, first_seen_at_block: int = FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, first_seen_at=FILQ_A_ETHEREUM_FIRST_SEEN_AT) -> PotentialVaultMatch:
    """Create an address-scoped discovery lead.

    :return: FILQ potential vault match.
    """

    return PotentialVaultMatch(chain=SYGNUM_ETHEREUM_CHAIN_ID, address=address, first_seen_at_block=first_seen_at_block, first_seen_at=first_seen_at, deposit_count=0, withdrawal_count=0)


def upsert_filq_metadata(vault_db: VaultDatabase, rows: dict, end_block: int) -> None:
    """Upsert FILQ share classes while restoring the Ethereum discovery cursor.

    :param vault_db: Existing shared vault database.
    :param rows: Fresh FILQ metadata rows.
    :param end_block: Block at which metadata was read.
    :return: ``None``.
    """

    had_cursor = SYGNUM_ETHEREUM_CHAIN_ID in vault_db.last_scanned_block
    previous_cursor = vault_db.last_scanned_block.get(SYGNUM_ETHEREUM_CHAIN_ID)
    products = (
        (FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_A_ETHEREUM_FIRST_SEEN_AT),
        (FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_FIRST_SEEN_AT),
    )
    vault_db.update_leads_and_rows(
        chain_id=SYGNUM_ETHEREUM_CHAIN_ID,
        last_scanned_block=end_block,
        leads={address: create_lead(address, first_seen_at_block, first_seen_at) for address, first_seen_at_block, first_seen_at in products},
        rows=rows,
    )
    if had_cursor:
        assert previous_cursor is not None
        vault_db.last_scanned_block[SYGNUM_ETHEREUM_CHAIN_ID] = previous_cursor
    else:
        vault_db.last_scanned_block.pop(SYGNUM_ETHEREUM_CHAIN_ID, None)


def fetch_filq_historical_read(report: ChainlinkBundleReport, vault: SygnumVault) -> VaultHistoricalRead:
    """Fetch token supply for one Chainlink report and construct a price row.

    :param report: Hypersync-discovered FILQ bundle report.
    :param vault: FILQ share class matching ``report.data_id``.
    :return: Priced FILQ history row at the report block.
    """

    bundle_decimals = vault.fetch_validated_bundle_decimals(report.block_number)
    share_price = vault.decode_bundle_nav(report.bundle, bundle_decimals)
    if share_price <= 0:
        raise ValueError(f"FILQ report at block {report.block_number} returned invalid NAV {share_price}")
    total_supply = vault.fetch_total_supply(report.block_number)
    row = VaultHistoricalRead(
        vault=vault,
        block_number=report.block_number,
        timestamp=report.block_timestamp or report.update_time,
        share_price=share_price,
        total_assets=share_price * total_supply,
        total_supply=total_supply,
        performance_fee=None,
        management_fee=None,
        errors=None,
        deposits_open=False,
        redemption_open=False,
    )
    row.vault_poll_frequency = "chainlink_bundle_event"
    return row


def fetch_filq_historical_reads(
    reports: list[ChainlinkBundleReport],
    vaults_by_data_id: dict[bytes, SygnumVault],
    max_workers: int,
) -> list[VaultHistoricalRead]:
    """Fetch FILQ supplies for Chainlink reports in parallel.

    :param reports: Hypersync-discovered reports for the reviewed data ids.
    :param vaults_by_data_id: FILQ adapter keyed by Chainlink data id.
    :param max_workers: Maximum archive-RPC worker threads.
    :return: Historical rows ordered by block and log index.
    :raise ValueError: If Hypersync returned an unexpected feed identifier.
    """

    unknown_data_ids = {report.data_id for report in reports} - set(vaults_by_data_id)
    if unknown_data_ids:
        raise ValueError(f"Unexpected FILQ Chainlink data ids: {[data_id.hex() for data_id in sorted(unknown_data_ids)]}")
    return Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_filq_historical_read)(report, vaults_by_data_id[report.data_id]) for report in reports)


def write_filq_historical_reads(
    path: Path,
    reads: list[VaultHistoricalRead],
    start_block: int,
) -> tuple[int, int]:
    """Atomically replace FILQ rows in the shared raw price Parquet.

    Existing unrelated chains, vaults, rows before ``start_block`` and native
    protocol columns are retained. Schema migration failures propagate so a
    corrupt production Parquet can never be silently discarded.

    :param path: Shared uncleaned vault-price Parquet path.
    :param reads: Fresh FILQ event-derived rows.
    :param start_block: Inclusive replacement boundary.
    :return: Tuple of deleted and inserted row counts.
    """

    if not reads:
        message = "Cannot replace FILQ price history with an empty report set"
        raise ValueError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical_schema = VaultHistoricalRead.to_pyarrow_schema()
    selected_addresses = pa.array([FILQ_A_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_ADDRESS])
    if path.exists():
        existing_table = VaultHistoricalRead.migrate_parquet_schema(pq.read_table(path))
        mask = pc.and_(
            pc.and_(pc.equal(existing_table["chain"], SYGNUM_ETHEREUM_CHAIN_ID), pc.is_in(existing_table["address"], selected_addresses)),
            pc.greater_equal(existing_table["block_number"], start_block),
        )
        deleted_rows = pc.sum(mask).as_py() or 0
        existing_table = existing_table.filter(pc.invert(mask))
    else:
        existing_table = None
        deleted_rows = 0

    written_at = native_datetime_utc_now()
    new_table = pa.Table.from_pylist([read.export() for read in reads], schema=canonical_schema)
    new_table = new_table.set_column(
        new_table.schema.get_field_index("written_at"),
        "written_at",
        pa.array([written_at] * len(new_table), type=pa.timestamp("ms")),
    )
    if existing_table is not None:
        for field in existing_table.schema:
            if field.name not in new_table.schema.names:
                new_table = new_table.append_column(field, pa.nulls(len(new_table), type=field.type))
        new_table = new_table.select(existing_table.schema.names)
        combined = pa.concat_tables([existing_table.replace_schema_metadata(None), new_table.replace_schema_metadata(None)])
    else:
        combined = new_table
    order = pc.sort_indices(combined, sort_keys=[("chain", "ascending"), ("address", "ascending"), ("block_number", "ascending")])
    combined = combined.take(order)
    VaultHistoricalRead.write_uncleaned_arrow_table(combined, path)
    return deleted_rows, len(new_table)


def main() -> None:
    """Run the FILQ metadata and Chainlink bundle history migration.

    :return: ``None``.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=False)
    frequency = os.environ.get("FREQUENCY", "1d")
    if frequency != "1d":
        raise ValueError(f"Sygnum FILQ backfill supports only FREQUENCY=1d, got: {frequency}")
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    raw_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    reader_state_path = parse_path_env("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)
    start_block = int(os.environ.get("START_BLOCK", FILQ_A_BUNDLE_FIRST_SEEN_AT_BLOCK))
    json_rpc_url = read_json_rpc_url(SYGNUM_ETHEREUM_CHAIN_ID)
    web3 = create_multi_provider_web3(json_rpc_url)
    end_block = int(os.environ.get("END_BLOCK", web3.eth.block_number))
    if end_block < FILQ_D_BUNDLE_FIRST_SEEN_AT_BLOCK:
        raise ValueError(f"END_BLOCK must be at least the first FILQ-D bundle report {FILQ_D_BUNDLE_FIRST_SEEN_AT_BLOCK}")
    if start_block > end_block:
        raise ValueError(f"START_BLOCK {start_block} is after END_BLOCK {end_block}")
    plan = tabulate(
        [
            {"chain": "Ethereum", "address": FILQ_A_ETHEREUM_ADDRESS, "start_block": start_block, "end_block": end_block, "write_prices": True, "dry_run": dry_run},
            {"chain": "Ethereum", "address": FILQ_D_ETHEREUM_ADDRESS, "start_block": start_block, "end_block": end_block, "write_prices": True, "dry_run": dry_run},
        ],
        headers="keys",
        tablefmt="github",
    )
    logger.info("Sygnum FILQ migration plan:\n%s", plan)
    if dry_run:
        logger.info("DRY RUN: would read FILQ bundle reports through Hypersync and replace only FILQ price rows")
        return

    hypersync_client = configure_hypersync_from_env(web3).hypersync_client
    if hypersync_client is None:
        message = "Sygnum FILQ price backfill requires Hypersync"
        raise RuntimeError(message)
    hypersync_height = get_hypersync_block_height(hypersync_client)
    scan_end_block = min(end_block, hypersync_height)
    if scan_end_block < end_block:
        logger.warning("Clamping FILQ end block from %d to Hypersync indexed height %d", end_block, scan_end_block)

    token_cache = TokenDiskCache()
    vaults: list[SygnumVault] = []
    for address in (FILQ_A_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_ADDRESS):
        vault = create_vault_instance(web3, address, features={ERC4626Feature.sygnum_like}, token_cache=token_cache)
        if not isinstance(vault, SygnumVault):
            raise RuntimeError(f"Could not create Sygnum FILQ adapter for {address}")
        _ = vault.share_token
        vaults.append(vault)
    vaults_by_data_id = {vault.bundle_data_id: vault for vault in vaults}
    reports = fetch_chainlink_bundle_reports_hypersync(
        hypersync_client,
        aggregator_address=FILQ_BUNDLE_AGGREGATOR_ADDRESS,
        start_block=start_block,
        end_block=scan_end_block,
        data_ids={FILQ_A_BUNDLE_DATA_ID, FILQ_D_BUNDLE_DATA_ID},
    )
    seen_data_ids = {report.data_id for report in reports}
    missing_data_ids = set(vaults_by_data_id) - seen_data_ids
    if missing_data_ids:
        raise RuntimeError(f"Hypersync returned no FILQ reports for data ids: {[data_id.hex() for data_id in sorted(missing_data_ids)]}")
    historical_reads = fetch_filq_historical_reads(reports, vaults_by_data_id, max_workers=int(os.environ.get("MAX_WORKERS", "8")))
    deleted_rows, inserted_rows = write_filq_historical_reads(raw_path, historical_reads, start_block)
    logger.info("Replaced %d FILQ raw rows with %d Chainlink bundle reports", deleted_rows, inserted_rows)

    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    rows = {
        VaultSpec(SYGNUM_ETHEREUM_CHAIN_ID, address): create_vault_scan_record(web3, detection=create_detection(address, first_seen_at_block, first_seen_at), block_identifier=scan_end_block, token_cache=token_cache)
        for address, first_seen_at_block, first_seen_at in (
            (FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_A_ETHEREUM_FIRST_SEEN_AT),
            (FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_FIRST_SEEN_AT),
        )
    }
    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    upsert_filq_metadata(vault_db, rows, scan_end_block)
    vault_db.write(vault_db_path)

    selected_specs = {
        VaultSpec(SYGNUM_ETHEREUM_CHAIN_ID, FILQ_A_ETHEREUM_ADDRESS),
        VaultSpec(SYGNUM_ETHEREUM_CHAIN_ID, FILQ_D_ETHEREUM_ADDRESS),
    }
    retained_states = {spec: state for spec, state in read_reader_states(reader_state_path).items() if spec not in selected_specs}
    write_reader_states(reader_state_path, retained_states)
    if cleaned_path.exists():
        replace_cleaned_vault_histories(
            {spec.as_string_id() for spec in selected_specs},
            vault_db_path=vault_db_path,
            raw_price_df_path=raw_path,
            cleaned_price_df_path=cleaned_path,
        )
    else:
        generate_cleaned_vault_datasets(
            vault_db_path=vault_db_path,
            price_df_path=raw_path,
            cleaned_price_df_path=cleaned_path,
            display=False,
        )
    token_cache.commit()


if __name__ == "__main__":
    main()
