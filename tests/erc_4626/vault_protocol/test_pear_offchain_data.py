"""Verify sampled Pear NAV and the one-off import into the vault pipeline."""

import datetime
import runpy
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_FIRST_DEPOSIT_BLOCKS
from eth_defi.erc_4626.vault_protocol.pear.offchain_data import fetch_pear_hourly_nav
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.curator import get_curator_name, identify_curator
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext, discover_migrations

ADDRESS = HexAddress("0x65c9f4665c7d9ce96f53aafc67796dcad46f4096")
LAUNCH = PEAR_FIRST_DEPOSIT_BLOCKS[ADDRESS]
START = datetime.datetime.fromisoformat("2026-09-14")
MIGRATION = "097-pear-offchain-history.py"


def test_pear_hourly_nav_preserves_source_observation() -> None:
    """Scale Pear fixed-point values and retain the final funded observation within bounds."""
    session = Mock()
    session.get.return_value.json.return_value = {"points": [{"bucket": f"2026-09-14T{hour}:15:30.125Z", "blockNumber": str(LAUNCH + offset), "totalSupply": str(supply), "totalAssets": "1250000", "sharePrice": "1250000000000000000"} for hour, offset, supply in [("00", -1, 0), ("00", 1, 1000000), ("00", 2, 1000000), ("01", 3, 1000000), ("02", 4, 1000000)]]}
    rows = list(fetch_pear_hourly_nav(session, ADDRESS, START, START + datetime.timedelta(hours=3), end_block=LAUNCH + 4))
    assert [(row["timestamp"], row["block_number"]) for row in rows] == [
        (START.replace(minute=15, second=30, microsecond=125000), LAUNCH + 2),
        (START.replace(hour=1, minute=15, second=30, microsecond=125000), LAUNCH + 3),
    ]
    assert [(row["share_price"], row["total_assets"], row["total_supply"]) for row in rows] == [(1.25, 1.25, 1)] * 2


def test_migration_097_preserves_existing_prices_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0914 - Keep the persisted preview and retry assertions together.
    """Keep onchain prices and unrelated rows while refreshing catalogue copy."""
    spec = VaultSpec(999, ADDRESS)
    path = tmp_path / "vault-prices-1h.parquet"
    meta = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={spec: {"Protocol": "Pear", "Name": "Agent Pear", "_notes": "Preserve this."}}).write(meta)
    schema = VaultHistoricalRead.to_pyarrow_schema().append(pa.field("account_pnl", pa.float64()))
    first_live = START + datetime.timedelta(hours=4)
    cutoff = LAUNCH + 6
    saved = {"chain": 999, "address": ADDRESS, "block_number": cutoff, "timestamp": first_live, "share_price": 1.11, "vault_poll_frequency": "latest"}
    unrelated = dict(saved, chain=1, account_pnl=0.12)
    legacy = dict(saved, timestamp=START, block_number=LAUNCH, vault_poll_frequency=None)
    original_rows = pa.Table.from_pylist([legacy, saved, unrelated], schema=schema)
    pq.write_table(original_rows, path)
    original = path.read_bytes()
    migration = next(spec for spec in discover_migrations() if spec.name == MIGRATION)
    migrate = runpy.run_path(str(migration.path))["migrate"]
    namespace = migrate.__globals__
    monkeypatch.setitem(namespace, "TARGETS", {ADDRESS: START})
    monkeypatch.setitem(namespace, "native_datetime_utc_now", lambda: first_live + datetime.timedelta(hours=1))
    session = Mock()
    snapshot = {"bucket": "2026-09-14T02:15:30.125Z", "blockNumber": str(LAUNCH + 4), "totalSupply": "1000000", "totalAssets": "1250000", "sharePrice": "1250000000000000000"}
    earlier = dict(snapshot, bucket="2026-09-14T02:14:30.125Z", blockNumber=str(LAUNCH + 3))
    session.get.return_value.json.return_value = {"points": [earlier, snapshot]}
    monkeypatch.setattr(namespace["requests"], "Session", lambda: nullcontext(session))

    for dry_run, expected_count in [(True, 1), (False, 1), (False, 0)]:
        context = MigrationContext(tmp_path, dry_run)
        context.script_name = MIGRATION
        migrate(context)
        assert context.other_data["pear_price_rows"] == {spec.as_string_id(): expected_count}
        assert context.other_data["legacy_pear_price_rows_for_review"] == {spec.as_string_id(): 1}
        if dry_run:
            assert path.read_bytes() == original
    result = pq.read_table(path)
    assert len(result) == len(original_rows) + 1
    assert result.slice(0, len(original_rows)).to_pylist() == original_rows.to_pylist()
    row = VaultDatabase.read(meta).rows[spec]
    assert row["_curator_slug"] == identify_curator(999, "", "Agent Pear", ADDRESS, protocol_slug="pear") == "pear"
    assert get_curator_name(row["_curator_slug"]) == "Pear"
    assert row["_short_description"].endswith("usual relationship.")
    assert row["_description"].startswith("#### Vault's trading strategy")
    assert row["_notes"] == "Preserve this."
