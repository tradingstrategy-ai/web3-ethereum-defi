"""Tests for ERC-4626 vault classification.

Tests for create_probe_calls() chain filtering functionality.
"""

from eth_defi.erc_4626.classification import (
    CHAIN_RESTRICTED_PROBES,
    _should_yield_probe,
    create_probe_calls,
)


# Chain IDs for reference
ETHEREUM_MAINNET = 1
ARBITRUM = 42161
BASE = 8453
POLYGON = 137
BSC = 56
MONAD = 143
MANTLE = 5000


def test_chain_probe_filtering():
    """Test that create_probe_calls() correctly filters probes based on chain_id.

    This test verifies:
    - Core probes are always present regardless of chain
    - Protocol-specific probes are filtered based on deployment chains
    - Disabled protocols (empty sets) never yield probes
    """
    test_address = "0x0000000000000000000000000000000000000001"

    # Verify CHAIN_RESTRICTED_PROBES data is well-formed
    for func_name, chain_ids in CHAIN_RESTRICTED_PROBES.items():
        assert isinstance(chain_ids, set), f"{func_name} should be a set"
        for chain_id in chain_ids:
            assert isinstance(chain_id, int), f"{func_name} has non-integer chain_id: {chain_id}"

    # Test _should_yield_probe helper
    # No chain_id means all probes yield (for the helper function itself)
    assert _should_yield_probe("getPerformanceFeeData", None) is True
    assert _should_yield_probe("strategy", None) is True
    assert _should_yield_probe("unknown_func", None) is True

    # Unrestricted probes always yield
    assert _should_yield_probe("convertToShares", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("name", ARBITRUM) is True

    # IPOR restricted to Ethereum, Base, Arbitrum
    assert _should_yield_probe("getPerformanceFeeData", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("getPerformanceFeeData", BASE) is True
    assert _should_yield_probe("getPerformanceFeeData", ARBITRUM) is True
    assert _should_yield_probe("getPerformanceFeeData", POLYGON) is False

    # Accountable restricted to Monad
    assert _should_yield_probe("strategy", MONAD) is True
    assert _should_yield_probe("queue", MONAD) is True
    assert _should_yield_probe("strategy", ETHEREUM_MAINNET) is False

    # Brink restricted to Mantle
    assert _should_yield_probe("strategist", MANTLE) is True
    assert _should_yield_probe("strategist", ETHEREUM_MAINNET) is False

    # Disabled protocols (empty sets) never yield
    assert _should_yield_probe("outputToLp0Route", ETHEREUM_MAINNET) is False  # Baklava
    assert _should_yield_probe("agent", ARBITRUM) is False  # Astrolab

    # Test create_probe_calls filtering
    # Accountable probes on Monad
    probes_monad = list(create_probe_calls([test_address], chain_id=MONAD))
    func_names_monad = [p.func_name for p in probes_monad]
    assert "strategy" in func_names_monad
    assert "queue" in func_names_monad

    # Accountable probes excluded on Ethereum
    probes_eth = list(create_probe_calls([test_address], chain_id=ETHEREUM_MAINNET))
    func_names_eth = [p.func_name for p in probes_eth]
    assert "strategy" not in func_names_eth
    assert "queue" not in func_names_eth

    # Core probes always present
    assert "name" in func_names_eth
    assert "convertToShares" in func_names_eth

    # Polygon should have fewer probes than Monad (which has Accountable probes)
    probes_polygon = list(create_probe_calls([test_address], chain_id=POLYGON))
    assert len(probes_polygon) < len(probes_monad)
