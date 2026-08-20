"""Test reviewed pToken vault protocol support."""

import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.ptoken.constants import PTOKEN_BTC_3X_LONG_VAULT, PTOKEN_HOOD_3X_LONG_VAULT
from eth_defi.erc_4626.vault_protocol.ptoken.vault import PTokenVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ROBINHOOD_MIDNIGHT_BLOCK
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_ROBINHOOD = os.environ.get("JSON_RPC_ROBINHOOD")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ROBINHOOD is None, reason="JSON_RPC_ROBINHOOD needed to run these tests"),
    pytest.mark.xdist_group("fork:robinhood:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return a shared Robinhood fork at the canonical post-deployment block.

    :param anvil_fork_pool:
        Session-scoped shared Anvil process pool.
    :return:
        Web3 instance connected to the shared Robinhood Chain fork.
    """

    return anvil_fork_pool.get_web3(JSON_RPC_ROBINHOOD, ROBINHOOD_MIDNIGHT_BLOCK)


@pytest.mark.parametrize(
    ("vault_address", "name", "symbol"),
    (
        (PTOKEN_BTC_3X_LONG_VAULT, "BTC (3x Long)", "pBTC3x"),
        (PTOKEN_HOOD_3X_LONG_VAULT, "HOOD (3x Long)", "pHOOD3x"),
    ),
)
def test_ptoken_vaults(web3: Web3, vault_address: HexAddress, name: str, symbol: str) -> None:
    """Classify only the reviewed pToken vaults as unknown-issuer products.

    :param web3:
        Shared Robinhood Chain fork.
    :param vault_address:
        Reviewed pToken address.
    :param name:
        Expected onchain token name.
    :param symbol:
        Expected onchain token symbol.
    """

    vault = create_vault_instance_autodetect(web3, vault_address=vault_address)

    assert isinstance(vault, PTokenVault)
    assert ERC4626Feature.ptoken_like in vault.features
    assert get_vault_protocol_name(vault.features) == "pToken"
    assert vault.vault_contract.functions.name().call() == name
    assert vault.vault_contract.functions.symbol().call() == symbol
    assert vault.vault_contract.functions.asset().call().lower() == "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_risk() == VaultTechnicalRisk.dangerous
    assert vault.get_fee_mode() is None
    assert vault.manager_name is None
    assert vault.short_description == "Currently not yet identified issuer of reviewed USDG-denominated pTokens."
    assert vault.description is not None
    assert vault.description.startswith("Currently not yet identified.")
    assert "does not by itself identify the issuer" in vault.description
    assert vault.get_link() == f"https://robinhoodchain.blockscout.com/address/{Web3.to_checksum_address(vault_address)}"
