"""Exercise the complete October repair batch on isolated persisted metadata."""

import datetime
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.arcadia import offchain_metadata as arcadia
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SSFTUSD_DESCRIPTION, FLYING_TULIP_SSFTUSD_NOTES, FLYING_TULIP_SSFTUSD_SHORT_DESCRIPTION
from eth_defi.erc_4626.vault_protocol.gains.metadata import REVIEWED_GAINS_VAULTS
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsHistoricalReader
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS
from eth_defi.erc_4626.vault_protocol.nova_markets.vault import NovaMarketsVault
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.flag import VaultFlag, get_notes
from eth_defi.vault.handwritten_metadata import ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA, ALPHAPING_IPOR_VAULT_METADATA, ALPHAPING_VAULT_METADATA, NEUTRAL_AUTOPILOT_NOTE, NEUTRAL_AUTOPILOT_VAULT_METADATA, TRAVESSIA_TAURI_VAULT_METADATA
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import flying_tulip, price_backfill

FT = VaultSpec(1, "0x232128844e5af56fd4b3f8bf97e08ab477282ab5")
NOVA = VaultSpec(42161, NOVA_POOL_ADDRESS)
WRAPPED = VaultSpec(999, "0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5")
ARCADIA = VaultSpec(4663, "0xa5e1e1f92a244f192943899eb5810e2bab372ea4")


def detection(spec: VaultSpec, feature: ERC4626Feature) -> ERC4262VaultDetection:
    """Build stable discovery evidence for scanner compatibility assertions.

    :param spec: Deployment identity.
    :param feature: Original detected feature.
    :return: Detection with preserved counters and start boundary.
    """
    return ERC4262VaultDetection(spec.chain_id, spec.vault_address, 42, datetime.datetime(2026, 9, 1), {feature}, datetime.datetime(2026, 10, 1), 27, 1)


