"""No-RPC regressions for native Tori staking catalogue support."""

import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image
from web3 import Web3

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance_autodetect  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.tori.constants import TORI_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.tori.vault import COOLDOWN_DURATION_SELECTOR, ToriVault
from eth_defi.feed.sources import load_feeder_metadata, load_post_sources, resolve_feeder_id
from eth_defi.research.vault_metrics import slugify_protocol
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.curator import build_curator_metadata_json, get_curator_name, identify_curator, is_protocol_curator
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.strategy_tag import StrategyTag

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TAGS = {StrategyTag.multistrategy, StrategyTag.delta_neutral, StrategyTag.arbitrage, StrategyTag.carry_trade}


@pytest.fixture
def tori() -> ToriVault:
    """Build a pinned reader without a provider connection.

    Mock only the cooldown RPC response; static catalogue fields must not
    require a live service.

    :return: Reader configured at block 123.
    """
    web3 = Mock()
    web3.eth.chain_id = 1
    web3.eth.call.return_value = (604800).to_bytes(32, "big")
    return ToriVault(web3, VaultSpec(1, TORI_VAULT_ADDRESS), features={ERC4626Feature.tori_like}, default_block_identifier=123)


def test_tori_detection_and_static_metadata(tori: ToriVault) -> None:
    """Resolve the native deployment and preserve unknown fee information.

    Exact address classification must avoid the global probe surface and
    transaction capabilities must remain unavailable.

    :param tori: Pinned reader with a mocked provider.
    :return: ``None`` after asserting catalogue behaviour.
    """
    vault = create_vault_instance_autodetect(tori.web3, TORI_VAULT_ADDRESS)
    assert isinstance(vault, ToriVault)
    assert vault.features == {ERC4626Feature.tori_like}
    assert vault.get_protocol_name() == "Tori Finance"
    assert slugify_protocol(vault.get_protocol_name()) == "tori"
    assert vault.get_risk() is VaultTechnicalRisk.high
    assert vault.get_fee_mode() is VaultFeeMode.internalised_skimming
    fees = vault.get_fee_data()
    assert fees.management == 0
    assert fees.performance is None
    assert fees.deposit == fees.withdraw == 0
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_link() == "https://app.tori.finance/"
    assert vault.get_strategy_tags() == EXPECTED_TAGS
    tags = vault.get_strategy_tags()
    tags.clear()
    assert vault.get_strategy_tags() == EXPECTED_TAGS
    assert "\n" not in vault.description
    assert "\r" not in vault.description
    assert vault.short_description.count(".") == 1
    assert "unspecified rate" in vault.description
    tori.web3.eth.call.assert_not_called()


@pytest.mark.parametrize("chain_id", [None, 143, 42161, 8453])
def test_tori_rejects_other_chains(chain_id: int | None) -> None:
    """Avoid classifying a same-address token outside Ethereum.

    Bridged strUSD tokens are not native ERC-4626 staking vaults.

    :param chain_id: Missing or non-Ethereum chain identity.
    :return: ``None`` after checking the classification boundary.
    """
    assert _get_hardcoded_protocol_features(TORI_VAULT_ADDRESS, chain_id) is None
    assert _get_hardcoded_protocol_features(TORI_VAULT_ADDRESS.upper(), 1) == {ERC4626Feature.tori_like}


def test_tori_unmapped_tags(tori: ToriVault) -> None:
    """Preserve missing information for an unreviewed deployment.

    Protocol identity alone must not create an address-specific tag mapping.

    :param tori: Reader whose address is replaced for this assertion.
    :return: ``None`` after checking the missing mapping.
    """
    tori.spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    assert tori.get_strategy_tags() is None


