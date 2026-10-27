"""One-command Infinit migration, publication and automatic cache boundaries."""

import datetime
import logging
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from eth_defi.erc_4626.vault_protocol.infinit import migration
from eth_defi.erc_4626.vault_protocol.infinit.api import InfinitHistory, InfinitPricePoint
from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT, INFINIT_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.infinit.events import InfinitEvent
from eth_defi.erc_4626.vault_protocol.infinit.historical_context import InfinitHistoricalContextStore, InfinitHistoryCoverageError
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def recorded_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Provide recorded transport while exercising real files and staging.

    The source collector writes a real context database. Export transports
    produce small Parquet files, allowing byte-level isolation checks without
    live provider access or a full pipeline dataset.

    :param tmp_path: Isolated pipeline directory.
    :param monkeypatch: Replace only provider reads and price-export seams.
    :return: Dataset paths and an unrelated-vault sentinel.
    """
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TIMESTAMP_CACHE_PATH", str(tmp_path / "timestamps"))
    monkeypatch.delenv("DRY_RUN", raising=False)
    config = migration.InfinitMigrationConfig.from_env()
    foreign = VaultSpec(42161, "0x" + "77" * 20)
    database = VaultDatabase()
    database.rows[foreign] = {"sentinel": "unrelated metadata"}
    database.last_scanned_block[42161] = APEX_PRODUCT.deployment_block + 100
    database.write(config.metadata)
    timestamp = APEX_PRODUCT.deployed_at + datetime.timedelta(minutes=15)
    frame = pd.DataFrame({"chain": [INFINIT_CHAIN_ID], "address": [foreign.vault_address], "block_number": [APEX_PRODUCT.deployment_block], "timestamp": [timestamp], "share_price": [123.0]})
    frame.to_parquet(config.raw, index=False)
    frame.to_parquet(config.cleaned, index=False)
    reader = tmp_path / "vault-reader-state-1h.pickle"
    reader.write_bytes(b"critical reader state")
    with InfinitHistoricalContextStore(config.context) as store:
        store.connection.execute("CREATE TABLE unrelated AS SELECT 42 AS sentinel")
    config.timestamp_cache.mkdir()
    stamps = config.timestamp_cache / f"{INFINIT_CHAIN_ID}-timestamps.duckdb"
    with duckdb.connect(str(stamps)) as connection:
        connection.execute("CREATE TABLE block_timestamps AS SELECT ?+i AS block_number FROM range(4) t(i)", [APEX_PRODUCT.deployment_block])
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=INFINIT_CHAIN_ID, block_number=APEX_PRODUCT.deployment_block + 100))
    monkeypatch.setattr(migration, "read_json_rpc_url", lambda _chain: "https://recorded.invalid")
    monkeypatch.setattr(migration, "MultiProviderWeb3Factory", lambda _url: lambda: web3)
    monkeypatch.setattr(migration, "configure_hypersync_from_env", lambda _web3: SimpleNamespace(hypersync_client=object()))
    monkeypatch.setattr(migration, "InfinitVault", lambda *_args, **_kwargs: SimpleNamespace())

    def collect(**kwargs: object) -> int:
        """Persist one original observation and complete deployment provenance."""
        point = InfinitPricePoint(timestamp, Decimal("1.01"))
        history = InfinitHistory(APEX_PRODUCT.application_id, (point,), "recorded", "original response")
        events = [InfinitEvent(APEX_PRODUCT.deployment_block, index, 0, kind, amount, "0xcreated") for index, (kind, amount) in enumerate((("created", 0), ("deposit", 1_000_000), ("mint", 1_000_000)))]
        with InfinitHistoricalContextStore(kwargs["context_path"]) as store:
            return store.store(APEX_PRODUCT, history, (point,), {timestamp: APEX_PRODUCT.deployment_block + 2}, events, APEX_PRODUCT.deployment_block, kwargs["end_block"], expected_supply=1_000_000)

    def export_prices(**kwargs: object) -> dict[str, int]:
        """Replace only the selected vault's raw rows in the staged Parquet."""
        output = kwargs["output_fname"]
        existing = pd.read_parquet(output)
        own = frame.assign(address=APEX_PRODUCT.address, share_price=1.01, block_number=APEX_PRODUCT.deployment_block + 2)
        pd.concat([existing[existing.address != APEX_PRODUCT.address], own]).to_parquet(output, index=False)
        return {"rows_written": 1}

    def clean(ids: set[str], **kwargs: object) -> int:
        """Require refreshed staged metadata, then preserve unrelated clean rows."""
        assert ids == {migration.SPEC.as_string_id()}
        assert kwargs["vault_db_path"] != config.metadata
        assert VaultDatabase.read(kwargs["vault_db_path"]).rows[migration.SPEC]["_manager_name"] == "Apex Quant Capital"
        pd.read_parquet(kwargs["raw_price_df_path"]).to_parquet(kwargs["cleaned_price_df_path"], index=False)
        return 1

    monkeypatch.setattr(migration, "fetch_and_store_infinit_history", collect)
    monkeypatch.setattr(migration, "create_vault_scan_record", lambda *_args, **_kwargs: {"Protocol": "Infinit", "Name": "APEX MultiStrategy", "Shares": Decimal(1), "NAV": Decimal("1.01"), "_fees": {"management": 0.02}, "_manager_name": "Apex Quant Capital"})
    monkeypatch.setattr(migration, "scan_historical_prices_to_parquet", export_prices)
    monkeypatch.setattr(migration, "replace_cleaned_vault_histories", clean)
    return SimpleNamespace(config=config, foreign=foreign, sentinel=frame, reader=reader, stamps=stamps)


