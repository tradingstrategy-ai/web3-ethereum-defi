"""Verify the vault onboarding migration on isolated metadata.

Classification and scan record building are replaced: they are the ordinary
discovery code paths, covered by the vault protocol tests. The shared price
backfill helper is replaced too; ``tests/vault/test_price_backfill.py``
covers it.
"""

import datetime
import pickle
from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import price_backfill, vault_onboarding

SCRIPT_NAME = "082-llama-lend-wstgbp-crvusd.py"

#: Market onboarded by the migration.
MARKET = VaultSpec(1, "0xe279c1c1c246b6f8f32c55b7820c2164151c5570")

#: Ethereum lead cursor of the 2026-11-14 discovery pass, before the first deposit.
LEAD_CURSOR = 26_416_318

#: Ethereum price cursor in the isolated reader state.
PRICE_CURSOR = 26_447_393

#: Unrelated vault whose row, lead and reader state must be preserved.
OTHER = VaultSpec(1, "0x0000000000000000000000000000000000000001")


@pytest.fixture
def onboarding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolate the migration with production-like Ethereum lead and price cursors.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Replaces RPC reads and the backfill helper.
    :return: Data and script directories, recorded row builds and backfill targets, and a switch for broken reads.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    state = SimpleNamespace(built=[], backfilled=[], broken_read=False)

    def build_row(web3: object, target: vault_onboarding.VaultOnboardingTarget, block: int) -> dict:
        state.built.append((target.as_spec(), block))
        if state.broken_read:
            # A transient read failure produces the shared scanner's broken placeholder
            return {"Protocol": "<unknown>", "Name": "<broken: Web3RPCError>", "_rpc_failure_category": "unsupported"}
        features = {ERC4626Feature.llamma_like}
        found = ERC4262VaultDetection(chain=1, address=target.vault_address, first_seen_at_block=target.first_seen_at_block, first_seen_at=target.first_seen_at, features=features, updated_at=datetime.datetime(2026, 11, 19), deposit_count=0, redeem_count=0)
        return {"Protocol": "Llama Lend", "Name": "Llama Lend wstGBP / crvUSD", "Denomination": "crvUSD", "NAV": 5012, "_detection_data": found}

    monkeypatch.setattr(vault_onboarding, "create_chain_web3", lambda chain_id: SimpleNamespace(eth=SimpleNamespace(block_number=26_448_000)))
    monkeypatch.setattr(vault_onboarding, "build_vault_row", build_row)
    state.failing_backfills = 0

    def record_backfill(_context: object, targets: list) -> None:
        if state.failing_backfills:
            state.failing_backfills -= 1
            raise RuntimeError("Synthetic RPC outage")
        state.backfilled.extend(targets)

    monkeypatch.setattr(price_backfill, "run_price_backfill", record_backfill)

    state.data_dir = tmp_path / "data"
    state.data_dir.mkdir()
    other_lead = PotentialVaultMatch(chain=1, address=OTHER.vault_address, first_seen_at_block=1, first_seen_at=datetime.datetime(2025, 1, 1), deposit_count=9)
    VaultDatabase(rows={OTHER: {"Protocol": "Morpho"}}, leads={OTHER: other_lead}, last_scanned_block={1: LEAD_CURSOR}).write(state.data_dir / "vault-metadata-db.pickle")
    with (state.data_dir / "vault-reader-state-1h.pickle").open("wb") as out:
        pickle.dump({OTHER: {"last_block": PRICE_CURSOR}, VaultSpec(8453, "0x0000000000000000000000000000000000000002"): {"last_block": 99_999_999}}, out)
    (state.data_dir / "vault-prices-1h.parquet").write_bytes(b"Preserve historical data")
    state.scripts_dir = tmp_path / "scripts"
    state.scripts_dir.mkdir()
    for spec in runner.discover_migrations():
        if spec.name == SCRIPT_NAME:
            (state.scripts_dir / spec.name).write_bytes(spec.source)
    state.options = {"data_dir": state.data_dir, "scripts_dir": state.scripts_dir, "provenance": {"in_docker": False}}
    return state


