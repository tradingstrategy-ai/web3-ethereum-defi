"""Test Atoma vault metadata."""

import datetime

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_VAULT_ADDRESS, AtomaVault
from eth_defi.vault.base import VaultSpec


def test_atoma_hardcoded_protocol() -> None:
    """Atoma's single Arbitrum vault is classified by hardcoded address."""
    features = HARDCODED_PROTOCOLS[ATOMA_VAULT_ADDRESS]

    assert features == {ERC4626Feature.atoma_like}
    assert get_vault_protocol_name(features) == "Atoma"


def test_atoma_create_vault_instance() -> None:
    """Atoma features create an AtomaVault adapter."""
    web3 = Web3()
    web3.eth._chain_id = lambda: 42161

    vault = create_vault_instance(
        web3,
        ATOMA_VAULT_ADDRESS,
        features={ERC4626Feature.atoma_like},
    )

    assert isinstance(vault, AtomaVault)


def test_atoma_static_fee_metadata() -> None:
    """Atoma exposes fixed fee and lock-up metadata from verified source."""
    vault = AtomaVault(Web3(), VaultSpec(42161, ATOMA_VAULT_ADDRESS), features={ERC4626Feature.atoma_like})

    assert vault.has_custom_fees()
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == pytest.approx(0.20)
    assert vault.get_withdraw_fee("latest") == pytest.approx(0.005)
    assert vault.get_fee_data().withdraw == pytest.approx(0.005)
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.get_link() == "https://app.atoma.fi/"
