"""Focused tests for shared Morpho quotas and provider cooldowns."""

# Subprocesses execute fixed Python snippets against temporary local state.
# ruff: noqa: S404, S603, PLR2004

import datetime
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests
from filelock import FileLock

from eth_defi.erc_4626.vault_protocol.morpho.session import MorphoSession, create_morpho_session, parse_retry_after
from eth_defi.rate_limit import clear_sqlite_rate_limit_databases
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import NOT_IN_MORPHO_API, VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase


@dataclass(slots=True)
class _Clock:
    """UTC epoch and monotonic clock advanced by fake sleeps."""

    #: Fixed UTC epoch seconds, advanced without real sleeps.
    now: float = 1794484800

    def read(self) -> float:
        """Return the current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance without wall-clock waiting.

        :param seconds: Requested sleep.
        :return: None.
        """
        self.now += seconds


@pytest.fixture
def session(tmp_path: Path) -> MorphoSession:
    """Provide isolated quota/cooldown state with a controllable clock.

    HTTP remains unpatched so each test explicitly selects its transport.

    :param tmp_path: Isolated state directory.
    :return: Session whose waits advance fake time.
    """
    result = create_morpho_session(tmp_path)
    clock = _Clock()
    result._state.clock = clock.read
    result._state.monotonic = clock.read
    result._state.sleeper = clock.sleep
    return result


def _response(status: int = 200, retry_after: str | None = None) -> requests.Response:
    """Create an HTTP response without network I/O.

    :param status: HTTP status to exercise.
    :param retry_after: Optional provider cooldown header.
    :return: Requests response suitable for the session read path.
    """
    response = requests.Response()
    response.status_code = status
    response._content = b'{"data": {}}'
    response._content_consumed = True
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return response


@pytest.mark.parametrize("value,delay", [(None, 600), ("bad", 600), ("-1", 600), ("0", 600), ("nan", 600), ("inf", 600), ("1e300", 600), ("604800", 604800), ("60", 60)])
def test_retry_after(value: str | None, delay: int) -> None:
    """Keep documented seven-day bans and default malformed headers safely.

    :param value: Provider header under test.
    :param delay: Expected delay in seconds.
    """
    assert parse_retry_after(value, 1700000000) == 1700000000 + delay


def test_retry_after_http_date() -> None:
    """Support UTC dates without accepting expired provider deadlines."""
    now = datetime.datetime(2026, 11, 12, 12, tzinfo=datetime.UTC).timestamp()
    assert parse_retry_after("Thu, 12 Nov 2026 12:01:00 GMT", now) == now + 60
    assert parse_retry_after("Thu, 12 Nov 2026 11:00:00 GMT", now) == now + 600


