"""Real-provider ERC-4626 classification coverage for Arc mainnet."""

import os

import pytest

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ARC = os.environ.get("JSON_RPC_ARC")

#: Steakhouse Prime USDC, discovered during the initial Arc mainnet scan.
STEAKHOUSE_PRIME_USDC = "0xbeef0016cb2fd5c352ea7ca08a9f54739dfa7298"

pytestmark = pytest.mark.skipif(JSON_RPC_ARC is None, reason="JSON_RPC_ARC needed to run this test")


def test_arc_steakhouse_prime_usdc_vault() -> None:
    """Classify a stable real Arc vault through the configured provider.

    This current-state integration test exercises the same provider and generic
    ERC-4626 classification path used by the metadata scanner. It deliberately
    avoids a historical fork until Arc's fixed-block Anvil support is proven.

    :return:
        ``None``. Assertions validate current contract metadata and temporary
        Morpho API coverage handling.
    """
    assert JSON_RPC_ARC is not None
    web3 = create_multi_provider_web3(JSON_RPC_ARC)
    vault = create_vault_instance_autodetect(web3, STEAKHOUSE_PRIME_USDC)

    assert isinstance(vault, MorphoV2Vault)
    assert vault.features == {ERC4626Feature.morpho_v2_like}
    assert vault.name == "Steakhouse Prime USDC"
    assert vault.symbol == "steakUSDC"
    assert vault.denomination_token.symbol == "USDC"
    assert VaultFlag.not_in_morpho_api not in vault.get_flags()
