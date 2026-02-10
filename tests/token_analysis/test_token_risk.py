"""Token Risk integration tests."""

import os

import pytest

from eth_defi.token_analysis.tokenrisk import CachedTokenRisk
from eth_defi.token_analysis.tokenrisk import is_tradeable_token

TOKEN_RISK_API_KEY = os.environ.get("TOKEN_RISK_API_KEY")
pytestmark = pytest.mark.skipif(not TOKEN_RISK_API_KEY, reason="This test needs TOKEN_RISK_API_KEY set")


def test_token_risk_cached(tmp_path):
    """Check TokenSniffer API works"""

    db_file = tmp_path / "test.sqlite"

    token_risk = CachedTokenRisk(
        TOKEN_RISK_API_KEY,
        db_file,
    )

    # COW
    data = token_risk.fetch_token_info(56, "0x7aaaa5b10f97321345acd76945083141be1c5631")
    assert data["cached"] is False
    assert data["score"] in (0, 77, 78, 79, 80)  # Formula changed

    assert not is_tradeable_token(data)
    info = token_risk.get_diagnostics()
    assert type(info) == str

    # Caching works
    token_risk = CachedTokenRisk(
        TOKEN_RISK_API_KEY,
        db_file,
    )
    data = token_risk.fetch_token_info(56, "0x7aaaa5b10f97321345acd76945083141be1c5631")
    assert data["cached"] is True
