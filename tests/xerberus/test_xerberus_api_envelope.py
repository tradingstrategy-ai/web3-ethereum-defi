"""Offline tests for Xerberus API envelope unwrapping."""

import pytest

from eth_defi.xerberus.api import _unwrap_success_data
from eth_defi.xerberus.errors import XerberusAPIError


def test_unwrap_success_list():
    data = _unwrap_success_data({"status": "success", "data": [{"id": "a"}]}, endpoint="test")
    assert data == [{"id": "a"}]


def test_unwrap_auth_middleware_error():
    with pytest.raises(XerberusAPIError, match="Missing Email"):
        _unwrap_success_data({"error": "Missing Email or API key"}, endpoint="test")


def test_unwrap_validation_error():
    with pytest.raises(XerberusAPIError, match="bad type"):
        _unwrap_success_data({"status": "error", "message": "bad type"}, endpoint="test")


def test_unwrap_application_error():
    with pytest.raises(XerberusAPIError, match="upstream"):
        _unwrap_success_data(
            {"status": "error", "error": {"message": "upstream", "code": "E1"}},
            endpoint="test",
        )


def test_unwrap_missing_data():
    with pytest.raises(XerberusAPIError, match="missing data"):
        _unwrap_success_data({"status": "success"}, endpoint="test")
