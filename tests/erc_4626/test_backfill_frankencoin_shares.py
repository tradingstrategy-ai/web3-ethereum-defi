"""Preservation and failure boundaries of the targeted FCS backfill."""

# Test fixtures use fixed event counts and explicit boolean modes.

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.frankencoin import backfill
from eth_defi.erc_4626.vault_protocol.frankencoin.constants import FRANKENCOIN_SHARES_ADDRESS, FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK, FRANKENCOIN_SHARES_DEPLOYMENT_TIME
from eth_defi.erc_4626.vault_protocol.frankencoin.shares import FrankencoinSharesVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flow_events import normalise_event_topic
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def vault() -> FrankencoinSharesVault:
    """Create an offline reviewed adapter for migration boundary tests.

    Its ABI binding needs no provider. Network reads are replaced only in
    tests of migration scope and publication ordering.

    :return: Ethereum FCS adapter.
    """
    return FrankencoinSharesVault(Web3(), backfill.FRANKENCOIN_SHARES_SPEC)


@pytest.fixture
def detection() -> ERC4262VaultDetection:
    """Build a pre-existing generic discovery record with observed counts.

    The first event intentionally follows deployment to check that observed
    discovery history is retained during reclassification.

    :return: Generic ERC-4626 detection for FCS.
    """
    return ERC4262VaultDetection(chain=1, address=FRANKENCOIN_SHARES_ADDRESS, first_seen_at_block=FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK + 100, first_seen_at=FRANKENCOIN_SHARES_DEPLOYMENT_TIME, features=set(), updated_at=FRANKENCOIN_SHARES_DEPLOYMENT_TIME, deposit_count=7, redeem_count=3, configuration_count=2)


def test_fcs_reclassification_preserves_history_and_cursors(vault: FrankencoinSharesVault, detection: ERC4262VaultDetection) -> None:
    """Retain discovery history and chain progress while relabelling FCS.

    Reclassification must be idempotent and preserve unrelated rows and leads.

    :param vault: Offline Ethereum FCS adapter.
    :param detection: Existing generic detection with observed event counts.
    :return: ``None`` after checking the in-memory migration.
    """
    unrelated = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    other_chain = VaultSpec(8453, FRANKENCOIN_SHARES_ADDRESS)
    db = VaultDatabase(rows={backfill.FRANKENCOIN_SHARES_SPEC: {"_detection_data": detection}, unrelated: {"Name": "Keep"}, other_chain: {"Name": "Other chain"}}, last_scanned_block={1: 123, 8453: 456})
    rebuilt = backfill.fetch_frankencoin_shares_detection(vault, db, Mock(), FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK + 200)
    assert rebuilt is not detection
    assert rebuilt.features == {ERC4626Feature.frankencoin_fcs_like}
    assert (rebuilt.deposit_count, rebuilt.redeem_count, rebuilt.configuration_count) == (7, 3, 2)
    assert rebuilt.first_seen_at_block == detection.first_seen_at_block
    assert detection.features == set()
    row = {"Name": "Frankencoin Shares", "_detection_data": rebuilt}
    backfill.apply_frankencoin_shares_metadata(db, rebuilt, row)
    lead = db.leads[backfill.FRANKENCOIN_SHARES_SPEC]
    assert (lead.deposit_count, lead.withdrawal_count, lead.configuration_count) == (7, 3, 2)
    assert db.rows[unrelated] == {"Name": "Keep"}
    assert db.rows[other_chain] == {"Name": "Other chain"}
    assert db.last_scanned_block == {1: 123, 8453: 456}
    backfill.apply_frankencoin_shares_metadata(db, rebuilt, row)
    assert db.leads[backfill.FRANKENCOIN_SHARES_SPEC] is lead


def test_fcs_seed_separates_wraps_from_zchf_flows(vault: FrankencoinSharesVault, monkeypatch: pytest.MonkeyPatch) -> None:
    """Count wrapping separately when seeding an absent FCS lead.

    Use verified ABI topics with controlled event observations to distinguish
    fresh ZCHF deposits from migrations of existing FPS capital.

    :param vault: Offline Ethereum FCS adapter.
    :param monkeypatch: Replacement for the Hypersync event fetcher.
    :return: ``None`` after checking discovery counts and scope.
    """
    events = vault.vault_contract.events
    topics = [normalise_event_topic(get_topic_signature_from_event(event)) for event in (events.Deposit, events.Withdraw, events.Wrapped, events.Unwrapped)]
    fetch = Mock(return_value=[SimpleNamespace(topics=[topics[index]]) for index in (0, 0, 1, 2, 2, 3)])
    monkeypatch.setattr(backfill, "fetch_vault_flow_logs_hypersync", fetch)
    db = VaultDatabase()
    result = backfill.fetch_frankencoin_shares_detection(vault, db, Mock(), FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK + 200)
    assert (result.deposit_count, result.redeem_count, result.configuration_count) == (2, 1, 3)
    assert result.first_seen_at_block == FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK
    assert result.first_seen_at == FRANKENCOIN_SHARES_DEPLOYMENT_TIME
    assert fetch.call_args.kwargs["vault_address"] == FRANKENCOIN_SHARES_ADDRESS
    assert fetch.call_args.kwargs["start_block"] == FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK
    assert db.rows == db.leads == db.last_scanned_block == {}


