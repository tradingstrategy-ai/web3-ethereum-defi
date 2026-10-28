"""Real HTTP coverage for the settlement deadline, without moving any funds.

A mocked clock cannot establish that DNS/response/socket waits are bounded.
The local server supplies responses that stay active while exceeding the
budget, reproducing the weakness of an ordinary socket inactivity timeout.
"""

import json
import subprocess  # noqa: S404 -- constructs a mocked process result; no process is started by this unit test
import threading
import time
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from eth_defi.hyperliquid.api import fetch_user_vault_equities, wait_for_vault_deposit_confirmation
from eth_defi.hyperliquid.session import create_hyperliquid_session

USER = "0x0000000000000000000000000000000000000001"
VAULT = "0x0000000000000000000000000000000000000002"
SCHEDULING_TOLERANCE_SECONDS = 0.6
LIVE_REQUEST_BUDGET_SECONDS = 10


@pytest.mark.parametrize("request_timeout,remaining", [(0.1, 5.0), (10.0, 0.05)])
def test_bounded_request_uses_shorter_timeout(request_timeout: float, remaining: float) -> None:
    """Respect both a single read's timeout and the shared observation budget.

    1. Prepare a session with a fixed clock and a successful process response.
    2. Read with either the request timeout or remaining deadline being shorter.
    3. Check curl and the process receive the same smaller allowance.
    """
    # 1. Only process execution and time are mocked: this isolates the budget
    # calculation, while the tests below establish the real socket bound.
    result = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"[]\n200")
    with create_hyperliquid_session() as session, patch("eth_defi.hyperliquid.session.time.monotonic", return_value=100.0), patch("eth_defi.hyperliquid.session.subprocess.run", return_value=result) as transfer:
        # 2. A new per-request allowance must never outlive the overall wait.
        session.post_info({"type": "userVaultEquities", "user": USER}, timeout=request_timeout, deadline=100.0 + remaining)
        # 3. The two independent timeout mechanisms share one elapsed budget.
        expected = min(request_timeout, remaining)
        assert transfer.call_args.kwargs["timeout"] == pytest.approx(expected)
        arguments = transfer.call_args.args[0]
        assert float(arguments[arguments.index("--max-time") + 1]) == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["success", "headers_stall", "body_stall", "trickle", "429", "502"])
def test_confirmation_obeys_real_http_deadline(mode: str, tmp_path: Path) -> None:
    """Bound a complete response rather than each socket's inactivity period.

    1. Start a real local Info server with a successful, stalled or throttled response.
    2. Verify through the actual public request transport with a short deadline.
    3. Check prompt success or bounded failure, one request and unchanged retry settings.
    """
    # 1. A local provider makes socket timing reproducible without requiring
    # private keys or relying on the availability of Hyperliquid's API.
    stopped = threading.Event()
    requests_seen: list[dict] = []
    body = json.dumps([{"vaultAddress": VAULT, "equity": "50.0", "lockedUntilTimestamp": 1893456000000}]).encode()

    class InfoHandler(BaseHTTPRequestHandler):
        """Serve enough of the public Info endpoint to exercise real transfers."""

        def log_message(self, message_format: str, *args: object) -> None:
            """Suppress HTTP access noise; assertions report any failed transfer."""

        def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler dispatches to this method name
            """Keep slow responses alive so inactivity alone cannot end them."""
            requests_seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if mode == "headers_stall":
                stopped.wait(2)
            status = int(mode) if mode in {"429", "502"} else HTTPStatus.OK
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            if status != HTTPStatus.OK:
                # Existing outage recovery can honour this for minutes. The
                # advisory path must expose the response without any retry.
                self.send_header("Retry-After", "600")
            self.end_headers()
            if mode == "body_stall":
                stopped.wait(2)
            try:
                if mode == "trickle":
                    for byte in body:
                        if stopped.is_set():
                            break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        stopped.wait(0.03)
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Closing an expired response is the expected client action.
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), InfoHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    session = create_hyperliquid_session(api_url=f"http://127.0.0.1:{server.server_port}", rate_limit_db_path=tmp_path / "rate.sqlite")
    adapter = session.get_adapter(session.api_url)
    retries_before = adapter.max_retries.total

    # 2. The caller's clock remains real: only the external provider is
    # substituted. In particular, do not patch the network call or its timer.
    # Success needs enough process-startup headroom on a loaded CI runner.
    # Slow responses keep the short budget so a retry cannot hide in it.
    budget = 2.0 if mode == "success" else 0.3
    started = time.monotonic()
    try:
        with patch.object(session, "post") as normal_request:
            if mode == "success":
                equity = wait_for_vault_deposit_confirmation(session, USER, VAULT, Decimal("50"), timeout=budget, poll_interval=0.01)
                assert equity.equity == Decimal("50")
            else:
                expected_error = requests.HTTPError if mode in {"429", "502"} else requests.Timeout
                with pytest.raises(expected_error):
                    wait_for_vault_deposit_confirmation(session, USER, VAULT, Decimal("50"), timeout=budget, poll_interval=0.01)
            elapsed = time.monotonic() - started
            normal_request.assert_not_called()

        # 3. A small tolerance covers process scheduling, not another request
        # or a fresh socket timeout. Shared download retry settings survive.
        assert elapsed < (budget if mode == "success" else SCHEDULING_TOLERANCE_SECONDS)
        assert requests_seen == [{"type": "userVaultEquities", "user": USER}]
        assert adapter.max_retries.total == retries_before
        assert session.rotation_count == 0
        assert session.request_count == 1
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        session.close()


def test_bounded_equity_read_from_live_hyperliquid() -> None:
    """Exercise the real public provider with the same deadline transport.

    1. Create a mainnet session without credentials or transaction capability.
    2. Fetch the HLP vault account's equity positions through the bounded path.
    3. Confirm a decoded response and a complete transfer within the budget.
    """
    # 1. This is a public read of a known protocol account; it cannot deposit,
    # redeem, sign or change any production state.
    with create_hyperliquid_session() as session:
        started = time.monotonic()
        # 2. The deadline covers the whole transfer, including DNS and body.
        positions = fetch_user_vault_equities(session, "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303", deadline=started + LIVE_REQUEST_BUDGET_SECONDS)
        # 3. An empty list is valid for this account; decoding and successful
        # HTTP completion establish that the provider integration works.
        assert isinstance(positions, list)
        assert time.monotonic() - started < LIVE_REQUEST_BUDGET_SECONDS
