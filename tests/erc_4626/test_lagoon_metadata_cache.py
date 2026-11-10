"""Independent detail freshness and public membership without RPC or live HTTP."""

import datetime
import json
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from requests import Response
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon import offchain_metadata as metadata

ADDRESS = "0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45"
SECOND = "0x1111111111111111111111111111111111111111"
NOW = datetime.datetime(2026, 11, 10, 12)


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate cache state and refuse unintended external requests.

    Each test supplies only the endpoint observations needed for its scenario.
    """
    monkeypatch.setattr(metadata, "_cached_vaults", {})
    monkeypatch.setattr(metadata, "_request_locks", {})
    monkeypatch.setattr(metadata, "LAGOON_PRIVATE_VAULT_ALLOWLIST", frozenset())
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: {"vaults": [], "hasNextPage": False})

    def unexpected_http(*args: object, **kwargs: object) -> None:
        """Prevent a missing mock from turning a unit test into live traffic."""
        pytest.fail("Unit test attempted live HTTP")

    monkeypatch.setattr(metadata.requests, "get", unexpected_http)


def raw_detail(address: str = ADDRESS, description: str | None = "Long strategy.", short: str | None = "Short strategy.", closed: bool = False) -> dict:
    """Build a provider-shaped payload identifying an exact deployment.

    :param address: Contract identity.
    :param description: Optional long strategy.
    :param short: Optional summary.
    :param closed: Whether the provider has closed this product.
    :return: JSON-compatible provider data.
    """
    return {"chain": {"id": "1"}, "address": address, "name": "Example", "description": description, "shortDescription": short, "state": {"state": "Closed" if closed else "Open"}, "curators": []}


def provider(monkeypatch: pytest.MonkeyPatch, payload: dict | None, outcome: str = "success", delay: float = 3600) -> list[str]:
    """Install a detail response recorder.

    :param monkeypatch: Provider patch fixture.
    :param payload: Raw successful response or None.
    :param outcome: Expected response classification.
    :param delay: Retry interval for absence/failure.
    :return: Address requests made by the cache.
    """
    calls = []

    def fetch(chain_id: int, address: str, api_base_url: str) -> metadata.LagoonDetailResponse:
        """Record one requested deployment and return its configured outcome."""
        calls.append(address)
        return metadata.LagoonDetailResponse(payload, outcome, delay)

    monkeypatch.setattr(metadata, "_fetch_detail_response", fetch)
    return calls


def fetch(cache_path: Path, address: str = ADDRESS, now: datetime.datetime = NOW) -> metadata.LagoonMetadataSnapshot:
    """Fetch through the public snapshot API with a controlled clock.

    :param cache_path: Isolated cache directory.
    :param address: Requested deployment.
    :param now: Naive UTC observation time.
    :return: Detail plus independently observed membership.
    """
    return metadata.fetch_lagoon_metadata_snapshot(1, address, now, cache_path)


def test_unlisted_detail_does_not_confer_membership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetch both descriptions even when the address is outside the catalogue."""
    calls = provider(monkeypatch, raw_detail())
    result = fetch(tmp_path)
    assert result.metadata["description"] == "Long strategy."
    assert result.metadata["short_description"] == "Short strategy."
    assert result.listed is False
    assert fetch(tmp_path, Web3.to_checksum_address(ADDRESS)).metadata == result.metadata
    assert calls == [ADDRESS]


@pytest.mark.parametrize(("payload", "days"), [(raw_detail(), 1), (raw_detail(description=None, short=""), 7), (raw_detail(closed=True), 7)])
def test_independent_detail_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict, days: int) -> None:
    """Active, empty and closed observations use their documented deadlines."""
    calls = provider(monkeypatch, payload)
    fetch(tmp_path)
    fetch(tmp_path, now=NOW + datetime.timedelta(days=days, seconds=-1))
    assert len(calls) == 1
    fetch(tmp_path, now=NOW + datetime.timedelta(days=days))
    assert len(calls) == 2


@pytest.mark.parametrize(("outcome", "delay"), [("not_found", 86400), ("unavailable", 3600), ("unavailable", 7200)])
def test_negative_retry_and_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, delay: int) -> None:
    """Cache misses and failures do not hammer the provider and later recover."""
    calls = provider(monkeypatch, None, outcome, delay)
    assert fetch(tmp_path).metadata is None
    assert fetch(tmp_path, now=NOW + datetime.timedelta(seconds=delay - 1)).metadata is None
    assert len(calls) == 1
    provider(monkeypatch, raw_detail())
    assert fetch(tmp_path, now=NOW + datetime.timedelta(seconds=delay)).metadata is not None