@pytest.fixture
def batch_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create reviewed inventories and unrelated records without real provider calls."""
    monkeypatch.delenv("VAULT_DB", raising=False)
    monkeypatch.delenv("VAULT_DB_PATH", raising=False)
    rows = {VaultSpec(chain, address): {"Protocol": "Gains Network", "_detection_data": detection(VaultSpec(chain, address), ERC4626Feature.gains_like)} for chain, address in REVIEWED_GAINS_VAULTS}
    rows[NOVA] = {"Protocol": "Gains Network", "_detection_data": detection(NOVA, ERC4626Feature.gains_like), "_manager_name": "Preserve manager", "NAV": 123}
    rows[FT] = {"Protocol": "ERC-4626", "_detection_data": detection(FT, ERC4626Feature.flying_tulip_like), "manual_enrichment": "keep", "_external_reward_token": "stale"}
    for chain, address in ALPHAPING_VAULT_METADATA:
        rows[VaultSpec(chain, address)] = {"Protocol": "IPOR Fusion" if (chain, address) in ALPHAPING_IPOR_VAULT_METADATA else "Morpho", "_notes": "Keep warning"}
    for chain, address in TRAVESSIA_TAURI_VAULT_METADATA | ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA:
        rows[VaultSpec(chain, address)] = {"Protocol": "Accountable", "_manager_name": "Existing attribution"}
    rows[WRAPPED] = {"Protocol": "Hyperlend", "_flags": {VaultFlag.paused}, "_notes": "old"}
    rows[ARCADIA] = {"Protocol": "Arcadia Finance", "Name": "Senior ArcadiaV2 Global Dollar", "Denomination": "USDG", "_description": "Arcadia USDG Pool"}
    rows[VaultSpec(1, WRAPPED.vault_address)] = {"Protocol": "Different deployment", "_flags": set(), "_notes": "untouched"}
    rows[VaultSpec(1, "0x0000000000000000000000000000000000000001")] = {"_description": "First.\r\n\r\n\r\nSecond.", "_short_description": "First. Second.", "custom": 123}
    VaultDatabase(rows=rows, last_scanned_block={1: 12345, 42161: 67890}).write(tmp_path / "vault-metadata-db.pickle")
    return tmp_path


def test_complete_batch_preview_apply_and_scanner_semantics(batch_database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All numbered repairs compose without touching historical or unrelated state."""

    def rebuild(web3: object, discovery: ERC4262VaultDetection, block: int, cache: object) -> dict:
        return {"Protocol": "Flying Tulip", "Name": "ssftUSD", "Denomination": "ftUSD", "NAV": 1, "_detection_data": discovery, "_description": "Compounded yield.\nSecond sentence."}

    monkeypatch.setattr(flying_tulip, "read_json_rpc_url", lambda _: "unused")
    monkeypatch.setattr(flying_tulip, "create_multi_provider_web3", lambda _: SimpleNamespace(eth=SimpleNamespace(block_number=123)))
    monkeypatch.setattr(flying_tulip, "create_vault_scan_record", rebuild)
    monkeypatch.setattr(arcadia, "fetch_arcadia_vaults", lambda *args, **kwargs: None)
    # Price backfills need archive RPC access; their helper has dedicated tests.
    backfilled = []
    monkeypatch.setattr(price_backfill, "run_price_backfill", lambda _context, targets, **_kwargs: backfilled.extend(targets))
    path = batch_database / "vault-metadata-db.pickle"
    for name in ("vault-prices-1h.parquet", "vault-reader-state-1h.pickle", "timestamps.duckdb"):
        (batch_database / name).write_bytes(b"unrecoverable history")
    original = {p.name: p.read_bytes() for p in batch_database.iterdir()}
    version = {"in_docker": False, "git_commit_hash": "a" * 40}
    expected_migrations = len(tuple(runner.discover_migrations()))
    assert any(spec.name == "010-flying-tulip-strategy-descriptions.py" for spec in runner.discover_migrations())
    assert len(runner.run_pending_migrations(data_dir=batch_database, dry_run=True, provenance=version)) == expected_migrations
    assert {p.name: p.read_bytes() for p in batch_database.iterdir()} == original
    assert len(runner.run_pending_migrations(data_dir=batch_database, dry_run=False, provenance=version)) == expected_migrations
    database = VaultDatabase.read(path)
    assert database.last_scanned_block == {1: 12345, 42161: 67890}
    assert database.rows[NOVA]["Protocol"] == "Nova Markets"
    assert database.rows[NOVA]["_manager_name"] == "Preserve manager"
    adapter = NovaMarketsVault(web3=Web3(), spec=NOVA, features={ERC4626Feature.nova_markets_like})
    assert database.rows[NOVA]["_strategy_tags"] == adapter.get_strategy_tags()
    assert database.rows[NOVA]["_notes"] == adapter.get_notes()
    assert database.rows[NOVA]["_description"] == normalise_vault_description(adapter.description)
    assert database.rows[NOVA]["_short_description"] == normalise_vault_short_description(adapter.short_description)
    assert database.rows[FT]["manual_enrichment"] == "keep"
    assert "_external_reward_token" not in database.rows[FT]
    assert database.rows[FT]["_description"] == FLYING_TULIP_SSFTUSD_DESCRIPTION
    assert database.rows[FT]["_short_description"] == FLYING_TULIP_SSFTUSD_SHORT_DESCRIPTION
    assert database.rows[FT]["_notes"] == FLYING_TULIP_SSFTUSD_NOTES
    for chain_id, address in NEUTRAL_AUTOPILOT_VAULT_METADATA:
        autopilot = database.rows[VaultSpec(chain_id, address)]
        assert autopilot["_description"] == NEUTRAL_AUTOPILOT_VAULT_METADATA[chain_id, address].description
        assert autopilot["_notes"] == NEUTRAL_AUTOPILOT_NOTE
        assert autopilot["_strategy_tags"] == {StrategyTag.multistrategy, StrategyTag.delta_neutral}
        assert autopilot["_manager_name"] == "Existing attribution"
    assert database.rows[WRAPPED]["_flags"] == {VaultFlag.paused, VaultFlag.illiquid}
    assert database.rows[WRAPPED]["_notes"] == get_notes(WRAPPED.vault_address, chain_id=999, protocol_name="Hyperlend")
    assert database.rows[VaultSpec(1, WRAPPED.vault_address)]["_flags"] == set()
    for spec, row in database.rows.items():
        if row.get("_description"):
            assert row["_description"] == normalise_vault_description(row["_description"])
        if (spec.chain_id, spec.vault_address) in ALPHAPING_VAULT_METADATA:
            assert row["_notes"] == "Keep warning"
    assert database.rows[VaultSpec(1, "0x0000000000000000000000000000000000000001")]["_description"] == "First.\n\nSecond."
    assert all((batch_database / name).read_bytes() == payload for name, payload in original.items() if name != path.name)
    applied = path.read_bytes()
    assert {target.as_spec() for target in backfilled} == {VaultSpec(chain_id, address) for chain_id, address in NEUTRAL_AUTOPILOT_VAULT_METADATA}
    assert runner.run_pending_migrations(data_dir=batch_database, dry_run=False, provenance=version) == []
    assert path.read_bytes() == applied
    state = runner.load_state(batch_database / "migration.json")
    assert all(entry["success"] for entry in state.values())


