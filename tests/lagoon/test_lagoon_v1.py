"""Fixed-block characterisation tests for a deployed Lagoon v1 vault."""

import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

#: Base block that reproduced the production scanner failure.
LAGOON_V1_BASE_BLOCK = 51_649_628

#: Production Lagoon v1 proxy characterised by this test.
LAGOON_V1_VAULT: HexAddress = "0x7eea189f34e10e7fe5386ba8d49ab41f95b7d54f"

#: Exact fixed-block addresses returned by public views or characterised slots.
LAGOON_V1_ASSET: HexAddress = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
LAGOON_V1_SAFE: HexAddress = "0xA471116E04286cDc61b888e8DcC4b4733bA7D51a"
LAGOON_V1_SILO: HexAddress = "0xA7e9CD8f80485609368536B82899447212bF2511"
LAGOON_V1_WHITELIST_MANAGER: HexAddress = "0xaD33cb035CF7b091a49918D124700652aa2f1b9c"
LAGOON_V1_FEE_RECEIVER: HexAddress = "0x42dABf368DD76138bc1DB1a099e5D55AF1Fe40A4"
LAGOON_V1_FEE_REGISTRY: HexAddress = "0xc74A3Fd2bB0A0EE73c135BAA0cDcdB17C1999Da2"
LAGOON_V1_VALUATION_MANAGER: HexAddress = "0x7665C5fAa40007b51Bb2B36A2d839733d7873aFf"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests"),
    pytest.mark.xdist_group("fork:base:51649628"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create Web3 on the shared fixed-block Base fork.

    :return:
        Web3 connection backed by the pooled Anvil process and committed RPC
        cache seed for :data:`LAGOON_V1_BASE_BLOCK`.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, LAGOON_V1_BASE_BLOCK)


@pytest.fixture(scope="module")
def lagoon_v1(web3: Web3) -> LagoonVault:
    """Construct the production Lagoon v1 vault at the selected block.

    :param web3:
        Fixed-block Base fork connection.
    :return:
        Lagoon adapter pinned to the characterised historical state.
    """
    return LagoonVault(
        web3,
        VaultSpec(8453, LAGOON_V1_VAULT),
        default_block_identifier=LAGOON_V1_BASE_BLOCK,
    )


def test_lagoon_v1_fixed_block_metadata(lagoon_v1: LagoonVault) -> None:
    """Read the deployed Lagoon v1 metadata and modern storage fields.

    The Safe slot is cross-checked against an independent public view so the
    expected role values do not validate storage decoding circularly.
    """
    vault = lagoon_v1
    info = vault.fetch_vault_info()

    assert vault.version is LagoonVersion.v_1_0_0
    assert vault.vault_abi == "lagoon/v0.6.0/Vault.json"
    assert vault.name == "Happyfund Vault 1"
    assert vault.symbol == "HFV1"
    assert vault.denomination_token.address == Web3.to_checksum_address(LAGOON_V1_ASSET)
    assert vault.silo_address == Web3.to_checksum_address(LAGOON_V1_SILO)

    assert info["asset"] == Web3.to_checksum_address(LAGOON_V1_ASSET)
    assert info["safe"] == Web3.to_checksum_address(LAGOON_V1_SAFE)
    assert info["whitelistManager"] == Web3.to_checksum_address(LAGOON_V1_WHITELIST_MANAGER)
    assert info["feeReceiver"] == Web3.to_checksum_address(LAGOON_V1_FEE_RECEIVER)
    assert info["feeRegistry"] == Web3.to_checksum_address(LAGOON_V1_FEE_REGISTRY)
    assert info["valuationManager"] == Web3.to_checksum_address(LAGOON_V1_VALUATION_MANAGER)
    assert info["broken"] is False

    assert vault.vault_contract.functions.safe().call(block_identifier=LAGOON_V1_BASE_BLOCK) == info["safe"]


def test_lagoon_v1_fixed_block_erc4626_and_access_views(lagoon_v1: LagoonVault) -> None:
    """Read the v1 ERC-4626, access-policy and fee views at one block.

    Exact results characterise only the compatibility surface consumed by the
    adapter; they do not imply that the unverified implementation is v0.6.
    """
    vault = lagoon_v1
    contract = vault.vault_contract

    assert contract.functions.paused().call(block_identifier=LAGOON_V1_BASE_BLOCK) is False
    assert vault.fetch_total_assets(LAGOON_V1_BASE_BLOCK) == 0
    assert vault.fetch_total_supply(LAGOON_V1_BASE_BLOCK) == 0
    assert vault.fetch_share_price(LAGOON_V1_BASE_BLOCK) == 0
    assert contract.functions.maxDeposit(ZERO_ADDRESS_STR).call(block_identifier=LAGOON_V1_BASE_BLOCK) == 0
    assert contract.functions.maxRedeem(ZERO_ADDRESS_STR).call(block_identifier=LAGOON_V1_BASE_BLOCK) == 0

    assert vault.is_whitelisted_deposit() is True
    assert vault.is_account_whitelisted(ZERO_ADDRESS_STR) is False

    assert vault.get_management_fee(LAGOON_V1_BASE_BLOCK) == 0.0
    assert vault.get_performance_fee(LAGOON_V1_BASE_BLOCK) == pytest.approx(0.2)
    assert vault.get_deposit_fee(LAGOON_V1_BASE_BLOCK) == 0.0
    assert vault.get_withdraw_fee(LAGOON_V1_BASE_BLOCK) == 0.0
    assert vault.has_custom_fees() is False

    fee_data = vault.get_fee_data()
    assert fee_data.management == 0.0
    assert fee_data.performance == pytest.approx(0.2)
    assert fee_data.deposit == 0.0
    assert fee_data.withdraw == 0.0


def test_lagoon_v1_fixed_block_async_capability_and_pending_state(lagoon_v1: LagoonVault) -> None:
    """Expose Lagoon v1 as a two-way asynchronous ERC-7540 vault.

    Capability and pending-state reads cover the scanner-facing asynchronous
    interface without mutating the historical fork.
    """
    vault = lagoon_v1
    capability = vault.get_deposit_manager_capability()
    flow_manager = vault.get_flow_manager()

    assert capability.can_deposit is True
    assert capability.can_redeem is True
    assert capability.deposit_flow == "asynchronous"
    assert capability.redemption_flow == "asynchronous"
    assert capability.supports_anvil_settlement is True
    assert flow_manager.fetch_pending_deposit(LAGOON_V1_BASE_BLOCK) == 0
    assert flow_manager.fetch_pending_redemption(LAGOON_V1_BASE_BLOCK) == 0


def test_lagoon_v1_scanner_style_construction(web3: Web3) -> None:
    """Construct the v1 adapter through the scanner classification path.

    This is the direct regression for the production ``NotImplementedError``
    raised before ``v1.0.0`` was recognised.
    """
    vault = create_vault_instance(
        web3,
        LAGOON_V1_VAULT,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier=LAGOON_V1_BASE_BLOCK,
        require_denomination_token=True,
    )

    assert isinstance(vault, LagoonVault)
    assert vault.version is LagoonVersion.v_1_0_0
