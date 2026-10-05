"""Metadata candidate isolation and durable queue recovery without event replay."""

import datetime
import pickle
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_typing import HexAddress
from requests.exceptions import ConnectionError

from eth_defi.erc_4626 import lead_scan_core, scan
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import LeadScanReport, PotentialVaultMatch
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
    monkeypatch.setattr(lead_scan_core, "fetch_metadata_snapshots", lambda *args, **kwargs: {})
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

    monkeypatch.setattr(lead_scan_core, "fetch_vault_scan_record_in_worker", candidate)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=124))
    assert lead_scan_core.resume_pending_vault_metadata(web3, "https://rpc.example", path, 1, RPCRequestStats()) == 0
    assert seen == addresses
    restored = VaultDatabase.read(path)
    assert restored.last_scanned_block == {1: 123}
    assert restored.rows[VaultSpec(1, addresses[0])]["Name"] == "successful"
    assert restored.rows[VaultSpec(1, addresses[0])]["_available_liquidity"] == Decimal(100)
    assert load_rpc_scan_state(tmp_path / "rpc-metadata-1.json")[addresses[0]]["economics_stale"] == ["_available_liquidity", "_utilisation"]
    assert load_rpc_scan_state(pending_path) == {}
    assert load_rpc_scan_state(tmp_path / "rpc-metadata-1.json")[addresses[1]]["status"] == "unavailable"
    assert lead_scan_core.resume_pending_vault_metadata(web3, "https://rpc.example", path, 1, RPCRequestStats()) == 0
    assert seen == addresses