@pytest.mark.parametrize("existing_cleaned", [True, False])
def test_infinit_single_migration_dry_run_and_repeated_apply(recorded_migration: SimpleNamespace, *, existing_cleaned: bool) -> None:
    """A fresh catalogue needs one invocation, including automatic cleaned output.

    Dry runs leave all datasets unchanged; repeated apply preserves unrelated
    rows, discovery cursors, source provenance, timestamps and reader state.

    :param recorded_migration: Real staged files with recorded provider seams.
    :param existing_cleaned: Whether a cleaned dataset already exists.
    :return: ``None`` after checking isolation and idempotent row values.
    """
    fixture = recorded_migration
    config = fixture.config
    if not existing_cleaned:
        config.cleaned.unlink()
    paths = [config.metadata, config.context, config.raw, fixture.reader, fixture.stamps]
    if existing_cleaned:
        paths.append(config.cleaned)
    before = {path: path.read_bytes() for path in paths}
    summary = migration.run_infinit_migration(config)
    assert summary["mode"] == "dry-run"
    assert summary["deposits"] == 1
    assert ("cleaned_rows" in summary) == existing_cleaned
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not list(config.directory.glob("*.bak"))
    config.dry_run = False
    for _ in range(2):
        summary = migration.run_infinit_migration(config)
        assert summary["mode"] == "applied"
        database = VaultDatabase.read(config.metadata)
        assert database.rows[fixture.foreign] == {"sentinel": "unrelated metadata"}
        assert database.last_scanned_block[42161] == APEX_PRODUCT.deployment_block + 100
        assert database.rows[migration.SPEC]["_risk"] == VaultTechnicalRisk.dangerous
        assert database.leads[migration.SPEC].deposit_count == 1
        for path in (config.raw, config.cleaned) if existing_cleaned else (config.raw,):
            prices = pd.read_parquet(path)
            assert len(prices[prices.address == APEX_PRODUCT.address]) == 1
            pd.testing.assert_frame_equal(prices[prices.address == fixture.foreign.vault_address].reset_index(drop=True), fixture.sentinel)
        with InfinitHistoricalContextStore(config.context, read_only=True) as store:
            assert store.activity_counts(APEX_PRODUCT) == {"created": 1, "deposit": 1, "mint": 1}
            assert store.connection.execute("SELECT sentinel FROM unrelated").fetchone() == (42,)
        assert fixture.reader.read_bytes() == before[fixture.reader]
        assert fixture.stamps.read_bytes() == before[fixture.stamps]
    assert config.cleaned.exists() == existing_cleaned


