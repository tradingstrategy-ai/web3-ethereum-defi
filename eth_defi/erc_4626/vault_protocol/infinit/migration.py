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

import pyarrow.parquet as pq
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT, INFINIT_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.infinit.events import fetch_infinit_events
from eth_defi.erc_4626.vault_protocol.infinit.historical_context import fetch_and_store_infinit_history, validate_infinit_price_coverage
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
    """Explicit paths and safe defaults shared by the three entry points."""

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
    #: Recompute only Infinit's cleaned history after raw backfill.
    clean_prices: bool = False
    #: Exclusive source horizon, defaulting to a safe RPC head.
    end_block: int | None = None
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
            clean_prices=read_bool("CLEAN_PRICES", default=False),
            end_block=int(os.environ["END_BLOCK"]) if os.environ.get("END_BLOCK") else None,
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
            if not config.dry_run:
                # A live WAL beside a replaced DuckDB would replay the old
                # database's writes. Refuse it instead of deleting evidence.
                if any(Path(str(t) + ".wal").exists() for t in targets if t.suffix == ".duckdb"):
                    msg = "Checkpoint the existing context DuckDB before apply; its WAL must not be discarded"
                    raise ValueError(msg)
                publish_staged_files(staged)
            return summary | {"mode": "dry-run" if config.dry_run else "applied"}


def run_infinit_migration(mode: str, config: InfinitMigrationConfig) -> dict[str, object]:
    """Read reviewed sources and update only the selected Infinit dataset.

    Leads, metadata and history are separate operator steps. Chain cursors and
    reader-state pickles are deliberately outside the write set.

    :param mode: ``leads``, ``metadata`` or ``history``.
    :param config: Explicit paths and dry-run setting.
    :return: Observable source and row counts.
    """
    if mode not in {"leads", "metadata", "history"}:
        raise ValueError(f"Unknown Infinit migration: {mode}")
    factory = MultiProviderWeb3Factory(read_json_rpc_url(INFINIT_CHAIN_ID))
    web3 = factory()
    if web3.eth.chain_id != INFINIT_CHAIN_ID:
        msg = "Infinit requires Arbitrum RPC"
        raise ValueError(msg)
    end = config.end_block or web3.eth.block_number - 64
    if end <= APEX_PRODUCT.deployment_block:
        msg = "END_BLOCK must follow the reviewed deployment"
        raise ValueError(msg)
    targets = (config.metadata,) if mode != "history" else (config.context, config.raw) + ((config.cleaned,) if config.clean_prices else ())

    def operation(staged: dict[Path, Path]) -> dict[str, object]:  # noqa: PLR0914 - scoped maintenance orchestration
        metadata_path = staged.get(config.metadata, config.metadata)
        db = VaultDatabase.read(metadata_path) if metadata_path.exists() else VaultDatabase()
        summary = {"operation": mode, "vault": str(SPEC), "end_block_exclusive": end}
        if mode == "leads":
            client = configure_hypersync_from_env(web3).hypersync_client
            if client is None:
                msg = "Hypersync is required for Infinit provenance"
                raise ValueError(msg)
            events = tuple(fetch_infinit_events(client, APEX_PRODUCT, end, config.timestamp_cache))
            creations = [e for e in events if e.kind == "created"]
            if len(creations) != 1 or creations[0].block_number != APEX_PRODUCT.deployment_block:
                msg = "Reviewed Infinit factory creation was not recovered"
                raise ValueError(msg)
            deposits = sum(e.kind == "deposit" for e in events)
            withdrawals = sum(e.kind == "withdraw" for e in events)
            db.leads[SPEC] = PotentialVaultMatch(INFINIT_CHAIN_ID, APEX_PRODUCT.address, APEX_PRODUCT.deployment_block, APEX_PRODUCT.deployed_at, deposits, withdrawals, configuration_count=1)
            db.write(metadata_path)
            summary.update(deposits=deposits, withdrawals=withdrawals, creation_transaction=creations[0].transaction_hash)
        elif mode == "metadata":
            lead = db.leads.get(SPEC)
            if lead is None:
                msg = "Run migrate-historical-leads.py first"
                raise ValueError(msg)
            detection = ERC4262VaultDetection(INFINIT_CHAIN_ID, APEX_PRODUCT.address, APEX_PRODUCT.deployment_block, APEX_PRODUCT.deployed_at, {ERC4626Feature.infinit_like}, native_datetime_utc_now(), lead.deposit_count, lead.withdrawal_count, configuration_count=1)
            row = create_vault_scan_record(web3, detection, end - 1, token_cache={})
            if row["Protocol"] != "Infinit" or row["Name"].startswith("<") or row.get("_fees") is None:
                msg = "Infinit metadata validation failed; refusing to publish a broken row"
                raise ValueError(msg)
            db.rows[SPEC] = db.rows.get(SPEC, {}) | row | {"_risk": VaultTechnicalRisk.dangerous}
            db.write(metadata_path)
            summary.update(name=row["Name"], risk="dangerous", shares=str(row["Shares"]), nav=str(row["NAV"]))
        else:
            if SPEC not in db.rows:
                msg = "Run migrate-metadata.py first"
                raise ValueError(msg)
            client = configure_hypersync_from_env(web3).hypersync_client
            if client is None:
                msg = "Hypersync is required for Infinit supply"
                raise ValueError(msg)
            count = fetch_and_store_infinit_history(web3=web3, hypersync_client=client, product=APEX_PRODUCT, end_block=end, context_path=staged[config.context], timestamp_cache_path=config.timestamp_cache)
            validate_infinit_price_coverage(staged[config.raw], staged[config.context], APEX_PRODUCT, APEX_PRODUCT.deployment_block, end)
            vault = InfinitVault(web3, SPEC, token_cache={}, default_block_identifier=end - 1)
            vault.historical_context_path = staged[config.context]
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
            summary.update(source_observations=count, raw_rows=result["rows_written"])
            if config.clean_prices:
                summary["cleaned_rows"] = replace_cleaned_vault_histories({f"{INFINIT_CHAIN_ID}-{APEX_PRODUCT.address}"}, vault_db_path=config.metadata, raw_price_df_path=staged[config.raw], cleaned_price_df_path=staged[config.cleaned], logger=logger.info)
        return summary

    return run_staged(config, targets, operation)


def main(mode: str) -> None:
    """Run an environment-configured maintenance entry point.

    Logging remains visible during source scans and staging. Failures abort
    without publishing incomplete output.

    :param mode: Selected maintenance operation.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    summary = run_infinit_migration(mode, InfinitMigrationConfig.from_env())
    logger.info("\n%s", tabulate([summary], headers="keys", tablefmt="github"))
