"""Test Frankencoin vault metadata."""

import datetime

from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.frankencoin.vault import (
    FRANKENCOIN_BASE_SAVINGS_VAULT,
    FRANKENCOIN_ETHEREUM_SAVINGS_VAULT,
    FRANKENCOIN_GNOSIS_SAVINGS_VAULT,
    FRANKENCOIN_SAVINGS_VAULTS,
    FrankencoinVault,
)
from eth_defi.vault.base import VaultSpec, VaultTechnicalRisk
from eth_defi.vault.fee import VaultFeeMode


def test_frankencoin_hardcoded_protocols() -> None:
    """Official Frankencoin Savings Vaults are classified by hardcoded address."""
    for vault_address in FRANKENCOIN_SAVINGS_VAULTS:
        features = HARDCODED_PROTOCOLS[vault_address]

        assert features == {ERC4626Feature.frankencoin_like}
        assert get_vault_protocol_name(features) == "Frankencoin"


def test_frankencoin_create_vault_instance() -> None:
    """Frankencoin features create a FrankencoinVault adapter."""
    web3 = Web3()
    web3.eth._chain_id = lambda: 100

    vault = create_vault_instance(
        web3,
        FRANKENCOIN_GNOSIS_SAVINGS_VAULT,
        features={ERC4626Feature.frankencoin_like},
    )

    assert isinstance(vault, FrankencoinVault)
    assert vault.get_protocol_name() == "Frankencoin"


def test_frankencoin_static_fee_metadata() -> None:
    """Frankencoin exposes static fee, lock-up, risk, and link metadata."""
    vault = FrankencoinVault(
        Web3(),
        VaultSpec(100, FRANKENCOIN_GNOSIS_SAVINGS_VAULT),
        features={ERC4626Feature.frankencoin_like},
    )

    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=3)
    assert vault.get_link() == "https://frankencoin.com/token/#svzchf"
    assert vault.get_risk() == VaultTechnicalRisk.low
    assert vault.get_fee_mode() == VaultFeeMode.internalised_skimming


def test_frankencoin_addresses() -> None:
    """Frankencoin savings vault address constants stay lower-case."""
    assert FRANKENCOIN_ETHEREUM_SAVINGS_VAULT == "0xe5f130253ff137f9917c0107659a4c5262abf6b0"
    assert FRANKENCOIN_BASE_SAVINGS_VAULT == "0xa09ebdf8a01b9ef04149319d64f83b9c01a5b585"
    assert FRANKENCOIN_GNOSIS_SAVINGS_VAULT == "0x6165946250dd04740ab1409217e95a4f38374fe9"
