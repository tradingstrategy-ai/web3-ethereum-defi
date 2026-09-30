"""Metadata candidate isolation and durable queue recovery without event replay."""

import datetime
import pickle
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from requests.exceptions import ConnectionError

from eth_defi.erc_4626 import lead_scan_core, scan
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.exception import UnsupportedVaultVersion
from eth_defi.vault.rpc_scan_state import load_rpc_scan_state, save_reader_publication_journal, save_rpc_scan_state
from eth_defi.vault.vaultdb import VaultDatabase


def test_unsupported_constructor_isolated_and_programming_error_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported versions yield a candidate outcome; generic errors stay fatal."""
    detection = ERC4262VaultDetection(chain=1, address="0x" + "1" * 40, features={ERC4626Feature.lagoon_like}, updated_at=datetime.datetime(2026, 9, 30), first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 1, 1), deposit_count=100, redeem_count=0)

    def unsupported(*args, **kwargs):
        raise UnsupportedVaultVersion("unsupported Lagoon release")

    monkeypatch.setattr(scan, "create_vault_instance", unsupported)
    row = scan.create_vault_scan_record(SimpleNamespace(), detection, 123, token_cache=None)
    assert row["_rpc_failure_category"] == "unsupported"
    monkeypatch.setattr(scan, "create_vault_instance", lambda *args, **kwargs: None)
    assert scan.create_vault_scan_record(SimpleNamespace(), detection, 123, token_cache=None)["Name"] == ""

    def programming_error(*args, **kwargs):
        raise NotImplementedError("unexpected implementation gap")

    monkeypatch.setattr(scan, "create_vault_instance", programming_error)
    with pytest.raises(NotImplementedError, match="implementation gap"):
        scan.create_vault_scan_record(SimpleNamespace(), detection, 123, token_cache=None)


def test_pending_metadata_resumes_candidates_without_event_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restart drains successes and schedules permanent negatives weekly."""
    monkeypatch.setenv("VAULT_RPC_OPTIMISATIONS", "false")
    path = tmp_path / "metadata.pickle"
    addresses = ["0x" + "1" * 40, "0x" + "2" * 40]
    first_seen = datetime.datetime(2026, 1, 1)
    leads = {address: PotentialVaultMatch(chain=1, address=address, first_seen_at_block=1, first_seen_at=first_seen, deposit_count=100, withdrawal_count=0) for address in addresses}
    database = VaultDatabase()
    database.update_leads_and_rows(1, 123, leads, {})
    old_detection = ERC4262VaultDetection(chain=1, address=addresses[0], features=set(), updated_at=first_seen, first_seen_at_block=1, first_seen_at=first_seen, deposit_count=100, redeem_count=0)
    database.rows[old_detection.get_spec()] = {"Name": "old", "_detection_data": old_detection, "_available_liquidity": Decimal(100), "_utilisation": Decimal("0.5")}
    database.write(path)
    pending_path = tmp_path / "rpc-pending-metadata-1.json"
    save_rpc_scan_state(pending_path, {address: {"next_attempt_at": first_seen.isoformat(), "features": []} for address in addresses})
    save_rpc_scan_state(tmp_path / "rpc-metadata-1.json", {addresses[0]: {"features": []}})
    seen = []

    def candidate(factory, detection, block, **kwargs):
        seen.append(detection.address)
        return {"Name": "successful" if detection.address == addresses[0] else "<broken: UnsupportedVaultVersion>", "_detection_data": detection, "_rpc_failure_category": "unsupported", "_lending_fields_unavailable": ["_available_liquidity", "_utilisation"]}

    monkeypatch.setattr(lead_scan_core, "create_vault_scan_record_subprocess", candidate)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=124))
    assert lead_scan_core.fetch_pending_vault_metadata(web3, "https://rpc.example", path, 1, RPCRequestStats()) == 0
    assert seen == addresses
    restored = VaultDatabase.read(path)
    assert restored.last_scanned_block == {1: 123}
    assert restored.rows[VaultSpec(1, addresses[0])]["Name"] == "successful"
    assert restored.rows[VaultSpec(1, addresses[0])]["_available_liquidity"] == Decimal(100)
    assert load_rpc_scan_state(tmp_path / "rpc-metadata-1.json")[addresses[0]]["economics_stale"] == ["_available_liquidity", "_utilisation"]
    assert load_rpc_scan_state(pending_path) == {}
    assert load_rpc_scan_state(tmp_path / "rpc-metadata-1.json")[addresses[1]]["status"] == "unavailable"
    assert lead_scan_core.fetch_pending_vault_metadata(web3, "https://rpc.example", path, 1, RPCRequestStats()) == 0
    assert seen == addresses


