"""Test the retrying HTTP sessions for Lighter SDK calls and signed submissions.

The official SDK is not an eth-defi dependency, so these tests drive the sessions
with scripted stand-ins and a local HTTP server. trade-executor, which installs
the SDK, runs the same behaviour through a real ``SignerClient``.
"""

import asyncio
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
import requests

import eth_defi.lighter.session as session_module
from eth_defi.lighter.session import (
    LIGHTER_SDK_SLOW_TRANSACTION_POLICY,
    LIGHTER_SEND_TX_PATH,
    LighterSDKRetryPolicy,
    LighterSDKSession,
    create_lighter_session,
    create_lighter_submission_session,
)
from eth_defi.lighter.testing import find_lighter_session_bypasses

BASE_URL = "https://lighter.example"
SEND_TX_URL = f"{BASE_URL}{LIGHTER_SEND_TX_PATH}"
READ_URL = f"{BASE_URL}/api/v1/account?auth=secret-token"
REPOSITORY_ROOT = Path(__file__).parents[2]


class ScriptedResponse:
    """The parts of the SDK's ``RESTResponse`` the session uses."""

    def __init__(self, status: int, retry_after: str | None = None, read_error: Exception | None = None):
        self.status = status
        self.retry_after = retry_after
        self.read_error = read_error
        self.data = None

    def getheader(self, name: str) -> str | None:
        """Return the scripted ``Retry-After`` header."""
        assert name == "Retry-After"
        return self.retry_after

    async def read(self) -> bytes:
        """Read the body, or fail like a dropped connection."""
        if self.read_error is not None:
            raise self.read_error
        self.data = b"{}"
        return self.data


class ScriptedRESTClient:
    """Stand-in for the SDK's ``RESTClientObject`` that plays back a script."""

    def __init__(self, *steps: ScriptedResponse | Exception):
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def request(self, method: str, url: str, headers: dict | None = None, body: Any = None, post_params: Any = None, _request_timeout: float | None = None) -> ScriptedResponse:
        """Record one attempt, mutating headers as the real client does."""
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body, "post_params": post_params, "timeout": _request_timeout})
        if headers is not None:
            headers.pop("Content-Type", None)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def close(self) -> None:
        """Record cleanup."""
        self.closed = True


def create_session(rest_client: ScriptedRESTClient, policy: LighterSDKRetryPolicy = LighterSDKRetryPolicy()) -> tuple[LighterSDKSession, list[float]]:
    """Create a session on a virtual clock and return it with its recorded waits."""
    now = [0.0]
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    return LighterSDKSession(rest_client, policy, clock=lambda: now[0], sleep=sleep), waits


@pytest.mark.timeout(30)
def test_send_tx_is_resent_unchanged_after_rate_limit(caplog: pytest.LogCaptureFixture) -> None:
    """A rate-limited transaction is resent with identical signed bytes and logged safely.

    1. Script a 429, a 405 and an accepted transaction.
    2. Send a form-encoded transaction carrying a header the client mutates.
    3. Assert every attempt had the same body and headers, and that waits grew.
    """
    # 1. Script the rate limit and the accepted response.
    rest_client = ScriptedRESTClient(ScriptedResponse(429), ScriptedResponse(405), ScriptedResponse(200))
    session, waits = create_session(rest_client)
    post_params = [("tx_type", "9"), ("tx_info", '{"signed": "payload"}')]

    # 2. Send one transaction.
    response = asyncio.run(session.request("POST", SEND_TX_URL, headers={"Content-Type": "multipart/form-data"}, post_params=post_params))

    # 3. Three attempts, all identical, and no signed payload in the logs.
    assert response.status == 200
    assert response.data == b"{}"
    assert len(rest_client.calls) == 3
    assert all(call == rest_client.calls[0] for call in rest_client.calls)
    assert rest_client.calls[0]["headers"] == {"Content-Type": "multipart/form-data"}
    assert rest_client.calls[0]["timeout"] == 30.0
    assert 1.0 <= waits[0] <= 1.25
    assert 2.0 <= waits[1] <= 2.5
    assert "signed" not in caplog.text


