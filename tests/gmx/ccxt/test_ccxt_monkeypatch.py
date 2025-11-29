"""Test CCXT monkeypatch functionality for GMX."""

import ccxt

from eth_defi.gmx.ccxt.monkeypatch import gmx_ccxt_patch, is_patched, patch_ccxt, unpatch_ccxt


def test_patch_ccxt_exchanges_list():
    """Test that exchanges list is properly maintained."""
    # Clean start
    unpatch_ccxt()

    # Get initial count
    initial_count = len(ccxt.exchanges)
    assert "gmx" not in ccxt.exchanges

    # Patch
    patch_ccxt()
    assert is_patched()
    patched_count = len(ccxt.exchanges)

    # Should have one more exchange
    assert patched_count == initial_count + 1
    assert "gmx" in ccxt.exchanges

    # List should be sorted
    assert ccxt.exchanges == sorted(ccxt.exchanges)

    # Unpatch
    unpatch_ccxt()
    final_count = len(ccxt.exchanges)

    # Should be back to original
    assert final_count == initial_count
    assert "gmx" not in ccxt.exchanges


def test_context_manager():
    """Test the context manager for temporary patching."""

    # Clean start
    unpatch_ccxt()

    # Before context
    assert not is_patched()
    assert "gmx" not in ccxt.exchanges

    # Inside context
    with gmx_ccxt_patch():
        assert is_patched()
        assert "gmx" in ccxt.exchanges
        assert hasattr(ccxt, "gmx")

    # After context
    assert not is_patched()
    assert "gmx" not in ccxt.exchanges

    try:
        with gmx_ccxt_patch():
            assert is_patched()
            raise ValueError("Test exception")
    except ValueError:
        pass

    # Should still be unpatched after exception
    assert not is_patched()
    assert "gmx" not in ccxt.exchanges


def test_ensure_patched():
    """Test the ensure_patched convenience function."""
    import ccxt

    from eth_defi.gmx.ccxt.monkeypatch import ensure_patched, is_patched, unpatch_ccxt

    # Clean start
    unpatch_ccxt()
    assert not is_patched()

    # First call should patch
    ensure_patched()
    assert is_patched()
    assert "gmx" in ccxt.exchanges

    # Second call should be safe (no-op)
    ensure_patched()
    assert is_patched()

    # Clean up
    unpatch_ccxt()


def test_gmx_instantiation(web3):
    """Test that GMX can be instantiated through CCXT after patching."""

    # Patch CCXT
    patch_ccxt()

    try:
        # Create GMX instance using CCXT
        exchange = ccxt.gmx({"rpcUrl": web3.provider.endpoint_uri})

        # Should be a GMX instance
        assert exchange.id == "gmx"
        assert exchange.name == "GMX"

        # Should have CCXT methods
        assert hasattr(exchange, "fetch_markets")
        assert hasattr(exchange, "fetch_ticker")
        assert hasattr(exchange, "create_order")

        # Fetch markets
        markets = exchange.fetch_markets()

        # Should have markets
        assert isinstance(markets, list)
        assert len(markets) > 0

        # Each market should have expected structure
        market = markets[0]
        assert "symbol" in market
        assert "id" in market
        assert "base" in market
        assert "quote" in market

    finally:
        unpatch_ccxt()


def test_multiple_patch_unpatch_cycles():
    """Test that multiple patch/unpatch cycles work correctly."""

    # Clean start
    unpatch_ccxt()

    for i in range(3):
        # Should not be patched
        assert not is_patched()
        assert "gmx" not in ccxt.exchanges

        # Patch
        patch_ccxt()
        assert is_patched()
        assert "gmx" in ccxt.exchanges

        # Unpatch
        unpatch_ccxt()

    # Final state should be unpatched
    assert not is_patched()
