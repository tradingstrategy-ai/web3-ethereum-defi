"""Tests for GMX market data disk cache.

This test suite tests the GMXMarketCache class functionality including
TTL support, JSON encoding/decoding, and cache entry management.
"""

import tempfile
import time
from pathlib import Path

import pytest

from eth_defi.gmx.cache import GMXMarketCache


def test_cache_creation():
    """Test that cache can be created and initialised."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache_file = cache_dir / "test_markets_arbitrum.sqlite"

        cache = GMXMarketCache(filename=cache_file)

        assert cache is not None

        # Write some data to ensure cache works
        test_data = {"test": "data"}
        cache.set_markets(data=test_data, loading_mode="test", ttl=3600)

        # Verify data was stored
        retrieved = cache.get_markets(loading_mode="test")
        assert retrieved == test_data

        # File should exist after write
        assert cache_file.exists()


def test_cache_get_cache_helper():
    """Test get_cache class method."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)

        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
            disabled=False,
        )

        assert cache is not None

        # Write some data to ensure cache works
        test_data = {"test": "data"}
        cache.set_markets(data=test_data, loading_mode="test", ttl=3600)

        # Verify data was stored
        retrieved = cache.get_markets(loading_mode="test")
        assert retrieved == test_data

        # File should exist after write
        assert (cache_dir / "markets_arbitrum.sqlite").exists()


def test_cache_disabled():
    """Test that cache returns None when disabled."""
    cache = GMXMarketCache.get_cache(
        chain="arbitrum",
        disabled=True,
    )

    assert cache is None


def test_cache_set_get_markets():
    """Test storing and retrieving markets data from cache."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
        )

        # Create test market data
        test_markets = {
            "ETH/USDC:USDC": {
                "symbol": "ETH/USDC:USDC",
                "base": "ETH",
                "quote": "USDC",
            },
            "BTC/USDC:USDC": {
                "symbol": "BTC/USDC:USDC",
                "base": "BTC",
                "quote": "USDC",
            },
        }

        # Store markets with long TTL
        cache.set_markets(
            data=test_markets,
            loading_mode="rest_api",
            ttl=3600,  # 1 hour
        )

        # Retrieve markets
        retrieved = cache.get_markets(
            loading_mode="rest_api",
            check_expiry=True,
        )

        assert retrieved is not None
        assert retrieved == test_markets


def test_cache_loading_mode_separation():
    """Test that different loading modes have separate cache entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
        )

        # Store different data for different loading modes
        rest_api_data = {"mode": "rest_api", "markets": ["ETH", "BTC"]}
        graphql_data = {"mode": "graphql", "markets": ["ETH"]}

        cache.set_markets(data=rest_api_data, loading_mode="rest_api", ttl=3600)
        cache.set_markets(data=graphql_data, loading_mode="graphql", ttl=3600)

        # Retrieve and verify separation
        rest_retrieved = cache.get_markets(loading_mode="rest_api")
        graphql_retrieved = cache.get_markets(loading_mode="graphql")

        assert rest_retrieved != graphql_retrieved
        assert rest_retrieved == rest_api_data
        assert graphql_retrieved == graphql_data


def test_cache_expiry():
    """Test that expired cache entries return None when check_expiry=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
        )

        # Store markets with very short TTL
        test_markets = {"ETH/USDC:USDC": {"symbol": "ETH/USDC:USDC"}}

        cache.set_markets(
            data=test_markets,
            loading_mode="rest_api",
            ttl=1,  # 1 second
        )

        # Immediately retrieve - should work
        retrieved = cache.get_markets(
            loading_mode="rest_api",
            check_expiry=True,
        )
        assert retrieved == test_markets

        # Wait for expiry
        time.sleep(1.5)

        # Try to retrieve after expiry - should return None
        expired_retrieve = cache.get_markets(
            loading_mode="rest_api",
            check_expiry=True,
        )
        assert expired_retrieve is None

        # But can still retrieve if we skip expiry check
        no_check_retrieve = cache.get_markets(
            loading_mode="rest_api",
            check_expiry=False,
        )
        assert no_check_retrieve == test_markets


def test_cache_apy_storage():
    """Test storing and retrieving APY data from cache."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
        )

        # Create test APY data
        test_apy = {
            "0xmarket1": {"apy": 0.15, "baseApy": 0.15, "bonusApr": 0},
            "0xmarket2": {"apy": 0.25, "baseApy": 0.20, "bonusApr": 0.05},
        }

        # Store APY with TTL
        cache.set_apy(
            data=test_apy,
            period="30d",
            ttl=300,  # 5 minutes
        )

        # Retrieve APY
        retrieved = cache.get_apy(
            period="30d",
            check_expiry=True,
        )

        assert retrieved is not None
        assert retrieved == test_apy


def test_cache_apy_period_separation():
    """Test that different APY periods have separate cache entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
        )

        # Store different APY data for different periods
        apy_7d = {"0xmarket1": {"apy": 0.10}}
        apy_30d = {"0xmarket1": {"apy": 0.15}}

        cache.set_apy(data=apy_7d, period="7d", ttl=300)
        cache.set_apy(data=apy_30d, period="30d", ttl=300)

        # Retrieve and verify separation
        retrieved_7d = cache.get_apy(period="7d")
        retrieved_30d = cache.get_apy(period="30d")

        assert retrieved_7d != retrieved_30d
        assert retrieved_7d == apy_7d
        assert retrieved_30d == apy_30d


def test_cache_clear_expired():
    """Test clearing expired cache entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
        )

        # Add some entries with different TTLs
        cache.set_markets(
            data={"market1": "data1"},
            loading_mode="rest_api",
            ttl=1,  # Short TTL
        )
        cache.set_markets(
            data={"market2": "data2"},
            loading_mode="graphql",
            ttl=3600,  # Long TTL
        )

        # Wait for first entry to expire
        time.sleep(1.5)

        # Clear expired entries
        removed = cache.clear_expired()

        # Should have removed at least one entry
        assert removed >= 1

        # Long TTL entry should still be there
        remaining = cache.get_markets(loading_mode="graphql", check_expiry=False)
        assert remaining is not None


def test_cache_json_encoding():
    """Test that complex data structures are properly JSON encoded/decoded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)
        cache = GMXMarketCache.get_cache(
            chain="arbitrum",
            cache_dir=cache_dir,
        )

        # Create complex nested structure
        complex_data = {
            "markets": [
                {
                    "symbol": "ETH/USDC:USDC",
                    "info": {
                        "market_token": "0x123",
                        "leverage": [1.1, 50.0],
                        "flags": {"active": True, "synthetic": False},
                    },
                }
            ],
            "metadata": {"count": 1, "timestamp": 1234567890},
        }

        # Store and retrieve
        cache.set_markets(data=complex_data, loading_mode="rest_api", ttl=3600)
        retrieved = cache.get_markets(loading_mode="rest_api")

        # Should be identical after round-trip
        assert retrieved == complex_data
