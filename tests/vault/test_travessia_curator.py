"""Check Travessia curator attribution and chain-scoped Tauri classifications."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_typing import HexAddress
from PIL import Image

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.tags import TRAVESSIA_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import build_curator_metadata_json, get_curator_name, identify_curator, is_protocol_curator
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

MIGRATION_NUMBER = 43
TARGET_COUNT = 7
TARGETS = tuple(VaultSpec(chain, address) for chain, addresses in TRAVESSIA_STRATEGY_TAGS.items() for address in addresses)
EXPECTED = {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.inventory_financing}


@pytest.mark.parametrize("spec", TARGETS)
def test_travessia_attribution_and_tags(spec: VaultSpec) -> None:
    """Identify the Tauri product and classify each reviewed share deployment.

    The adapter lookup must normalise address casing, return independent sets
    and leave same-address deployments on unreviewed chains unclassified.

    :param spec: Reviewed chain and share-token address.
    :return: ``None`` after checking curator and strategy resolution.
    """
    for name in ("Travessia Tauri Vault", "Travessia Tauri Vault - USDC", "Travessia Tauri Vault - USDT", "Travessia Tauri Vault - BRZ"):
        assert identify_curator(spec.chain_id, "TTV", name, spec.vault_address, "accountable") == "travessia-credit"
    vault = SimpleNamespace(chain_id=spec.chain_id, address=HexAddress(spec.vault_address.upper()))
    tags = AccountableVault.get_strategy_tags(vault)
    assert tags == EXPECTED
    tags.clear()
    assert AccountableVault.get_strategy_tags(vault) == EXPECTED
    vault.chain_id = 42
    assert AccountableVault.get_strategy_tags(vault) is None


def test_travessia_curator_metadata() -> None:
    """Export source-linked descriptions and standard curator logo variants.

    Travessia is independent of the hosting protocol. Avoid matching Tauri alone
    or a similarly named organisation that does not use the verified product name.

    :return: ``None`` after validating the public curator metadata.
    """
    assert get_curator_name("travessia-credit") == "Travessia Credit"
    assert not is_protocol_curator("travessia-credit")
    for name in ("Tauri Vault", "Travessia unrelated vault", "Travessia Taurian Vault"):
        assert identify_curator(1, "TTV", name, "0x0000000000000000000000000000000000000000", "accountable") is None
    data = build_curator_metadata_json(Path("eth_defi/data/feeds/curators/travessia-credit.yaml"), public_url="https://example.invalid")
    assert data["short_description"]
    assert "[Travessia Credit](https://www.travessiacredit.com/)" in data["long_description"]
    for variant in ("generic", "light", "dark"):
        assert data["logos"][variant].endswith(f"/travessia-credit/{variant}.png")
        with Image.open(Path("eth_defi/data/vaults/formatted_logos/travessia-credit") / f"{variant}.png") as logo:
            assert logo.size == (256, 256)
            assert logo.mode == "RGBA"


@pytest.mark.parametrize("dry_run", [True, False])
def test_travessia_migration(tmp_path: Path, *, dry_run: bool) -> None:
    """Refresh only the reviewed tags with preview isolation and idempotence.

    All seven deployments exercise the shared persisted resolver. Existing notes,
    discovery evidence and an unrelated row must survive the refresh unchanged.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether changes must remain in memory.
    :return: ``None`` after checking staged and persisted metadata.
    """
    now = datetime.datetime(2026, 11, 9, tzinfo=datetime.UTC).replace(tzinfo=None)
    rows = {
        spec: {
            "Protocol": "Accountable",
            "_notes": "Preserve this note",
            "_strategy_tags": None,
            "_detection_data": ERC4262VaultDetection(spec.chain_id, spec.vault_address, 42, now, {ERC4626Feature.accountable_like}, now, 1, 1),
        }
        for spec in TARGETS
    }
    other = VaultSpec(42, TARGETS[0].vault_address)
    rows[other] = {"_strategy_tags": None}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    original = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == MIGRATION_NUMBER)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    assert context.other_data["changed_rows"] == TARGET_COUNT
    for spec in TARGETS:
        assert context.metadata().rows[spec] == rows[spec] | {"_strategy_tags": EXPECTED}
    assert context.metadata().rows[other] == rows[other]
    if dry_run:
        assert path.read_bytes() == original
    else:
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0
        assert VaultDatabase.read(path).rows == context.metadata().rows


@pytest.mark.parametrize("missing", [True, False])
def test_travessia_migration_guards(tmp_path: Path, *, missing: bool) -> None:
    """Report missing targets and reject mismatched protocol detection.

    Validation must fail before the metadata file changes, and missing vaults
    must not be created by this metadata-only refresh.

    :param tmp_path: Isolated pipeline directory.
    :param missing: Whether the target is absent instead of misclassified.
    :return: ``None`` after checking reporting or validation failure.
    """
    rows = {} if missing else {TARGETS[0]: {"_detection_data": None}}
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows).write(path)
    original = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == MIGRATION_NUMBER)
    context = runner.MigrationContext(tmp_path, True, script_name=migration.name)
    if missing:
        runner._execute(migration, context)
        assert len(context.other_data["missing_specs"]) == TARGET_COUNT
        assert context.other_data["changed_rows"] == 0
    else:
        with pytest.raises(ValueError, match="not an Accountable vault"):
            runner._execute(migration, context)
    assert path.read_bytes() == original
