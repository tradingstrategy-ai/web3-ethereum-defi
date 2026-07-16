"""Aave V3 ATokenVault hardcoded classification tests."""

from eth_defi.erc_4626.classification import AAVE_ATOKEN_VAULTS_BY_CHAIN, _get_hardcoded_protocol_features  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature

EXPECTED_AAVE_ATOKEN_VAULT_COUNT = 119


def test_aave_atoken_vaults_are_chain_aware() -> None:
    """Classify the verified Wrapped Aave EURe vault only on Gnosis.

    The same address may be deployed on multiple networks, so the registry must
    not infer an Aave classification on another chain.
    """
    vault_address = "0x9f40ca84a70685d2c48003b9dec27b3d98ace348"

    assert vault_address in AAVE_ATOKEN_VAULTS_BY_CHAIN[100]
    assert _get_hardcoded_protocol_features(vault_address, chain_id=100) == {ERC4626Feature.aave_like}
    assert _get_hardcoded_protocol_features(vault_address, chain_id=1) is None


def test_aave_atoken_vault_registry_is_complete() -> None:
    """Keep every ATokenVault verified during the Aave-name scan classified."""
    assert sum(len(vaults) for vaults in AAVE_ATOKEN_VAULTS_BY_CHAIN.values()) == EXPECTED_AAVE_ATOKEN_VAULT_COUNT
