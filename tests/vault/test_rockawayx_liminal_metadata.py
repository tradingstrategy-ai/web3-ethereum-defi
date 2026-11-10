"""Check the lending vault's maintained copy and isolated metadata refresh."""

import copy
import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.handwritten_metadata import ROCKAWAYX_LIMINAL_VAULT_METADATA, get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Exact reviewed deployment and same-address wrong-chain control.
TARGET = VaultSpec(999, "0x83107e84f9c5eaaf31f6d2e2b06e0aa14121370e")
OTHER = VaultSpec(1, TARGET.vault_address)
SCRIPT_NAME = "058-rockawayx-liminal-metadata.py"


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Prepare an isolated migration with stale copy and incorrect trading tags.

    Include a same-address row on Ethereum to detect accidental scope expansion
    and historical bytes to verify that a metadata repair preserves prices.

    :param tmp_path: Temporary storage for the runner and database.
    :param monkeypatch: Remove external database overrides.
    :return: Arguments for the real numbered migration runner.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    timestamp = datetime.datetime(2026, 10, 14)  # noqa: DTZ001 - Repository uses naive UTC.
    detection = ERC4262VaultDetection(chain=999, address=TARGET.vault_address, first_seen_at_block=1, first_seen_at=timestamp, updated_at=timestamp, features={ERC4626Feature.morpho_v2_like}, deposit_count=10, redeem_count=10)
    database = VaultDatabase(
        rows={
            TARGET: {"Address": TARGET.vault_address, "Symbol": "roxLiminalUSDC", "Share token": "RockawayX Liminal USDC", "Protocol": "Morpho", "Name": "RockawayX Liminal USDC", "Denomination": "USDC", "_detection_data": detection, "_description": "Old copy", "_short_description": None, "_strategy_tags": {StrategyTag.delta_neutral}, "_notes": "Keep warning", "Link": "https://app.morpho.org", "_fees": FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=0.0, performance=0.1, deposit=0.0, withdraw=0.0)},
            OTHER: {"Protocol": "Unrelated", "_description": "Keep copy"},
        },
        last_scanned_block={999: 123},
    )
    database.write(data_dir / "vault-metadata-db.pickle")
    (data_dir / "vault-prices-1h.parquet").write_bytes(b"Preserve historical prices")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}


def test_liminal_adapter_copy_and_lending_tags() -> None:
    """Resolve reviewed copy through the adapter without network reads.

    The collateral's trading remit must not become this lender's strategy.
    Chain and address scoping must survive address case changes.

    :return: None after checking descriptions, classification and isolation.
    """
    vault = MorphoV2Vault(None, TARGET, features={ERC4626Feature.morpho_v2_like})
    metadata = ROCKAWAYX_LIMINAL_VAULT_METADATA[TARGET.chain_id, TARGET.vault_address]
    assert vault.description == metadata.description
    assert vault.short_description == metadata.short_description
    assert vault.get_strategy_tags() == {StrategyTag.lending}
    assert get_handwritten_vault_metadata(999, TARGET.vault_address.upper()) == metadata
    assert get_handwritten_vault_metadata(1, TARGET.vault_address) is None
    assert get_handwritten_vault_metadata(999, "0x0000000000000000000000000000000000000001") is None


def test_liminal_migration_preserves_state_and_exports_copy(pipeline: dict) -> None:
    """Preview safely, apply three fields and pass them through public metrics.

    Exercise the real runner, idempotency and field preservation. The price
    DataFrame supplies daily share prices and asset/supply totals as floats,
    integer chain/block/event fields and string vault identifiers.

    :param pipeline: Isolated runner configuration and synthetic metadata.
    :return: None after persistence and public export assertions.
    """
    data_dir = pipeline["data_dir"]
    path = data_dir / "vault-metadata-db.pickle"
    original = VaultDatabase.read(path)
    rows = copy.deepcopy(original.rows)
    before = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **pipeline) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT_NAME]
    result = VaultDatabase.read(path)
    metadata = ROCKAWAYX_LIMINAL_VAULT_METADATA[TARGET.chain_id, TARGET.vault_address]
    expected = rows[TARGET] | {"_description": metadata.description, "_short_description": metadata.short_description, "_strategy_tags": {StrategyTag.lending}}
    assert result.rows == rows | {TARGET: expected}
    assert result.last_scanned_block == original.last_scanned_block
    assert (data_dir / "vault-prices-1h.parquet").read_bytes() == before["vault-prices-1h.parquet"]
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == []
    (data_dir / "migration.json").unlink()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT_NAME]
    record = json.loads((data_dir / "migration.json").read_text())[SCRIPT_NAME]
    assert record["other_data"]["updated_fields"] == 0
    prices = pd.DataFrame({"id": TARGET.as_string_id(), "address": TARGET.vault_address, "chain": 999, "block_number": range(35), "share_price": [1 + i * 0.0001 for i in range(35)], "total_assets": 100_000.0, "total_supply": 100_000.0, "event_count": 20}, index=pd.date_range("2026-10-01", periods=35, freq="D"))
    metrics = calculate_lifetime_metrics(calculate_hourly_returns_for_all_vaults(prices), {TARGET: result.rows[TARGET]})
    exported = json.loads(json.dumps(export_lifetime_row(metrics.iloc[0]), allow_nan=False))
    assert exported["description"] == metadata.description
    assert exported["short_description"] == metadata.short_description
    assert exported["strategy_tags"] == ["lending"]


def test_liminal_migration_reports_missing_target(pipeline: dict) -> None:
    """Report a missing target without creating a row or rewriting the database.

    :param pipeline: Isolated runner directories.
    :return: None after verifying the report and unchanged database bytes.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    del database.rows[TARGET]
    database.write(path)
    before = path.read_bytes()
    runner.run_pending_migrations(dry_run=False, **pipeline)
    assert path.read_bytes() == before
    record = json.loads((pipeline["data_dir"] / "migration.json").read_text())[SCRIPT_NAME]
    assert record["other_data"]["missing_specs"] == [TARGET.as_string_id()]


@pytest.mark.parametrize("protocol", [None, "Unrelated"])
def test_liminal_migration_rejects_conflicting_protocol(pipeline: dict, protocol: str | None) -> None:
    """Refuse conflicting records without writing a partial repair.

    :param pipeline: Isolated runner directories.
    :param protocol: Missing or unexpected persisted protocol label.
    :return: None after checking the failure preserves database bytes.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.rows[TARGET]["Protocol"] = protocol
    database.write(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="not classified as Morpho V2"):
        runner.run_pending_migrations(dry_run=False, **pipeline)
    assert path.read_bytes() == before
