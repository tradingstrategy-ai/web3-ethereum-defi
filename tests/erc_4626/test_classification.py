"""Tests for ERC-4626 vault classification.

Tests for create_probe_calls() chain filtering functionality.
"""

import dataclasses
import datetime
from collections.abc import Iterator
from types import SimpleNamespace

import eth_abi
import pytest
from web3 import Web3

from eth_defi.erc_4626 import classification, discovery_base
from eth_defi.erc_4626.classification import (
    CHAIN_RESTRICTED_PROBES,
    ROYCO_CHAIN_IDS,
    VaultFeatureProbe,
    _get_hardcoded_protocol_features,  # noqa: PLC2701
    _ProbeResultsDict,  # noqa: PLC2701
    _should_yield_probe,  # noqa: PLC2701
    create_probe_calls,
    identify_vault_features,
    probe_vaults,
)
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import LeadScanReport, PotentialVaultMatch, VaultDiscoveryBase
from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BRIDGE_VAULT, ARCUS_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ETHEREUM_STAKED_USDX_VAULT, AXIS_PLASMA_STAKED_USDX_VAULT
from eth_defi.erc_4626.vault_protocol.frax.constants import FRAX_STAKING_VAULTS_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NEST_CHAIN_NAMES
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault_street.constants import PRIME_USD_ADDRESS

# Chain IDs for reference
ETHEREUM_MAINNET = 1
ARBITRUM = 42161
BASE = 8453
POLYGON = 137
BSC = 56
MONAD = 143
ROBINHOOD = 4663
MANTLE = 5000
AVALANCHE = 43114
HYPEREVM = 999
INK = 57073
PLASMA = 9745
PLUME = 98866
WORLDCHAIN = 480
ARC = 5042
MORPH = 2818


def test_vault_street_hardcoded_protocol_is_ethereum_only() -> None:
    """Classify the hardcoded primeUSD address only on its deployment chain."""

    assert _get_hardcoded_protocol_features(PRIME_USD_ADDRESS, chain_id=ETHEREUM_MAINNET) == {ERC4626Feature.vault_street_like}
    assert _get_hardcoded_protocol_features(PRIME_USD_ADDRESS, chain_id=ARBITRUM) is None


def test_frax_staking_vaults_are_hardcoded_on_ethereum() -> None:
    """Route reviewed Frax staking vaults only on their deployment chain."""

    for address in FRAX_STAKING_VAULTS_BY_CHAIN[ETHEREUM_MAINNET]:
        assert _get_hardcoded_protocol_features(address, chain_id=ETHEREUM_MAINNET) == {ERC4626Feature.frax_staking_like}
        assert _get_hardcoded_protocol_features(address, chain_id=ARBITRUM) is None


