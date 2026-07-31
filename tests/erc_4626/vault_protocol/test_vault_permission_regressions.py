"""Fixed-block regressions for vault deposit-permission classifications."""

import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault, EulerVault
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import VaultSpec

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