@pytest.mark.timeout(30)
@pytest.mark.parametrize("step", [ScriptedResponse(503), ScriptedResponse(500), TimeoutError(), aiohttp.ServerDisconnectedError()])
def test_send_tx_outcome_unknown_is_not_replayed(step: ScriptedResponse | Exception) -> None:
    """A 5xx or transport failure on ``sendTx`` is ambiguous and reaches the caller at once."""
    rest_client = ScriptedRESTClient(step)
    session, waits = create_session(rest_client)
    if isinstance(step, Exception):
        with pytest.raises(type(step)):
            asyncio.run(session.request("POST", SEND_TX_URL))
    else:
        assert asyncio.run(session.request("POST", SEND_TX_URL)) is step
    assert len(rest_client.calls) == 1
    assert waits == []


@pytest.mark.timeout(30)
@pytest.mark.parametrize("path", ["/api/v1/sendTxBatch", "/api/v1/fastwithdraw", "/api/v1/tokens/create"])
def test_other_mutations_are_never_retried(path: str) -> None:
    """Only ``sendTx`` has been audited, so other POST paths get no retry, even on 429."""
    rest_client = ScriptedRESTClient(ScriptedResponse(429))
    session, waits = create_session(rest_client)
    assert asyncio.run(session.request("POST", f"{BASE_URL}{path}")).status == 429
    assert waits == []


@pytest.mark.timeout(30)
def test_reads_survive_outage_and_truncated_body(caplog: pytest.LogCaptureFixture) -> None:
    """A read rides out statuses, timeouts and a body that breaks after the headers.

    1. Script each transient failure and then a good response.
    2. Assert the good response is returned with its body already cached.
    3. Assert the query string, which can hold a token, stays out of the logs.
    """
    # 1. Script the failures.
    good = ScriptedResponse(200)
    rest_client = ScriptedRESTClient(
        ScriptedResponse(503),
        TimeoutError(),
        ScriptedResponse(200, read_error=aiohttp.ClientPayloadError("truncated")),
        ScriptedResponse(429),
        good,
    )
    session, waits = create_session(rest_client)

    # 2. The read succeeds.
    assert asyncio.run(session.request("GET", READ_URL)) is good
    assert good.data == b"{}"
    assert len(waits) == 4

    # 3. The token is not logged.
    assert "secret-token" not in caplog.text
    assert "/api/v1/account" in caplog.text