@pytest.mark.parametrize("invalid_column", ["share_price", "total_assets", "total_supply"])
def test_fcs_prices_are_stateless_and_address_scoped(vault: FrankencoinSharesVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_column: str) -> None:
    """Require dense timestamps and restrict price replacement to FCS.

    Check missing and incomplete file-backed caches before invoking the writer,
    and reject empty results without changing scheduled reader state.

    :param vault: Offline Ethereum FCS adapter.
    :param tmp_path: Isolated timestamp and staged-output directory.
    :param monkeypatch: Replacement for the common Parquet writer.
    :param invalid_column: Accounting field with a simulated failed RPC read.
    :return: ``None`` after checking cache and writer boundaries.
    """
    writer = Mock(return_value={"price_rows_written_by_vault": {FRANKENCOIN_SHARES_ADDRESS: 1}})
    monkeypatch.setattr(backfill, "scan_historical_prices_to_parquet", writer)
    kwargs = {"rpc_url": "https://example.invalid", "output_path": tmp_path / "staged.parquet", "token_cache": Mock(), "hypersync_client": Mock(), "timestamp_cache_path": tmp_path, "start_block": FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK, "end_block": FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK + 1000, "max_workers": 1}
    with pytest.raises(ValueError, match="dense Ethereum timestamp cache"):
        backfill.fetch_frankencoin_shares_prices(vault, **kwargs)
    writer.assert_not_called()
    cache_path = tmp_path / "1-timestamps.duckdb"
    connection = duckdb.connect(str(cache_path))
    try:
        connection.execute("CREATE TABLE block_timestamps AS SELECT range AS block_number, 1790583671 AS timestamp FROM range(?, ?)", [kwargs["start_block"], kwargs["end_block"]])
    finally:
        connection.close()
    with pytest.raises(ValueError, match="Prepopulate dense Ethereum timestamp coverage"):
        backfill.fetch_frankencoin_shares_prices(vault, **kwargs)
    writer.assert_not_called()
    connection = duckdb.connect(str(cache_path))
    try:
        connection.execute("INSERT INTO block_timestamps VALUES (?, 1790583671)", [kwargs["end_block"]])
    finally:
        connection.close()
    observation = {"chain": [1], "address": [FRANKENCOIN_SHARES_ADDRESS], "block_number": [kwargs["start_block"]], "share_price": [3.0], "total_assets": [0.0], "total_supply": [0.0]}
    pq.write_table(pa.table(observation), kwargs["output_path"])
    backfill.fetch_frankencoin_shares_prices(vault, **kwargs)
    assert writer.call_args.kwargs["vault_addresses"] == {FRANKENCOIN_SHARES_ADDRESS}
    assert writer.call_args.kwargs["vaults"] == [vault]
    assert writer.call_args.kwargs["reader_states"] is None
    assert writer.call_args.kwargs["require_multicall_result"] is True
    observation[invalid_column] = [float("nan")]
    pq.write_table(pa.table(observation), kwargs["output_path"])
    with pytest.raises(RuntimeError, match="failed accounting reads"):
        backfill.fetch_frankencoin_shares_prices(vault, **kwargs)
    writer.return_value = {"price_rows_written_by_vault": {}}
    with pytest.raises(RuntimeError, match="no valid prices"):
        backfill.fetch_frankencoin_shares_prices(vault, **kwargs)


