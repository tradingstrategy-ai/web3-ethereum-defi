"""Verify the Tori metadata migration against isolated persisted files."""

import copy
import dataclasses
import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.tori.constants import TORI_ASSET_ADDRESS, TORI_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.tori.vault import ToriVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import tori

TARGET = VaultSpec(1, TORI_VAULT_ADDRESS)
OTHER_CHAIN = VaultSpec(143, TORI_VAULT_ADDRESS)
ECOSYSTEM = VaultSpec(1, "0xcd69123b3fbbfc666e1f6a501da27b564c00de54")
SCRIPT_NAME = "012-tori-staking-metadata.py"


@pytest.fixture
def tori_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Mock]:
    """Isolate the numbered script, stale metadata and provider.

    Historical files contain sentinel bytes so any accidental repair outside
    the catalogue database is visible.

    :param tmp_path: Temporary test directory.
    :param monkeypatch: Configuration and provider replacement support.
    :return: Data directory, migration directory and mocked Ethereum provider.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    first_seen = datetime.datetime(2026, 6, 22, 12, 35, 47, tzinfo=datetime.UTC).replace(tzinfo=None)
    updated_at = datetime.datetime(2026, 10, 28, tzinfo=datetime.UTC).replace(tzinfo=None)
    detection = ERC4262VaultDetection(1, TORI_VAULT_ADDRESS, 25373179, first_seen, set(), updated_at, 1516, 178)
    rows = {
        TARGET: {
            "Protocol": "ERC-4626",
            "Address": TORI_VAULT_ADDRESS,
            "Name": "Staked trUSD",
            "Symbol": "strUSD",
            "Denomination": "trUSD",
            "NAV": 123,
            "Shares": 100,
            "First seen": detection.first_seen_at,
            "_detection_data": detection,
            "_denomination_token": {"address": TORI_ASSET_ADDRESS},
            "_notes": "Preserve independent note",
            "manual": "Preserve enrichment",
            "_deposit_manager": {"can_deposit": True, "can_redeem": True},
            "_curator_slug": "stale",
        },
        OTHER_CHAIN: {"Protocol": "Unrelated token", "manual": 7},
        ECOSYSTEM: {"Protocol": "Upshift", "_curator_slug": "rockawayx"},
    }
    VaultDatabase(rows=rows, last_scanned_block={1: 26290000}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve history")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    web3 = Mock()
    web3.eth.chain_id = 1
    web3.eth.block_number = 26290000
    web3.eth.call.return_value = (604800).to_bytes(32, "big")
    monkeypatch.setattr(tori, "read_json_rpc_url", lambda _: "unused")
    monkeypatch.setattr(tori, "create_multi_provider_web3", lambda _: web3)
    return data_dir, scripts_dir, web3


def test_tori_migration_preview_apply_and_retry(tori_migration: tuple[Path, Path, Mock]) -> None:
    """Preview safely, persist the exact refresh and skip a successful retry.

    The runner must retain byte-identical historical files and a backup of
    the original catalogue. The transformation itself must also be idempotent.

    :param tori_migration: Isolated migration resources and provider.
    :return: ``None`` after checking preview, apply, preservation and retries.
    """
    data_dir, scripts_dir, web3 = tori_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    old_database = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert runner.run_pending_migrations(dry_run=True, **options) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original
    assert runner.run_pending_migrations(dry_run=False, **options) == [SCRIPT_NAME]
    database = VaultDatabase.read(path)
    row = database.rows[TARGET]
    vault = ToriVault(web3, TARGET, features={ERC4626Feature.tori_like}, default_block_identifier=26290000)
    assert row["Protocol"] == "Tori Finance"
    assert row["protocol_slug"] == "tori"
    assert row["features"] == {ERC4626Feature.tori_like}
    assert row["_description"] == vault.description
    assert row["_short_description"] == vault.short_description
    assert row["_strategy_tags"] == vault.get_strategy_tags()
    assert isinstance(row["_strategy_tags"], set)
    assert row["_fees"] == vault.get_fee_data()
    assert row["_withdrawal_period"] == vault.get_withdrawal_period()
    assert row["_lockup"] == datetime.timedelta(days=7)
    assert row["_deposit_manager"] is None
    assert row["_curator_slug"] is None
    old_detection = old_database.rows[TARGET]["_detection_data"]
    detection = row["_detection_data"]
    assert detection.first_seen_at_block == old_detection.first_seen_at_block
    assert detection.first_seen_at == old_detection.first_seen_at
    assert detection.updated_at == old_detection.updated_at
    assert (detection.deposit_count, detection.redeem_count) == (1516, 178)
    for key in ("Name", "NAV", "Shares", "First seen", "manual", "_notes", "_denomination_token"):
        assert row[key] == old_database.rows[TARGET][key]
    assert database.last_scanned_block == old_database.last_scanned_block
    assert database.rows[OTHER_CHAIN] == old_database.rows[OTHER_CHAIN]
    assert database.rows[ECOSYSTEM] == old_database.rows[ECOSYSTEM]
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["changed_rows"] == 1
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    before = copy.deepcopy(database.rows)
    assert tori.refresh_tori_metadata(database, web3)["changed_rows"] == 0
    assert database.rows == before
    applied = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == applied


@pytest.mark.parametrize("field", ["address", "detection", "protocol", "symbol", "asset", "provider"])
def test_tori_migration_validates_before_staging(tori_migration: tuple[Path, Path, Mock], field: str) -> None:
    """Reject conflicting identities and provider failures without partial updates.

    Cached conflicts are validated before the live cooldown read. A failed
    provider read must leave every in-memory row untouched.

    :param tori_migration: Isolated migration resources and provider.
    :param field: Identity field to corrupt, or provider failure to simulate.
    :return: ``None`` after checking atomic failure behaviour.
    """
    data_dir, _, web3 = tori_migration
    database = VaultDatabase.read(data_dir / "vault-metadata-db.pickle")
    row = database.rows[TARGET]
    if field == "address":
        row["Address"] = ECOSYSTEM.vault_address
    elif field == "detection":
        row["_detection_data"] = None
    elif field == "protocol":
        row["Protocol"] = "Upshift"
    elif field == "symbol":
        row["Symbol"] = "etrUSD"
    elif field == "asset":
        row["_denomination_token"] = {"address": ECOSYSTEM.vault_address}
    else:
        web3.eth.call.side_effect = ConnectionError("Provider unavailable")
    before = copy.deepcopy(database)
    with pytest.raises(ConnectionError if field == "provider" else ValueError):
        tori.refresh_tori_metadata(database, web3)
    assert database == before
    if field != "provider":
        web3.eth.call.assert_not_called()


def test_tori_migration_reports_missing(tori_migration: tuple[Path, Path, Mock]) -> None:
    """Do not manufacture a row or make RPC reads when the target is absent.

    Deployments outside this catalogue can be discovered through the normal
    scanner without changing existing history.

    :param tori_migration: Isolated migration resources and provider.
    :return: ``None`` after verifying missing-row reporting.
    """
    data_dir, _, web3 = tori_migration
    database = VaultDatabase.read(data_dir / "vault-metadata-db.pickle")
    del database.rows[TARGET]
    before = copy.deepcopy(database.rows)
    result = tori.refresh_tori_metadata(database, web3)
    assert result["missing"] == [TARGET.as_string_id()]
    assert result["inspected_rows"] == result["changed_rows"] == 0
    assert database.rows == before
    web3.eth.call.assert_not_called()


@pytest.mark.parametrize("legacy_feature", [ERC4626Feature.erc_7540_like, ERC4626Feature.share_price_equivalence])
@pytest.mark.parametrize("legacy_tags", [["arbitrage"], {"arbitrage"}])
def test_tori_migration_clears_legacy_features_and_repairs_tags(tori_migration: tuple[Path, Path, Mock], legacy_feature: ERC4626Feature, legacy_tags: list[str] | set[str]) -> None:
    """Replace old probe flags and canonicalise legacy saved tag values.

    Generic non-empty feature sets can carry either fallback protocol label.
    Neither stale interface flags nor old string-tag formats may prevent the
    reviewed native deployment receiving its canonical classification.

    :param tori_migration: Isolated migration resources and provider.
    :param legacy_feature: Stale generic interface or helper feature.
    :param legacy_tags: Legacy list or string-set tag representation.
    :return: ``None`` after verifying canonical fields and idempotence.
    """
    data_dir, _, web3 = tori_migration
    database = VaultDatabase.read(data_dir / "vault-metadata-db.pickle")
    row = database.rows[TARGET]
    row["_detection_data"] = dataclasses.replace(row["_detection_data"], features={legacy_feature})
    row["features"] = {legacy_feature}
    row["Protocol"] = get_vault_protocol_name({legacy_feature})
    row["_strategy_tags"] = legacy_tags
    assert tori.refresh_tori_metadata(database, web3)["changed_rows"] == 1
    assert row["features"] == row["_detection_data"].features == {ERC4626Feature.tori_like}
    assert row["Features"] == "tori_like"
    assert row["_strategy_tags"] == ToriVault(web3, TARGET, features={ERC4626Feature.tori_like}).get_strategy_tags()
    assert isinstance(row["_strategy_tags"], set)
    assert tori.refresh_tori_metadata(database, web3)["changed_rows"] == 0
