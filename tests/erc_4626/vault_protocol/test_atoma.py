"""Test Atoma vault metadata."""

import datetime

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_VAULT_ADDRESS, ATOMA_VAULT_ADDRESSES, AtomaVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk


def test_atoma_hardcoded_protocol() -> None:
    """All Atoma Arbitrum vaults are classified by hardcoded address."""
    features_by_address = {address: HARDCODED_PROTOCOLS[address] for address in ATOMA_VAULT_ADDRESSES}

    assert all(features == {ERC4626Feature.atoma_like} for features in features_by_address.values())
    assert all(get_vault_protocol_name(features) == "Atoma" for features in features_by_address.values())


@pytest.mark.parametrize("vault_address", ATOMA_VAULT_ADDRESSES)
def test_atoma_create_vault_instance(vault_address: HexAddress) -> None:
    """Atoma features create an AtomaVault adapter."""
    web3 = Web3()
    web3.eth._chain_id = lambda: 42161

    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.atoma_like},
    )

    assert isinstance(vault, AtomaVault)


def test_atoma_static_fee_metadata() -> None:
    """Atoma exposes fixed fee and lock-up metadata from verified source."""
    vault = AtomaVault(Web3(), VaultSpec(42161, ATOMA_VAULT_ADDRESS), features={ERC4626Feature.atoma_like})

    assert vault.has_custom_fees()
    assert vault.get_risk() == VaultTechnicalRisk.severe
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == pytest.approx(0.20)
    assert vault.get_withdraw_fee("latest") == pytest.approx(0.005)
    fee_data = vault.get_fee_data()
    net_fee_data = fee_data.get_net_fees()
    assert fee_data.performance == pytest.approx(0.20)
    assert fee_data.withdraw == pytest.approx(0.005)
    assert net_fee_data.performance == 0
    assert net_fee_data.withdraw == pytest.approx(0.005)
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.get_link() == "https://app.atoma.fi/"
