# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Import the initial Pear vault price history and refresh reviewed display metadata.

Purpose: Fill the HyperEVM history gap that archive calls cannot recover from HyperCore.
Scope: Four reviewed Pear deployments on chain 999, from initial funding to the
    start of the first hour with a verified price, or the current UTC hour.
Branch: examine-pear-garden-vault-protocol
PR title: feat: add Pear vault support on HyperEVM
PR: See the pull request for the branch above.
Preserves: Existing prices, other catalogue fields, leads, cursors and reader state.
Expected: Append hourly observations and refresh curator and descriptions for existing rows.

LLM-generated operational migration; changes require human review.
Run after merge following .claude/docs/migration-scripts.md. Stop the looped
scanner before both preview and apply. Pear provides both observation time and
block, so this import needs neither archive RPC nor a dense timestamp cache.
"""

import datetime
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID, PEAR_CURATORS, PEAR_FIRST_DEPOSIT_BLOCKS, PEAR_PRICE_SOURCES
from eth_defi.erc_4626.vault_protocol.pear.offchain_data import fetch_pear_hourly_nav
from eth_defi.vault.base import RawVaultPriceRow, VaultHistoricalRead, VaultSpec
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PRICE_FILE_NAME, publish_staged_price_backfill, read_backfill_block_limit

#: UTC dates of the first standard Deposit event for each reviewed vault.
#: The reader also filters by the exact first-deposit block.
TARGETS = {
    "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096": datetime.datetime.fromisoformat("2026-09-14"),
    "0xd38a67a4f54a9b4286d72f08440485a395ddfa70": datetime.datetime.fromisoformat("2026-09-22"),
    "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84": datetime.datetime.fromisoformat("2026-11-04"),
    "0x69f2ed780a4cec49e552dbcd35a60f41368bc362": datetime.datetime.fromisoformat("2026-11-09"),
}


def _fetch_missing_history(session: requests.Session, spec: VaultSpec, start: datetime.datetime, now: datetime.datetime, saved: pa.Table, *, block_limit: int | None) -> list[RawVaultPriceRow]:
    """Read the gap before the first usable price and keep existing blocks.

    :param session: Caller-owned Pear HTTP session.
    :param spec: Reviewed vault on HyperEVM.
    :param start: Midnight UTC on the first-deposit date.
    :param now: Naive UTC import time recorded in the raw rows' written_at column.
    :param saved: Existing raw price rows for this vault.
    :param block_limit: Optional preview length in blocks from the first deposit.
    :return: Funded hourly observations for previously unrecorded blocks.
    :raises ValueError: If an unrestricted import cannot establish a verified price.
    """
    previous = saved.select(["block_number", "timestamp", "share_price", "vault_poll_frequency"]).to_pylist()
    blocks = {point["block_number"] for point in previous}
    first_price = min(
        (point for point in previous if point["vault_poll_frequency"] in PEAR_PRICE_SOURCES and point["timestamp"] is not None and point["share_price"] is not None and math.isfinite(point["share_price"]) and point["share_price"] > 0),
        key=lambda point: point["block_number"],
        default=None,
    )
    # The earliest saved observation already represents its UTC hour. Stopping
    # at its timestamp would import the preceding minute of that same hour on
    # every retry after publication.
    cutoff = min(now, first_price["timestamp"]) if first_price else now
    end = cutoff.replace(minute=0, second=0, microsecond=0)
    end_block = first_price["block_number"] if first_price else None
    if block_limit is not None:
        limit = PEAR_FIRST_DEPOSIT_BLOCKS[spec.vault_address] + block_limit
        end_block = min(limit, end_block) if end_block is not None else limit
    rows = []
    for observation in fetch_pear_hourly_nav(session, spec.vault_address, start, end, end_block=end_block):
        block = observation["block_number"]
        if block in blocks:
            continue
        blocks.add(block)
        observation["written_at"] = now
        rows.append(observation)
    if not rows and first_price is None and block_limit is None:
        raise ValueError(f"No verified price for {spec}: the requested range produced no new funded observation")
    return rows


def migrate(context: MigrationContext) -> None:
    """Append the initial Pear history and apply the reviewed catalogue metadata.

    Finish the API reads and verify the staged Parquet before publishing any
    data. Existing block identities win, including when an apply is restarted
    after publishing prices but before recording the metadata update.

    :param context: Runner-owned metadata, audit journal and guarded data directory.
    :return: None after preview or publication.
    """
    block_limit = read_backfill_block_limit(dry_run=context.dry_run)
    path = context.data_dir / PRICE_FILE_NAME
    existing = VaultHistoricalRead.migrate_parquet_schema(pq.read_table(path)) if path.exists() else VaultHistoricalRead.to_pyarrow_schema().empty_table()
    now = native_datetime_utc_now()

    new_rows = []
    counts = {}
    legacy_counts = {}
    missing_specs = []
    with requests.Session() as session:
        for address, start in tqdm(TARGETS.items(), desc="Backfilling Pear vaults", unit="vault"):
            spec = VaultSpec(PEAR_CHAIN_ID, address)
            row = context.metadata().rows.get(spec)
            if row is None:
                missing_specs.append(spec.as_string_id())
                continue
            if row.get("Protocol") != "Pear":
                raise ValueError(f"Conflicting protocol for Pear backfill target {spec}")
            entry = get_handwritten_vault_metadata(spec.chain_id, spec.vault_address)
            assert entry is not None, f"Missing reviewed metadata for {spec}"
            row.update(_curator_slug=PEAR_CURATORS[address], _short_description=entry.short_description, _description=entry.description)

            saved = existing.filter((pc.field("chain") == PEAR_CHAIN_ID) & (pc.field("address") == address))
            # Pre-integration archive rows may contain stale HyperCore values.
            # Keep them for review and never let them set the import cutoff.
            legacy_counts[spec.as_string_id()] = sum(label not in PEAR_PRICE_SOURCES for label in saved["vault_poll_frequency"].to_pylist())
            observations = _fetch_missing_history(session, spec, start, now, saved, block_limit=block_limit)
            new_rows.extend(observations)
            counts[spec.as_string_id()] = len(observations)

    with TemporaryDirectory(dir=context.data_dir) as directory:
        if new_rows:
            # Keep native-only columns alongside the canonical vault schema.
            # The standard writer validates the new file before publication.
            staged_path = Path(directory) / path.name
            additions = pa.Table.from_pylist(new_rows, schema=existing.schema)
            VaultHistoricalRead.write_uncleaned_arrow_table(pa.concat_tables([existing, additions]), staged_path)
            if not context.dry_run:
                publish_staged_price_backfill(context, path, staged_path)

    context.report(backfill_block_limit=block_limit, pear_price_rows=counts, legacy_pear_price_rows_for_review=legacy_counts, missing_metadata_specs=missing_specs, price_backfill_published=bool(new_rows) and not context.dry_run)
    context.commit_metadata()