@pytest.mark.parametrize(("missing_row", "force_classification_refresh"), [(True, False), (False, True), (False, False)], ids=["restored-missing-row", "classification-repair", "fresh-existing-row"])
def test_metadata_schedule_rebuilds_missing_rows_and_honours_classification_repairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_row: bool, force_classification_refresh: bool) -> None:
    """Metadata scheduling honours catalogue restores and explicit repairs.

    An operator may restore the metadata pickle without restoring its optional
    scheduling sidecars. The discovery cursor still owns event progress, but
    a fresh successful metadata hint must not delay rebuilding the absent row
    and its eventual price reader. A classification-only repair must also read
    fresh metadata for an existing row, while an ordinary tick keeps its valid
    deadline. No contract/indexer request is needed here.

    :param tmp_path: Isolated authoritative catalogue and scheduling hints.
    :param monkeypatch: Replaces network reads and pins the observation time.
    :param missing_row: Simulate a restored catalogue without the observed row.
    :param force_classification_refresh: Explicit library-level classification repair.
    :return: None; checks worker selection, persisted metadata and event progress.
    """
    now = datetime.datetime(2026, 9, 30)
    detection = ERC4262VaultDetection(chain=1, address="0x" + "1" * 40, features=set(), updated_at=now, first_seen_at_block=1, first_seen_at=now, deposit_count=100, redeem_count=0)
    lead = PotentialVaultMatch(chain=1, address=detection.address, first_seen_at_block=1, first_seen_at=now, deposit_count=100)
    path = tmp_path / "metadata.pickle"
    database = VaultDatabase()
    database.update_leads_and_rows(1, 123, {detection.address: lead}, {})
    if not missing_row:
        database.rows[detection.get_spec()] = {"Name": "Existing", "First seen": now, "_detection_data": detection}
    database.write(path)
    monkeypatch.setattr(lead_scan_core, "native_datetime_utc_now", lambda: now)
    metadata_path = tmp_path / "rpc-metadata-1.json"
    save_rpc_scan_state(metadata_path, {detection.address: {"checked_at": now.isoformat(), "next_attempt_at": (now + datetime.timedelta(days=7)).isoformat(), "status": "ok", "features": [], "classifier_version": lead_scan_core.create_vault_classifier_signature()}})

    report = LeadScanReport(start_block=124, end_block=125, leads={detection.address: lead}, detections={detection.address: detection})

    def scan_recorded_vaults(_start: int, _end: int, *, greylist: frozenset[HexAddress]) -> LeadScanReport:
        """Return event coverage while checking the caller's explicit policy.

        Metadata scheduling must retain its recorded discovery coverage without
        inheriting HyperEVM exceptions for this Ethereum repair. Keep the keyword
        explicit so a renamed or dropped policy cannot pass unnoticed.

        :param _start: Requested discovery start, unused by the recorded report.
        :param _end: Requested discovery end, unused by the recorded report.
        :param greylist: Caller-selected isolation policy, empty in this test.
        :return: Existing lead/classification report for metadata scheduling.
        """
        assert greylist == frozenset()
        return report

    discoverer = SimpleNamespace(cached_features={}, seed_existing_leads=lambda leads: None, scan_vaults=scan_recorded_vaults)
    monkeypatch.setattr(lead_scan_core, "HypersyncVaultDiscover", lambda *args, **kwargs: discoverer)
    monkeypatch.setattr(lead_scan_core, "get_provider_name", lambda provider: "test provider")
    monkeypatch.setattr(lead_scan_core, "configure_hypersync_from_env", lambda *args, **kwargs: SimpleNamespace(hypersync_client=object(), hypersync_url="https://hypersync.example"))
    monkeypatch.setattr(lead_scan_core, "fetch_metadata_snapshots", lambda *args, **kwargs: {})
    monkeypatch.setattr(lead_scan_core, "display_vaults_table", lambda *args, **kwargs: None)
    seen = []

    def fetch_candidate(factory: object, candidate: ERC4262VaultDetection, block: int, **kwargs: object) -> dict:
        """Return a successful row so this test isolates catalogue scheduling.

        :param factory: Unused worker factory; providers are not contacted.
        :param candidate: Detection that must bypass the fresh sidecar hint.
        :param block: Source block chosen by discovery.
        :param kwargs: Optional worker snapshot/current-state arguments.
        :return: Minimal serialisable row for persistence and table preparation.
        """
        seen.append((candidate.address, block))
        return {"Name": "Recovered", "First seen": now, "_detection_data": candidate}

    monkeypatch.setattr(lead_scan_core, "fetch_vault_scan_record_in_worker", fetch_candidate)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=125), provider=object())
    lead_scan_core.scan_leads("https://rpc.example", path, max_workers=1, end_block=125, web3=web3, printer=lambda message: None, force_classification_refresh=force_classification_refresh)
    refreshed = missing_row or force_classification_refresh
    assert seen == ([(detection.address, 125)] if refreshed else [])
    restored = VaultDatabase.read(path)
    assert restored.rows[detection.get_spec()]["Name"] == ("Recovered" if refreshed else "Existing")
    assert restored.last_scanned_block[1] == 125
    assert not load_rpc_scan_state(tmp_path / "rpc-pending-metadata-1.json")


