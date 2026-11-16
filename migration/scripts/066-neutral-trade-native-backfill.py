"""Seed reviewed Neutral Trade native leads and their recoverable source history.

Purpose: Onboard the native Solana USDC vaults through the common REST collector.
Scope: Thirteen public Solana USDC accounts reviewed on 2026-11-12, through that UTC date. WSOL and EVM are excluded from this migration.
Branch: check-neutral-drade-api-docs
PR title: feat: collect Neutral Trade native vault history through the partner API
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1761
Preserves: Existing EVM metadata/prices, reader state, timestamp caches and unmatched native price history; discontinuous source bases stay private.
Expected: Thirteen retained leads, with native price publication only for verified continuous canonical intervals. Current data has two quarantined histories.

LLM-generated operational migration; changes require human review. Stop the
looped scanner before preview or apply. Requires NEUTRAL_TRADE_API_KEY.
Preview uses temporary source/output files. Apply preserves private backups.
"""

import datetime
import os
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path

import pyarrow.parquet as pq
from filelock import FileLock

from eth_defi.compat import native_datetime_utc_now
from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID, make_solana_vault_address
from eth_defi.neutral_trade.metrics import NeutralTradeDatabase, run_scan
from eth_defi.neutral_trade.reporting import render_verification_report
from eth_defi.neutral_trade.vault_data_export import merge_into_vault_database
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.post_processing import merge_native_protocols
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext

# ruff: noqa: N999, T201 - numbered operational migration with tabulated report

#: Fixed public inventory, excluding WSOL and EVM, from authenticated directory.
VAULTS = {
    "3vZKAcd74bzwNYZmsJndYExkGj6ABQwrUoiMtdNfzLZe",
    "46gSEJLNTdDFq7DPjH8RbcTmG1gyw4JCKCr1XfuAfYvh",
    "4QBTzqUbn1crLg2PDUGt8abJfqHjeuZkjNLjCCit69ui",
    "Aea2VZfX6hw758cxx2WaGm7kqmdVjhbYsLfde5chVG1W",
    "C68A4mAhA9EE4rWq9HmFnq3SPcbNmst6qiBWcna5VDHy",
    "De47QBuMP7xukYBek5r4ScZF4HEk1kHW34ymKASz3DLt",
    "DzmixPGgru86YkQY4LSEuM1Sfz7ZFPc1wHwSm4fejXbJ",
    "Ec6uSz5EsffwfXp43pvpz4rf9ttJRFKES4ZSVX7gVVkQ",
    "Eo3m78EQcHnwMyHtxaiz1vw4nwHa85RuGE8jLsrELhxj",
    "G5aMxQTbGWMnYycpfjHpD7Y1muoKBwaB1HtCdpUUcQZp",
    "HWjMfYfc7KoPchoEDa5UFyVSpJxY3aV1RAccKvABYb9z",
    "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ",
    "nE1x7KQq2sm3GQrafQUUdBkSPPT52FmiMM9qAS1dgnC",
}


def migrate(context: MigrationContext) -> None:  # noqa: PLR0914 - one guarded preview/apply operation
    """Collect the frozen scope and stage metadata under runner-owned locks.

    Source and raw Parquet are backed up before apply. Preview writes only
    temporary files; no RPC reads, signing or transactions are performed.

    :param context: Guarded preview/apply migration with shared staged metadata.
    :return: None after reporting committed or previewed coverage.
    """
    key = os.environ.get("NEUTRAL_TRADE_API_KEY")
    assert key, "NEUTRAL_TRADE_API_KEY is required"
    source = context.data_dir / "neutral-trade-vaults.duckdb"
    raw_prices = context.data_dir / "vault-prices-1h.parquet"
    with tempfile.TemporaryDirectory(prefix="neutral-onboarding-") as work:
        workdir = Path(work)
        staged = context.metadata()
        targets = {VaultSpec(SOLANA_DATASET_CHAIN_ID, make_solana_vault_address(address)) for address in VAULTS}
        metadata_path = workdir / "metadata.pickle"
        # A failed apply may already have metadata, or the recurring collector
        # may have discovered these accounts. Reuse the normal merge semantics.
        VaultDatabase(rows={spec: row for spec, row in staged.rows.items() if spec in targets}).write(metadata_path)
        actual_source = workdir / source.name if context.dry_run else source
        if context.dry_run and source.exists():
            with FileLock(str(source) + ".lock", timeout=0):
                assert not Path(str(source) + ".wal").exists(), "Close/checkpoint source archive before preview"
                shutil.copy2(source, actual_source)
        destination = workdir / raw_prices.name if context.dry_run else raw_prices
        if context.dry_run and raw_prices.exists():
            shutil.copy2(raw_prices, destination)
        before_count = pq.read_metadata(destination).num_rows if destination.exists() else 0
        if not context.dry_run:
            for path in (source, raw_prices):
                if not path.exists():
                    continue
                suffix = native_datetime_utc_now().strftime("%Y%m%dT%H%M%S%f")
                backup = path.with_name(path.name + ".bak-066-" + suffix)
                with FileLock(str(source) + ".lock", timeout=0) if path == source else nullcontext():
                    if path == source:
                        assert not Path(str(source) + ".wal").exists(), "Close/checkpoint source archive before backup"
                    with backup.open("xb") as output, path.open("rb") as original:
                        shutil.copyfileobj(original, output)
                context.record_modified_file(backup, backup=True)
        with NeutralTradeDatabase(actual_source) as db:
            result = run_scan(db, key, vault_ids=VAULTS, end_date=datetime.date(2026, 11, 12), max_workers=2, page_budget=10000, time_budget=1800)
            assert not result.failures and not result.pending, "Onboarding coverage is incomplete; rerun to resume"
            found = {r["address"] for r in db.iter_leads()}
            assert VAULTS <= found, "Frozen inventory is missing from the directory"
            proposed = merge_into_vault_database(db, metadata_path, vault_ids=VAULTS)
            report = render_verification_report(db)
        print(report)
        for spec, row in proposed.rows.items():
            staged.rows[spec] = row
        context.report(inspected_rows=len(VAULTS), changed_rows=len(proposed.rows), changed_fields=["native metadata", "canonical price history"], pages=result.pages)
        outcome = merge_native_protocols(merge_neutral_trade=True, neutral_trade_vault_ids=VAULTS, neutral_trade_db_path=actual_source, uncleaned_parquet_path=destination)
        assert outcome.get("neutral-trade-price-merge") is True
        after_count = pq.read_metadata(destination).num_rows
        assert after_count >= before_count, "Archival price merge must preserve existing rows"
        context.commit_metadata()
        context.report(price_rows_before=before_count, price_rows_after=after_count)
        if not context.dry_run:
            context.record_modified_file(source)
            context.record_modified_file(raw_prices)