def test_quota_paces_attempts(session: MorphoSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """A third actual HTTP attempt waits for the shared second bucket.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    """
    sent = []

    def post(_url: str, **kwargs: object) -> requests.Response:
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == 30
        sent.append(session._state.clock())
        return _response()

    monkeypatch.setattr(session._http, "post", post)
    for _ in range(3):
        assert session.fetch_response({"query": "{ __typename }"}) is not None
    assert sent[1] == sent[0]
    assert sent[2] - sent[0] >= 1


def test_cooldown_survives_restart_and_quota_clear(session: MorphoSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A real persisted ban stops new sessions and survives startup clearing.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    :param caplog: Captured transition warnings.
    """
    calls = []

    def post(*_args: object, **_kwargs: object) -> requests.Response:
        calls.append(1)
        return _response(429, "604800")

    monkeypatch.setattr(session._http, "post", post)
    assert session.fetch_response({}) is None
    assert len(calls) == 1
    session._state.record_cooldown("1")
    deadline = session._state.read_retry_at()
    assert deadline == session._state.clock() + 604800
    assert session.fetch_response({}) is None
    assert len(calls) == 1
    clear_sqlite_rate_limit_databases(session._state.directory)
    assert session._state.read_retry_at() == deadline
    # Read the same file in a separate process, rather than reusing globals.
    code = "from pathlib import Path; from eth_defi.erc_4626.vault_protocol.morpho.session import create_morpho_session; import sys; state = create_morpho_session(Path(sys.argv[1]))._state; state.clock = lambda: float(sys.argv[2]); state.record_cooldown('1'); assert state.cooling_down()"
    child = subprocess.run([sys.executable, "-c", code, str(session._state.directory), str(session._state.clock())], check=True, capture_output=True, timeout=15)
    assert b"persisted provider cooldown" in child.stderr
    assert session._state.read_retry_at() == deadline
    assert caplog.text.count("Morpho HTTP 429") == 1


def test_queued_worker_observes_cooldown(session: MorphoSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ban recorded while quota is exhausted prevents the queued request.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    """
    assert session._state.acquire(60) == 0
    assert session._state.acquire(60) == 0
    monkeypatch.setattr(session._state, "sleeper", lambda _seconds: session._state.record_cooldown("600"))
    monkeypatch.setattr(session._http, "post", lambda *_args, **_kwargs: pytest.fail("queued request bypassed cooldown"))
    assert session.fetch_response({}) is None


def test_invalid_state_recovers_after_repair(session: MorphoSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Broken state defers reads, warns once, and is re-read after repair.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    :param caplog: Captured local-state warnings.
    """
    path = session._state.cooldown_path
    path.write_text("not JSON")
    for _ in range(3):
        assert session.fetch_response({}) is None
    assert caplog.text.count("cooldown read") == 1
    path.unlink()
    monkeypatch.setattr(session._http, "post", lambda *_args, **_kwargs: _response())
    assert session.fetch_response({}) is not None


def test_cooldown_expiry(session: MorphoSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired persisted ban permits a normally paced request.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    """
    session._state.record_cooldown("60")
    session._state.sleeper(61)
    monkeypatch.setattr(session._http, "post", lambda *_args, **_kwargs: _response())
    assert session.fetch_response({}) is not None
    assert session._state.cooldown_path.exists()


def test_failed_cooldown_write_stops_other_sessions(session: MorphoSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Unsaved bans still cover all worker sessions in this process.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    :param caplog: Captured persistence warning.
    """

    def deny_write(*_args: object, **_kwargs: object) -> None:
        message = "read-only directory"
        raise PermissionError(message)

    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.morpho.session.atomic_write", deny_write)
    session._state.record_cooldown("604800")
    other = create_morpho_session(session._state.directory, timeout=1)
    assert other is not session
    monkeypatch.setattr(other._http, "post", lambda *_args, **_kwargs: pytest.fail("unsaved ban bypassed"))
    assert other.fetch_response({}) is None
    assert "other processes may not see this ban" in caplog.text
    assert not any(record.exc_info for record in caplog.records)


def test_queue_budget(session: MorphoSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Repeated bucket waits cannot exceed the local queue budget forever.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    :param caplog: Captured once-per-process deferral warning.
    """
    session.queue_timeout = 3
    monkeypatch.setattr(type(session._state), "acquire", lambda _self, remaining: min(1, remaining))
    monkeypatch.setattr(session._http, "post", lambda *_args, **_kwargs: pytest.fail("queue budget bypassed"))
    started = session._state.monotonic()
    assert session.fetch_response({}) is None
    assert session._state.monotonic() - started == 3
    assert session.fetch_response({}) is None
    assert caplog.text.count("quota queue budget") == 1


def test_quota_database_failure_releases_lock(session: MorphoSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLite errors defer HTTP without stranding the cross-process lock.

    Rebuilding the failed limiter after local operator repair restores reads.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    """
    path = session._state.directory / "rate-limit.sqlite"
    path.write_bytes(b"not a SQLite database")
    assert session.fetch_response({}) is None
    with FileLock(str(path) + ".lock", timeout=0.1):
        path.unlink()
    monkeypatch.setattr(session._http, "post", lambda *_args, **_kwargs: _response())
    assert session.fetch_response({}) is not None


@pytest.mark.parametrize("status", [500, 503, 401, 302])
def test_no_hidden_retries(session: MorphoSession, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """Non-429 responses return directly for existing result classification.

    :param session: Isolated paced session.
    :param monkeypatch: Pytest patch fixture.
    :param status: Non-success status.
    """
    calls = []

    def post(*_args: object, **_kwargs: object) -> requests.Response:
        calls.append(1)
        return _response(status)

    monkeypatch.setattr(session._http, "post", post)
    assert session.fetch_response({}).status_code == status
    assert len(calls) == 1
    assert all(adapter.max_retries.total == 0 for adapter in session._http.adapters.values())


def test_threads_share_quota(tmp_path: Path) -> None:
    """Worker-local sessions share the limiter and database.

    :param tmp_path: Isolated real-clock state directory.
    """

    def send(_index: int) -> float:
        worker = create_morpho_session(tmp_path)
        worker._http.post = lambda *_args, **_kwargs: _response()
        assert worker.fetch_response({}) is not None
        return time.monotonic()

    with ThreadPoolExecutor(max_workers=4) as pool:
        sent = sorted(pool.map(send, range(4)))
    assert sent[2] - sent[0] >= 0.9


def test_processes_share_quota(tmp_path: Path) -> None:
    """Separate processes coordinate through the mounted lock, not /tmp.

    :param tmp_path: State and barrier shared by two child interpreters.
    """
    code = """
import json, sys, time
from pathlib import Path
from requests import Response
from eth_defi.erc_4626.vault_protocol.morpho.session import create_morpho_session
root, index = Path(sys.argv[1]), sys.argv[2]
session = create_morpho_session(root)
session._http.post = lambda *args, **kwargs: Response()
(root / ('ready-' + index)).touch()
deadline = time.monotonic() + 10
while not (root / 'go').exists():
    assert time.monotonic() < deadline
    time.sleep(0.01)
sent = []
for _ in range(2):
    assert session.fetch_response({}) is not None
    sent.append(time.time())
(root / ('sent-' + index)).write_text(json.dumps(sent))
"""
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(index)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for index in range(2)]
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / f"ready-{index}").exists() for index in range(2)):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        (tmp_path / "go").touch()
        for child in children:
            _, error = child.communicate(timeout=15)
            assert child.returncode == 0, error.decode()
        sent = sorted(value for index in range(2) for value in json.loads((tmp_path / f"sent-{index}").read_text()))
        assert sent[2] - sent[0] >= 0.9
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize("flag", [VaultFlag.morpho_issues, VaultFlag.not_in_morpho_api])
def test_transient_merge_preserves_warning_data(flag: VaultFlag) -> None:
    """A provider cooldown must not turn a previously flagged vault clean.

    Old rows may lack the new outcome key. Onchain data and maintained text
    continue to update, and a subsequent successful clean read clears warnings.

    :param flag: API-derived flag that must survive a transient read.
    """
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    warnings = {"vault_warnings": [{"type": "short_timelock", "level": "RED"}], "market_warnings": []}
    existing = {"Name": "Known", "Denomination": "USDC", "NAV": 1, "_morpho_offchain_data": warnings, "_manager_name": "Curator", "_flags": {flag}, "_notes": "Previously observed warning", "_description": "Old maintained description"}
    fresh = existing | {"NAV": 2, "_morpho_offchain_data": None, "_manager_name": None, "_flags": set(), "_notes": None, "_description": "New maintained description", "_morpho_api_status": "transient_error"}
    database = VaultDatabase(rows={spec: existing})
    database._merge_rows({spec: fresh})
    retained = database.rows[spec]
    assert retained["_morpho_offchain_data"] == warnings
    assert retained["_manager_name"] == "Curator"
    assert retained["_flags"] == {flag}
    expected_note = NOT_IN_MORPHO_API if flag is VaultFlag.not_in_morpho_api else "Morpho has flagged this vault with the following issues: short_timelock"
    assert retained["_notes"] == expected_note
    assert retained["NAV"] == 2
    assert retained["_description"] == "New maintained description"
    noted = fresh | {"_notes": "New maintained note", "_flags": {VaultFlag.unofficial}}
    database._merge_rows({spec: noted})
    database._merge_rows({spec: noted})
    assert database.rows[spec]["_flags"] == {flag, VaultFlag.unofficial}
    assert database.rows[spec]["_notes"].count("New maintained note") == 1
    assert database.rows[spec]["_notes"] == "New maintained note"
    database._merge_rows({spec: fresh | {"_morpho_api_status": "found", "_morpho_offchain_data": {"vault_warnings": [], "market_warnings": []}}})
    assert database.rows[spec]["_flags"] == set()
    assert database.rows[spec]["_notes"] is None