def test_address_scoped_repair_bypasses_not_due_admission_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit price selection must be visited before bounded replacement.

    Routine admission would skip this tiny vault until its next probe deadline.
    An address-scoped repair must construct and probe it now, while leaving the
    routine probe sidecar intact. The fake writer verifies that the selected
    adapter reaches the publication boundary without making provider requests.
    """
    now = datetime.datetime(2026, 9, 30)
    detection = ERC4262VaultDetection(chain=1, address="0x" + "1" * 40, features=set(), updated_at=now, first_seen_at_block=1, first_seen_at=now, deposit_count=0, redeem_count=0)
    path = tmp_path / "metadata.pickle"
    VaultDatabase(rows={detection.get_spec(): {"_detection_data": detection}}).write(path)
    probe_path = tmp_path / "rpc-tvl-probes-1.json"
    save_rpc_scan_state(probe_path, {detection.address: {"tvl_usd": "0", "next_probe_at": (now + datetime.timedelta(days=7)).isoformat()}})
    before = probe_path.read_bytes()
    monkeypatch.setattr(scan_all_chains, "native_datetime_utc_now", lambda: now)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=123))
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *args, **kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda: SimpleNamespace())
    monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *args, **kwargs: SimpleNamespace(hypersync_client=None))
    vault = SimpleNamespace(address=detection.address, first_seen_at_block=1, get_spec=detection.get_spec)
    constructed = []
    probed = []

    def construct(*args: object, **kwargs: object) -> object:
        """Record adapter preparation without issuing constructor RPCs.

        :param args: Web3, address and protocol features from the selector.
        :param kwargs: Optional adapter constructor arguments.
        :return: The selected test adapter.
        """
        constructed.append(args[1])
        return vault

    def fetch_probes(vaults: list, factory: object, block: int, workers: int) -> list:
        """Supply verified admission without masking which candidates are due.

        :param vaults: Candidates sent to the shared batch path.
        :param factory: Unused connection factory.
        :param block: Actual numeric source block.
        :param workers: Configured read concurrency.
        :return: Verified meaningful USD admission outcomes.
        """
        probed.extend(vaults)
        return [(candidate, Decimal(1800), False) for candidate in vaults]

    def writer(**kwargs: object) -> dict:
        """Verify selection before returning successful publication diagnostics.

        :param kwargs: Writer input, including its bounded vault address subset.
        :return: Minimal successful scan result; no price file is written.
        """
        assert kwargs["vaults"] == [vault]
        assert kwargs["vault_addresses"] == {detection.address}
        return {"reader_states": {}, "rows_written": 1, "freshness_rows_written": 0, "freshness_eligible_vaults": 0, "overdue_vaults": {}, "unknown_conversion_vaults": [], "start_block": 1, "end_block": 123}

    monkeypatch.setattr(scan_all_chains, "create_vault_instance", construct)
    monkeypatch.setattr(scan_all_chains, "fetch_batched_tvl_probes", fetch_probes)
    monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", writer)
    success, metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=path, reader_state_path=tmp_path / "readers.pickle", uncleaned_price_path=tmp_path / "prices.parquet", vault_addresses={detection.address})
    assert success
    assert constructed == [detection.address]
    assert probed == [vault]
    assert metrics["tvl_probes_cached"] == 0
    assert metrics["tvl_probes_due"] == 1
    assert probe_path.read_bytes() == before


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


@pytest.mark.parametrize("blacklisted, address_scoped", [(False, False), (True, False), (True, True)])
def test_price_selection_distinguishes_blacklist_from_unsupported_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blacklisted: bool, address_scoped: bool) -> None:
    """Preserve required coverage while respecting intentional exclusions.

    A previously meaningful vault with an unsupported adapter still needs a
    visible freshness failure. A deliberately blacklisted contract must skip
    construction and freshness admission, as the historical writer cannot read
    it. The Monad smoke scan exposed this distinction with an old test vault.

    :param tmp_path: Isolated persisted catalogue and reader state directory.
    :param monkeypatch: Replace provider reads and the publication boundary.
    :param blacklisted: Whether policy intentionally excludes the old vault.
    :param address_scoped: Whether a manual repair explicitly selects both vaults.
    :return: None; selection, coverage and state preservation are asserted.
    """
    database = VaultDatabase()
    detections = [ERC4262VaultDetection(chain=1, address="0x" + str(number) * 40, features=set(), updated_at=datetime.datetime(2026, 9, 30), first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 1, 1), deposit_count=0 if number == 1 else 100, redeem_count=0) for number in (1, 2)]
    database.rows = {d.get_spec(): {"_detection_data": d} for d in detections}
    database.rows[detections[0].get_spec()]["_denomination_token"] = {"symbol": "USDC"}
    path = tmp_path / "metadata.pickle"
    database.write(path)
    reader_path = tmp_path / "readers.pickle"
    old_state = {"last_tvl": Decimal(2000), "max_tvl": Decimal(2000), "last_block": 1}
    with reader_path.open("wb") as output:
        pickle.dump({detections[0].get_spec(): old_state}, output)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=123))
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *args, **kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda: SimpleNamespace())
    monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *args, **kwargs: SimpleNamespace(hypersync_client=None))
    good = SimpleNamespace(address=detections[1].address, first_seen_at_block=1, get_spec=detections[1].get_spec)
    monkeypatch.setattr(scan_all_chains, "BROKEN_VAULT_CONTRACTS", {detections[0].address} if blacklisted else set())
    constructed = []

    def construct(web3: object, address: HexAddress, *args: object, **kwargs: object) -> SimpleNamespace:
        """Expose any attempt to construct the deliberately excluded adapter.

        :param web3: Stub connection supplied by the price selector.
        :param address: Selected vault contract address.
        :param args: Unused protocol-feature positional arguments.
        :param kwargs: Unused cache arguments.
        :return: The good adapter; the other release is unsupported.
        """
        constructed.append(address)
        if address == detections[0].address:
            raise UnsupportedVaultVersion("Lagoon release")
        return good

    def writer(**kwargs: object) -> dict:
        """Force the real state-save path after checking freshness selection.

        Returning new progress for the good vault requires the scanner to
        rewrite the complete reader map, making preservation of the skipped
        legacy entry observable rather than simply rereading the input file.

        :param kwargs: Selected adapters and required live coverage.
        :return: New reader progress and synthetic publication diagnostics.
        """
        assert kwargs["vaults"] == [good]
        assert kwargs["expected_live_vaults"] == (set() if blacklisted else {detections[0].address})
        return {"reader_states": {detections[1].get_spec(): {"last_block": 123}}, "rows_written": 1, "freshness_rows_written": 0, "freshness_eligible_vaults": int(not blacklisted), "overdue_vaults": {} if blacklisted else {detections[0].address: "reader_unavailable"}, "unknown_conversion_vaults": [], "start_block": 1, "end_block": 123}

    monkeypatch.setattr(scan_all_chains, "create_vault_instance", construct)
    monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", writer)
    success, metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=path, reader_state_path=reader_path, uncleaned_price_path=tmp_path / "prices.parquet", vault_addresses={d.address for d in detections} if address_scoped else None)
    if address_scoped:
        assert not success and "blacklisted; refusing bounded deletion" in metrics["error"]
        assert constructed == []
        assert not (tmp_path / "prices.parquet").exists()
        with reader_path.open("rb") as input_file:
            assert pickle.load(input_file) == {detections[0].get_spec(): old_state}
        return
    assert success
    assert constructed == ([detections[1].address] if blacklisted else [d.address for d in detections])
    assert metrics["overdue_vaults"] == ({} if blacklisted else {detections[0].address: "reader_unavailable"})
    assert metrics["blacklisted_vaults"] == int(blacklisted)
    with reader_path.open("rb") as input_file:
        saved_states = pickle.load(input_file)
    assert saved_states[detections[1].get_spec()] == {"last_block": 123}
    assert saved_states[detections[0].get_spec()] == (old_state if blacklisted else {**old_state, "token_symbol": "USDC"})


def test_orphan_metadata_sidecar_is_reconciled_after_restore(tmp_path: Path) -> None:
    """A restored catalogue does not replay an orphaned candidate forever."""
    path = tmp_path / "metadata.pickle"
    VaultDatabase(last_scanned_block={1: 123}).write(path)
    pending = tmp_path / "rpc-pending-metadata-1.json"
    save_rpc_scan_state(pending, {"0x" + "1" * 40: {"next_attempt_at": "2026-01-01T00:00:00", "features": []}})
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    assert lead_scan_core.resume_pending_vault_metadata(web3, "https://rpc.example", path, 1, RPCRequestStats()) == 0
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


@pytest.mark.parametrize("failure", [ConnectionError("candidate provider unavailable"), ExtraValueError({"code": -32090, "message": "request rejected"}), ProbablyNodeHasNoBlock("missing state")])
def test_low_activity_constructor_transport_failure_keeps_active_prices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException) -> None:
    """An unqualified candidate outage leaves a healthy vault's prices runnable."""
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
