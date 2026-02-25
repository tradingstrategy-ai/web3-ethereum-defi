"""TokenSniffer integration tests.

.. note::

    These tests are currently skipped because the TokenSniffer API key has expired
    and the service is no longer actively supported by the project.
"""

import pytest

pytestmark = pytest.mark.skip(reason="TokenSniffer API key expired and service no longer supported")


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


def test_token_sniffer_404(tmp_path):
    """Check we correctly get 404 exception from TokenSniffer."""

    db_file = tmp_path / "test.sqlite"

    sniffer = CachedTokenSniffer(
        db_file,
        TOKENSNIFFER_API_KEY,
    )

    with pytest.raises(TokenSnifferError) as e:
        # fake address
        _ = sniffer.fetch_token_info(1, "0xfff59322be8e50d80a4b868d186cc5ab148543a")

    assert e.value.status_code == 400
    assert e.value.address == "0xfff59322be8e50d80a4b868d186cc5ab148543a"