def test_detail_survives_listing_failure_and_delisting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep historical payloads and refresh requested addresses after removal."""
    listed = {"vaults": [{"chain": {"id": 1}, "address": ADDRESS}, {"chain": {"id": 1}, "address": SECOND}], "hasNextPage": False}
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: listed)
    provider(monkeypatch, raw_detail())
    assert fetch(tmp_path).listed is True
    # A partial catalogue can legitimately remove one vault without losing text.
    listed["vaults"] = listed["vaults"][1:]
    provider(monkeypatch, raw_detail(description="Historical strategy."))
    result = fetch(tmp_path, now=NOW + datetime.timedelta(days=1))
    assert result.listed is False
    assert result.metadata["description"] == "Historical strategy."
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: None)
    provider(monkeypatch, None, "unavailable")
    result = fetch(tmp_path, now=NOW + datetime.timedelta(days=2))
    assert result.metadata["description"] == "Historical strategy."
    assert result.listed is False


def test_listing_outage_does_not_block_first_exact_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown membership remains distinct from a successful detail response."""
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: None)
    provider(monkeypatch, raw_detail())
    result = fetch(tmp_path)
    assert result.listed is None
    assert result.metadata is not None


def test_legacy_upgrade_preserves_eligibility_during_outage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed prior eligibility without treating new arbitrary details as listed."""
    file = metadata._get_cache_file(tmp_path, 1)
    parsed = metadata._parse_vault_detail(raw_detail())
    file.write_text(json.dumps({ADDRESS: parsed}))
    timestamp = (NOW - datetime.timedelta(days=2)).replace(tzinfo=datetime.UTC).timestamp()
    os.utime(file, (timestamp, timestamp))
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: None)
    provider(monkeypatch, None, "unavailable")
    result = fetch(tmp_path)
    assert result.listed is True
    assert result.metadata == parsed
    assert json.loads(file.read_text())["schema_version"] == 2


def test_successful_empty_listing_has_own_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A detail write cannot keep the catalogue perpetually fresh."""
    calls = []

    def listing(*args: object, **kwargs: object) -> dict:
        """Record a successful empty catalogue observation."""
        calls.append(True)
        return {"vaults": [], "hasNextPage": False}

    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", listing)
    provider(monkeypatch, raw_detail())
    fetch(tmp_path)
    fetch(tmp_path, SECOND, NOW + datetime.timedelta(hours=23))
    fetch(tmp_path, SECOND, NOW + datetime.timedelta(days=1))
    assert len(calls) == 2


@pytest.mark.parametrize("content", [b"\xff", b"[]", b'{"schema_version":99}', b'{"not-an-address":{}}'])
def test_corrupt_or_unknown_cache_fails_hard(tmp_path: Path, content: bytes) -> None:
    """Reject incompatible cache data without resetting historical metadata."""
    file = metadata._get_cache_file(tmp_path, 1)
    file.write_bytes(content)
    with pytest.raises(RuntimeError, match="Could not parse Lagoon"):
        metadata._load_cached_vaults(file, 1)
    assert file.read_bytes() == content


def test_two_threads_share_one_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same-process concurrent misses coalesce into one exact-address request."""
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def detail(*args: object) -> metadata.LagoonDetailResponse:
        """Hold the first request until both callers have been submitted."""
        calls.append(True)
        entered.set()
        assert release.wait(10)
        return metadata.LagoonDetailResponse(raw_detail(), "success")

    monkeypatch.setattr(metadata, "_fetch_detail_response", detail)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(fetch, tmp_path)
        assert entered.wait(10)
        second = pool.submit(fetch, tmp_path)
        release.set()
        assert first.result().metadata == second.result().metadata
    assert len(calls) == 1


def test_independent_writers_merge_and_memory_sees_disk_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Network completions for different addresses never lose another writer."""
    barrier = threading.Barrier(2)

    def detail(chain_id: int, address: str, api_base_url: str) -> metadata.LagoonDetailResponse:
        """Synchronise network work outside the cross-process writer lock."""
        barrier.wait(timeout=10)
        return metadata.LagoonDetailResponse(raw_detail(address), "success")

    monkeypatch.setattr(metadata, "_fetch_detail_response", detail)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda address: fetch(tmp_path, address), [ADDRESS, SECOND]))
    assert all(r.metadata for r in results)
    file = metadata._get_cache_file(tmp_path, 1)
    assert len(metadata._load_cached_vaults(file, 1)) == 2
    data = json.loads(file.read_text())
    data["details"][ADDRESS]["metadata"]["description"] = "Written by another process."
    file.write_text(json.dumps(data))
    assert fetch(tmp_path).metadata["description"] == "Written by another process."