@pytest.mark.timeout(30)
def test_backoff_caps_and_honours_retry_after() -> None:
    """Waits double to the cap, and ``Retry-After`` raises a wait but never lowers it."""
    policy = LighterSDKRetryPolicy(initial_delay=10.0, max_delay=20.0, read_deadline=10_000.0)
    rest_client = ScriptedRESTClient(
        ScriptedResponse(429),
        ScriptedResponse(429),
        ScriptedResponse(429),
        ScriptedResponse(429, retry_after="90"),
        ScriptedResponse(429, retry_after="1"),
        ScriptedResponse(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"),
        ScriptedResponse(200),
    )
    session, waits = create_session(rest_client, policy)
    asyncio.run(session.request("GET", READ_URL))
    assert 10.0 <= waits[0] <= 12.5
    assert waits[1:3] == [20.0, 20.0]
    assert waits[3] == 90.0
    assert waits[4:6] == [20.0, 20.0]


@pytest.mark.timeout(30)
def test_deadlines_end_retries() -> None:
    """Live transactions give up in 30 seconds, and sub-account creation outlasts the firewall.

    1. A transaction under the default policy stops before its deadline and
       returns the final 429 for the SDK to raise.
    2. The same limit under the slow policy keeps going past the 60-second cooldown.
    3. A read that keeps failing re-raises its last error.
    """
    # 1. Default transaction deadline.
    rest_client = ScriptedRESTClient(*[ScriptedResponse(429) for _ in range(20)])
    session, waits = create_session(rest_client)
    assert asyncio.run(session.request("POST", SEND_TX_URL)).status == 429
    assert sum(waits) <= 30.0

    # 2. Slow policy.
    rest_client = ScriptedRESTClient(*[ScriptedResponse(429) for _ in range(20)])
    session, waits = create_session(rest_client, LIGHTER_SDK_SLOW_TRANSACTION_POLICY)
    assert asyncio.run(session.request("POST", SEND_TX_URL)).status == 429
    assert 60.0 < sum(waits) <= 300.0

    # 3. Read errors.
    rest_client = ScriptedRESTClient(*[TimeoutError() for _ in range(20)])
    session, waits = create_session(rest_client)
    with pytest.raises(TimeoutError):
        asyncio.run(session.request("GET", READ_URL))
    assert 60.0 < sum(waits) <= 130.0


@pytest.mark.timeout(30)
def test_close_reaches_the_sdk_client() -> None:
    """Closing the session closes the wrapped client and its connector."""
    rest_client = ScriptedRESTClient()
    session, _waits = create_session(rest_client)
    asyncio.run(session.close())
    assert rest_client.closed is True


class SubmissionHandler(BaseHTTPRequestHandler):
    """Local ``sendTx`` endpoint replaying a scripted list of behaviours."""

    #: Scripted ``(status, headers)`` tuples, or ``None`` to drop the connection.
    script: list[tuple[int, dict[str, str]] | None] = []

    #: Seconds to wait before answering.
    response_delay: float = 0.0

    #: Bodies received.
    received: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        """Serve the next scripted behaviour."""
        self.received.append(self.rfile.read(int(self.headers["Content-Length"])))
        step = self.script.pop(0)
        time.sleep(self.response_delay)
        if step is None:
            self.close_connection = True
            self.connection.close()
            return
        status, headers = step
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Keep test output clean."""


@pytest.fixture()
def submission_server() -> Iterator[str]:
    """Run the scripted endpoint on a local port and return its base URL."""
    SubmissionHandler.script = []
    SubmissionHandler.received = []
    SubmissionHandler.response_delay = 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), SubmissionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def post_submission(base_url: str, deadline: float = 5.0) -> requests.Response:
    """Post one signed transaction the way ``LighterPoolExecutor.submit()`` does."""
    with create_lighter_submission_session(base_url, deadline=deadline) as session:
        return session.post(f"{session.api_url}{LIGHTER_SEND_TX_PATH}", data={"tx_type": 9, "tx_info": "signed"}, allow_redirects=False)


@pytest.mark.timeout(60)
def test_submission_session_retries_only_rate_limits(submission_server: str) -> None:
    """A signed transaction is resent after a rate limit and never after anything ambiguous.

    1. A 429 and a 405 are retried with the same bytes.
    2. A 503 with ``Retry-After`` is returned after one attempt.
    3. A dropped connection raises after one attempt.
    4. A rate limit that outlasts the deadline returns the final 429.
    """
    # 1. Rate limits.
    SubmissionHandler.script = [(429, {}), (405, {}), (200, {})]
    assert post_submission(submission_server).status_code == 200
    assert len(SubmissionHandler.received) == 3
    assert len(set(SubmissionHandler.received)) == 1

    # 2. An ambiguous status.
    SubmissionHandler.received.clear()
    SubmissionHandler.script = [(503, {"Retry-After": "0"})]
    assert post_submission(submission_server).status_code == 503
    assert len(SubmissionHandler.received) == 1

    # 3. A dropped connection.
    SubmissionHandler.received.clear()
    SubmissionHandler.script = [None]
    with pytest.raises(requests.ConnectionError):
        post_submission(submission_server)
    assert len(SubmissionHandler.received) == 1

    # 4. Exhaustion.
    SubmissionHandler.received.clear()
    SubmissionHandler.script = [(429, {}) for _ in range(10)]
    assert post_submission(submission_server, deadline=2.0).status_code == 429
    assert len(SubmissionHandler.received) == 2


@pytest.mark.timeout(60)
def test_submission_deadline_covers_slow_refusals(submission_server: str) -> None:
    """A slow answer cannot lead to a replay after the deadline.

    1. A refusal that uses most of the deadline is returned without a retry.
    2. An answer later than the deadline times out after one attempt, and the
       caller's reconciliation by hash decides the outcome.
    """
    # 1. A slow refusal.
    SubmissionHandler.response_delay = 0.3
    SubmissionHandler.script = [(429, {}), (200, {})]
    assert post_submission(submission_server, deadline=1.0).status_code == 429
    assert len(SubmissionHandler.received) == 1

    # 2. Later than the deadline.
    SubmissionHandler.received.clear()
    SubmissionHandler.response_delay = 1.0
    SubmissionHandler.script = [(200, {})]
    with pytest.raises(requests.Timeout):
        post_submission(submission_server, deadline=0.2)
    assert len(SubmissionHandler.received) == 1


@pytest.mark.timeout(60)
def test_submission_is_not_replayed_after_oversleeping(submission_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process that wakes after the deadline does not send the transaction again.

    Only the session's clock is replaced, so the local server is not slowed down.
    """
    oversleeping_clock = SimpleNamespace(monotonic=time.monotonic, sleep=lambda _delay: time.sleep(2.1))
    monkeypatch.setattr(session_module, "time", oversleeping_clock)
    SubmissionHandler.script = [(429, {}), (200, {})]
    assert post_submission(submission_server, deadline=2.0).status_code == 429
    assert len(SubmissionHandler.received) == 1


@pytest.mark.timeout(30)
def test_read_session_treats_405_as_rate_limit(tmp_path: Path) -> None:
    """Reads retry Lighter's documented 405 and outlast its firewall cooldown."""
    session = create_lighter_session(rate_limit_db_path=tmp_path / "limit.sqlite")
    retry = session.get_adapter(session.api_url).max_retries
    waits = [0.5 * 2**number for number in range(1, retry.total)]
    assert 405 in retry.status_forcelist
    assert 429 in retry.status_forcelist
    assert sum(waits) > 60


@pytest.mark.timeout(30)
def test_no_lighter_code_bypasses_the_sessions(tmp_path: Path) -> None:
    """Every Lighter call stays behind a session, so a new caller cannot skip backoff.

    1. Check that the scan catches each kind of bypass, on one line and on several.
    2. Check that it accepts the calls we do want.
    3. Scan eth-defi's Lighter code.
    """
    # 1. Check that the scan catches each kind of bypass.
    bad = tmp_path / "bad.py"
    bad.write_text(
        """
client.withdraw(amount=1, api_key_index=4)
client.create_market_order_limited_slippage(
    market_index=1,
    api_key_index=4,
)
client.check_client()
lighter.SignerClient(url="x")
lighter.ApiClient()
requests.get("https://x")
requests.Session()
"""
    )
    assert len(find_lighter_session_bypasses([bad])) == 7

    # 2. Check that it accepts the calls we do want.
    good = tmp_path / "good.py"
    good.write_text(
        """
client.withdraw(amount=1)
client.withdraw(amount=1, nonce=3, api_key_index=4)
client.sign_withdraw(1, nonce=3, api_key_index=4)
zk.functions.withdraw(1, 2)
session.get("https://x")
create_lighter_sub_accounts(api_key_index=4)
"""
    )
    assert find_lighter_session_bypasses([good]) == []

    # 3. Scan eth-defi's Lighter code. sdk.py holds the factories.
    paths = [*(REPOSITORY_ROOT / "eth_defi" / "lighter").glob("*.py"), *(REPOSITORY_ROOT / "scripts" / "lighter").glob("*.py"), *(REPOSITORY_ROOT / "scripts" / "lagoon").glob("*lighter*.py")]
    assert find_lighter_session_bypasses([path for path in paths if path.name != "sdk.py"]) == []
