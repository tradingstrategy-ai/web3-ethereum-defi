# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Onboard the frozen 60-vault Buoy read-only inventory on HyperEVM.

Purpose: Onboard Buoy identities, published valuation history and descriptions.
Scope: 60 HyperEVM factory creations through block 51,248,351, reviewed 2026-11-14.
Branch: analyse-buoy-finance-vault-protocol
PR title: feat: add read-only Buoy vault support on HyperEVM
PR: See the pull request for the branch above.
Preserves: Unrelated metadata and prices, scheduled reader state and timestamp caches.
Expected: Refresh 60 catalogue rows and backfill sampled early accounting where published.

Populate onchain accounting and optional descriptions, preserving manual flags,
notes and unrelated rows. Backfill only missing early price intervals, with
no API pricing and no reader-state reset. Native request execution is unsupported.
The runner owns daemon exclusion, writer locks, preview and backups. Apply and
preview both require the looped scanner stopped and the dense chain timestamp
cache available. See docs/source/vaults/buoy/index.rst for run reminders.

LLM-generated operational migration; changes require human review.
"""

import logging
import pickle
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow.dataset as ds
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.vault_protocol.buoy.constants import BUOY_FACTORY
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.buoy.offchain_metadata import use_buoy_metadata_cache
from eth_defi.erc_4626.vault_protocol.buoy.vault import BuoyVault
from eth_defi.vault.rpc_scan_state import fetch_reader_publication_progress
from eth_defi.erc_4626.vault_protocol.buoy.deployments import BUOY_REVIEWED_LEADS, BUOY_REVIEW_BLOCK
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PriceBackfillTarget, run_price_backfill

logger = logging.getLogger(__name__)


def migrate(context: MigrationContext) -> None:
    """Stage reviewed catalogue rows and backfill bounded early history.

    Complete every metadata read before modifying the staged database. Required
    price-write failures propagate; the migration cannot mark skipped work done.

    :param context: Runner-owned metadata, storage and reporting context.
    :return: None after preview or application of the reviewed scope.
    """
    with TemporaryDirectory(prefix="buoy-metadata-") as directory:
        with use_buoy_metadata_cache(Path(directory)):
            _migrate(context)


def _migrate(context: MigrationContext) -> None:
    """Read and stage the frozen inventory within an isolated cache scope.

    The backfill joins the saved chain cursor rather than the research cutoff,
    which would leave an unrecoverable gap before the next ordinary scan.

    :param context: Runner-owned staged metadata and data paths.
    :return: None after checked metadata and historical work.
    """
    database = context.metadata()
    web3 = create_multi_provider_web3(read_json_rpc_url(999))
    block = web3.eth.block_number
    price_path = context.data_dir / "vault-prices-1h.parquet"
    reader_path = context.data_dir / "vault-reader-state-1h.pickle"
    if price_path.exists() and not reader_path.exists():
        raise RuntimeError("Restore the scheduled reader state before Buoy onboarding")
    if fetch_reader_publication_progress(reader_path.with_suffix(".publication.pickle"), price_path):
        raise RuntimeError("Recover pending reader publication with a normal scan before Buoy onboarding")
    states = {}
    if reader_path.exists():
        with reader_path.open("rb") as source:
            states = pickle.load(source)
    last_scanned = max((state.get("last_block") or 0 for spec, state in states.items() if spec.chain_id == 999), default=0)
    if last_scanned > block:
        raise RuntimeError("Scheduled HyperEVM cursor is ahead of the supplied RPC")
    handoff_block = last_scanned + 1 if last_scanned else block + 1
    rebuilt = {}
    token_cache = {}
    for address, first_block, first_timestamp in tqdm(BUOY_REVIEWED_LEADS, desc="Read reviewed Buoy metadata"):
        spec = VaultSpec(999, address)
        detection = ERC4262VaultDetection(chain=999, address=address, features={ERC4626Feature.buoy_like}, first_seen_at_block=first_block, first_seen_at=native_datetime_utc_fromtimestamp(first_timestamp), updated_at=native_datetime_utc_now(), deposit_count=0, redeem_count=0)
        row = create_vault_scan_record(web3, detection, block, token_cache)
        if row.get("Protocol") != "Buoy" or row.get("_denomination_token") is None or row.get("Name", "").startswith("<broken"):
            raise RuntimeError(f"Incomplete reviewed Buoy metadata: {address}")
        previous = database.rows.get(spec, {})
        if row.get("_strategy_tags") is None and previous.get("_strategy_tags") is not None:
            row["_strategy_tags"] = previous["_strategy_tags"]
        for field in ("_flags", "_notes"):
            if previous.get(field):
                row[field] = previous[field]
        if row.get("_buoy_metadata_available") is False:
            for field in ("_description", "_short_description", "_manager_name"):
                if previous.get(field) is not None:
                    row[field] = previous[field]
        rebuilt[spec] = previous | row
    earliest = {}
    if price_path.exists():
        dataset = ds.dataset(price_path, format="parquet")
        addresses = [address for address, _, _ in BUOY_REVIEWED_LEADS]
        existing = dataset.to_table(columns=["address", "block_number"], filter=(ds.field("chain") == 999) & ds.field("address").isin(addresses))
        for address, number in zip(existing["address"].to_pylist(), existing["block_number"].to_pylist(), strict=True):
            earliest[address] = min(earliest.get(address, number), number)
    targets = []
    uninitialised = []
    for address, first_block, _ in BUOY_REVIEWED_LEADS:
        end_block = min(earliest.get(address, handoff_block), handoff_block)
        if end_block <= first_block:
            continue
        # Eligibility must match this historical interval, not current metadata:
        # a publication after the handoff belongs to the next ordinary scan.
        vault = BuoyVault(web3, VaultSpec(999, address), default_block_identifier=block)
        snapshot = vault.fetch_accounting(end_block - 1)
        if not snapshot.nav_timestamp:
            uninitialised.append(address)
            continue
        targets.append(PriceBackfillTarget(999, address, first_block, end_block, allow_empty_supply=snapshot.supply == 0))
    # Persist factory evidence as normal leads too: advancing discovery cursors
    # must not orphan these older creations on later classification refreshes.
    for address, first_block, first_timestamp in BUOY_REVIEWED_LEADS:
        spec = VaultSpec(999, address)
        old_lead = database.leads.get(spec)
        database.leads[spec] = PotentialVaultMatch(chain=999, address=address, first_seen_at_block=first_block, first_seen_at=native_datetime_utc_fromtimestamp(first_timestamp), deposit_count=getattr(old_lead, "deposit_count", 0), withdrawal_count=getattr(old_lead, "withdrawal_count", 0), configuration_count=getattr(old_lead, "configuration_count", 0), buoy_factory=BUOY_FACTORY)
    database.rows.update(rebuilt)
    context.report(changed_rows=len(rebuilt), reviewed_end_block=BUOY_REVIEW_BLOCK, scanner_handoff_block=handoff_block, historical_targets=len(targets), uninitialised_vaults=uninitialised)
    context.commit_metadata()
    if targets:
        run_price_backfill(context, targets)
    logger.info("Buoy read-only onboarding complete: %d metadata rows, %d bounded price targets", len(rebuilt), len(targets))
