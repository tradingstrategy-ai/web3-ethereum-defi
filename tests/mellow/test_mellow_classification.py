"""Test Mellow vault classification and routing."""

import datetime
import inspect
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_abi.exceptions import DecodingError
from web3 import Web3

from eth_defi.abi import get_abi_by_filename
from eth_defi.erc_4626.classification import _ProbeResultsDict, create_probe_calls, create_vault_instance, identify_vault_features  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name, is_activity_filter_exempt
from eth_defi.mellow.abi import ERC20_ABI_FILENAME, FACTORY_ABI_FILENAME, FEE_MANAGER_ABI_FILENAME, ORACLE_ABI_FILENAME, VAULT_ABI_FILENAME
from eth_defi.mellow.discovery import decode_mellow_created_event, fetch_mellow_created_event_topic, fetch_mellow_factories_for_chain
from eth_defi.mellow.vault import MellowVault
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk

TOPIC_HEX_LENGTH = 66
ETHEREUM_CHAIN_ID = 1
PLASMA_CHAIN_ID = 9745
ARBITRUM_CHAIN_ID = 42161
MONAD_CHAIN_ID = 143
BASE_CHAIN_ID = 8453
POLYGON_CHAIN_ID = 137
DEFAULT_CORE_FACTORY = "0x4E38F679e46B3216f0bd4B314E9C429AFfB1dEE3"
MONAD_CORE_FACTORY = "0x04c0287DEdE16e0C04A1C2A52F31400a88f1dF4c"
LIDO_EARN_USD_VAULT = "0x014e6DA8F283C4aF65B2AA0f201438680A004452"
LIDO_EARN_USD_OWNER = Web3.to_checksum_address("0xf067739E4F3C7f496065Ed6518548a1052F99BC7")
FACTORY_ENV_VARS = (
    "MELLOW_ETHEREUM_VAULT_FACTORY",
    "MELLOW_PLASMA_VAULT_FACTORY",
    "MELLOW_ARBITRUM_VAULT_FACTORY",
    "MELLOW_MONAD_VAULT_FACTORY",
    "MELLOW_BASE_VAULT_FACTORY",
)
VAULT_BASE_FEE_ACCESSOR_METHODS = (
    "has_custom_fees",
    "get_management_fee",
    "get_performance_fee",
    "get_deposit_fee",
    "get_withdraw_fee",
    "get_fee_data",
)


class FakeCallResult:
    """Minimal call result for feature identification tests."""

    def __init__(self, success: bool, result: bytes = b""):  # noqa: FBT001
        """Create fake call result.

        :param success:
            Whether the probe succeeded.

        :param result:
            Raw return bytes.
        """

        self._success = success
        self._result = result

    @property
    def success(self) -> bool:
        """Whether the call succeeded."""

        return self._success

    @property
    def result(self) -> bytes:
        """Raw call result."""

        return self._result


def test_mellow_protocol_name() -> None:
    """Mellow feature maps to protocol name."""

    assert get_vault_protocol_name({ERC4626Feature.mellow_like}) == "Mellow"


def test_mellow_risk_level() -> None:
    """Mellow protocol is classified as low technical risk."""

    assert get_vault_risk("Mellow", LIDO_EARN_USD_VAULT) is VaultTechnicalRisk.low


def test_mellow_created_topic_is_hypersync_hex() -> None:
    """Mellow factory topic is encoded with ``0x`` for Hypersync."""

    topic = fetch_mellow_created_event_topic()

    assert topic.startswith("0x")
    assert len(topic) == TOPIC_HEX_LENGTH


