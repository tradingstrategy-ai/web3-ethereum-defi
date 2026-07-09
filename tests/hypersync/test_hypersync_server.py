"""Test Hypersync server metadata."""

from eth_defi.hypersync.server import get_hypersync_server, is_hypersync_supported_chain


def test_robinhood_hypersync_server():
    """Robinhood Chain has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(4663) is True
    assert get_hypersync_server(4663) == "https://4663.hypersync.xyz"