def test_vault_onboarding_seeds_lead_row_and_backfills(onboarding: SimpleNamespace) -> None:
    """Preview writes nothing; apply seeds a zero-count lead and row, then backfills up to the price cursor.

    :param onboarding: Isolated migration state.
    """
    path = onboarding.data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in onboarding.data_dir.iterdir()}

    assert len(runner.run_pending_migrations(dry_run=True, **onboarding.options)) == 1
    assert {file.name: file.read_bytes() for file in onboarding.data_dir.iterdir()} == original
    onboarding.backfilled.clear()

    assert len(runner.run_pending_migrations(dry_run=False, **onboarding.options)) == 1
    database = VaultDatabase.read(path)
    lead = database.leads[MARKET]
    # Discovery adds the market's events when it resumes from the unchanged lead cursor
    assert (lead.first_seen_at_block, lead.first_seen_at, lead.deposit_count, lead.withdrawal_count) == (26_436_263, datetime.datetime(2026, 11, 17, 17, 29, 11), 0, 0)
    assert database.last_scanned_block == {1: LEAD_CURSOR}
    assert database.rows[MARKET]["Protocol"] == "Llama Lend"
    assert database.rows[OTHER] == {"Protocol": "Morpho"}
    assert database.leads[OTHER].deposit_count == 9
    assert onboarding.built[-1] == (MARKET, 26_448_000)
    assert [(target.as_spec(), target.start_block, target.end_block) for target in onboarding.backfilled] == [(MARKET, 26_436_263, PRICE_CURSOR + 1)]
    assert all((onboarding.data_dir / name).read_bytes() == payload for name, payload in original.items() if name != path.name)

    report = runner.load_state(onboarding.data_dir / "migration.json")[SCRIPT_NAME]["other_data"]
    assert (report["inserted_rows"], report["inserted_leads"], report["existing_rows"]) == (1, 1, [])
    assert report["price_cursor_end_blocks"] == {"1": PRICE_CURSOR + 1}

    applied = {file.name: file.read_bytes() for file in onboarding.data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **onboarding.options) == []
    assert {file.name: file.read_bytes() for file in onboarding.data_dir.iterdir()} == applied


def test_vault_onboarding_keeps_row_found_by_discovery(onboarding: SimpleNamespace) -> None:
    """A market discovery already found keeps its lead and row, and still gets the price backfill.

    :param onboarding: Isolated migration state.
    """
    path = onboarding.data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.rows[MARKET] = {"Protocol": "Llama Lend", "Name": "Discovered row"}
    database.leads[MARKET] = PotentialVaultMatch(chain=1, address=MARKET.vault_address, first_seen_at_block=26_436_263, first_seen_at=datetime.datetime(2026, 11, 17, 17, 29, 11), deposit_count=5, withdrawal_count=1)
    database.write(path)

    assert len(runner.run_pending_migrations(dry_run=False, **onboarding.options)) == 1
    database = VaultDatabase.read(path)
    assert database.rows[MARKET]["Name"] == "Discovered row"
    assert (database.leads[MARKET].deposit_count, database.leads[MARKET].withdrawal_count) == (5, 1)
    assert onboarding.built == []
    assert [target.as_spec() for target in onboarding.backfilled] == [MARKET]
    report = runner.load_state(onboarding.data_dir / "migration.json")[SCRIPT_NAME]["other_data"]
    assert (report["inserted_rows"], report["existing_rows"]) == (0, [MARKET.as_string_id()])


@pytest.mark.parametrize("problem", ["broken_read", "discovery_passed"])
def test_vault_onboarding_rejects_without_commit(onboarding: SimpleNamespace, problem: str) -> None:
    """A broken read or a first event discovery has already scanned fails before any write.

    :param onboarding: Isolated migration state.
    :param problem: Failure to provoke.
    """
    path = onboarding.data_dir / "vault-metadata-db.pickle"
    if problem == "broken_read":
        onboarding.broken_read = True
        expected = "Could not read 1-0xe279c1c1c246b6f8f32c55b7820c2164151c5570 as Llama Lend"
    else:
        # The cursor has passed the first deposit, so the market is missing for another reason
        database = VaultDatabase.read(path)
        database.last_scanned_block[1] = 26_436_263
        database.write(path)
        expected = "investigate discovery instead"
    original = path.read_bytes()

    with pytest.raises(ValueError, match=expected):
        runner.run_pending_migrations(dry_run=False, **onboarding.options)
    assert path.read_bytes() == original
    assert onboarding.backfilled == []


def test_vault_onboarding_retries_failed_backfill(onboarding: SimpleNamespace) -> None:
    """A backfill failing after the metadata commit is retried without rebuilding the seeded lead and row.

    :param onboarding: Isolated migration state.
    """
    path = onboarding.data_dir / "vault-metadata-db.pickle"
    onboarding.failing_backfills = 1

    with pytest.raises(RuntimeError, match="Synthetic RPC outage"):
        runner.run_pending_migrations(dry_run=False, **onboarding.options)
    seeded = VaultDatabase.read(path)
    assert MARKET in seeded.rows
    assert MARKET in seeded.leads
    assert onboarding.backfilled == []

    assert len(runner.run_pending_migrations(dry_run=False, **onboarding.options)) == 1
    database = VaultDatabase.read(path)
    assert len(onboarding.built) == 1
    assert database.rows[MARKET] == seeded.rows[MARKET]
    assert database.leads[MARKET].deposit_count == 0
    assert [target.as_spec() for target in onboarding.backfilled] == [MARKET]
    assert runner.load_state(onboarding.data_dir / "migration.json")[SCRIPT_NAME]["success"]
