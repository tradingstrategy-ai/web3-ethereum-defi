"""Tests for ERC-4626 vault classification.

Tests for create_probe_calls() chain filtering functionality.
"""

from types import SimpleNamespace

import eth_abi

from eth_defi.erc_4626.classification import (
    CHAIN_RESTRICTED_PROBES,
    ROYCO_CHAIN_IDS,
    _get_hardcoded_protocol_features,  # noqa: PLC2701
    _ProbeResultsDict,  # noqa: PLC2701
    _should_yield_probe,  # noqa: PLC2701
    create_probe_calls,
    identify_vault_features,
)
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.frax.constants import FRAX_STAKING_VAULTS_BY_CHAIN
from eth_defi.vault_street.constants import PRIME_USD_ADDRESS

# Chain IDs for reference
ETHEREUM_MAINNET = 1
ARBITRUM = 42161
BASE = 8453
POLYGON = 137
BSC = 56
MONAD = 143
MANTLE = 5000
AVALANCHE = 43114


def test_vault_street_hardcoded_protocol_is_ethereum_only() -> None:
    """Classify the hardcoded primeUSD address only on its deployment chain."""

    assert _get_hardcoded_protocol_features(PRIME_USD_ADDRESS, chain_id=ETHEREUM_MAINNET) == {ERC4626Feature.vault_street_like}
    assert _get_hardcoded_protocol_features(PRIME_USD_ADDRESS, chain_id=ARBITRUM) is None


def test_frax_staking_vaults_are_hardcoded_on_ethereum() -> None:
    """Route reviewed Frax staking vaults only on their deployment chain."""

    for address in FRAX_STAKING_VAULTS_BY_CHAIN[ETHEREUM_MAINNET]:
        assert _get_hardcoded_protocol_features(address, chain_id=ETHEREUM_MAINNET) == {ERC4626Feature.frax_staking_like}
        assert _get_hardcoded_protocol_features(address, chain_id=ARBITRUM) is None


def test_fraxlend_probe_requires_event_derived_deployer() -> None:
    """Accept the single Fraxlend probe only for a reviewed Frax deployer."""

    address = "0x0000000000000000000000000000000000000001"
    core_probe = SimpleNamespace(success=True, result=eth_abi.encode(["uint256"], [1]))

    reviewed_calls = _ProbeResultsDict(
        {
            "convertToShares": core_probe,
            "DEPLOYER_ADDRESS": SimpleNamespace(
                success=True,
                result=eth_abi.encode(["address"], ["0x7ab788d0483551428f2291232477f1818952998c"]),
            ),
        }
    )
    reviewed_features = identify_vault_features(address, reviewed_calls, debug_text=None, chain_id=ETHEREUM_MAINNET)
    assert ERC4626Feature.frax_like in reviewed_features

    fork_calls = _ProbeResultsDict(
        {
            "convertToShares": core_probe,
            "DEPLOYER_ADDRESS": SimpleNamespace(
                success=True,
                result=eth_abi.encode(["address"], ["0x0000000000000000000000000000000000000002"]),
            ),
        }
    )
    fork_features = identify_vault_features(address, fork_calls, debug_text=None, chain_id=ETHEREUM_MAINNET)
    assert ERC4626Feature.frax_like not in fork_features


def test_chain_probe_filtering():
    """Test that create_probe_calls() correctly filters probes based on chain_id.

    1. Verify chain-restricted probe configuration is well formed.
    2. Verify core probes are always present regardless of chain.
    3. Verify protocol-specific probes are filtered based on deployment chains.
    4. Verify disabled protocols never yield probes.
    5. Verify Royco tranche detection uses one probe on Royco chains.
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

    # Fraxlend is currently deployed on Ethereum and Arbitrum.
    assert _should_yield_probe("DEPLOYER_ADDRESS", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("DEPLOYER_ADDRESS", ARBITRUM) is True
    assert _should_yield_probe("DEPLOYER_ADDRESS", BASE) is False

    # IPOR restricted to Ethereum, Base, Arbitrum
    assert _should_yield_probe("getPerformanceFeeData", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("getPerformanceFeeData", BASE) is True
    assert _should_yield_probe("getPerformanceFeeData", ARBITRUM) is True
    assert _should_yield_probe("getPerformanceFeeData", POLYGON) is False

    # Accountable restricted to Ethereum and Monad
    assert _should_yield_probe("strategy", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("queue", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("strategy", MONAD) is True
    assert _should_yield_probe("queue", MONAD) is True
    assert _should_yield_probe("strategy", BASE) is False

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

    # Accountable probes included on Ethereum for OnRe Core Vault.
    probes_eth = list(create_probe_calls([test_address], chain_id=ETHEREUM_MAINNET))
    func_names_eth = [p.func_name for p in probes_eth]
    assert "strategy" in func_names_eth
    assert "queue" in func_names_eth

    royco_tranche_probe_names = ["getRawNAV"]
    assert sum(1 for func_name in func_names_eth if func_name in royco_tranche_probe_names) == 1
    assert "getRawNAV" in func_names_eth
    fraxlend_probe_names = ["DEPLOYER_ADDRESS"]
    assert sum(1 for func_name in func_names_eth if func_name in fraxlend_probe_names) == 1
    assert "DEPLOYER_ADDRESS" in func_names_eth
    assert _should_yield_probe("getRawNAV", ARBITRUM) is True
    assert _should_yield_probe("getRawNAV", AVALANCHE) is True

    for chain_id in ROYCO_CHAIN_IDS:
        probes = list(create_probe_calls([test_address], chain_id=chain_id))
        func_names = [p.func_name for p in probes]
        assert sum(1 for func_name in func_names if func_name in royco_tranche_probe_names) == 1

    # Core probes always present
    assert "name" in func_names_eth
    assert "convertToShares" in func_names_eth

    # Polygon should have fewer probes than Monad (which has Accountable probes)
    probes_polygon = list(create_probe_calls([test_address], chain_id=POLYGON))
    func_names_polygon = [p.func_name for p in probes_polygon]
    assert "getRawNAV" not in func_names_polygon
    assert len(probes_polygon) < len(probes_monad)
