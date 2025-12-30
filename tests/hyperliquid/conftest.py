"""Shared pytest fixtures for Hyperliquid tests.

This module provides common fixtures used across all Hyperliquid test modules.
"""

import datetime

import pytest

from eth_defi.hyperliquid.session import create_hyperliquid_session


@pytest.fixture(scope="module")
def hyperliquid_sample_vault() -> str:
    """Test vault address (Trading Strategy - IchiV3 LS).

    https://app.hyperliquid.xyz/vaults/0x3df9769bbbb335340872f01d8157c779d73c6ed0
    """
    return "0x3df9769bbbb335340872f01d8157c779d73c6ed0"


@pytest.fixture(scope="module")
def hyperliquid_test_period_start() -> datetime.datetime:
    """Fixed test time range start."""
    return datetime.datetime(2025, 12, 1)


@pytest.fixture(scope="module")
def hyperliquid_test_period_end() -> datetime.datetime:
    """Fixed test time range end."""
    return datetime.datetime(2025, 12, 28)


@pytest.fixture(scope="module")
def session():
    """Create a shared HTTP session for all tests in this module."""
    return create_hyperliquid_session()
