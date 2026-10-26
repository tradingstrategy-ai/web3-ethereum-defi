"""Fixed-block characterisation tests for a deployed Lagoon v0.5.2 vault.

The RockSolid rETH vault was upgraded in place from Lagoon v0.5.0 to v0.5.2 at
Ethereum block 26,136,796, after the canonical Ethereum midnight block, so
this module uses the block at which the production scanner rejected the
unknown version.
"""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import VaultSpec

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Ethereum block that reproduced the production scanner failure.
LAGOON_V052_ETHEREUM_BLOCK = 26_279_104

#: Production Lagoon v0.5.2 proxy characterised by this test.
LAGOON_V052_VAULT: HexAddress = "0x936facdf10c8c36294e7b9d28345255539d81bc7"

#: Exact fixed-block addresses returned by public views.
LAGOON_V052_ASSET: HexAddress = "0xae78736Cd615f374D3085123A210448E74Fc6393"
LAGOON_V052_SAFE: HexAddress = "0x9Ca1d6E730Eb9fbfD45c9FF5F0AC4E3d172d8F4d"
LAGOON_V052_SILO: HexAddress = "0x81Cc01446cbD882e31D054fa89E5f4439484eC0a"
LAGOON_V052_FEE_RECEIVER: HexAddress = "0xe841d56c504C55693A098948Bd3b74F4e01e593A"
LAGOON_V052_FEE_REGISTRY: HexAddress = "0x6dA4D1859bA1d02D095D2246142CdAd52233e27C"
LAGOON_V052_VALUATION_MANAGER: HexAddress = "0x5856FcBE7B15a5A2CDCCfF5051b59bB8BDD54204"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:26279104"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create Web3 on the shared fixed-block Ethereum fork.

    :return:
        Web3 connection backed by the pooled Anvil process and committed RPC
        cache seed for :data:`LAGOON_V052_ETHEREUM_BLOCK`.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, LAGOON_V052_ETHEREUM_BLOCK)


@pytest.fixture(scope="module")
def lagoon_v052(web3: Web3) -> LagoonVault:
    """Construct the production Lagoon v0.5.2 vault at the selected block.

    :param web3:
        Fixed-block Ethereum fork connection.
    :return:
        Lagoon adapter pinned to the characterised historical state.
    """
    return LagoonVault(
        web3,
        VaultSpec(1, LAGOON_V052_VAULT),
        default_block_identifier=LAGOON_V052_ETHEREUM_BLOCK,
    )


def test_lagoon_v052_fixed_block_metadata(lagoon_v052: LagoonVault) -> None:
    """Read the deployed v0.5.2 metadata through the v0.5.0 ABI and storage layout.

    The Safe returned by the role getter is cross-checked against the
    independent ``safe()`` view, and the Silo slot must hold deployed code.
    """
    vault = lagoon_v052
    info = vault.fetch_vault_info()

    assert vault.version is LagoonVersion.v_0_5_2
    assert vault.vault_abi == "lagoon/v0.5.0/Vault.json"
    assert vault.name == "RockSolid rETH Vault"
    assert vault.symbol == "rock.rETH"
    assert vault.denomination_token.address == Web3.to_checksum_address(LAGOON_V052_ASSET)
    assert vault.silo_address == Web3.to_checksum_address(LAGOON_V052_SILO)
    assert len(vault.web3.eth.get_code(vault.silo_address)) == 257

    assert info["asset"] == Web3.to_checksum_address(LAGOON_V052_ASSET)
    assert info["safe"] == Web3.to_checksum_address(LAGOON_V052_SAFE)
    assert info["whitelistManager"] == ZERO_ADDRESS_STR
    assert info["feeReceiver"] == Web3.to_checksum_address(LAGOON_V052_FEE_RECEIVER)
    assert info["feeRegistry"] == Web3.to_checksum_address(LAGOON_V052_FEE_REGISTRY)
    assert info["valuationManager"] == Web3.to_checksum_address(LAGOON_V052_VALUATION_MANAGER)
    assert info["broken"] is False

    assert vault.vault_contract.functions.safe().call(block_identifier=LAGOON_V052_ETHEREUM_BLOCK) == info["safe"]


def test_lagoon_v052_fixed_block_erc4626_access_and_fees(lagoon_v052: LagoonVault) -> None:
    """Read the v0.5.2 ERC-4626, whitelist and fee views at one block.

    ``isWhitelisted(0x0)`` is unconditionally true in v0.5.2, so the policy
    must come from the storage flag rather than the v0.5.0 sentinel.
    """
    vault = lagoon_v052
    block = LAGOON_V052_ETHEREUM_BLOCK

    assert vault.vault_contract.functions.paused().call(block_identifier=block) is False
    assert vault.fetch_total_assets(block) == Decimal("8161.861502741725374792")
    assert vault.fetch_total_supply(block) == Decimal("7989.011275768287077705")
    assert vault.fetch_share_price(block) == Decimal("1.021635997372755696842129249")

    assert vault.is_account_whitelisted(ZERO_ADDRESS_STR) is True
    assert vault._fetch_v0_5_2_whitelist_activated(block) is False
    assert vault.is_whitelisted_deposit() is False

    assert vault.get_management_fee(block) == pytest.approx(0.01)
    assert vault.get_performance_fee(block) == pytest.approx(0.1)
    assert vault.get_deposit_fee(block) == 0.0
    assert vault.get_withdraw_fee(block) == 0.0
    assert vault.has_custom_fees() is False