@pytest.mark.parametrize("failure", [ConnectionError("provider down"), NotImplementedError("programming error")])
def test_active_constructor_outage_stays_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException) -> None:
    """Active transport failures remain retryable instead of empty successes."""
    database = VaultDatabase()
    detection = ERC4262VaultDetection(chain=1, address="0x" + "1" * 40, features=set(), updated_at=datetime.datetime(2026, 9, 30), first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 1, 1), deposit_count=100, redeem_count=0)
    database.rows[detection.get_spec()] = {"_detection_data": detection}
    path = tmp_path / "metadata.pickle"
    database.write(path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=123))
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *args, **kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda: SimpleNamespace())

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(scan_all_chains, "create_vault_instance", fail)
    success, metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=path, reader_state_path=tmp_path / "readers.pickle", uncleaned_price_path=tmp_path / "prices.parquet")
    assert not success
    assert metrics["error_category"] == ("transient" if isinstance(failure, ConnectionError) else "internal")


def test_active_unsupported_version_keeps_other_vault_and_prior_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A known unsupported release leaves a good vault and expected coverage."""
    database = VaultDatabase()
    detections = [ERC4262VaultDetection(chain=1, address="0x" + str(number) * 40, features=set(), updated_at=datetime.datetime(2026, 9, 30), first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 1, 1), deposit_count=0 if number == 1 else 100, redeem_count=0) for number in (1, 2)]
    database.rows = {d.get_spec(): {"_detection_data": d} for d in detections}
    path = tmp_path / "metadata.pickle"
    database.write(path)
    reader_path = tmp_path / "readers.pickle"
    with reader_path.open("wb") as output:
        pickle.dump({detections[0].get_spec(): {"token_symbol": "USDC", "last_tvl": Decimal(2000), "max_tvl": Decimal(2000), "last_block": 1}}, output)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=123))
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *args, **kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda: SimpleNamespace())
    monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *args, **kwargs: SimpleNamespace(hypersync_client=None))
    good = SimpleNamespace(address=detections[1].address, first_seen_at_block=1, get_spec=detections[1].get_spec)

    def construct(web3, address, *args, **kwargs):
        if address == detections[0].address:
            raise UnsupportedVaultVersion("Lagoon release")
        return good

    def writer(**kwargs):
        assert kwargs["vaults"] == [good]
        assert kwargs["expected_live_vaults"] == {detections[0].address}
        return {"reader_states": {}, "rows_written": 1, "freshness_rows_written": 0, "freshness_eligible_vaults": 1, "overdue_vaults": {detections[0].address: "reader_unavailable"}, "unknown_conversion_vaults": [], "start_block": 1, "end_block": 123}

    monkeypatch.setattr(scan_all_chains, "create_vault_instance", construct)
    monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", writer)
    success, metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=path, reader_state_path=reader_path, uncleaned_price_path=tmp_path / "prices.parquet")
    assert success
    assert metrics["overdue_vaults"] == {detections[0].address: "reader_unavailable"}


def test_orphan_metadata_sidecar_is_reconciled_after_restore(tmp_path: Path) -> None:
    """A restored catalogue does not replay an orphaned candidate forever."""
    path = tmp_path / "metadata.pickle"
    VaultDatabase(last_scanned_block={1: 123}).write(path)
    pending = tmp_path / "rpc-pending-metadata-1.json"
    save_rpc_scan_state(pending, {"0x" + "1" * 40: {"next_attempt_at": "2026-01-01T00:00:00", "features": []}})
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    assert lead_scan_core.fetch_pending_vault_metadata(web3, "https://rpc.example", path, 1, RPCRequestStats()) == 0
    assert load_rpc_scan_state(pending) == {}


def test_manual_backfill_requires_pending_publication_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A manual rewrite cannot orphan durable reader continuation progress."""
    metadata = tmp_path / "metadata.pickle"
    VaultDatabase(last_scanned_block={1: 123}).write(metadata)
    reader_path = tmp_path / "readers.pickle"
    prices = tmp_path / "prices.parquet"
    temporary = tmp_path / "prices.tmp"
    temporary.write_bytes(b"durable published prices")
    save_reader_publication_journal(reader_path.with_suffix(".publication.pickle"), temporary, prices, {VaultSpec(1, "0x" + "1" * 40): {"last_block": 123}})
    temporary.replace(prices)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=124))
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *args, **kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda: SimpleNamespace())
    success, metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=metadata, reader_state_path=reader_path, uncleaned_price_path=prices, start_block=1, end_block=124, persist_reader_state=False)
    assert not success
    assert "Recover pending committed reader progress" in metrics["error"]
    assert prices.read_bytes() == b"durable published prices"


