"""Fixed-block regressions for vault deposit-permission classifications."""

import datetime
import os
from unittest.mock import patch

import pytest
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.erc_4626 import scan as scan_module
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault, EulerVault
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoGateAddressMissing, MorphoV2Vault
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Block used to independently verify the TESS AccessManager configuration.
PERMISSION_REGRESSION_BLOCK = 25_651_723

#: IPOR role assigned to allow-listed depositors.
IPOR_WHITELIST_ROLE = 800

#: Trade-executor Safe used by the production vault simulation.
EXECUTOR_SAFE = "0xa2b04c6a053AB2EFBC699f5DD0F0957742A41629"

#: Exact vaults from the production classification report.
TESS_USDT_SUSDS = "0x9fec8a63a6c6ef9eadddfbd79daba5918965794e"
IPOR_BITCOIN_DOLLAR_USDC = "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8"
MORPHO_9S_USR = "0x00b6f2c15e4439749f192d10c70f65354848cf4b"
MORPHO_V2_APYX_USDC = "0x069662d2588fcac24b5c209456db965d151556f0"
EULER_VAULTS = (
    "0x3cd3718f8f047aa32f775e2cb4245a164e1c99fb",
    "0x8aff4fe319c30475d27ec623d7d44bd5ecfe9616",
    "0x9bd52f2805c6af014132874124686e7b248c2cbb",
    "0xab2726daf820aa9270d14db9b18c8d187cbf2f30",
)

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:vault-permission-regressions"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create one shared fixed Ethereum fork for all permission controls."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, PERMISSION_REGRESSION_BLOCK)


def test_ipor_tess_restricted_and_public_controls(web3: Web3) -> None:
    """Test IPOR's selector role retains both restricted and public controls.

    1. Bind the exact TESS and Bitcoin Dollar IPOR deployments.
    2. Read TESS authority, selector role, and executor Safe access.
    3. Confirm TESS remains restricted while Bitcoin Dollar remains public.
    """
    # 1. Bind the exact TESS and Bitcoin Dollar IPOR deployments.
    tess = IPORVault(web3, VaultSpec(1, TESS_USDT_SUSDS))
    public = IPORVault(web3, VaultSpec(1, IPOR_BITCOIN_DOLLAR_USDC))

    # 2. Read TESS authority, selector role, and executor Safe access.
    access_manager = tess.access_manager
    assert access_manager is not None
    authority = tess.plasma_vault.functions.authority().call()
    configured_manager = tess.plasma_vault.functions.getAccessManagerAddress().call()
    role = access_manager.functions.getTargetFunctionRole(
        tess.address,
        tess.get_deposit_function_selector(),
    ).call()
    admitted, delay = tess.fetch_selector_access(EXECUTOR_SAFE, tess.get_deposit_function_selector())

    # 3. Confirm TESS remains restricted while Bitcoin Dollar remains public.
    assert authority == configured_manager == access_manager.address
    assert role == IPOR_WHITELIST_ROLE
    assert admitted is False
    assert delay == 0
    assert tess.is_whitelisted_deposit() is True
    assert public.is_whitelisted_deposit() is False


def test_morpho_9s_is_permissionless(web3: Web3) -> None:
    """Test the exact 9S Mount Kosciuszko MetaMorpho vault is public.

    1. Autodetect the production vault at the fixed block.
    2. Confirm it uses the canonical MetaMorpho V1 adapter.
    3. Confirm deposits do not require identity approval.
    """
    # 1. Autodetect the production vault at the fixed block.
    vault = create_vault_instance_autodetect(web3, MORPHO_9S_USR)

    # 2. Confirm it uses the canonical MetaMorpho V1 adapter.
    assert isinstance(vault, MorphoV1Vault)

    # 3. Confirm deposits do not require identity approval.
    assert vault.is_whitelisted_deposit() is False


def test_morpho_v2_gate_getters_are_permissionless(web3: Web3) -> None:
    """Test Morpho V2's deployed gate getters and public default.

    1. Autodetect the production Apyx vault at the fixed block.
    2. Read the source-defined share-receiver and asset-sender gate slots.
    3. Confirm both gates are disabled and deposits are permissionless.
    """
    # 1. Autodetect the production Apyx vault at the fixed block.
    vault = create_vault_instance_autodetect(web3, MORPHO_V2_APYX_USDC)
    assert isinstance(vault, MorphoV2Vault)

    # 2. Read the source-defined share-receiver and asset-sender gate slots.
    receive_shares_gate, send_assets_gate = vault.fetch_deposit_gates()

    # 3. Confirm both gates are disabled and deposits are permissionless.
    assert int(receive_shares_gate, 16) == 0
    assert int(send_assets_gate, 16) == 0
    assert vault.is_whitelisted_deposit() is False


