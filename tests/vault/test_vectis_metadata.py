"""Check Vectis attribution, chain-scoped tags and the persisted tag refresh."""

import datetime
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import build_curator_metadata_json, identify_curator
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs.strategy_tags import resolve_strategy_tags

TARGET = VaultSpec(1, "0x05ed3da1deeaaa57c8e09986e95ac271572dcf89")
EXPECTED_TAGS = {StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading}
MIGRATION_NUMBER = 44


def test_vectis_curator() -> None:
    """Resolve Vectis through manager metadata and the verified vault branding.

    Missing manager metadata must still resolve the published vault name,
    while unrelated names must not acquire this curator.

    :return: ``None`` after checking attribution and public metadata export.
    """
    for name, manager in (("Unbranded vault", " Vectis Finance "), ("Vectis UltraX Arb", "")):
        assert identify_curator(1, "VUXA", name, TARGET.vault_address, "accountable", manager_name=manager) == "vectis-finance"
    assert identify_curator(1, "", "Unrelated Vectis strategy", TARGET.vault_address, "accountable") is None
    metadata = build_curator_metadata_json(Path("eth_defi/data/feeds/curators/vectis-finance.yaml"))
    assert metadata["name"] == "Vectis Finance"
    assert metadata["short_description"] and metadata["long_description"]
    assert metadata["twitter"] == "https://x.com/vectis_finance"
    assert not metadata["protocol_curator"]


@pytest.mark.parametrize("chain_id", [1, 143, 8453])
def test_vectis_strategy_tags(chain_id: int) -> None:
    """Keep the reviewed classification on Ethereum and return fresh sets.

    Construct a real adapter without making RPC calls. The same address on
    other chains and unreviewed addresses must retain missing information.

    :param chain_id: Candidate chain for the Vectis share-token address.
    :return: ``None`` after checking exact classifications and copy isolation.
    """
    vault = AccountableVault(Web3(), VaultSpec(chain_id, TARGET.vault_address))
    tags = vault.get_strategy_tags()
    assert tags == (EXPECTED_TAGS if chain_id == 1 else None)
    if tags is not None:
        tags.clear()
        assert vault.get_strategy_tags() == EXPECTED_TAGS
    assert AccountableVault(Web3(), VaultSpec(chain_id, "0x0000000000000000000000000000000000000000")).get_strategy_tags() is None
    spec = VaultSpec(chain_id, TARGET.vault_address)
    now = datetime.datetime(2026, 11, 9)  # noqa: DTZ001 - Repository timestamps use naive UTC.
    detection = ERC4262VaultDetection(chain_id, spec.vault_address, 25888215, now, {ERC4626Feature.accountable_like}, now, 22, 1)
    resolved = resolve_strategy_tags(spec, {"Protocol": "Accountable", "_detection_data": detection})
    assert (resolved[0] if resolved is not None else None) == (EXPECTED_TAGS if chain_id == 1 else None)


@pytest.mark.parametrize("dry_run", [True, False])
def test_vectis_tag_migration(tmp_path: Path, *, dry_run: bool) -> None:
    """Refresh only the reviewed row while preserving preview and idempotence.

    Exercise the numbered script against isolated persisted metadata, including
    a same-address deployment on Monad and unrelated fields on the target.

    :param tmp_path: Isolated pipeline directory.
    :param dry_run: Whether the repair must leave the persisted file untouched.
    :return: ``None`` after checking the staged and persisted rows.
    """
    now = datetime.datetime(2026, 11, 9)  # noqa: DTZ001 - Repository timestamps use naive UTC.
    detection = ERC4262VaultDetection(1, TARGET.vault_address, 25888215, now, {ERC4626Feature.accountable_like}, now, 22, 1)
    original = {"Protocol": "Accountable", "_detection_data": detection, "_notes": "Preserve notes", "_manager_name": "Vectis Finance", "_strategy_tags": None}
    other = VaultSpec(143, TARGET.vault_address)
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={TARGET: original, other: {"_strategy_tags": None}}).write(path)
    before = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == MIGRATION_NUMBER)
    context = runner.MigrationContext(tmp_path, dry_run, script_name=migration.name)
    runner._execute(migration, context)
    assert context.metadata().rows[TARGET] == original | {"_strategy_tags": EXPECTED_TAGS}
    assert context.metadata().rows[other] == {"_strategy_tags": None}
    assert context.other_data["changed_rows"] == 1
    if dry_run:
        assert path.read_bytes() == before
    else:
        assert VaultDatabase.read(path).rows[TARGET] == original | {"_strategy_tags": EXPECTED_TAGS}
        repeated = runner.MigrationContext(tmp_path, False, script_name=migration.name)
        runner._execute(migration, repeated)
        assert repeated.other_data["changed_rows"] == 0


@pytest.mark.parametrize("missing", [True, False])
def test_vectis_tag_migration_guards(tmp_path: Path, *, missing: bool) -> None:
    """Report a missing row and reject contradictory protocol evidence.

    Neither case may create or overwrite persisted vault metadata.

    :param tmp_path: Isolated pipeline directory.
    :param missing: Whether the target is absent or has conflicting metadata.
    :return: ``None`` after checking reporting and file preservation.
    """
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={} if missing else {TARGET: {"Protocol": "Morpho"}}).write(path)
    before = path.read_bytes()
    migration = next(spec for spec in runner.discover_migrations() if spec.number == MIGRATION_NUMBER)
    context = runner.MigrationContext(tmp_path, True, script_name=migration.name)
    if missing:
        runner._execute(migration, context)
        assert context.other_data["missing_specs"] == [TARGET.as_string_id()]
    else:
        with pytest.raises(ValueError, match="not classified as Accountable"):
            runner._execute(migration, context)
    assert path.read_bytes() == before