def test_mellow_abi_files_load() -> None:
    """Mellow ABI fragments are loaded from bundled JSON files."""

    factory_abi = get_abi_by_filename(FACTORY_ABI_FILENAME)
    vault_abi = get_abi_by_filename(VAULT_ABI_FILENAME)
    oracle_abi = get_abi_by_filename(ORACLE_ABI_FILENAME)
    fee_manager_abi = get_abi_by_filename(FEE_MANAGER_ABI_FILENAME)
    erc20_abi = get_abi_by_filename(ERC20_ABI_FILENAME)

    assert any(item["type"] == "event" and item["name"] == "Created" for item in factory_abi)
    assert any(item["type"] == "function" and item["name"] == "shareManager" for item in vault_abi)
    assert any(item["type"] == "function" and item["name"] == "getReport" for item in oracle_abi)
    assert any(item["type"] == "function" and item["name"] == "protocolFeeD6" for item in fee_manager_abi)
    assert any(item["type"] == "function" and item["name"] == "totalSupply" for item in erc20_abi)


def test_mellow_created_decoder_accepts_documented_layout() -> None:
    """Mellow factory decoder accepts the documented non-indexed event layout."""

    init_params = b"\x12\x34"
    log = SimpleNamespace(
        topics=[fetch_mellow_created_event_topic()],
        data=Web3.to_hex(encode(["address", "uint256", "address", "bytes"], [LIDO_EARN_USD_VAULT, 1, LIDO_EARN_USD_OWNER, init_params])),
    )

    instance, version, owner, decoded_init_params = decode_mellow_created_event(Web3(), log)

    assert instance == LIDO_EARN_USD_VAULT
    assert version == 1
    assert owner == LIDO_EARN_USD_OWNER
    assert decoded_init_params == init_params


def test_mellow_created_decoder_accepts_indexed_compatibility_layout() -> None:
    """Mellow factory decoder accepts defensive indexed event topics."""

    init_params = b"\x12\x34"
    log = SimpleNamespace(
        topics=[
            fetch_mellow_created_event_topic(),
            "0x" + LIDO_EARN_USD_VAULT[2:].lower().rjust(64, "0"),
            "0x" + format(1, "064x"),
            "0x" + LIDO_EARN_USD_OWNER[2:].lower().rjust(64, "0"),
        ],
        data=Web3.to_hex(init_params),
    )

    instance, version, owner, decoded_init_params = decode_mellow_created_event(Web3(), log)

    assert instance == LIDO_EARN_USD_VAULT
    assert version == 1
    assert owner == LIDO_EARN_USD_OWNER
    assert decoded_init_params == init_params


def test_mellow_created_decoder_rejects_malformed_data() -> None:
    """Mellow factory decoder rejects malformed non-indexed event data."""

    log = SimpleNamespace(
        topics=[fetch_mellow_created_event_topic()],
        data="0x1234",
    )

    with pytest.raises(DecodingError):
        decode_mellow_created_event(Web3(), log)


def test_mellow_fee_accessors_match_vault_base_surface() -> None:
    """Mellow fee accessors keep the shared VaultBase method contract."""

    for method_name in VAULT_BASE_FEE_ACCESSOR_METHODS:
        base_method = getattr(VaultBase, method_name)
        mellow_method = getattr(MellowVault, method_name)
        assert mellow_method is not base_method

        base_parameters = tuple(inspect.signature(base_method).parameters)
        mellow_parameters = tuple(inspect.signature(mellow_method).parameters)
        assert mellow_parameters == base_parameters


