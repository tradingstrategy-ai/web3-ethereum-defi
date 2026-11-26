"""Exercise Shift's expired-share repair through the migration runner."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault
from eth_defi.vault.publication_state import read_publication_state
from eth_defi.vault_protocols.migration import discover_migrations, run_pending_migrations
from eth_defi.vault_protocols.repairs.price_backfill import PRICE_FILE_NAME

LTLLP = "0x956bdd9c18b786b082fd50c52722d254f0cb6964"
SAMPLE_BLOCK = 527_445_008
SAMPLE_EPOCH = 1795616098
PUBLISH_EPOCH = 1795611611
SNAPSHOT_SUPPLY = 244389801066482356659602


@pytest.mark.parametrize("existing_nav_column", [False, True])
def test_shift_expiry_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, existing_nav_column: bool) -> None:
    """Reconstruct expired NAV and retain genuine zeros and unrelated prices."""
    spec = next(spec for spec in discover_migrations() if spec.name == "110-shift-expired-share-prices.py")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / spec.name).write_bytes(spec.source)
    monkeypatch.setenv("JSON_RPC_ARBITRUM", "https://example.invalid/rpc")
    monkeypatch.setattr("eth_defi.provider.multi_provider.create_multi_provider_web3", lambda _url: web3)

    web3 = Web3()

    def get_block(block_identifier: int) -> dict[str, int]:
        return {"number": block_identifier, "timestamp": SAMPLE_EPOCH}

    def call(function: ContractFunction, *, block_identifier: int | str = "latest") -> int | str | tuple[int, int, int]:
        match function.fn_name:
            case "tvlFeed":
                return "0x4062c539D09A76D79e366F8C2a2e2F987EAD1FE0"
            case "getLastTvl":
                value = 262645912431 if block_identifier == SAMPLE_BLOCK else 0
                return value, PUBLISH_EPOCH, SNAPSHOT_SUPPLY
            case "getSharePrice":
                return 0
            case "freshness":
                return 4200
            case "decimals":
                return 6
        raise AssertionError(function.fn_name)

    monkeypatch.setattr(web3.eth, "get_block", get_block)
    monkeypatch.setattr(ContractFunction, "call", call)
    monkeypatch.setattr(ShiftVault, "fetch_total_supply", lambda _self, _block: 244389)
    monkeypatch.setattr(ShiftVault, "share_token", type("Token", (), {"decimals": 18})())
    path = tmp_path / PRICE_FILE_NAME
    before = pa.table(
        {
            "chain": [42161, 42161, 8453],
            "address": [LTLLP, LTLLP, LTLLP],
            "block_number": [SAMPLE_BLOCK, SAMPLE_BLOCK + 1, SAMPLE_BLOCK],
            "share_price": [0.0, 0.0, 0.0],
            "total_assets": [0.0, 0.0, 0.0],
        },
        metadata={b"vault.native_revisions": b"{}", b"pandas": b"{}"},
    )
    publication = native_datetime_utc_fromtimestamp(PUBLISH_EPOCH)
    untouched_publication = publication if existing_nav_column else None
    if existing_nav_column:
        before = before.append_column("nav_timestamp", pa.array([None, untouched_publication, None], type=pa.timestamp("ms")))
    pq.write_table(before, path, row_group_size=1)
    original_bytes = path.read_bytes()

    assert run_pending_migrations(data_dir=tmp_path, scripts_dir=scripts) == [spec.name]
    assert path.read_bytes() == original_bytes
    marker = tmp_path / "post-processing-pending.json"
    assert not marker.exists()
    assert run_pending_migrations(dry_run=False, data_dir=tmp_path, scripts_dir=scripts, provenance={}) == [spec.name]
    assert read_publication_state(marker)["failed"] == ["clean-prices"]
    after = pq.read_table(path)
    expected_metadata = before.schema.metadata if existing_nav_column else {b"vault.native_revisions": b"{}"}
    assert after.schema.metadata == expected_metadata
    assert after.select(["chain", "address", "block_number"]).equals(before.select(["chain", "address", "block_number"]))
    assert after["share_price"].to_pylist() == [1.0747, 0.0, 0.0]
    assert after["total_assets"].to_pylist() == [262645.912431, 0.0, 0.0]
    assert after["nav_timestamp"].to_pylist() == [publication, untouched_publication, None]
    assert run_pending_migrations(dry_run=False, data_dir=tmp_path, scripts_dir=scripts, provenance={}) == []