def test_rpc_discovery_backend_rejected_before_provider_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An obsolete RPC backend fails before constructing a provider connection."""

    def unexpected_provider(*args: object, **kwargs: object) -> None:
        raise AssertionError("RPC backend validation must precede provider access")

    monkeypatch.setattr(lead_scan_core, "create_multi_provider_web3", unexpected_provider)
    with pytest.raises(ValueError, match="requires Hypersync"):
        lead_scan_core.scan_leads("https://rpc.example", tmp_path / "metadata.pickle", backend="rpc")


@pytest.mark.parametrize("reclassified", [False, True])
def test_legitimate_missing_economics_clear_previous_values(reclassified: bool) -> None:
    """A non-lending/empty outcome and reclassification cannot retain old liquidity."""
    now = datetime.datetime(2026, 9, 30)
    detection = ERC4262VaultDetection(chain=1, address="0x" + "1" * 40, features=set(), first_seen_at_block=1, first_seen_at=now, updated_at=now, deposit_count=0, redeem_count=0)
    row = {"_detection_data": detection, "_available_liquidity": None, "_utilisation": None}
    previous = {"_available_liquidity": Decimal(100), "_utilisation": Decimal("0.5")}
    observations = {detection.address: {"features": ["morpho_like"] if reclassified else []}}
    if reclassified:
        row["_lending_fields_unavailable"] = ["_available_liquidity", "_utilisation"]
    lead_scan_core._record_metadata_success(row, previous, {}, observations, 123, "signature", now)
    assert row["_available_liquidity"] is None
    assert row["_utilisation"] is None
    assert "economics_stale" not in observations[detection.address]


@pytest.mark.parametrize("optimise", ["true", "false"])
@pytest.mark.parametrize("failure", [ConnectionError("candidate provider unavailable"), ExtraValueError({"code": -32090, "message": "request rejected"}), ProbablyNodeHasNoBlock("missing state")])
def test_low_activity_constructor_transport_failure_keeps_active_prices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, optimise: str, failure: BaseException) -> None:
    """An unqualified candidate outage leaves a healthy vault's prices runnable."""
    monkeypatch.setenv("VAULT_RPC_OPTIMISATIONS", optimise)
    now = datetime.datetime(2026, 9, 30)
    detections = [ERC4262VaultDetection(chain=1, address="0x" + str(number) * 40, features=set(), updated_at=now, first_seen_at_block=1, first_seen_at=now, deposit_count=0 if number == 1 else 100, redeem_count=0) for number in (1, 2)]
    database = VaultDatabase(rows={d.get_spec(): {"_detection_data": d} for d in detections})
    path = tmp_path / "metadata.pickle"
    database.write(path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=123))
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *args, **kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda: SimpleNamespace())
    monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *args, **kwargs: SimpleNamespace(hypersync_client=None))
    good = SimpleNamespace(address=detections[1].address, first_seen_at_block=1, get_spec=detections[1].get_spec)

    constructed = []

    def construct(web3: object, address: str, *args: object, **kwargs: object) -> object:
        constructed.append(address)
        if address == detections[0].address:
            raise failure
        return good

    def writer(**kwargs: object) -> dict:
        assert kwargs["vaults"] == [good]
        return {"reader_states": {}, "rows_written": 1, "freshness_rows_written": 0, "freshness_eligible_vaults": 0, "overdue_vaults": {}, "unknown_conversion_vaults": [], "start_block": 1, "end_block": 123}

    monkeypatch.setattr(scan_all_chains, "create_vault_instance", construct)
    monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", writer)
    success, metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=path, reader_state_path=tmp_path / "readers.pickle", uncleaned_price_path=tmp_path / "prices.parquet")
    assert success and metrics["rows_written"] == 1
    assert metrics["low_activity_unverified"] == 1

    if optimise == "true":
        success, second_metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=path, reader_state_path=tmp_path / "readers.pickle", uncleaned_price_path=tmp_path / "prices.parquet")
        assert success and second_metrics["tvl_probes_cached"] == 1
        assert constructed.count(detections[0].address) == 1


def test_failed_reclassification_does_not_certify_old_economics() -> None:
    """A negative observation cannot relabel retained values as new-protocol data."""
    now = datetime.datetime(2026, 9, 30)
    detection = ERC4262VaultDetection(chain=1, address="0x" + "1" * 40, features=set(), first_seen_at_block=1, first_seen_at=now, updated_at=now, deposit_count=0, redeem_count=0)
    row = {"_detection_data": detection, "_available_liquidity": None, "_utilisation": None, "_lending_fields_unavailable": ["_available_liquidity", "_utilisation"]}
    observations = {detection.address: {"features": [], "status": "unavailable"}}
    lead_scan_core._record_metadata_success(row, {"_available_liquidity": Decimal(100)}, {}, observations, 123, "signature", now)
    assert row["_available_liquidity"] is None
