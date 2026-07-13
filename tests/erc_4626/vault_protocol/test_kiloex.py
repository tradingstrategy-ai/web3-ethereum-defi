"""Test KiloEx Hybrid Vault protocol support."""

import datetime
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.kiloex.constants import KILOEX_VAULTS_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.kiloex.vault import KiloExVault
from eth_defi.vault.fee import get_vault_fee_mode
from eth_defi.vault.risk import get_vault_risk


@pytest.mark.parametrize(
    ("chain_id", "address", "expected_link"),
    [
        (56, "0xa40e085d0584eed39daaa077fcc4cd153ae9a5b0", "https://app.kiloex.io/earn/chain/BNB/"),
        (56, "0x6e7a6eb5feec64bf6401a744757aba89c5c7e813", "https://app.kiloex.io/earn/chain/BNB/"),
        (56, "0x1c3f35f7883fc4ea8c4bca1507144dc6087ad0fb", "https://app.kiloex.io/earn/chain/BNB/"),
        (8453, "0x43e3e6ffb2e363e64cd480cbb7cd0cf47bc6b477", "https://app.kiloex.io/earn/chain/Base/"),
    ],
)
def test_kiloex_hardcoded_vault_detection(chain_id: int, address: str, expected_link: str) -> None:
    """Known KiloEx addresses override the Gains-compatible contract probe."""
    features = _get_hardcoded_protocol_features(address, chain_id=chain_id)

    assert features == {ERC4626Feature.kiloex_like}
    assert get_vault_protocol_name(features) == "KiloEx"

    vault = create_vault_instance(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id)),
        address=address,
        features=features,
    )
    assert isinstance(vault, KiloExVault)
    assert vault.get_link() == expected_link
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=9)


def test_kiloex_hardcoded_detection_is_chain_aware() -> None:
    """Do not misidentify a matching address if it is deployed on another chain."""
    chain_id, address = next(iter(KILOEX_VAULTS_BY_CHAIN))

    assert _get_hardcoded_protocol_features(address, chain_id=chain_id) == {ERC4626Feature.kiloex_like}
    assert _get_hardcoded_protocol_features(address) == {ERC4626Feature.kiloex_like}
    assert _get_hardcoded_protocol_features(address, chain_id=1) is None


def test_kiloex_protocol_risk_and_fee_data_are_unknown() -> None:
    """KiloEx integration does not infer unpublished protocol fee or risk data."""
    assert get_vault_risk("KiloEx") is None
    assert get_vault_fee_mode("KiloEx", "0x1c3f35f7883fc4ea8c4bca1507144dc6087ad0fb") is None