def test_axis_staked_usdx_deployments_are_hardcoded_by_chain() -> None:
    """Route both reviewed Axis StakedUSDx vaults only on their deployment chains."""

    plasma_features = {ERC4626Feature.axis_like}
    ethereum_features = plasma_features | {ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    assert _get_hardcoded_protocol_features(AXIS_PLASMA_STAKED_USDX_VAULT, chain_id=PLASMA) == plasma_features
    assert _get_hardcoded_protocol_features(AXIS_PLASMA_STAKED_USDX_VAULT, chain_id=ETHEREUM_MAINNET) is None
    assert _get_hardcoded_protocol_features(AXIS_ETHEREUM_STAKED_USDX_VAULT, chain_id=ETHEREUM_MAINNET) == ethereum_features
    assert _get_hardcoded_protocol_features(AXIS_ETHEREUM_STAKED_USDX_VAULT, chain_id=PLASMA) is None


def test_lagoon_legacy_and_recent_interfaces_are_detected() -> None:
    """Identify Lagoon through its legacy and recent contract interfaces."""

    probe_names = {call.func_name for call in create_probe_calls(["0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc"], chain_id=ETHEREUM_MAINNET)}
    assert "isTotalAssetsValid" in probe_names

    erc4626_probe = SimpleNamespace(success=True, result=eth_abi.encode(["uint256"], [1]))
    bool_probe = SimpleNamespace(success=True, result=eth_abi.encode(["bool"], [False]))
    legacy_probe = SimpleNamespace(success=True, result=eth_abi.encode(["uint256"], [1]))

    legacy_features = identify_vault_features(
        "0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc",
        _ProbeResultsDict(
            {
                "convertToShares": erc4626_probe,
                "isOperator": bool_probe,
                "MAX_MANAGEMENT_RATE": legacy_probe,
            }
        ),
        debug_text=None,
        chain_id=ETHEREUM_MAINNET,
    )
    assert legacy_features == {ERC4626Feature.erc_7540_like, ERC4626Feature.lagoon_like}

    recent_features = identify_vault_features(
        "0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc",
        _ProbeResultsDict(
            {
                "convertToShares": erc4626_probe,
                "isOperator": bool_probe,
                "isTotalAssetsValid": bool_probe,
            }
        ),
        debug_text=None,
        chain_id=ETHEREUM_MAINNET,
    )
    assert recent_features == {ERC4626Feature.erc_7540_like, ERC4626Feature.lagoon_like}

    non_erc7540_features = identify_vault_features(
        "0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc",
        _ProbeResultsDict(
            {
                "convertToShares": erc4626_probe,
                "isTotalAssetsValid": bool_probe,
            }
        ),
        debug_text=None,
        chain_id=ETHEREUM_MAINNET,
    )
    assert ERC4626Feature.lagoon_like not in non_erc7540_features


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


def test_upshift_multi_asset_probe_is_unrestricted() -> None:
    """Probe Upshift's multi-asset discriminator on every EVM chain.

    ``assetsWhitelistAddress`` is intentionally unrestricted (not in
    ``CHAIN_RESTRICTED_PROBES``): Upshift multi-asset vaults may appear on any
    chain, so the probe must yield regardless of ``chain_id``.
    """

    test_address = "0x0000000000000000000000000000000000000001"
    upshift_chain_ids = (ETHEREUM_MAINNET, POLYGON, MONAD, HYPEREVM, BASE, PLASMA, AVALANCHE, INK)

    for chain_id in upshift_chain_ids:
        call_names = {call.func_name for call in create_probe_calls([test_address], chain_id=chain_id)}
        assert "assetsWhitelistAddress" in call_names


def test_nest_probe_is_limited_to_nest_deployment_chains() -> None:
    """Avoid classifying an unrelated async vault as Nest on another chain."""

    assert CHAIN_RESTRICTED_PROBES["operatorRegistry"] == set(NEST_CHAIN_NAMES) - {MORPH}
    test_address = "0x0000000000000000000000000000000000000001"
    for chain_id in (AVALANCHE, PLUME, WORLDCHAIN, ARC, BASE, MONAD, ROBINHOOD):
        calls = {call.func_name for call in create_probe_calls([test_address], chain_id=chain_id)}
        assert "operatorRegistry" in calls

    for chain_id in (HYPEREVM, MORPH):
        calls = {call.func_name for call in create_probe_calls([test_address], chain_id=chain_id)}
        assert "operatorRegistry" not in calls


def test_arcus_probe_is_limited_to_robinhood_and_expected_bridge_vault() -> None:
    """Recognise Arcus's reviewed pToken surface with its uncommon accessor."""

    test_address = "0x0000000000000000000000000000000000000001"
    robinhood_calls = {call.func_name for call in create_probe_calls([test_address], chain_id=ARCUS_CHAIN_ID)}
    base_calls = {call.func_name for call in create_probe_calls([test_address], chain_id=BASE)}

    assert "bridgeVault" in robinhood_calls
    assert "bridgeVault" not in base_calls

    erc4626_probe = SimpleNamespace(success=True, result=eth_abi.encode(["uint256"], [1]))
    bridge_vault_probe = SimpleNamespace(success=True, result=eth_abi.encode(["address"], [ARCUS_BRIDGE_VAULT]))
    arcus_features = identify_vault_features(
        test_address,
        _ProbeResultsDict({"convertToShares": erc4626_probe, "bridgeVault": bridge_vault_probe}),
        debug_text=None,
        chain_id=ARCUS_CHAIN_ID,
    )
    assert arcus_features == {ERC4626Feature.arcus_like}

    non_arcus_bridge_vault_probe = SimpleNamespace(success=True, result=eth_abi.encode(["address"], [test_address]))
    non_arcus_features = identify_vault_features(
        test_address,
        _ProbeResultsDict({"convertToShares": erc4626_probe, "bridgeVault": non_arcus_bridge_vault_probe}),
        debug_text=None,
        chain_id=ARCUS_CHAIN_ID,
    )
    assert ERC4626Feature.arcus_like not in non_arcus_features

    wrong_chain_features = identify_vault_features(
        test_address,
        _ProbeResultsDict({"convertToShares": erc4626_probe, "bridgeVault": bridge_vault_probe}),
        debug_text=None,
        chain_id=BASE,
    )
    assert ERC4626Feature.arcus_like not in wrong_chain_features


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

    # Accountable probes are enabled on every scanner chain.
    assert _should_yield_probe("strategy", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("queue", ETHEREUM_MAINNET) is True
    assert _should_yield_probe("strategy", MONAD) is True
    assert _should_yield_probe("queue", MONAD) is True
    assert _should_yield_probe("strategy", ROBINHOOD) is True
    assert _should_yield_probe("queue", ROBINHOOD) is True
    assert _should_yield_probe("strategy", BASE) is True

    # Brink restricted to Mantle
    assert _should_yield_probe("strategist", MANTLE) is True
    assert _should_yield_probe("strategist", ETHEREUM_MAINNET) is False

    # T3tris deployments use the same protocol-specific accounting getter on
    # Arbitrum and Robinhood. Without the Robinhood probe, discovery retains a
    # generic ERC-4626 lead but cannot instantiate the T3tris adapter.
    assert _should_yield_probe("getGrossTVL", ARBITRUM) is True
    assert _should_yield_probe("getGrossTVL", ROBINHOOD) is True
    assert _should_yield_probe("getGrossTVL", ETHEREUM_MAINNET) is False

    # Disabled protocols (empty sets) never yield
    assert _should_yield_probe("outputToLp0Route", ETHEREUM_MAINNET) is False  # Baklava
    assert _should_yield_probe("agent", ARBITRUM) is False  # Astrolab

    # Test create_probe_calls filtering
    # Accountable probes on Monad
    probes_monad = list(create_probe_calls([test_address], chain_id=MONAD))
    func_names_monad = [p.func_name for p in probes_monad]
    assert "strategy" in func_names_monad
    assert "queue" in func_names_monad

    # Accountable probes on Robinhood for Meridian Liquidity Provider.
    probes_robinhood = list(create_probe_calls([test_address], chain_id=ROBINHOOD))
    func_names_robinhood = [p.func_name for p in probes_robinhood]
    assert "strategy" in func_names_robinhood
    assert "queue" in func_names_robinhood

    # Accountable probes included on Ethereum for OnRe Core Vault.
    probes_eth = list(create_probe_calls([test_address], chain_id=ETHEREUM_MAINNET))
    func_names_eth = [p.func_name for p in probes_eth]
    assert "strategy" in func_names_eth
    assert "queue" in func_names_eth

    probes_robinhood = list(create_probe_calls([test_address], chain_id=ROBINHOOD))
    assert "getGrossTVL" in {probe.func_name for probe in probes_robinhood}

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


def test_vault_morpho_cap_probe_preserves_interface_features() -> None:
    """The extra cap identifies a separate implementation without asserting its operator.

    1. Supply the common ERC-4626 and MORPHO probes.
    2. Compare canonical MetaMorpho with the capped implementation.
    3. Ignore tvlCap on an unrelated vault without MORPHO.
    """
    # 1. Fixed contract responses isolate classification from node availability.
    address = "0x0000000000000000000000000000000000000001"
    success = SimpleNamespace(success=True, result=eth_abi.encode(["uint256"], [1]))
    # 2. The shared MORPHO getter must not erase the extra cap distinction.
    for has_morpho, has_cap in ((True, True), (True, False), (False, True)):
        probes = {"convertToShares": success}
        if has_morpho:
            probes["MORPHO"] = success
        if has_cap:
            probes["tvlCap"] = success
        features = identify_vault_features(address, _ProbeResultsDict(probes), debug_text=None, chain_id=ETHEREUM_MAINNET)
        # 3. A cap getter alone does not establish this implementation family.
        assert (ERC4626Feature.vault_morpho_like in features) == (has_morpho and has_cap)
        assert (ERC4626Feature.morpho_like in features) == has_morpho


def test_probe_vaults_groups_checksummed_probe_with_its_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """A result whose address differs only in case joins its vault's results.

    Before 2026-11-14 the ``tvlCap()`` probe was built from a Web3 contract proxy
    and carried a checksummed address, while signature probes kept the caller's
    lower-case lead address. Grouping by the raw string produced a second,
    ``tvlCap``-only probe that decoded as ``broken`` and overwrote production
    classifications. The reader here checksums that result's address to keep
    grouping case-insensitive even if a future probe does the same.
    """
    address = "0x6b13c060f13af1fdb319f52315bbbf3fb1d88844"
    morpho_word = eth_abi.encode(["address"], ["0x" + "11" * 20])
    succeeding = {"convertToShares": eth_abi.encode(["uint256"], [1]), "MORPHO": morpho_word, "tvlCap": eth_abi.encode(["uint256"], [100])}

    def fake_read_multicall_chunked(_chain_id: int, _web3factory: object, calls: list[EncodedCall], **_kwargs: object) -> Iterator[EncodedCallResult]:
        """Serve ERC-4626, Morpho and cap probes, as for a VaultMorpho deployment."""
        for call in calls:
            result = succeeding.get(call.func_name)
            if call.func_name == "tvlCap":
                call = dataclasses.replace(call, address=Web3.to_checksum_address(call.address))
            yield EncodedCallResult(call=call, success=result is not None, result=result or b"", block_identifier=1)

    monkeypatch.setattr(classification, "read_multicall_chunked", fake_read_multicall_chunked)
    probes = list(probe_vaults(ETHEREUM_MAINNET, SimpleNamespace(), [address], block_identifier=1))

    assert [probe.address for probe in probes] == [address]
    assert ERC4626Feature.morpho_like in probes[0].features
    assert ERC4626Feature.vault_morpho_like in probes[0].features
    assert ERC4626Feature.broken not in probes[0].features


#: Chains with their own probe selection, plus chains without restrictions.
PROBE_ADDRESS_CHAINS = sorted({ETHEREUM_MAINNET, ARBITRUM, BASE, POLYGON, ARCUS_CHAIN_ID} | {chain for chains in CHAIN_RESTRICTED_PROBES.values() for chain in chains})


@pytest.mark.parametrize("chain_id", PROBE_ADDRESS_CHAINS)
@pytest.mark.parametrize("address", ["0x6b13c060f13af1fdb319f52315bbbf3fb1d88844", "0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844"])
def test_probe_calls_share_the_callers_address_string(chain_id: int, address: str) -> None:
    """Every probe for one vault carries exactly the caller's address string.

    Batched classification groups results by call address. A probe built from a
    Web3 contract proxy checksums its address, so a mixed set split one vault
    into two classifications on 2026-11-14.
    """
    assert {call.address for call in create_probe_calls([address], chain_id=chain_id)} == {address}


def test_probe_vaults_rejects_unrequested_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A result for an address that was not requested fails instead of adding a classification."""
    address = "0x6b13c060f13af1fdb319f52315bbbf3fb1d88844"
    stray = EncodedCall.from_keccak_signature(address="0x" + "22" * 20, signature=b"\x00" * 4, function="name", data=b"", extra_data=None)

    def fake_read_multicall_chunked(_chain_id: int, _web3factory: object, _calls: list[EncodedCall], **_kwargs: object) -> Iterator[EncodedCallResult]:
        """Return one result for an address outside the probe request."""
        yield EncodedCallResult(call=stray, success=False, result=b"", block_identifier=1)

    monkeypatch.setattr(classification, "read_multicall_chunked", fake_read_multicall_chunked)
    with pytest.raises(ValueError, match="unrequested address"):
        list(probe_vaults(ETHEREUM_MAINNET, SimpleNamespace(), [address], block_identifier=1))


class _SingleLeadDiscovery(VaultDiscoveryBase):
    """Discovery backend serving one event lead, without RPC or Hypersync."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=ETHEREUM_MAINNET))
    web3factory = object()
    lead_address = "0x6b13c060f13af1fdb319f52315bbbf3fb1d88844"

    def fetch_leads(self, _start_block: int, _end_block: int, _display_progress: bool = True) -> LeadScanReport:  # noqa: FBT001, FBT002 - mirrors the base class signature
        """Return the single lead."""
        lead = PotentialVaultMatch(chain=ETHEREUM_MAINNET, address=self.lead_address, first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None), deposit_count=1)
        return LeadScanReport(leads={self.lead_address: lead})


def test_scan_vaults_refuses_more_detections_than_leads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate vault identities from classification abort the discovery scan.

    Reproduces the 2026-11-14 failure mode: the classifier yielded a full and a
    ``broken`` probe for the same vault under two address spellings.
    """
    address = _SingleLeadDiscovery.lead_address

    def fake_probe_vaults(_chain: int, _web3factory: object, _addresses: list[str], **_kwargs: object) -> Iterator[VaultFeatureProbe]:
        """Yield two identities for the one lead."""
        yield VaultFeatureProbe(address=address, features={ERC4626Feature.morpho_like})
        yield VaultFeatureProbe(address=Web3.to_checksum_address(address), features={ERC4626Feature.broken})

    monkeypatch.setattr(discovery_base, "probe_vaults", fake_probe_vaults)
    with pytest.raises(RuntimeError, match="2 detections for 1 leads"):
        _SingleLeadDiscovery(max_workers=1).scan_vaults(0, 10, display_progress=False, hardcoded_lead_sources=())


def test_buoy_probe_is_hyperevm_only_and_checks_snapshot_shape() -> None:
    """Route a correctly shaped Buoy snapshot without claiming ERC-4626 flows.

    The adapter subsequently verifies the clone and official registry, rather
    than using token name or symbol as identity evidence.
    """
    address = "0x87f76a7e6c346904192c884d48c02d3ac640de1e"
    assert "getNavSnapshot" in {call.func_name for call in create_probe_calls([address], chain_id=HYPEREVM)}
    assert "getNavSnapshot" not in {call.func_name for call in create_probe_calls([address], chain_id=BASE)}
    snapshot = SimpleNamespace(success=True, result=bytes(224))
    calls = _ProbeResultsDict({"getNavSnapshot": snapshot})
    assert identify_vault_features(address, calls, debug_text=None, chain_id=HYPEREVM) == {ERC4626Feature.buoy_like}
    snapshot.result = bytes(192)
    assert ERC4626Feature.buoy_like not in identify_vault_features(address, calls, debug_text=None, chain_id=HYPEREVM)