def test_mellow_factory_registry_matches_documented_core_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory defaults cover all documented Mellow Core deployment chains."""

    for env_var in FACTORY_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    assert fetch_mellow_factories_for_chain(ETHEREUM_CHAIN_ID) == [DEFAULT_CORE_FACTORY]
    assert fetch_mellow_factories_for_chain(PLASMA_CHAIN_ID) == [DEFAULT_CORE_FACTORY]
    assert fetch_mellow_factories_for_chain(ARBITRUM_CHAIN_ID) == [DEFAULT_CORE_FACTORY]
    assert fetch_mellow_factories_for_chain(MONAD_CHAIN_ID) == [MONAD_CORE_FACTORY]
    assert fetch_mellow_factories_for_chain(BASE_CHAIN_ID) == []


def test_mellow_probes_are_limited_to_official_core_deployment_chains() -> None:
    """Mellow probes are only emitted on chains listed in official Core deployments."""

    vault_address = "0x014e6da8f283c4af65b2aa0f201438680a004452"
    supported_chains = (ETHEREUM_CHAIN_ID, PLASMA_CHAIN_ID, ARBITRUM_CHAIN_ID, MONAD_CHAIN_ID)
    unsupported_chains = (BASE_CHAIN_ID, POLYGON_CHAIN_ID)

    for chain_id in supported_chains:
        call_names = {call.func_name for call in create_probe_calls([vault_address], chain_id=chain_id)}
        assert "shareManager" in call_names
        assert "getAssetCount" in call_names

    for chain_id in unsupported_chains:
        call_names = {call.func_name for call in create_probe_calls([vault_address], chain_id=chain_id)}
        assert "shareManager" not in call_names
        assert "getAssetCount" not in call_names


def test_mellow_detection_is_activity_filter_exempt() -> None:
    """Mellow detections bypass deposit/redeem activity count filters."""

    detection = ERC4262VaultDetection(
        chain=1,
        address="0x014e6da8f283c4af65b2aa0f201438680a004452",
        first_seen_at_block=23_000_000,
        first_seen_at=datetime.datetime(2026, 1, 1),  # noqa: DTZ001
        features={ERC4626Feature.mellow_like},
        updated_at=datetime.datetime(2026, 1, 1),  # noqa: DTZ001
        deposit_count=0,
        redeem_count=0,
    )

    assert is_activity_filter_exempt(detection) is True


def test_mellow_probe_classifies_before_broken_erc4626() -> None:
    """Mellow probes classify even when ERC-4626 convertToShares fails."""

    calls = _ProbeResultsDict(
        {
            "shareManager": FakeCallResult(True, b"\x00" * 32),
            "getAssetCount": FakeCallResult(True, b"\x00" * 32),
            "convertToShares": FakeCallResult(False, b""),
            "EVM IS BROKEN SHIT": FakeCallResult(False),
        }
    )

    features = identify_vault_features(
        "0x014e6da8f283c4af65b2aa0f201438680a004452",
        calls,
        debug_text="mellow",
    )

    assert features == {ERC4626Feature.mellow_like}


def test_mellow_probe_does_not_override_broken_impossible_function() -> None:
    """All-success broken contracts are not misrouted as Mellow."""

    calls = _ProbeResultsDict(
        {
            "shareManager": FakeCallResult(True, b"\x00" * 32),
            "getAssetCount": FakeCallResult(True, b"\x00" * 32),
            "convertToShares": FakeCallResult(True, b"\x00" * 32),
            "EVM IS BROKEN SHIT": FakeCallResult(True, b"\x00" * 32),
        }
    )

    features = identify_vault_features(
        "0x014e6da8f283c4af65b2aa0f201438680a004452",
        calls,
        debug_text="broken",
    )

    assert features == {ERC4626Feature.broken}


def test_create_vault_instance_routes_mellow() -> None:
    """Adapter factory returns MellowVault for mellow_like detections."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    vault = create_vault_instance(
        web3,
        "0x014e6da8f283c4af65b2aa0f201438680a004452",
        features={ERC4626Feature.mellow_like},
    )

    assert isinstance(vault, MellowVault)
    assert vault.address == "0x014e6DA8F283C4aF65B2AA0f201438680A004452"


def test_mellow_vault_exposes_vault_address_alias() -> None:
    """Shared historical scanners can use the ERC-4626-style address alias."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    vault = MellowVault(
        web3,
        VaultSpec(1, "0x014e6da8f283c4af65b2aa0f201438680a004452"),
        features={ERC4626Feature.mellow_like},
    )

    assert vault.vault_address == vault.address