@pytest.mark.parametrize("failure_phase", ["cleaning", "source_coverage"])
def test_infinit_single_migration_failure_publishes_nothing(recorded_migration: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure_phase: str) -> None:
    """A source-coverage or late cleaning failure cannot publish earlier changes.

    The entire workflow shares one stage, including an already populated
    context database. Failure must leave every original dataset unchanged.

    :param recorded_migration: Isolated file-backed workflow.
    :param monkeypatch: Inject a failure after staging the earlier steps.
    :param failure_phase: Publication guard or final cleaning step to fail.
    :return: ``None`` after checking the complete write set.
    """
    config = recorded_migration.config
    config.dry_run = False
    paths = (config.metadata, config.context, config.raw, config.cleaned)
    before = {path: path.read_bytes() for path in paths}

    def fail(*_args: object, **_kwargs: object) -> None:
        """Fail after all source, lead, metadata and raw-price work completed."""
        message = "migration failed"
        error = InfinitHistoryCoverageError if failure_phase == "source_coverage" else ValueError
        raise error(message)

    operation = "validate_infinit_price_coverage" if failure_phase == "source_coverage" else "replace_cleaned_vault_histories"
    monkeypatch.setattr(migration, operation, fail)
    with pytest.raises(ValueError, match="migration failed"):
        migration.run_infinit_migration(config)
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not list(config.directory.glob("*.bak"))


def test_infinit_migration_uses_safe_cached_boundary(recorded_migration: SimpleNamespace) -> None:
    """The range needs no operator setting and never exceeds safe RPC history.

    A cache ahead of RPC and a cache behind RPC both yield a bounded range.
    Missing cache data is an explicit error rather than a sparse bootstrap.

    :param recorded_migration: Four cached blocks after vault deployment.
    :return: ``None`` after checking both automatic range limits.
    """
    cache = recorded_migration.config.timestamp_cache
    web3 = SimpleNamespace(eth=SimpleNamespace(block_number=APEX_PRODUCT.deployment_block + 100))
    assert migration.fetch_migration_end_block(web3, cache) == APEX_PRODUCT.deployment_block + 4
    web3.eth.block_number = APEX_PRODUCT.deployment_block + 66
    assert migration.fetch_migration_end_block(web3, cache) == APEX_PRODUCT.deployment_block + 2
    # Other integrations may cache isolated timestamps beyond the dense prefix.
    with duckdb.connect(str(recorded_migration.stamps)) as connection:
        connection.execute("INSERT INTO block_timestamps VALUES (?)", [APEX_PRODUCT.deployment_block + 10])
    web3.eth.block_number = APEX_PRODUCT.deployment_block + 100
    assert migration.fetch_migration_end_block(web3, cache) == APEX_PRODUCT.deployment_block + 4
    with duckdb.connect(str(recorded_migration.stamps)) as connection:
        connection.execute("DELETE FROM block_timestamps WHERE block_number=?", [APEX_PRODUCT.deployment_block])
    with pytest.raises(ValueError, match="does not cover the Infinit deployment"):
        migration.run_infinit_migration(recorded_migration.config)
    assert not list(recorded_migration.config.directory.glob("*.bak"))
    recorded_migration.stamps.unlink()
    with pytest.raises(ValueError, match="dense Arbitrum timestamp cache"):
        migration.fetch_migration_end_block(web3, cache)


def test_infinit_migration_requires_existing_metadata(recorded_migration: SimpleNamespace) -> None:
    """A wrong or missing catalogue path cannot create a one-vault replacement.

    Other datasets may already contain years of history and must remain intact
    even when the metadata input was accidentally removed.

    :param recorded_migration: Existing pipeline files with recorded transport.
    :return: ``None`` after checking fail-closed behaviour.
    """
    config = recorded_migration.config
    before = config.raw.read_bytes()
    config.metadata.unlink()
    config.dry_run = False
    with pytest.raises(ValueError, match="Existing vault metadata database required"):
        migration.run_infinit_migration(config)
    assert not config.metadata.exists()
    assert config.raw.read_bytes() == before


def test_infinit_migration_summary_visible_at_container_log_level(recorded_migration: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The documented container command always shows progress and its summary.

    Compose supplies warning-level logging by default. The migration must still
    show its dry-run result without requiring another operator setting.

    :param recorded_migration: Complete isolated workflow.
    :param monkeypatch: Supply the container's default logging environment.
    :param capsys: Capture real console logging.
    :return: ``None`` after checking progress and the final publication mode.
    """
    monkeypatch.setenv("LOG_LEVEL", "warning")
    before = recorded_migration.config.metadata.read_bytes()
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        migration.main()
        output = capsys.readouterr().err
        assert "Infinit: writing hourly history" in output
        assert "dry-run" in output
        assert recorded_migration.config.metadata.read_bytes() == before
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