def test_morpho_v2_empty_gate_response_is_safe_for_permission_scans(web3: Web3) -> None:
    """Test a malformed gate response from the deployed Morpho V2 Apyx vault.

    1. Autodetect the production Morpho V2 vault at the fixed block.
    2. Simulate the empty gate response that interrupted the permission migration.
    3. Check typed diagnostics and that the scanner reports an unknown policy.
    """
    # 1. Autodetect the production Morpho V2 vault at the fixed block.
    vault = create_vault_instance_autodetect(web3, MORPHO_V2_APYX_USDC)
    assert isinstance(vault, MorphoV2Vault)

    # 2. Simulate the empty gate response that interrupted the permission migration.
    with patch.object(EncodedCall, "call", return_value=HexBytes("0x")):
        with pytest.raises(MorphoGateAddressMissing) as exception_info:
            vault.fetch_deposit_gates()

        error = exception_info.value
        assert error.vault_address == vault.address
        assert error.function_name == "receiveSharesGate"
        assert error.response == HexBytes("0x")
        assert vault.address in str(error)
        assert "expected one 32-byte ABI-encoded address" in str(error)

        # 3. Check typed diagnostics and that the scanner reports an unknown policy.
        assert fetch_deposit_permission(vault) == VaultDepositPermission.unknown


def test_morpho_v2_empty_gate_response_keeps_scan_record(web3: Web3) -> None:
    """Test a missing Morpho V2 gate address retains the vault JSON row.

    1. Autodetect the production Morpho V2 vault and its persisted detection envelope.
    2. Simulate an empty response from only the receive-shares gate getter.
    3. Create a complete scan record and confirm the permission is unknown.
    """
    # 1. Autodetect the production Morpho V2 vault and its persisted detection envelope.
    vault = create_vault_instance_autodetect(web3, MORPHO_V2_APYX_USDC)
    assert isinstance(vault, MorphoV2Vault)
    scan_timestamp = datetime.datetime(2026, 8, 2, 9, 0)  # noqa: DTZ001 - Repository convention is naive UTC.
    detection = ERC4262VaultDetection(
        chain=1,
        address=vault.address,
        first_seen_at_block=PERMISSION_REGRESSION_BLOCK,
        first_seen_at=scan_timestamp,
        features={ERC4626Feature.morpho_v2_like},
        updated_at=scan_timestamp,
        deposit_count=1,
        redeem_count=1,
    )
    original_call = EncodedCall.call

    def return_empty_receive_shares_gate(call, *args, **kwargs):
        if call.func_name == "receiveSharesGate":
            return HexBytes("0x")
        return original_call(call, *args, **kwargs)

    # 2. Simulate an empty response from only the receive-shares gate getter.
    with (
        patch.object(EncodedCall, "call", new=return_empty_receive_shares_gate),
        patch.object(scan_module, "create_vault_instance", return_value=vault),
    ):
        # 3. Create a complete scan record and confirm the permission is unknown.
        record = scan_module.create_vault_scan_record(
            web3,
            detection,
            PERMISSION_REGRESSION_BLOCK,
            token_cache=TokenDiskCache(),
        )

    assert record["Name"] == vault.name
    assert record["Protocol"] == "Morpho"
    assert record["_detection_data"] is detection
    assert record["_deposit_permission"] == VaultDepositPermission.unknown.value


@pytest.mark.parametrize("vault_address", EULER_VAULTS)
def test_reported_euler_vaults_are_permissionless(web3: Web3, vault_address: str) -> None:
    """Test each reported Euler false positive against its deployed hook state.

    1. Autodetect the exact production vault at the fixed block.
    2. Confirm the deployment uses an Euler EVK or EulerEarn adapter.
    3. Confirm its canonical deposit path does not require identity approval.
    """
    # 1. Autodetect the exact production vault at the fixed block.
    vault = create_vault_instance_autodetect(web3, vault_address)

    # 2. Confirm the deployment uses an Euler EVK or EulerEarn adapter.
    assert isinstance(vault, (EulerVault, EulerEarnVault))

    # 3. Confirm its canonical deposit path does not require identity approval.
    assert vault.is_whitelisted_deposit() is False
