"""Exercise the complete October repair batch on isolated persisted metadata."""

import datetime
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.arcadia import offchain_metadata as arcadia
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SSFTUSD_DESCRIPTION, FLYING_TULIP_SSFTUSD_NOTES, FLYING_TULIP_SSFTUSD_SHORT_DESCRIPTION
from eth_defi.erc_4626.vault_protocol.gains.metadata import REVIEWED_GAINS_VAULTS
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsHistoricalReader
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS
from eth_defi.erc_4626.vault_protocol.nova_markets.vault import NovaMarketsVault
from eth_defi.erc_4626.vault_protocol.veranta.constants import VERANTA_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.veranta.vault import VerantaVault
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.flag import VaultFlag, get_notes
from eth_defi.vault.handwritten_metadata import ACCOUNTABLE_HANDWRITTEN_VAULT_METADATA, ALPHAPING_IPOR_VAULT_METADATA, ALPHAPING_VAULT_METADATA, NEUTRAL_AUTOPILOT_NOTE, NEUTRAL_AUTOPILOT_VAULT_METADATA, TRAVESSIA_TAURI_VAULT_METADATA
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import flying_tulip, price_backfill, veranta

FT = VaultSpec(1, "0x232128844e5af56fd4b3f8bf97e08ab477282ab5")
NOVA = VaultSpec(42161, NOVA_POOL_ADDRESS)
WRAPPED = VaultSpec(999, "0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5")
ARCADIA = VaultSpec(4663, "0xa5e1e1f92a244f192943899eb5810e2bab372ea4")
#: Gearbox deployment required by migration 025.
EDGE_ULTRAYIELD = VaultSpec(143, "0x6b343f7b797f1488aa48c49d540690f2b2c89751")
#: 3Jane tranche deployments reviewed by migration 029.
THREEJANE_TRANCHES = (VaultSpec(1, "0x056b269eb1f75477a8666ae8c7fe01b64dd55ecc"), VaultSpec(1, "0xf689555121e529ff0463e191f9bd9d1e496164a7"))


#: Peapods deployments required by migration 036.
PEAPODS_USDC = tuple(VaultSpec(8453, address) for address in ("0x2e5109aa82a4ebcc776e89abeb68f5d0b91ccea1", "0x53ce611b5477997972dad83585a0ad5cb56f433b"))


#: Euler deployment required by migration 040.
K3_CLUSTER = VaultSpec(43114, "0x6fc9b3a52944a577cd8971fd8fde0819001bc595")


def detection(spec: VaultSpec, feature: ERC4626Feature) -> ERC4262VaultDetection:
    """Build stable discovery evidence for scanner compatibility assertions.

    :param spec: Deployment identity.
    :param feature: Original detected feature.
    :return: Detection with preserved counters and start boundary.
    """
    return ERC4262VaultDetection(spec.chain_id, spec.vault_address, 42, datetime.datetime(2026, 9, 1), {feature}, datetime.datetime(2026, 10, 1), 27, 1)


@pytest.mark.parametrize("dry_run", [True, False])
def test_systemic_strategy_tag_refresh(tmp_path: Path, dry_run: bool) -> None:
    """Refresh only the reviewed native row and preserve preview isolation.

    Exercise persistence and idempotence without RPC access, including a
    same-address EVM row outside the synthetic Hypercore chain.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether the repair must remain in memory.
    :return: ``None`` after checking the staged and persisted rows.
    """
    target = VaultSpec(9999, "0x07fd993f0fa3a185f7207adccd29f7a87404689d")
    other_chain = VaultSpec(999, target.vault_address)
    old_tags = {StrategyTag.directional_trading, StrategyTag.grid_trading, StrategyTag.perpetual_futures}
    original_row = {"Protocol": "Hyperliquid", "_strategy_tags": old_tags, "_notes": "Preserve notes", "_detection_data": detection(target, ERC4626Feature.hypercore_native)}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={target: original_row, other_chain: {"_strategy_tags": old_tags}}).write(path)
    original_bytes = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == 41)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    expected_row = original_row | {"_strategy_tags": old_tags | {StrategyTag.algorithmic_trading, StrategyTag.high_frequency_high_win_rate}}
    assert context.metadata().rows[target] == expected_row
    assert context.metadata().rows[other_chain] == {"_strategy_tags": old_tags}
    assert context.other_data["changed_rows"] == 1
    if dry_run:
        assert path.read_bytes() == original_bytes
    else:
        assert VaultDatabase.read(path).rows[target] == expected_row
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0