@pytest.fixture
def script() -> ModuleType:
    """Load the environment-driven script without running its entrypoint.

    Dashed script names are loaded through importlib, following other
    protocol migration tests.

    :return: Backfill script module.
    """
    path = Path(__file__).resolve().parents[2] / "scripts/erc-4626/backfill-frankencoin-shares.py"
    spec = importlib.util.spec_from_file_location("backfill_frankencoin_shares", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("scan_prices", [False, True])
def test_fcs_script_preserves_files_or_backs_up_metadata(script: ModuleType, detection: ERC4262VaultDetection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, apply: bool, scan_prices: bool) -> None:
    """Preserve original files or publish validated metadata with a backup.

    Exercise both modes and a failed price preparation to ensure unrelated
    metadata, prices and scheduled reader state survive.

    :param script: Imported backfill entrypoint.
    :param detection: Prepared FCS discovery record.
    :param tmp_path: Isolated pipeline and staging directory.
    :param monkeypatch: Controlled settings and provider replacements.
    :param apply: Whether the first invocation publishes metadata.
    :param scan_prices: Whether price preparation is requested.
    :return: ``None`` after checking persisted files.
    """
    db_path = tmp_path / "metadata.pickle"
    parquet = tmp_path / "prices.parquet"
    reader_state = tmp_path / "vault-reader-state-1h.pickle"
    other = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    VaultDatabase(rows={other: {"Name": "Keep"}}, last_scanned_block={1: 99}).write(db_path)
    parquet.write_bytes(b"existing prices")
    reader_state.write_bytes(b"existing reader state")
    original = db_path.read_bytes()
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_DB_PATH", str(db_path))
    monkeypatch.setenv("PARQUET_PATH", str(parquet))
    monkeypatch.setenv("DRY_RUN", "false" if apply else "true")
    monkeypatch.setenv("FRANKENCOIN_SHARES_SCAN_PRICES", "true" if scan_prices else "false")
    monkeypatch.setattr(script, "read_json_rpc_url", Mock(return_value="https://example.invalid"))
    monkeypatch.setattr(script, "create_multi_provider_web3", Mock())
    monkeypatch.setattr(script, "get_almost_latest_block_number", Mock(return_value=FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK + 1000))
    monkeypatch.setattr(script, "configure_hypersync_from_env", Mock())
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setattr(script.tempfile, "mkdtemp", Mock(return_value=str(stage)))
    row = {"Name": "Frankencoin Shares", "Protocol": "Frankencoin", "_manager_name": "Frankencoin", "_strategy_tags": {StrategyTag.protocol_equity, StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending}, "_detection_data": detection}
    monkeypatch.setattr(script, "fetch_frankencoin_shares_metadata", Mock(return_value=(Mock(), detection, row)))
    # Publication only needs an already validated staged price file. The
    # helper's accounting checks are exercised separately above.
    prepared_prices = stage / parquet.name
    monkeypatch.setattr(script, "fetch_frankencoin_shares_prices", Mock(side_effect=lambda *_args, **_kwargs: prepared_prices.write_bytes(b"validated prices")))
    monkeypatch.setattr(script, "pformat_scan_result", Mock(return_value="prepared price report"))
    script.main()
    if apply:
        updated = VaultDatabase.read(db_path)
        assert updated.rows[backfill.FRANKENCOIN_SHARES_SPEC] == row
        assert updated.rows[other] == {"Name": "Keep"}
        assert updated.last_scanned_block == {1: 99}
        backups = tuple(tmp_path.glob("metadata.pickle.bak-fcs-*"))
        assert len(backups) == 1 and backups[0].read_bytes() == original
    else:
        assert db_path.read_bytes() == original
        assert VaultDatabase.read(stage / db_path.name).rows[backfill.FRANKENCOIN_SHARES_SPEC] == row
    expected_prices = b"validated prices" if apply and scan_prices else b"existing prices"
    assert parquet.read_bytes() == expected_prices
    assert reader_state.read_bytes() == b"existing reader state"
    # A failed price preparation must never publish either prepared file.
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("FRANKENCOIN_SHARES_SCAN_PRICES", "true")
    failure = RuntimeError("no valid prices")
    monkeypatch.setattr(script, "fetch_frankencoin_shares_prices", Mock(side_effect=failure))
    before_failure = db_path.read_bytes()
    with pytest.raises(RuntimeError, match="no valid prices"):
        script.main()
    assert db_path.read_bytes() == before_failure
    assert parquet.read_bytes() == expected_prices
    assert reader_state.read_bytes() == b"existing reader state"


def test_fcs_script_rejects_ambiguous_write_setting(script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a misspelt dry-run setting before writes can be enabled.

    A typo must fail explicitly rather than select the apply path.

    :param script: Imported backfill entrypoint.
    :param monkeypatch: Controlled environment setting.
    :return: ``None`` after checking the configuration error.
    """
    monkeypatch.setenv("DRY_RUN", "flase")
    with pytest.raises(ValueError, match="DRY_RUN must be true or false"):
        script.parse_bool_env("DRY_RUN", default=True)


def test_fcs_script_refuses_apply_without_metadata_database(script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject an applied repair aimed at a missing metadata database.

    A wrong operator path must fail before provider access or creation of an
    FCS-only database. Dry runs may still prepare a previously absent lead.

    :param script: Imported backfill entrypoint.
    :param tmp_path: Isolated directory containing no metadata database.
    :param monkeypatch: Controlled apply mode and metadata path.
    :return: ``None`` after checking the missing-database error.
    """
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "missing.pickle"))
    monkeypatch.setenv("DRY_RUN", "false")
    with pytest.raises(FileNotFoundError, match="requires an existing metadata database"):
        script.main()
