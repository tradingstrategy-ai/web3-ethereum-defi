"""Test Hypersync server metadata."""

from eth_defi.hypersync.server import get_hypersync_server, is_hypersync_supported_chain


def test_robinhood_hypersync_server():
    """Robinhood Chain has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(4663) is True
    assert get_hypersync_server(4663) == "https://4663.hypersync.xyz"


def test_arc_testnet_hypersync_server():
    """Arc Testnet has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(5042002) is True
    assert get_hypersync_server(5042002) == "https://arc-testnet.hypersync.xyz"


def test_tempo_hypersync_server():
    """Tempo has a configured Hypersync endpoint."""

    assert is_hypersync_supported_chain(4217) is True
    assert get_hypersync_server(4217) == "https://tempo.hypersync.xyz"