@pytest.mark.parametrize("missing", [True, False])
def test_systemic_strategy_tag_refresh_guards(tmp_path: Path, missing: bool) -> None:
    """Report an absent target and reject conflicting detection without writes.

    The migration must not invent a missing vault or reclassify an EVM row.

    :param tmp_path: Isolated pipeline directory.
    :param missing: Whether the target is absent rather than misclassified.
    :return: ``None`` after checking the report or validation error.
    """
    target = VaultSpec(9999, "0x07fd993f0fa3a185f7207adccd29f7a87404689d")
    rows = {} if missing else {target: {"_detection_data": detection(target, ERC4626Feature.threejane_like)}}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    original = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == 41)
    context = runner.MigrationContext(tmp_path, True, script_name=migration.name)
    if missing:
        runner._execute(migration, context)
        assert context.other_data["missing_specs"] == [target.as_string_id()]
        assert context.other_data["changed_rows"] == 0
    else:
        with pytest.raises(ValueError, match="not a native Hyperliquid vault"):
            runner._execute(migration, context)
    assert path.read_bytes() == original


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
        rows[VaultSpec(chain, address)] = {"Protocol": "Accountable", "_manager_name": "Existing attribution", "_detection_data": detection(VaultSpec(chain, address), ERC4626Feature.accountable_like)}
    rows[WRAPPED] = {"Protocol": "Hyperlend", "_flags": {VaultFlag.paused}, "_notes": "old"}
    for spec in PEAPODS_USDC:
        rows[spec] = {"Protocol": "Peapods", "_flags": {VaultFlag.paused}, "_notes": "old"}
    rows[K3_CLUSTER] = {"Protocol": "Euler", "_flags": {VaultFlag.paused}, "_notes": "old"}
    rows[EDGE_ULTRAYIELD] = {"Protocol": "Gearbox", "_flags": {VaultFlag.paused}, "_notes": "old"}
    for tranche in THREEJANE_TRANCHES:
        rows[tranche] = {"Protocol": "3Jane", "_detection_data": detection(tranche, ERC4626Feature.threejane_like), "_strategy_tags": None}
    rows[VaultSpec(1, "0xa6f6887ed116f3c41497a837a0319264a2eef3fa")] = {"Protocol": "Silo Finance"}
    rows[VaultSpec(1, "0xff5b00730ba463994fe0dbf574c538da258bc2ca")] = {"Protocol": "Euler"}
    rows[VaultSpec(146, "0x17271da949bbd4713c7c599759e2bf30604fc8da")] = {"Protocol": "Euler"}
    for chain_id, address in (
        (8453, "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5"),
        (10, "0x08dcdbf7bade91ccd42cb2a4ea8e5d199d285957"),
        (43114, "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7"),
        (43114, "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd"),
    ):
        rows[VaultSpec(chain_id, address)] = {"Protocol": "40acres"}
    # Migration 024 requires these reviewed deployments; 022 handles 40acres.
    rows[VaultSpec(42161, "0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a")] = {"Protocol": "Plutus"}
    rows[VaultSpec(1868, "0x2ba3fc5bdbbe7be2630fb71e759739625246bcd9")] = {"Protocol": "Asseto"}
    rows[VaultSpec(1, "0xb250c9e0f7be4cff13f94374c993ac445a1385fe")] = {"Protocol": "ETH Strategy"}
    rows[VaultSpec(42161, "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004")] = {"Protocol": "D2 Finance"}
    veranta_spec = VaultSpec(8453, VERANTA_VAULT_ADDRESS)
    rows[veranta_spec] = {"Protocol": "<protocol not yet identified>", "_detection_data": detection(veranta_spec, ERC4626Feature.gains_tranche_like)}
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
    # Veranta live reads are separately fork-tested; the batch checks composition.
    provider = Mock()
    provider.eth.chain_id = 8453
    provider.eth.block_number = 123
    provider.eth.get_storage_at.return_value = bytes.fromhex("00" * 12 + "e79184763b27fa8bb0eb2d6af1f0123d43af0e89")
    contract = Mock()
    contract.functions.asset.return_value.call.return_value = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    contract.functions.feesOn.return_value.call.return_value = False
    monkeypatch.setattr(veranta, "read_json_rpc_url", lambda _: "unused")
    monkeypatch.setattr(veranta, "create_multi_provider_web3", lambda _: provider)
    monkeypatch.setattr(VerantaVault, "vault_contract", property(lambda _: contract))
    path = batch_database / "vault-metadata-db.pickle"
    # Migration 018 validates the price file even when no reviewed vault has rows.
    # Keep real unrelated Parquet data so the complete batch exercises that path.
    pq.write_table(pa.table({"chain": [1], "address": ["0x0000000000000000000000000000000000000001"], "share_price": [1.25], "available_liquidity": [123.0]}), batch_database / "vault-prices-1h.parquet")
    for name in ("vault-reader-state-1h.pickle", "timestamps.duckdb"):
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
        assert autopilot["_strategy_tags"] == {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.market_making, StrategyTag.perpetual_futures, StrategyTag.options, StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following}
        assert autopilot["_manager_name"] == "Existing attribution"
    assert database.rows[WRAPPED]["_flags"] == {VaultFlag.paused, VaultFlag.illiquid}
    assert database.rows[WRAPPED]["_notes"] == get_notes(WRAPPED.vault_address, chain_id=999, protocol_name="Hyperlend")
    assert database.rows[EDGE_ULTRAYIELD]["_flags"] == {VaultFlag.paused, VaultFlag.illiquid}
    assert database.rows[EDGE_ULTRAYIELD]["_notes"] == "EDGE UltraYield USDC is illiquid."
    assert database.rows[K3_CLUSTER]["_flags"] == {VaultFlag.paused, VaultFlag.illiquid}
    assert database.rows[K3_CLUSTER]["_notes"] == "This vault is illiquid."
    for spec in PEAPODS_USDC:
        assert database.rows[spec]["_flags"] == {VaultFlag.paused, VaultFlag.illiquid}
        assert database.rows[spec]["_notes"] == "Peapods vault is illiquid"
    for tranche in THREEJANE_TRANCHES:
        assert database.rows[tranche]["_strategy_tags"] == {StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending, StrategyTag.inventory_financing}
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


