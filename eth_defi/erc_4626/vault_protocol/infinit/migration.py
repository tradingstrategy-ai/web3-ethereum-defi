"""Staged, address-scoped Infinit maintenance for local and mounted datasets.

All source reads run in dry-run mode too. Only validated staged files may be
published, under the same lock as the scanner, with exclusive backups.
See the operational commands in ``docs/source/vaults/infinit/index.rst``.
"""

import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT, INFINIT_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.infinit.historical_context import InfinitHistoricalContextStore, fetch_and_store_infinit_history, validate_infinit_price_coverage
from eth_defi.erc_4626.vault_protocol.infinit.vault import InfinitVault
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec, VaultTechnicalRisk
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)
SPEC = VaultSpec(INFINIT_CHAIN_ID, APEX_PRODUCT.address)


def read_bool(name: str, *, default: bool) -> bool:
    """Parse a strict boolean environment setting.

    Invalid values abort before maintenance begins.

    :param name: Variable name.
    :param default: Default when absent.
    :return: Parsed boolean.
    """
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1"}


@dataclass(slots=True)
class InfinitMigrationConfig:
    """Dataset paths and the single dry-run/apply switch."""

    #: Pipeline directory owning the scanner lock and temporary staging.
    directory: Path
    #: Vault catalogue pickle.
    metadata: Path
    #: Raw shared price Parquet.
    raw: Path
    #: Cleaned shared price Parquet.
    cleaned: Path
    #: Shared contextual-history database.
    context: Path
    #: Read-only input directory containing dense per-chain timestamps.
    timestamp_cache: Path
    #: Stage real reads without publishing outputs.
    dry_run: bool = True
    #: Common historical writer's maximum worker count.
    max_workers: int = 4

    @classmethod
    def from_env(cls) -> "InfinitMigrationConfig":
        """Read paths without redirecting HOME or production mounts.

        :return: Environment-configured maintenance inputs.
        """
        directory = get_pipeline_data_dir().resolve()

        def path(name: str, filename: str) -> Path:
            """Resolve an optional dataset override relative to the working directory."""
            return Path(os.environ.get(name, directory / filename)).expanduser().resolve()

        return cls(
            directory=directory,
            metadata=path("VAULT_DB_PATH", "vault-metadata-db.pickle"),
            raw=path("UNCLEANED_PRICE_DATABASE", "vault-prices-1h.parquet"),
            cleaned=path("CLEANED_PRICE_DATABASE", "cleaned-vault-prices-1h.parquet"),
            context=path("HISTORICAL_CONTEXT_PATH", "vault-historical-context.duckdb"),
            timestamp_cache=Path(os.environ.get("TIMESTAMP_CACHE_PATH", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser().resolve(),
            dry_run=read_bool("DRY_RUN", default=True),
            max_workers=int(os.environ.get("MAX_WORKERS", "4")),
        )


def publish_staged_files(staged: dict[Path, Path]) -> None:
    """Back up and atomically replace each validated maintenance output.

    The caller holds the pipeline lock. Backups are never overwritten. On an
    I/O failure, already published files are restored from those backups.

    :param staged: Destination to staged-file mapping, on the same filesystem.
    """
    suffix = native_datetime_utc_now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex
    backups = {}
    for target in staged:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_name(target.name + f".infinit-{suffix}.bak")
            with target.open("rb") as source, backup.open("xb") as dest:
                shutil.copyfileobj(source, dest)
            backups[target] = backup
            logger.info("Backup: %s", backup)
    published = []
    try:
        for target, source in staged.items():
            os.replace(source, target)
            published.append(target)
    except OSError:
        for target in reversed(published):
            if target in backups:
                restore = target.with_name(target.name + f".{suffix}.restore")
                shutil.copy2(backups[target], restore)
                os.replace(restore, target)
            else:
                target.unlink()
        raise


def run_staged(config: InfinitMigrationConfig, targets: tuple[Path, ...], operation: Callable[[dict[Path, Path]], dict[str, object]]) -> dict[str, object]:
    """Run real maintenance on copies, then optionally publish its outputs.

    Existing Parquet must be readable before copying. No reader-state or chain
    discovery cursor is reset. Dry runs retain only their observable summary.

    :param config: Selected dataset paths and mode.
    :param targets: Files the operation is authorised to change.
    :param operation: Maintenance callback receiving destination/stage mapping.
    :return: Operation summary including publication mode.
    """
    if len(set(targets)) != len(targets):
        msg = "Infinit output paths must be distinct"
        raise ValueError(msg)
    config.directory.mkdir(parents=True, exist_ok=True)
    with wait_other_writers(config.directory / "scan-pipeline", timeout=60):
        with tempfile.TemporaryDirectory(prefix=".infinit-stage-", dir=config.directory) as directory:
            staged = {target: Path(directory) / f"{i}-{target.name}" for i, target in enumerate(targets)}
            for target, stage in staged.items():
                if target.exists():
                    logger.info("Staging %s (%s bytes)", target, target.stat().st_size)
                    if target.suffix == ".parquet":
                        pq.ParquetFile(target)
                    shutil.copy2(target, stage)
                    wal = Path(str(target) + ".wal")
                    if target.suffix == ".duckdb" and wal.exists():
                        shutil.copy2(wal, Path(str(stage) + ".wal"))
            summary = operation(staged)
            for stage in staged.values():
                if not stage.exists():
                    raise ValueError(f"Maintenance did not produce its staged output: {stage.name}")
                if stage.suffix == ".parquet":
                    pq.ParquetFile(stage)
                if stage.suffix == ".duckdb" and Path(str(stage) + ".wal").exists():
                    msg = "Staged Infinit context has an uncheckpointed WAL; refusing incomplete publication"
                    raise ValueError(msg)
            if not config.dry_run:
                # A live WAL beside a replaced DuckDB would replay the old
                # database's writes. Refuse it instead of deleting evidence.
                if any(Path(str(t) + ".wal").exists() for t in targets if t.suffix == ".duckdb"):
                    msg = "Checkpoint the existing context DuckDB before apply; its WAL must not be discarded"
                    raise ValueError(msg)
                publish_staged_files(staged)
            return summary | {"mode": "dry-run" if config.dry_run else "applied"}


def fetch_migration_end_block(web3: Web3, cache_path: Path) -> int:
    """Choose an exclusive source boundary covered by the existing cache.

    The migration leaves the timestamp cache unchanged and limits its source
    horizon to both cached history and the safe RPC head. Dense coverage is
    checked by the historical reader before any outputs are published.

    :param web3: Configured Arbitrum connection.
    :param cache_path: Directory containing the dense per-chain timestamp cache.
    :return: Exclusive cached block boundary, at most RPC head minus 64.
    """
    path = cache_path / f"{INFINIT_CHAIN_ID}-timestamps.duckdb"
    if not path.exists():
        raise ValueError(f"Restore or prepopulate the dense Arbitrum timestamp cache: {path}")
    safe_end = web3.eth.block_number - 64
    with duckdb.connect(str(path), read_only=True) as connection:
        first, last = connection.execute(
            """SELECT min(block_number),
                min(block_number) FILTER (WHERE next_block IS NULL OR next_block<>block_number+1)
            FROM (
                SELECT block_number,lead(block_number) OVER (ORDER BY block_number) AS next_block
                FROM block_timestamps WHERE block_number>=? AND block_number<?
            )""",
            [APEX_PRODUCT.deployment_block, safe_end],
        ).fetchone()
    if first != APEX_PRODUCT.deployment_block or last is None:
        msg = "Arbitrum timestamp cache does not cover the Infinit deployment"
        raise ValueError(msg)
    end = last + 1
    logger.info("Infinit history will run through cached block %s", end - 1)
    return end


def run_infinit_migration(config: InfinitMigrationConfig) -> dict[str, object]:
    """Migrate Infinit discovery, metadata and history together.

    One staged operation recovers provenance, refreshes metadata, backfills raw
    prices and updates cleaned history when that dataset exists. A failed step
    prevents publication of every output. Chain cursors and reader state remain
    outside the write set. See https://app.infinit.tech/vaults/apex-multistrategy.

    :param config: Dataset paths and dry-run/apply setting.
    :return: Source, metadata and price counts with publication mode.
    """
    if not config.metadata.is_file():
        raise ValueError(f"Existing vault metadata database required: {config.metadata}")
    factory = MultiProviderWeb3Factory(read_json_rpc_url(INFINIT_CHAIN_ID))
    web3 = factory()
    if web3.eth.chain_id != INFINIT_CHAIN_ID:
        msg = "Infinit requires Arbitrum RPC"
        raise ValueError(msg)
    end = fetch_migration_end_block(web3, config.timestamp_cache)
    client = configure_hypersync_from_env(web3).hypersync_client
    if client is None:
        msg = "Hypersync is required for Infinit provenance and supply"
        raise ValueError(msg)
    targets = (config.metadata, config.context, config.raw) + ((config.cleaned,) if config.cleaned.exists() else ())

    def operation(staged: dict[Path, Path]) -> dict[str, object]:
        metadata_path = staged[config.metadata]
        db = VaultDatabase.read(metadata_path)
        logger.info("Infinit: collecting source prices and indexed deployment/activity/supply")
        count = fetch_and_store_infinit_history(web3=web3, hypersync_client=client, product=APEX_PRODUCT, end_block=end, context_path=staged[config.context], timestamp_cache_path=config.timestamp_cache)
        with InfinitHistoricalContextStore(staged[config.context], read_only=True) as store:
            creations = store.connection.execute("SELECT block_number,transaction_hash FROM infinit_events WHERE vault_address=? AND kind='created' AND block_number<?", [APEX_PRODUCT.address, end]).fetchall()
            if len(creations) != 1 or creations[0][0] != APEX_PRODUCT.deployment_block:
                msg = "Reviewed Infinit factory creation was not recovered"
                raise ValueError(msg)
            activity = dict(store.connection.execute("SELECT kind,count(*) FROM infinit_events WHERE vault_address=? AND block_number<? GROUP BY kind", [APEX_PRODUCT.address, end]).fetchall())
        deposits, withdrawals = activity.get("deposit", 0), activity.get("withdraw", 0)
        logger.info("Infinit: refreshing lead and metadata (%s deposits, %s withdrawals)", deposits, withdrawals)
        db.leads[SPEC] = PotentialVaultMatch(INFINIT_CHAIN_ID, APEX_PRODUCT.address, APEX_PRODUCT.deployment_block, APEX_PRODUCT.deployed_at, deposits, withdrawals, configuration_count=1)
        detection = ERC4262VaultDetection(INFINIT_CHAIN_ID, APEX_PRODUCT.address, APEX_PRODUCT.deployment_block, APEX_PRODUCT.deployed_at, {ERC4626Feature.infinit_like}, native_datetime_utc_now(), deposits, withdrawals, configuration_count=1)
        row = create_vault_scan_record(web3, detection, end - 1, token_cache={})
        if row["Protocol"] != "Infinit" or row["Name"].startswith("<") or row.get("_fees") is None:
            msg = "Infinit metadata validation failed; refusing to publish a broken row"
            raise ValueError(msg)
        db.rows[SPEC] = db.rows.get(SPEC, {}) | row | {"_risk": VaultTechnicalRisk.dangerous}
        db.write(metadata_path)
        validate_infinit_price_coverage(staged[config.raw], staged[config.context], APEX_PRODUCT, APEX_PRODUCT.deployment_block, end)
        vault = InfinitVault(web3, SPEC, token_cache={}, default_block_identifier=end - 1)
        vault.historical_context_path = staged[config.context]
        logger.info("Infinit: writing hourly history")
        token_cache = TokenDiskCache(staged[config.raw].parent / "tokens.sqlite")
        try:
            result = scan_historical_prices_to_parquet(
                output_fname=staged[config.raw],
                web3=web3,
                web3factory=factory,
                vaults=[vault],
                token_cache=token_cache,
                start_block=APEX_PRODUCT.deployment_block,
                end_block=end,
                max_workers=config.max_workers,
                frequency="1h",
                hypersync_client=client,
                timestamp_cache_file=config.timestamp_cache,
                vault_addresses={APEX_PRODUCT.address},
            )
        finally:
            token_cache.close()
        summary = {"vault": SPEC.as_string_id(), "end_block_exclusive": end, "creation_transaction": creations[0][1], "deposits": deposits, "withdrawals": withdrawals, "name": row["Name"], "risk": "dangerous", "shares": str(row["Shares"]), "nav": str(row["NAV"]), "source_observations": count, "raw_rows": result["rows_written"]}
        if config.cleaned in staged:
            logger.info("Infinit: refreshing existing cleaned history")
            summary["cleaned_rows"] = replace_cleaned_vault_histories({SPEC.as_string_id()}, vault_db_path=metadata_path, raw_price_df_path=staged[config.raw], cleaned_price_df_path=staged[config.cleaned], logger=logger.info)
        return summary

    return run_staged(config, targets, operation)


def main() -> None:
    """Run the single Infinit migration with ``DRY_RUN=true`` by default.

    Logging remains visible during source scans and staging. Failures abort
    without publishing incomplete output.

    :return: ``None`` after logging the complete migration summary.
    """
    setup_console_logging(default_log_level="info", std_out_log_level="info")
    summary = run_infinit_migration(InfinitMigrationConfig.from_env())
    logger.info("\n%s", tabulate([summary], headers="keys", tablefmt="github"))