def test_older_response_cannot_overwrite_newer_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a newer process commit while an older HTTP attempt is in flight."""

    def detail(chain_id: int, address: str, api_base_url: str) -> metadata.LagoonDetailResponse:
        """Commit a newer observation before returning an older response."""

        def newer(data: dict) -> None:
            data["details"][ADDRESS] = {"outcome": "success", "attempt_at": (NOW + datetime.timedelta(seconds=1)).isoformat(), "retry_at": (NOW + datetime.timedelta(days=1)).isoformat(), "fetched_at": NOW.isoformat(), "metadata": metadata._parse_vault_detail(raw_detail(description="Newer."))}

        metadata._commit_cache(metadata._get_cache_file(tmp_path, 1), 1, newer)
        return metadata.LagoonDetailResponse(raw_detail(description="Older."), "success")

    monkeypatch.setattr(metadata, "_fetch_detail_response", detail)
    assert fetch(tmp_path).metadata["description"] == "Newer."


@pytest.mark.parametrize(("status", "body", "expected"), [(200, raw_detail(), "success"), (200, raw_detail(SECOND), "unavailable"), (200, {**raw_detail(), "chain": {"id": 8453}}, "unavailable"), (200, [], "unavailable"), (404, {}, "not_found"), (429, {}, "unavailable"), (500, {}, "unavailable")])
def test_http_identity_and_status_validation(monkeypatch: pytest.MonkeyPatch, status: int, body: object, expected: str) -> None:
    """Exercise the actual HTTP decoder, including wrong-chain provider results."""
    response = Response()
    response.status_code = status
    response.headers["Retry-After"] = "7200"
    response._content = json.dumps(body).encode()
    monkeypatch.setattr(metadata.requests, "get", lambda *args, **kwargs: response)
    result = metadata._fetch_detail_response(1, ADDRESS)
    assert result.outcome == expected
    if status == 429:
        assert result.retry_after == 7200
    if expected != "success":
        assert result.raw is None


def _commit_in_process(directory: str, address: str, barrier: object) -> None:
    """Seed separate process caches before racing atomic commits.

    :param directory: Shared disposable cache directory.
    :param address: Distinct target for this process.
    :param barrier: Spawn-safe rendezvous before either writer commits.
    """
    file = metadata._get_cache_file(Path(directory), 1)
    metadata._read_cache(file, 1)
    barrier.wait(timeout=30)

    def update(data: dict) -> None:
        """Merge this process's address into the latest committed envelope."""
        data["details"][address] = {"metadata": metadata._parse_vault_detail(raw_detail(address)), "fetched_at": NOW.isoformat(), "attempt_at": NOW.isoformat(), "retry_at": (NOW + datetime.timedelta(days=1)).isoformat(), "outcome": "success"}

    metadata._commit_cache(file, 1, update)


def test_process_writers_do_not_lose_cached_rows(tmp_path: Path) -> None:
    """Separate processes with stale memory merge both address payloads.

    Network is absent: only the production cache reader/writer and file lock
    cross this process boundary, so failure cannot be hidden by HTTP fixtures.
    """

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [context.Process(target=_commit_in_process, args=(str(tmp_path), address, barrier)) for address in (ADDRESS, SECOND)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=45)
            assert process.exitcode == 0
        assert len(metadata._load_cached_vaults(metadata._get_cache_file(tmp_path, 1), 1)) == 2
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()


@pytest.mark.parametrize("outcome", ["not_found", "unavailable"])
def test_failed_refresh_retains_last_good_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    """A 404 or outage after a real observation cannot delete historical text."""
    provider(monkeypatch, raw_detail())
    original = fetch(tmp_path)
    provider(monkeypatch, None, outcome)
    result = fetch(tmp_path, now=NOW + datetime.timedelta(days=1))
    assert result.metadata == original.metadata
    assert result.outcome == outcome
    assert result.fetched_at == original.fetched_at


@pytest.mark.parametrize("patch", [{"description": []}, {"curators": "not a list"}, {"state": "Closed"}])
def test_malformed_success_is_not_cached(monkeypatch: pytest.MonkeyPatch, patch: dict) -> None:
    """A successful HTTP status cannot hide an incompatible detail schema."""
    response = Response()
    response.status_code = 200
    response._content = json.dumps(raw_detail() | patch).encode()
    monkeypatch.setattr(metadata.requests, "get", lambda *args, **kwargs: response)
    assert metadata._fetch_detail_response(1, ADDRESS).outcome == "unavailable"


