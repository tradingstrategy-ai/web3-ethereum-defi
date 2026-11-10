"""Keep researched curator-vault copy through adapters, scans and migrations."""

import copy
import datetime
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from eth_defi.erc_4626 import scan
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import LagoonMetadataSnapshot
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.handwritten_metadata import LAGOON_CURATOR_VAULT_METADATA
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: The migration must retain its original fixed inventory on later catalogue edits.
SCRIPT = "061-lagoon-curator-descriptions.py"
#: Representative Lagoon and Morpho deployments for the full scanner/export path.
SPECS = [VaultSpec(1, "0x7d2c2f54792ad72cb834d298f542145b06b703cb"), VaultSpec(1, "0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac")]
#: Morpho products have direct lending descriptions, separate from the Lagoon allocator.
MORPHO_ADDRESSES = {"0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac", "0x29ca54dd3ae9e43abb10880f5e08da439bf25f99", "0xcda5dbf58a016158ecd4a226109752cc3a627ec8", "0x9f209b43559b13b783eaa0a6686921c7b174f269", "0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10"}

#: Reviewed deployments without substantive individual strategy evidence.
UNDESCRIBED_SPECS = [
    VaultSpec(1, "0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a"),
    VaultSpec(1, "0x47491305479635357df29fe0cb6f1af10e51d4da"),
    VaultSpec(1, "0x66dcb62da5430d800a1c807822c25be17138fda8"),
    VaultSpec(1, "0x71652d4898de9a7dd35e472a5fe4577ec69d82f2"),
    VaultSpec(1, "0x7d798ce99ee2adc183a865d76b112810d5395ea2"),
    VaultSpec(1, "0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb"),
    VaultSpec(999, "0x7dd73a986f6188da1baaf68541fd6ebd455abd1d"),
    VaultSpec(8453, "0xfce2064b4221c54651b21c868064a23695e78f09"),
    VaultSpec(42161, "0x99cd0b8b32b15922f0754fddc21323b5278c5261"),
]


