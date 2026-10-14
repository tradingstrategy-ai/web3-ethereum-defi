"""Unit tests for GRVT API-key session authentication."""

from unittest.mock import Mock

import pytest
from requests import Session

from eth_defi.grvt.session import authenticate_grvt_api_key_session


def test_authenticate_session_uses_account_id_from_login_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use GRVT's API-key-login account ID instead of caller configuration."""
    session = Session()
    response = Mock()
    response.headers = {"X-Grvt-Account-Id": "api-key-account"}
    response.raise_for_status = Mock()
    post = Mock(return_value=response)
    monkeypatch.setattr(session, "post", post)

    authenticate_grvt_api_key_session(session, "https://edge.grvt.io", "api-key", timeout=12.0)

    assert session.headers["X-Grvt-Account-Id"] == "api-key-account"
    post.assert_called_once_with(
        "https://edge.grvt.io/auth/api_key/login",
        json={"api_key": "api-key"},
        timeout=12.0,
    )
    response.raise_for_status.assert_called_once_with()


def test_authenticate_session_requires_login_account_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail clearly if GRVT accepts a login but omits its account header."""
    session = Session()
    response = Mock()
    response.headers = {}
    response.raise_for_status = Mock()
    monkeypatch.setattr(session, "post", Mock(return_value=response))

    with pytest.raises(ValueError, match="X-Grvt-Account-Id"):
        authenticate_grvt_api_key_session(session, "https://edge.grvt.io", "api-key")