def test_nova_reader_retains_existing_cursor() -> None:
    """Reclassification uses the compatible inherited reader, never a reset cursor."""
    adapter = NovaMarketsVault(web3=Web3(), spec=NOVA, features={ERC4626Feature.nova_markets_like})
    reader = adapter.get_historical_reader(stateful=True)
    assert isinstance(reader, GainsHistoricalReader)
    saved = reader.reader_state.save()
    saved["last_block"] = 518876926
    reader.reader_state.load(saved)
    assert reader.reader_state.last_block == 518876926


@pytest.mark.parametrize("number,target", [(1, FT), (2, NOVA), (7, WRAPPED)])
def test_missing_required_single_target_fails(batch_database: Path, tmp_path: Path, number: int, target: VaultSpec) -> None:
    """Absent reviewed targets remain pending instead of being marked complete."""
    path = batch_database / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    del database.rows[target]
    database.write(path)
    directory = tmp_path / "selected-test-resource"
    directory.mkdir()
    spec = next(s for s in runner.discover_migrations() if s.number == number)
    (directory / spec.name).write_bytes(spec.source)
    with pytest.raises(ValueError, match="missing"):
        runner.run_pending_migrations(data_dir=batch_database, scripts_dir=directory, dry_run=False, provenance={"in_docker": False})
    assert not runner.load_state(batch_database / "migration.json")[spec.name]["success"]


@pytest.mark.skipif(os.environ.get("RUN_MIGRATION_LIVE_TEST") != "1", reason="Set RUN_MIGRATION_LIVE_TEST=1 and supply JSON_RPC_ETHEREUM for isolated real-provider previews")
def test_live_flying_tulip_and_arcadia_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read real Ethereum and Arcadia providers through the numbered preview path."""
    assert os.environ.get("JSON_RPC_ETHEREUM"), "Supply JSON_RPC_ETHEREUM through .local-test.env"
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    VaultDatabase(rows={FT: {"Protocol": "ERC-4626", "_detection_data": detection(FT, ERC4626Feature.flying_tulip_like)}, ARCADIA: {"Protocol": "Arcadia Finance", "Name": "Senior ArcadiaV2 Global Dollar", "Denomination": "USDG"}}).write(tmp_path / "vault-metadata-db.pickle")
    directory = tmp_path / "resources"
    directory.mkdir()
    for spec in runner.discover_migrations():
        if spec.number in {1, 6}:
            (directory / spec.name).write_bytes(spec.source)
    original = (tmp_path / "vault-metadata-db.pickle").read_bytes()
    # Assert a successful real API response independently of the repair's fallback.
    with TemporaryDirectory() as cache:
        metadata = arcadia.fetch_arcadia_vaults(4663, cache_path=Path(cache))
        assert metadata and ARCADIA.vault_address in metadata
    assert len(runner.run_pending_migrations(data_dir=tmp_path, scripts_dir=directory, dry_run=True)) == 2
    assert (tmp_path / "vault-metadata-db.pickle").read_bytes() == original
    assert not (tmp_path / "migration.json").exists()


@pytest.mark.parametrize("number", [3, 4, 5])
def test_reviewed_inventory_missing_rows_are_reported(number: int, batch_database: Path) -> None:
    """Partial historical inventories are allowed without inserting absent rows."""
    spec = next(s for s in runner.discover_migrations() if s.number == number)
    context = runner.MigrationContext(batch_database, True, script_name=spec.name)
    database = context.metadata()
    # Keep one reviewed row, exercising the documented partial-inventory policy.
    protocols = {3: {"Gains Network"}, 4: {"Morpho", "IPOR Fusion"}, 5: {"Accountable"}}
    target = next(key for key, row in database.rows.items() if row["Protocol"] in protocols[number] and key != NOVA)
    database.rows = {target: database.rows[target]}
    runner._execute(spec, context)
    assert context.other_data["missing_count"] > 0
    assert set(database.rows) == {target}


@pytest.mark.parametrize("number", [3, 4, 5])
def test_absent_reviewed_inventory_fails(number: int, tmp_path: Path) -> None:
    """An entirely absent required inventory must never be recorded as applied."""
    VaultDatabase(rows={}).write(tmp_path / "vault-metadata-db.pickle")
    spec = next(s for s in runner.discover_migrations() if s.number == number)
    with pytest.raises(ValueError):
        runner._execute(spec, runner.MigrationContext(tmp_path, True, script_name=spec.name))


def test_empty_arcadia_inventory_is_noop(tmp_path: Path) -> None:
    """Arcadia's row-based scope explicitly permits an empty population."""
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={}).write(path)
    before = path.read_bytes()
    spec = next(s for s in runner.discover_migrations() if s.number == 6)
    context = runner.MigrationContext(tmp_path, True, script_name=spec.name)
    runner._execute(spec, context)
    assert context.other_data["changed_rows"] == 0
    assert context.other_data["chains"] == []
    assert path.read_bytes() == before
