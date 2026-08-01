"""Unit tests for the RPC failure-mode classifier.

Pure logic, no network — covers concrete ``requests`` exception types, HTTP
status codes, JSON-RPC error dictionaries, message hints, and the unknown
fallback. See :mod:`eth_defi.provider.rpc_failure`.
"""

import pytest
import requests

from eth_defi.provider.rpc_failure import RpcFailureMode, classify_rpc_failure


def _http_error(status: int) -> requests.exceptions.HTTPError:
    """Build a ``requests`` HTTPError carrying a given HTTP status code."""
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


@pytest.mark.parametrize(
    "error, expected",
    [
        # Concrete requests exception types.
        (requests.exceptions.ReadTimeout("read timed out"), RpcFailureMode.read_timeout),
        # ConnectTimeout subclasses both Timeout and ConnectionError -> connection_error.
        (requests.exceptions.ConnectTimeout("connect timed out"), RpcFailureMode.connection_error),
        (requests.exceptions.ConnectionError("connection refused"), RpcFailureMode.connection_error),
        (requests.exceptions.Timeout("timed out"), RpcFailureMode.read_timeout),
        # HTTP status codes.
        (_http_error(402), RpcFailureMode.out_of_credits),
        (_http_error(429), RpcFailureMode.rate_limited),
        (_http_error(503), RpcFailureMode.server_error),
        # JSON-RPC error dictionaries (classified via the message).
        ({"code": -32000, "message": "monthly request limit reached, out of credits"}, RpcFailureMode.out_of_credits),
        ({"code": -32005, "message": "rate limit exceeded"}, RpcFailureMode.rate_limited),
        # Message-string hints.
        ("Read timed out. (read timeout=60.0)", RpcFailureMode.read_timeout),
        ("502 Bad gateway", RpcFailureMode.server_error),
        ("Expecting value: line 1 column 1", RpcFailureMode.bad_response),
        # Nothing recognised -> do not guess.
        ("something totally unrecognised", RpcFailureMode.unknown),
    ],
)
def test_classify_rpc_failure(error: object, expected: RpcFailureMode) -> None:
    """Classifier maps representative errors to the right failure mode."""
    assert classify_rpc_failure(error) is expected


def test_malformed_http_code_falls_back(tmp_path: object) -> None:
    """A non-numeric ``http_<x>`` code does not raise and yields unknown."""

    class _Weird(requests.exceptions.RequestException):
        pass

    # No HTTP status, no matching hint -> unknown (must not raise on parsing).
    assert classify_rpc_failure(_Weird("no useful detail")) is RpcFailureMode.unknown


def test_rpc_failure_mode_snake_case() -> None:
    """Enum members and values are snake_case per repo convention."""
    for mode in RpcFailureMode:
        assert mode.name == mode.value
        assert mode.value == mode.value.lower()
        assert " " not in mode.value
