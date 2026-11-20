"""Frozen Buoy onboarding scope and preview preservation."""

import runpy
import pickle
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.erc_4626.vault_protocol.buoy import offchain_metadata
from eth_defi.erc_4626.vault_protocol.buoy.deployments import BUOY_REVIEWED_LEADS, BUOY_REVIEW_BLOCK
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext


def test_buoy_migration_preview_preserves_files_and_limits_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only reviewed leads are staged; existing early prices bound each backfill.

    Real runner staging and a real Parquet file are used while network reads
    and the already-tested expensive backfill helper are replaced.

    :param tmp_path: Isolated pipeline snapshot.
    :param monkeypatch: Replace external reads and record backfill scope.
    """
    namespace = runpy.run_path("migration/scripts/101-buoy-read-only.py")
    migrate = namespace["migrate"]
    globals_ = namespace["_migrate"].__globals__
    address, first_block, _ = BUOY_REVIEWED_LEADS[0]
    target = VaultSpec(999, address)
    unrelated = VaultSpec(1, "0x" + "11" * 20)
    database = VaultDatabase(rows={target: {"_notes": "Manual note", "_flags": {"manual"}, "custom": 42}, unrelated: {"Name": "Unrelated"}})
    metadata_path = tmp_path / "vault-metadata-db.pickle"
    database.write(metadata_path)
    price_path = tmp_path / "vault-prices-1h.parquet"
    pq.write_table(pa.table({"chain": [999, 1], "address": [address, unrelated.vault_address], "block_number": [first_block + 10, 999]}), price_path)
    reader_path = tmp_path / "vault-reader-state-1h.pickle"
    handoff = BUOY_REVIEW_BLOCK + 100
    reader_path.write_bytes(pickle.dumps({VaultSpec(999, "0x" + "12" * 20): {"last_block": handoff - 1}}))
    originals = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    web3 = Mock()
    web3.eth.block_number = BUOY_REVIEW_BLOCK + 200
    monkeypatch.setitem(globals_, "create_multi_provider_web3", lambda _: web3)
    monkeypatch.setitem(globals_, "read_json_rpc_url", lambda _: "unused test connection")

    def read_record(web3: object, detection: object, block: int, token_cache: dict) -> dict:
        """Return a structurally valid independent metadata result.

        :param web3: Mock connection.
        :param detection: Real migration-created detection.
        :param block: Frozen read block.
        :param token_cache: Shared cache for this batch.
        :return: New onchain metadata without manual fields.
        """
        # Exercise the actual optional provider cache inside the migration's
        # scope, proving it does not touch the persistent default directory.
        offchain_metadata.fetch_buoy_vaults_for_chain(999)
        return {"Name": "Buoy vault", "Protocol": "Buoy", "_detection_data": detection, "_denomination_token": {"symbol": "USDC"}, "_nav_timestamp": detection.first_seen_at}

    persistent_cache = tmp_path / "persistent-buoy-cache"
    monkeypatch.setattr(offchain_metadata, "DEFAULT_CACHE_PATH", persistent_cache)
    response = Mock()
    response.json.return_value = {"items": [{"address": address, "chainId": 999}], "page": {"offset": 0, "hasMore": False}}
    request = Mock(return_value=response)
    monkeypatch.setattr(offchain_metadata.requests, "get", request)
    monkeypatch.setitem(globals_, "create_vault_scan_record", read_record)
    # One currently initialised vault had no NAV at the scanner handoff. Its
    # later publication must be left for the normal live scanner.
    late_address = BUOY_REVIEWED_LEADS[-1][0]
    observations = []

    class IntervalVault:
        """Read endpoint eligibility independently of current catalogue values."""

        def __init__(self, connection: object, spec: VaultSpec, **kwargs: object) -> None:
            """Retain the target identity for the endpoint read."""
            self.spec = spec

        def fetch_accounting(self, block: int) -> Mock:
            """Record the historical endpoint and emulate late initialisation."""
            observations.append((self.spec.vault_address, block))
            return Mock(nav_timestamp=0 if self.spec.vault_address == late_address else 1, supply=0 if self.spec.vault_address == address else 100)

    monkeypatch.setitem(globals_, "BuoyVault", IntervalVault)
    backfill = Mock()
    monkeypatch.setitem(globals_, "run_price_backfill", backfill)
    context = MigrationContext(tmp_path, True, script_name="101-buoy-read-only.py")
    migrate(context)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == originals
    assert not persistent_cache.exists()
    assert request.call_count == 1
    assert len(context.metadata().rows) == 61
    assert len(context.metadata().leads) == 60
    assert context.metadata().leads[target].is_candidate()
    assert context.metadata().leads[target].buoy_factory is not None
    assert context.metadata().rows[unrelated] == database.rows[unrelated]
    assert context.metadata().rows[target]["custom"] == 42
    assert context.metadata().rows[target]["_notes"] == "Manual note"
    assert context.metadata().rows[target]["_flags"] == {"manual"}
    backfill.assert_called_once()
    targets = backfill.call_args.args[1]
    assert {item.vault_address for item in targets} == {lead[0] for lead in BUOY_REVIEWED_LEADS} - {late_address}
    assert next(item for item in targets if item.vault_address == address).end_block == first_block + 10
    assert max(item.end_block for item in targets) == handoff
    assert dict(observations)[late_address] == handoff - 1
    assert next(item for item in targets if item.vault_address == address).allow_empty_supply


@pytest.mark.parametrize("refusal, message", [("missing", "Restore the scheduled reader state"), ("pending", "Recover pending reader publication"), ("ahead", "cursor is ahead")])
def test_buoy_migration_rejects_unsafe_reader_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str, message: str) -> None:
    """Refuse onboarding when the critical price cursor cannot safely resume.

    A metadata repair must not mask reader-state loss or invent a replacement
    cursor. The operator must restore it before any metadata or price write.

    :param tmp_path: Isolated pipeline snapshot with unsafe reader progress.
    :param monkeypatch: Supply the current RPC head without network access.
    :param refusal: Missing state, pending publication or a cursor ahead of RPC.
    :param message: Required operational recovery diagnostic.
    :return: None after checking all original files remain unchanged.
    """
    namespace = runpy.run_path("migration/scripts/101-buoy-read-only.py")
    globals_ = namespace["_migrate"].__globals__
    VaultDatabase(rows={}).write(tmp_path / "vault-metadata-db.pickle")
    (tmp_path / "vault-prices-1h.parquet").write_bytes(b"Existing prices are never read or replaced")
    if refusal != "missing":
        last_block = BUOY_REVIEW_BLOCK + 1 if refusal == "ahead" else BUOY_REVIEW_BLOCK - 1
        (tmp_path / "vault-reader-state-1h.pickle").write_bytes(pickle.dumps({VaultSpec(999, BUOY_REVIEWED_LEADS[0][0]): {"last_block": last_block}}))
    if refusal == "pending":
        monkeypatch.setitem(globals_, "fetch_reader_publication_progress", lambda *args: {"committed state": {"last_block": BUOY_REVIEW_BLOCK}})
    originals = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    web3 = Mock()
    web3.eth.block_number = BUOY_REVIEW_BLOCK
    monkeypatch.setitem(globals_, "create_multi_provider_web3", lambda _: web3)
    monkeypatch.setitem(globals_, "read_json_rpc_url", lambda _: "unused test connection")
    with pytest.raises(RuntimeError, match=message):
        namespace["migrate"](MigrationContext(tmp_path, True, script_name="101-buoy-read-only.py"))
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == originals
