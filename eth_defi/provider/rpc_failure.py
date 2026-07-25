"""Classify upstream JSON-RPC failures into a small, stable set of modes.

Turns a heterogeneous provider error — an HTTP status, a JSON-RPC error body, a
:mod:`requests` timeout, or a raw exception — into one obvious
:class:`RpcFailureMode` so test output and CI logs name *why* an archive RPC
call failed ("out of credits", "rate limited", "read timeout") instead of a
generic stack trace.

This exists because shared Anvil mainnet-fork tests fail environmentally when
their upstream archive provider is exhausted or throttled under concurrent load
(see :mod:`eth_defi.testing.anvil_fork_pool` and
:file:`docs/README-test-suite-performance.md`). When that happens, an operator
reading the CI log needs to see the provider and the failure mode immediately,
not a bare ``eth_chainId`` ``RuntimeError``.

Classification is best-effort and deliberately conservative: an unrecognised
failure returns :attr:`RpcFailureMode.unknown` rather than being force-fit into a
specific mode, so the label never misleads.
"""

import enum
import logging

import requests

from eth_defi.provider.rpcdb import normalise_rpc_error

logger = logging.getLogger(__name__)


class RpcFailureMode(enum.Enum):
    """Coarse, stable classification of an upstream JSON-RPC failure.

    Values are snake_case strings safe to log, aggregate and grep in CI output.
    """

    #: Provider rejected the request for billing/quota reasons (HTTP 402, or a
    #: message mentioning credits/compute units/quota/plan limits).
    out_of_credits = "out_of_credits"

    #: Provider throttled the request (HTTP 429, "too many requests", throttling).
    rate_limited = "rate_limited"

    #: The request exceeded its read timeout (``requests`` ReadTimeout / Timeout).
    read_timeout = "read_timeout"

    #: Could not establish or keep a connection (refused, reset, DNS, pool).
    connection_error = "connection_error"

    #: Upstream returned a 5xx (bad gateway, service unavailable, gateway timeout).
    server_error = "server_error"

    #: A response arrived but was malformed / not valid JSON-RPC.
    bad_response = "bad_response"

    #: Nothing matched — do not guess.
    unknown = "unknown"


#: Lowercased message substrings mapped to a failure mode. Kept tight to avoid
#: mislabelling (e.g. "credits" is RPC-billing specific here; broad words like
#: "exceeded" are intentionally excluded because they also appear in benign
#: block-range messages).
_MESSAGE_HINTS: tuple[tuple[tuple[str, ...], RpcFailureMode], ...] = (
    (("out of credits", "out of compute", "compute units", "credit", "quota", "capacity limit", "monthly limit", "usage limit", "plan limit", "upgrade your plan", "payment required", "billing"), RpcFailureMode.out_of_credits),
    (("rate limit", "too many requests", "throttl", "429"), RpcFailureMode.rate_limited),
    (("read timed out", "timed out", "timeout"), RpcFailureMode.read_timeout),
    (("connection refused", "connection reset", "connection aborted", "max retries", "failed to establish", "name resolution", "connection pool"), RpcFailureMode.connection_error),
    (("bad gateway", "service unavailable", "gateway timeout", "internal server error", "502", "503", "504"), RpcFailureMode.server_error),
    (("expecting value", "not valid json", "jsondecodeerror", "invalid json"), RpcFailureMode.bad_response),
)

#: HTTP status codes mapped to a failure mode (from ``http_<status>`` codes).
_HTTP_STATUS_MODES: dict[int, RpcFailureMode] = {
    402: RpcFailureMode.out_of_credits,
    429: RpcFailureMode.rate_limited,
    500: RpcFailureMode.server_error,
    502: RpcFailureMode.server_error,
    503: RpcFailureMode.server_error,
    504: RpcFailureMode.server_error,
}


def classify_rpc_failure(error: BaseException | dict | str) -> RpcFailureMode:
    """Classify an upstream JSON-RPC failure into a :class:`RpcFailureMode`.

    Inspection order: concrete ``requests`` timeout/connection types first (the
    most reliable signal), then the normalised HTTP status code, then message
    substrings. Falls back to :attr:`RpcFailureMode.unknown` when nothing
    matches — the label is only ever set when there is positive evidence.

    :param error:
        A raised exception, a JSON-RPC error dictionary, or an error string.

    :return:
        The best-effort failure mode.
    """
    # Most reliable: concrete requests exception types.
    if isinstance(error, (requests.exceptions.ReadTimeout, requests.exceptions.Timeout)):
        return RpcFailureMode.read_timeout
    if isinstance(error, requests.exceptions.ConnectionError):
        return RpcFailureMode.connection_error

    # Normalised code: ``http_<status>`` or a JSON-RPC / class-name code.
    code, message = normalise_rpc_error(error) if not isinstance(error, str) else ("unknown", error)
    if code.startswith("http_"):
        try:
            status = int(code.removeprefix("http_"))
        except ValueError:
            status = None
        if status is not None and status in _HTTP_STATUS_MODES:
            return _HTTP_STATUS_MODES[status]

    # Fall back to message substring hints.
    haystack = f"{code} {message}".lower()
    for needles, mode in _MESSAGE_HINTS:
        if any(needle in haystack for needle in needles):
            return mode

    return RpcFailureMode.unknown