@pytest.mark.parametrize("key", LAGOON_CURATOR_VAULT_METADATA)
def test_reviewed_copy_precedes_provider(key: tuple[int, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every exact override available without reading the provider.

    A failing provider property detects accidental ordering regressions. Both
    Morpho versions must retain the same maintained copy.

    :param key: Reviewed chain and address.
    :param monkeypatch: Prevent provider text reads for overridden Lagoon rows.
    :return: None after testing accessor precedence and output formatting.
    """
    metadata = LAGOON_CURATOR_VAULT_METADATA[key]
    classes = (MorphoV1Vault, MorphoV2Vault) if key[1] in MORPHO_ADDRESSES else (LagoonVault,)
    monkeypatch.setattr(LagoonVault, "lagoon_metadata", property(Mock(side_effect=AssertionError("Override should not fetch provider text"))))
    for cls in classes:
        vault = cls.__new__(cls)
        vault.spec = VaultSpec(*key)
        assert vault.description == metadata.description
        assert vault.short_description == metadata.short_description
    assert metadata.description.startswith("#### Vault's trading strategy\n\n")
    assert normalise_vault_description(metadata.description) == metadata.description
    assert normalise_vault_short_description(metadata.short_description) == metadata.short_description
    assert not metadata.link or metadata.link.startswith("https://tradingstrategy.ai/vaults/")


def test_wrong_chain_retains_provider_copy() -> None:
    """Do not apply an override to an unrelated same-address deployment.

    The normal Lagoon cache still supplies both fields when no exact match exists.

    :return: None after verifying the independent provider descriptions.
    """
    vault = LagoonVault.__new__(LagoonVault)
    vault.spec = VaultSpec(8453, SPECS[0].vault_address)
    vault.__dict__["_lagoon_scan_snapshot"] = LagoonMetadataSnapshot({"description": "Provider strategy.", "short_description": "Provider summary. Extra sentence."}, True, "success", None)
    assert vault.description == "Provider strategy."
    assert vault.short_description == "Provider summary."


@pytest.mark.parametrize("spec", [*SPECS, UNDESCRIBED_SPECS[0]])
def test_copy_reaches_scanner_and_public_json(spec: VaultSpec, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass real adapter text through the scanner and individual vault JSON.

    Only unrelated RPC-backed properties are stubbed. Daily synthetic prices
    supply the lifetime exporter with its required metrics and timestamps.

    :param spec: Representative Lagoon or Morpho deployment.
    :param monkeypatch: Replace RPC reads while retaining text accessors.
    :return: None after confirming both fields survive the publication path.
    """
    morpho = spec.vault_address in MORPHO_ADDRESSES
    cls = MorphoV2Vault if morpho else LagoonVault
    feature = ERC4626Feature.morpho_v2_like if morpho else ERC4626Feature.lagoon_like
    vault = cls.__new__(cls)
    vault.spec = spec
    vault.__dict__["share_token"] = SimpleNamespace(name="Original share name", symbol="SHARE", export=Mock(return_value={"symbol": "SHARE"}))
    vault.__dict__["denomination_token"] = SimpleNamespace(symbol="USDC", export=Mock(return_value={"symbol": "USDC"}))
    monkeypatch.setattr(cls, "manager_name", property(lambda _self: "Previous manager"))
    if morpho:
        monkeypatch.setattr(cls, "morpho_offchain_data", property(lambda _self: None))
    else:
        monkeypatch.setattr(vault, "fetch_metadata_snapshot_for_scan", Mock())
        vault.__dict__["_lagoon_scan_snapshot"] = LagoonMetadataSnapshot(None, False, "success", None)
    monkeypatch.setattr(scan, "create_vault_instance", Mock(return_value=vault))
    monkeypatch.setattr(scan, "_fetch_activity_status", Mock(return_value={}))
    monkeypatch.setattr(scan, "_fetch_lending_stats", Mock(return_value={}))
    methods = {
        "get_fee_data": FeeData(fee_mode=VaultFeeMode.internalised_skimming, management=0.0, performance=0.1, deposit=0.0, withdraw=0.0),
        "fetch_total_assets": Decimal(100_000),
        "fetch_total_supply": Decimal(100_000),
        "get_withdrawal_period": None,
        "get_estimated_lock_up": None,
        "get_flags": set(),
        "get_strategy_tags": None,
        "get_link": "https://example.invalid",
        "get_notes": "Preserve operator note",
        "get_deposit_manager_capability": None,
        "get_whitelist_notes": None,
        "fetch_minimum_deposit": None,
        "fetch_minimum_redemption": None,
        "fetch_scan_record_extra_data": {},
    }
    for method, value in methods.items():
        monkeypatch.setattr(vault, method, Mock(return_value=value))
    monkeypatch.setattr(scan, "fetch_deposit_permission", Mock(return_value=SimpleNamespace(value="unknown")))
    first_seen = datetime.datetime(2026, 9, 1)  # noqa: DTZ001 - Repository timestamps are naive UTC.
    detection = ERC4262VaultDetection(chain=spec.chain_id, address=spec.vault_address, first_seen_at_block=1, first_seen_at=first_seen, updated_at=first_seen, features={feature}, deposit_count=10, redeem_count=10)
    record = scan.create_vault_scan_record(web3=None, detection=detection, block_identifier=1, token_cache={})
    metadata = LAGOON_CURATOR_VAULT_METADATA.get((spec.chain_id, spec.vault_address))
    expected_long = metadata.description if metadata else None
    expected_short = metadata.short_description if metadata else None
    assert record["_description"] == expected_long
    assert record["_short_description"] == expected_short
    prices = pd.DataFrame({"id": spec.as_string_id(), "address": spec.vault_address, "chain": spec.chain_id, "block_number": range(35), "share_price": [1 + i * 0.0001 for i in range(35)], "total_assets": 100_000.0, "total_supply": 100_000.0, "event_count": 20}, index=pd.date_range(first_seen, periods=35, freq="D"))
    metrics = calculate_lifetime_metrics(calculate_hourly_returns_for_all_vaults(prices), {spec: record})
    exported = json.loads(json.dumps(export_lifetime_row(metrics.iloc[0]), allow_nan=False))
    assert exported["description"] == expected_long
    assert exported["short_description"] == expected_short
    assert exported["name"] == "Original share name"


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Isolate all reviewed targets and historical sentinels from production.

    The real runner provides preview staging, backups, journalling and locks.

    :param tmp_path: Temporary metadata, history and migration directories.
    :param monkeypatch: Remove legacy paths that conflict with isolation.
    :return: Keyword arguments accepted by the migration runner.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    rows = {VaultSpec(*key): {"Protocol": "Morpho" if key[1] in MORPHO_ADDRESSES else "Lagoon Finance", "_description": "Old provider text", "_short_description": None, "_notes": "Keep notes", "_flags": {"unofficial"}, "_strategy_tags": None} for key in LAGOON_CURATOR_VAULT_METADATA}
    rows.update({spec: {"Protocol": "Lagoon Finance", "_description": "Subsequently published provider strategy.", "_short_description": "Subsequently published summary."} for spec in UNDESCRIBED_SPECS})
    rows[VaultSpec(8453, SPECS[0].vault_address)] = {"Protocol": "Other", "_description": "Wrong-chain text"}
    VaultDatabase(rows=rows, last_scanned_block={1: 123}).write(data / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data / name).write_bytes(b"Preserve historical state")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT)
    (scripts / SCRIPT).write_bytes(script.source)
    return {"data_dir": data, "scripts_dir": scripts, "provenance": {"in_docker": False}}


def test_description_preview_apply_and_repeat(pipeline: dict) -> None:
    """Change only the two description fields in the frozen 25-row scope.

    Preview leaves every byte intact; a repeated apply makes no extra changes.

    :param pipeline: Isolated real-runner inputs and historical sentinels.
    :return: None after preservation, count and repeatability assertions.
    """
    data = pipeline["data_dir"]
    path = data / "vault-metadata-db.pickle"
    before = copy.deepcopy(VaultDatabase.read(path).rows)
    files = {p.name: p.read_bytes() for p in data.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **pipeline) == [SCRIPT]
    assert {p.name: p.read_bytes() for p in data.iterdir()} == files
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    expected = copy.deepcopy(before)
    for key, metadata in LAGOON_CURATOR_VAULT_METADATA.items():
        expected[VaultSpec(*key)].update(_description=metadata.description, _short_description=metadata.short_description)
    result = VaultDatabase.read(path)
    assert result.rows == expected
    assert result.last_scanned_block == {1: 123}
    report = json.loads((data / "migration.json").read_text())[SCRIPT]["other_data"]
    assert report["changed_rows"] == 25  # noqa: PLR2004 - Frozen reviewed migration scope.
    assert report["updated_fields"] == 50  # noqa: PLR2004 - Two fields for each reviewed deployment.
    assert report["missing_specs"] == []
    assert all((data / name).read_bytes() == content for name, content in files.items() if name != path.name)
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == []
    (data / "migration.json").unlink()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    assert VaultDatabase.read(path).rows == expected
    assert json.loads((data / "migration.json").read_text())[SCRIPT]["other_data"]["updated_fields"] == 0


@pytest.mark.parametrize("missing", [True, False])
def test_description_missing_or_conflicting_target(pipeline: dict, *, missing: bool) -> None:
    """Report absent rows and abort conflicting attribution before writing.

    A text repair cannot admit new vaults or silently repair protocol identity.

    :param pipeline: Isolated runner inputs.
    :param missing: Remove a target instead of assigning a conflicting protocol.
    :return: None after checking the guard or missing-row report.
    """
    data = pipeline["data_dir"]
    path = data / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if missing:
        del database.rows[SPECS[0]]
    else:
        database.rows[SPECS[0]]["Protocol"] = "Unexpected"
    database.write(path)
    before = path.read_bytes()
    if missing:
        assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
        assert SPECS[0] not in VaultDatabase.read(path).rows
        assert json.loads((data / "migration.json").read_text())[SCRIPT]["other_data"]["missing_specs"] == [SPECS[0].as_string_id()]
    else:
        with pytest.raises(ValueError, match="Unexpected protocol"):
            runner.run_pending_migrations(dry_run=False, **pipeline)
        assert path.read_bytes() == before


@pytest.mark.parametrize("spec", UNDESCRIBED_SPECS)
@pytest.mark.parametrize("provider_copy", [None, "", "The vault lends stablecoins to earn borrower interest."])
def test_missing_copy_remains_empty_and_allows_future_provider_text(spec: VaultSpec, provider_copy: str | None) -> None:
    """Keep missing descriptions empty without suppressing future genuine copy.

    An absent handwritten entry must use normal provider fallback, so a later
    source update can fill either field without another override change.

    :param spec: Reviewed deployment without substantive source material.
    :param provider_copy: Missing or subsequently published provider description.
    :return: None after confirming both accessors reflect source availability.
    """
    assert (spec.chain_id, spec.vault_address) not in LAGOON_CURATOR_VAULT_METADATA
    vault = LagoonVault.__new__(LagoonVault)
    vault.spec = spec
    vault.__dict__["_lagoon_scan_snapshot"] = LagoonMetadataSnapshot({"description": provider_copy, "short_description": provider_copy}, False, "success", None)
    assert vault.description == provider_copy
    assert vault.short_description == provider_copy