@pytest.mark.parametrize("seconds", [0, 604800, 7776000])
def test_tori_cooldown(tori: ToriVault, seconds: int) -> None:
    """Decode the live delay and distinguish disabled cooldowns.

    Reads must use the configured metadata block rather than silently using
    latest state.

    :param tori: Pinned reader.
    :param seconds: Contract cooldown return value.
    :return: ``None`` after checking timing and calldata.
    """
    tori.web3.eth.call.return_value = seconds.to_bytes(32, "big")
    period = datetime.timedelta(seconds=seconds)
    assert tori.get_estimated_lock_up() == period
    assert tori.get_withdrawal_period() == (WithdrawalPeriod(period, period, WithdrawalDelayType.delay) if seconds else INSTANT_WITHDRAWAL_PERIOD)
    tori.web3.eth.call.assert_called_with({"to": tori.vault_address, "data": COOLDOWN_DURATION_SELECTOR}, block_identifier=123)
    assert COOLDOWN_DURATION_SELECTOR == Web3.keccak(text="cooldownDuration()")[:4]
    override_block = 456
    tori.fetch_cooldown_duration(override_block)
    assert tori.web3.eth.call.call_args.kwargs["block_identifier"] == override_block


@pytest.mark.parametrize("response", [b"", b"\x00", (2**24).to_bytes(32, "big")])
def test_tori_rejects_invalid_cooldown(tori: ToriVault, response: bytes) -> None:
    """Reject malformed responses instead of inventing a withdrawal delay.

    An empty response can indicate a wrong deployment or missing implementation.

    :param tori: Pinned reader.
    :param response: Malformed ABI return data.
    :return: ``None`` after asserting the hard error.
    """
    tori.web3.eth.call.return_value = response
    with pytest.raises(ValueError, match="Invalid cooldownDuration"):
        tori.get_withdrawal_period()


def test_tori_propagates_provider_failure(tori: ToriVault) -> None:
    """Leave provider failures visible to the caller.

    The published seven-day delay must never mask an unsuccessful chain read.

    :param tori: Pinned reader.
    :return: ``None`` after asserting failure propagation.
    """
    tori.web3.eth.call.side_effect = ConnectionError("Provider unavailable")
    with pytest.raises(ConnectionError, match="Provider unavailable"):
        tori.get_withdrawal_period()


def test_tori_curator_and_exports() -> None:
    """Export Tori identity and logos without changing ecosystem attribution.

    Protocol and curator posts resolve to the existing trUSD feeder. The
    ecosystem vault continues to resolve to RockawayX.

    :return: ``None`` after validating public metadata and shared assets.
    """
    assert identify_curator(1, "strUSD", "Staked trUSD", TORI_VAULT_ADDRESS, "tori") == "tori"
    assert get_curator_name("tori") == "Tori Finance"
    assert is_protocol_curator("tori")
    assert identify_curator(1, "etrUSD", "Tori Ecosystem Vault", "0xcd69123b3fbbfc666e1f6a501da27b564c00de54", "upshift") == "rockawayx"
    for role in ("protocols", "curators"):
        path = ROOT / f"eth_defi/data/feeds/{role}/tori.yaml"
        metadata = load_feeder_metadata(path)
        assert metadata["canonical-feeder-id"] == "trusd"
        assert not any(metadata.get(key) for key in ("twitter", "linkedin", "rss"))
    _, _, aliases = load_post_sources(ROOT / "eth_defi/data/feeds")
    assert resolve_feeder_id("tori", aliases) == "trusd"
    protocol = build_metadata_json(ROOT / "eth_defi/data/vaults/metadata/tori.yaml", "https://example.invalid")
    curator = build_curator_metadata_json(ROOT / "eth_defi/data/feeds/curators/tori.yaml", "https://example.invalid")
    assert protocol["name"] == curator["name"] == "Tori Finance"
    assert protocol["slug"] == curator["slug"] == "tori"
    assert curator["canonical_feeder_id"] == "trusd"
    assert curator["short_description"] and curator["long_description"]
    for variant in ("light", "dark"):
        assert protocol["logos"][variant]
        assert curator["logos"][variant]
        with Image.open(ROOT / f"eth_defi/data/vaults/formatted_logos/tori/{variant}.png") as logo:
            assert logo.size == (256, 256)
            assert logo.mode == "RGBA"
