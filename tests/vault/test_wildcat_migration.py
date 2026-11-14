"""Verify the Wildcat lead-seeding and price backfill migrations on isolated metadata.

The registry coverage check reads real Ethereum state. Scanner row building
and the shared price backfill helper are replaced: they are covered by
``tests/erc_4626/vault_protocol/test_wildcat.py`` and
``tests/vault/test_price_backfill.py``, and the preview script
``scripts/erc-4626/preview-wildcat-migrations.py`` runs them for real.
"""

import datetime
import os
import pickle
from pathlib import Path

import pytest

from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.vault_protocol.wildcat.discovery import WildcatMarketCandidate
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import price_backfill, wildcat

SCRIPT_NAMES = ("063-wildcat-market-leads.py", "064-wildcat-price-backfill.py")

#: Wintermute's market, seeded with an existing generic lead and row to check that both are preserved.
EXISTING_MARKET = VaultSpec(1, "0xc9499006a149c553d18171747ed19aa7c6dd19e2")

#: Ethereum price cursor in the isolated reader state.
PRICE_CURSOR = 25_600_000

pytestmark = pytest.mark.skipif(os.environ.get("JSON_RPC_ETHEREUM") is None, reason="JSON_RPC_ETHEREUM needed for the registry coverage check")


@pytest.fixture
def wildcat_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list, list]:
    """Isolate both migrations with Ethereum lead and price cursors near the midnight block.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Replaces scanner row building and the backfill helper.
    :return: Pipeline data directory, migration resource directory, recorded backfill targets and pending read failures.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)

    def build_rows(web3: object, candidates: list[WildcatMarketCandidate], block: int) -> dict[VaultSpec, dict]:
        # Rows are read at the lead cursor, where every candidate is deployed
        assert all(c.created_block <= block for c in candidates)
        rows = {VaultSpec(c.chain, c.address): {"Protocol": "Wildcat", "_detection_data": c} for c in candidates}
        if failing_reads:
            # A transient read failure produces the shared scanner's broken placeholder
            failing_reads.pop()
            rows[next(iter(rows))] = {"Protocol": "<unknown>", "Name": "<broken: Web3RPCError>", "_rpc_failure_category": "unsupported"}
        return rows

    backfilled = []
    failing_reads = []
    monkeypatch.setattr(wildcat, "build_market_rows", build_rows)

    def record_backfill(_context: object, targets: list, *, step: int | None = None) -> None:
        # Daily reads on Ethereum's 12-second blocks
        assert step == 7_200
        backfilled.extend(targets)

    monkeypatch.setattr(price_backfill, "run_price_backfill", record_backfill)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    existing = PotentialVaultMatch(chain=1, address=EXISTING_MARKET.vault_address, first_seen_at_block=1, first_seen_at=datetime.datetime(2025, 1, 1), deposit_count=3)
    # Production leads pickled before configuration_count existed cannot be repr'd
    del existing.configuration_count
    database = VaultDatabase(
        rows={EXISTING_MARKET: {"Protocol": "Wildcat", "Name": "Keep existing row"}, VaultSpec(1, "0x0000000000000000000000000000000000000001"): {"Protocol": "Morpho"}},
        leads={EXISTING_MARKET: existing},
        last_scanned_block={1: ETHEREUM_MIDNIGHT_BLOCK},
    )
    database.write(data_dir / "vault-metadata-db.pickle")
    with (data_dir / "vault-reader-state-1h.pickle").open("wb") as out:
        pickle.dump({VaultSpec(1, "0x0000000000000000000000000000000000000001"): {"last_block": PRICE_CURSOR}, VaultSpec(8453, "0x0000000000000000000000000000000000000002"): {"last_block": 99_999_999}}, out)
    (data_dir / "vault-prices-1h.parquet").write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    for spec in runner.discover_migrations():
        if spec.name in SCRIPT_NAMES:
            (scripts_dir / spec.name).write_bytes(spec.source)
    return data_dir, scripts_dir, backfilled, failing_reads


def test_wildcat_lead_and_backfill_migrations(wildcat_migration: tuple[Path, Path, list, list]) -> None:
    """Seed every market registered by the lead cursor, then backfill each one up to the price cursor.

    :param wildcat_migration: Isolated data, migration directories, recorded backfill targets and read failures.
    """
    data_dir, scripts_dir, backfilled, _ = wildcat_migration
    seeded = [(address, block) for address, block, _ in wildcat.WILDCAT_V2_MARKETS if block <= ETHEREUM_MIDNIGHT_BLOCK]
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 2
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    backfilled.clear()

    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 2
    after = VaultDatabase.read(path)
    assert len(after.leads) == len(seeded)
    assert all(lead.wildcat_market_candidate is not None for lead in after.leads.values())
    assert after.leads[EXISTING_MARKET].deposit_count == 3
    assert after.rows[EXISTING_MARKET]["Name"] == "Keep existing row"
    assert len(after.rows) == len(seeded) + 1
    assert after.last_scanned_block == before.last_scanned_block
    assert all((data_dir / name).read_bytes() == payload for name, payload in original.items() if name != path.name)

    # Markets registered after the lead cursor are left to ordinary scanning
    expected = set(seeded)
    assert {(target.vault_address, target.start_block) for target in backfilled} == expected
    assert {target.end_block for target in backfilled} == {PRICE_CURSOR + 1}

    state = runner.load_state(data_dir / "migration.json")
    assert state[SCRIPT_NAMES[0]]["other_data"]["inserted_leads"] == len(seeded) - 1
    assert state[SCRIPT_NAMES[0]]["other_data"]["updated_leads"] == 1
    assert state[SCRIPT_NAMES[0]]["other_data"]["cursor_block"] == ETHEREUM_MIDNIGHT_BLOCK
    assert state[SCRIPT_NAMES[1]]["other_data"]["backfill_targets"] == len(expected)

    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


def test_wildcat_lead_migration_failed_read_is_retryable(wildcat_migration: tuple[Path, Path, list, list]) -> None:
    """A broken metadata row fails 036 without committing, and a retry succeeds.

    :param wildcat_migration: Isolated data, migration directories, recorded backfill targets and read failures.
    """
    data_dir, scripts_dir, _, failing_reads = wildcat_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    failing_reads.append(True)
    with pytest.raises(ValueError, match="Could not read Wildcat metadata"):
        runner.run_pending_migrations(dry_run=False, **options)
    assert path.read_bytes() == original
    assert not runner.load_state(data_dir / "migration.json")[SCRIPT_NAMES[0]]["success"]

    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 2
    assert not any(row.get("_rpc_failure_category") for row in VaultDatabase.read(path).rows.values())


def test_wildcat_migrations_without_registered_markets(wildcat_migration: tuple[Path, Path, list, list], monkeypatch: pytest.MonkeyPatch) -> None:
    """With a lead cursor before every reviewed market, both migrations complete without changes.

    :param wildcat_migration: Isolated data, migration directories, recorded backfill targets and read failures.
    :param monkeypatch: Makes any backfill helper call fail the test.
    """
    data_dir, scripts_dir, _, _ = wildcat_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.last_scanned_block = {1: 18_700_000}
    database.write(path)
    original = path.read_bytes()

    def unexpected_backfill(*args: object, **kwargs: object) -> None:
        raise AssertionError("The backfill helper must not run without targets")

    monkeypatch.setattr(price_backfill, "run_price_backfill", unexpected_backfill)
    assert len(runner.run_pending_migrations(dry_run=False, data_dir=data_dir, scripts_dir=scripts_dir, provenance={"in_docker": False})) == 2
    assert path.read_bytes() == original
    assert runner.load_state(data_dir / "migration.json")[SCRIPT_NAMES[1]]["other_data"]["backfill_targets"] == 0
