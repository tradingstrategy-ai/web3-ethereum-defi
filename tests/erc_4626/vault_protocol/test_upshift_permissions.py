"""Read source-proven Upshift admission from an Ethereum archive provider.

USPC was deployed after the canonical shared Ethereum Anvil fork. These few
read-only policy calls use the real provider directly at the fixed audit block;
they do not start another Anvil fork or send any transactions.
"""

import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission, WhitelistingRequired

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
pytestmark = pytest.mark.skipif(not JSON_RPC_ETHEREUM, reason="JSON_RPC_ETHEREUM required for real Upshift admission reads")

#: Fixed Ethereum state used by the 2026-10-31 permission audit.
UPSHIFT_PERMISSION_BLOCK = 26_309_233
#: An arbitrary account excluded by USPC's configured sender allow-list.
UNAPPROVED_OWNER = HexAddress("0x1111111111111111111111111111111111111111")


@pytest.fixture(scope="module")
def permission_web3() -> Web3:
    """Connect to the supplied archive provider for fixed-state policy views.

    :return: Ethereum connection; no transactions or persistent caches.
    """
    return create_multi_provider_web3(JSON_RPC_ETHEREUM)


@pytest.mark.parametrize(
    ("address", "multi_asset", "expected"),
    [
        ("0x0A152C5b8D48C26619FF3ae85Ef1B54A79e20870", True, VaultDepositPermission.whitelisted),
        ("0x74aD2F789Ed583DBd141bbdafC673fE1F033718b", True, VaultDepositPermission.permissionless),
        ("0xE9B725010A9E419412ed67d0fA5f3A5f40159D32", False, VaultDepositPermission.permissionless),
    ],
    ids=("uspc-restricted", "sentora-public-multi-asset", "core-public-legacy"),
)
def test_upshift_deployment_admission(permission_web3: Web3, address: HexAddress, multi_asset: bool, expected: VaultDepositPermission) -> None:
    """Classify restricted and public deployments through the scanner resolver.

    The actual USPC policy must reject an unapproved caller through the shared
    typed preflight. Public controls prevent a blanket protocol-wide ban.

    :param permission_web3: Real Ethereum archive connection.
    :param address: Source-reviewed vault deployment.
    :param multi_asset: Whether the deployment has a sender-policy contract.
    :param expected: Persisted account-admission classification.
    """
    features = {ERC4626Feature.upshift_like}
    if multi_asset:
        features.add(ERC4626Feature.upshift_multi_asset_like)
    vault = UpshiftVault(permission_web3, VaultSpec(1, address), features=features, default_block_identifier=UPSHIFT_PERMISSION_BLOCK)

    assert fetch_deposit_permission(vault) is expected
    if expected is VaultDepositPermission.whitelisted:
        whitelist = vault.upshift_contract.functions.sendersWhitelistAddress().call(block_identifier=UPSHIFT_PERMISSION_BLOCK)
        assert whitelist.lower() == "0x86b525d64047f0a23269fcef0212691b09453f26"
        assert vault.is_account_whitelisted(UNAPPROVED_OWNER) is False
        with pytest.raises(WhitelistingRequired):
            vault.get_deposit_manager().check_deposit_whitelist(UNAPPROVED_OWNER)
    else:
        assert vault.is_account_whitelisted(UNAPPROVED_OWNER) is True
