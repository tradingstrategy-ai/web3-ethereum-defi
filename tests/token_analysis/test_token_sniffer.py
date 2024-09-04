"""TokenSniffer integration tests."""
import os

import pytest

from eth_defi.token_analysis.tokensniffer import CachedTokenSniffer, is_tradeable_token

TOKENSNIFFER_API_KEY = os.environ.get("TOKENSNIFFER_API_KEY")
pytestmark = pytest.mark.skipif(not TOKENSNIFFER_API_KEY, reason="This test needs TOKENSNIFFER_API_KEY set")


def test_token_sniffer_cached(tmp_path):
    """Check TokenSniffer API works"""

    db_file = tmp_path / "test.sqlite"

    sniffer = CachedTokenSniffer(
        db_file,
        TOKENSNIFFER_API_KEY,
    )
    # Ponzio the Cat
    # https://tradingstrategy.ai/trading-view/ethereum/tokens/0x873259322be8e50d80a4b868d186cc5ab148543a
    data = sniffer.fetch_token_info(1, "0x873259322be8e50d80a4b868d186cc5ab148543a")
    assert data["cached"] is False

    data = sniffer.fetch_token_info(1, "0x873259322be8e50d80a4b868d186cc5ab148543a")
    assert data["cached"] is True

    assert not is_tradeable_token(data)

    info = sniffer.get_diagnostics()
    assert type(info) == str