def test_forced_bulk_refresh_respects_failure_deadlines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit freshness overrides cannot hammer a rate-limited deployment."""
    monkeypatch.setattr(metadata, "LAGOON_PRIVATE_VAULT_ALLOWLIST", frozenset({(1, ADDRESS)}))
    calls = provider(monkeypatch, None, "unavailable", 7200)
    fetch(tmp_path)
    metadata.fetch_lagoon_vaults_for_chain(1, cache_path=tmp_path, now_=NOW, max_cache_duration=datetime.timedelta(0))
    assert calls == [ADDRESS]


def test_bulk_custom_success_freshness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain the public bulk caller's explicit cache-age override."""
    monkeypatch.setattr(metadata, "LAGOON_PRIVATE_VAULT_ALLOWLIST", frozenset({(1, ADDRESS)}))
    calls = provider(monkeypatch, raw_detail())
    metadata.fetch_lagoon_vaults_for_chain(1, cache_path=tmp_path, now_=NOW)
    metadata.fetch_lagoon_vaults_for_chain(1, cache_path=tmp_path, now_=NOW + datetime.timedelta(hours=25), max_cache_duration=datetime.timedelta(days=2))
    assert calls == [ADDRESS]
    metadata.fetch_lagoon_vaults_for_chain(1, cache_path=tmp_path, now_=NOW + datetime.timedelta(days=2), max_cache_duration=datetime.timedelta(days=2))
    assert calls == [ADDRESS, ADDRESS]


def test_legacy_file_is_read_only_for_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed v2 without overwriting the plain map still read by older releases."""
    legacy = tmp_path / "lagoon_vaults_chain_1.json"
    legacy.write_text(json.dumps({ADDRESS: metadata._parse_vault_detail(raw_detail())}))
    timestamp = (NOW - datetime.timedelta(days=2)).replace(tzinfo=datetime.UTC).timestamp()
    os.utime(legacy, (timestamp, timestamp))
    before = legacy.read_bytes()
    provider(monkeypatch, None, "unavailable")
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: None)
    assert fetch(tmp_path).listed is True
    assert legacy.read_bytes() == before
    assert legacy.stat().st_mtime == timestamp
    assert metadata._get_cache_file(tmp_path, 1) != legacy
    assert json.loads(metadata._get_cache_file(tmp_path, 1).read_text())["schema_version"] == 2


@pytest.mark.parametrize("curators", [[{"id": "unlabelled", "name": None}], None])
def test_nullable_curators_do_not_hide_valid_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, curators: list | None) -> None:
    """Accept optional curator identity independently of strategy availability."""
    response = Response()
    response.status_code = 200
    response._content = json.dumps(raw_detail() | {"curators": curators}).encode()
    monkeypatch.setattr(metadata.requests, "get", lambda *args, **kwargs: response)
    result = fetch(tmp_path)
    assert result.outcome == "success"
    assert result.metadata["description"] == "Long strategy."
    assert all(not curator["name"] for curator in result.metadata["curators"])


def test_rate_limit_defers_other_addresses_on_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One 429 stops the rest of a bulk pass, including forced refreshes."""
    monkeypatch.setattr(metadata, "LAGOON_PRIVATE_VAULT_ALLOWLIST", frozenset({(1, ADDRESS), (1, SECOND)}))
    calls = []
    response = Response()
    response.status_code = 429
    response.headers["Retry-After"] = "7200"

    def request(*args: object, **kwargs: object) -> Response:
        """Record provider traffic rather than bypassing the HTTP decoder."""
        calls.append(True)
        return response

    monkeypatch.setattr(metadata.requests, "get", request)
    metadata.fetch_lagoon_vaults_for_chain(1, tmp_path, now_=NOW)
    result = fetch(tmp_path, SECOND)
    assert result.metadata is None
    assert result.outcome == "unavailable"
    assert result.retry_at == (NOW + datetime.timedelta(hours=2)).isoformat()
    metadata.fetch_lagoon_vaults_for_chain(1, tmp_path, now_=NOW, max_cache_duration=datetime.timedelta(0))
    assert len(calls) == 1
    provider(monkeypatch, raw_detail(SECOND))
    assert fetch(tmp_path, SECOND, NOW + datetime.timedelta(hours=2)).metadata is not None


def test_valid_global_listing_can_remove_last_vault_on_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty chain within a populated global catalogue is not an outage."""
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: {"vaults": [{"chain": {"id": 1}, "address": ADDRESS}], "hasNextPage": False})
    provider(monkeypatch, raw_detail())
    assert fetch(tmp_path).listed is True
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: {"vaults": [{"chain": {"id": 8453}, "address": SECOND}], "hasNextPage": False})
    result = fetch(tmp_path, now=NOW + datetime.timedelta(days=1))
    assert result.listed is False
    assert result.metadata["description"] == "Long strategy."


def test_invalid_other_chain_row_cannot_invalidate_membership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid global catalogue is not proof a chain lost its last product."""
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: {"vaults": [{"chain": {"id": 1}, "address": ADDRESS}], "hasNextPage": False})
    provider(monkeypatch, raw_detail())
    assert fetch(tmp_path).listed is True
    monkeypatch.setattr(metadata, "_fetch_vault_listing_page", lambda *args, **kwargs: {"vaults": [{"chain": {"id": 8453}, "address": "invalid"}], "hasNextPage": False})
    assert fetch(tmp_path, now=NOW + datetime.timedelta(days=1)).listed is True