@pytest.mark.parametrize("number,target", [(1, FT), (2, NOVA), (7, WRAPPED), (40, K3_CLUSTER), *((36, spec) for spec in PEAPODS_USDC)])
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


@pytest.mark.parametrize("dry_run", [True, False])
def test_hibachi_grvt_strategy_tag_refresh(tmp_path: Path, dry_run: bool) -> None:
    """Refresh only the reviewed native row and preserve preview isolation.

    Exercise persistence and idempotence without RPC access, including a
    same-address EVM row outside the synthetic Hibachi chain.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether the repair must remain in memory.
    :return: ``None`` after checking the staged and persisted rows.
    """
    target = VaultSpec(9997, "hibachi-vault-4")
    other_chain = VaultSpec(999, target.vault_address)
    old_tags = {StrategyTag.perpetual_futures}
    original_row = {"Protocol": "Hibachi", "_strategy_tags": old_tags, "_notes": "Preserve notes", "_detection_data": detection(target, ERC4626Feature.hibachi_native)}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={target: original_row, other_chain: {"_strategy_tags": old_tags}}).write(path)
    credit = VaultSpec(325, "vlt:3eexdzmz72tqcinpbu4f4i0dm2z")
    db = VaultDatabase.read(path)
    db.rows[credit] = {"_strategy_tags": {StrategyTag.perpetual_futures, StrategyTag.arbitrage}, "_detection_data": detection(credit, ERC4626Feature.grvt_native)}
    db.write(path)
    original_bytes = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == 48)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    expected_row = original_row | {"_strategy_tags": old_tags | {StrategyTag.market_making, StrategyTag.market_making_clob, StrategyTag.liquidity_provider}}
    assert context.metadata().rows[target] == expected_row
    assert context.metadata().rows[other_chain] == {"_strategy_tags": old_tags}
    assert context.other_data["changed_rows"] == 2
    assert len(context.other_data["missing_specs"]) == 31
    assert context.metadata().rows[credit]["_strategy_tags"] == {StrategyTag.discretionary_trading, StrategyTag.rwa, StrategyTag.rwa_credit}
    if dry_run:
        assert path.read_bytes() == original_bytes
    else:
        assert VaultDatabase.read(path).rows[target] == expected_row
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0


@pytest.mark.parametrize("missing", [True, False])
def test_hibachi_grvt_strategy_tag_refresh_guards(tmp_path: Path, missing: bool) -> None:
    """Report an absent target and reject conflicting detection without writes.

    The migration must not invent a missing vault or reclassify an EVM row.

    :param tmp_path: Isolated pipeline directory.
    :param missing: Whether the target is absent rather than misclassified.
    :return: ``None`` after checking the report or validation error.
    """
    target = VaultSpec(9997, "hibachi-vault-4")
    rows = {} if missing else {target: {"_detection_data": detection(target, ERC4626Feature.threejane_like)}}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    original = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == 48)
    context = runner.MigrationContext(tmp_path, True, script_name=migration.name)
    if missing:
        runner._execute(migration, context)
        assert len(context.other_data["missing_specs"]) == 33
        assert context.other_data["changed_rows"] == 0
    else:
        with pytest.raises(ValueError, match="not a Hibachi/GRVT vault"):
            runner._execute(migration, context)
    assert path.read_bytes() == original
