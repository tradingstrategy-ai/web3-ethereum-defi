"""Check guard against Umami non-standard ERC-4626 deposit and redeem calls.

- Umami gmUSDC uses deposit(uint256,uint256,address) instead of standard deposit(uint256,address)
- Umami gmUSDC uses redeem(uint256,uint256,address,address) instead of standard redeem(uint256,address,address)
- The extra uint256 parameter is a minOutAfterFees slippage protection

Uses SimpleVaultV0 + GuardV0 pattern with Anvil Arbitrum fork.
We test guard validation logic via validateCall() (view function) to avoid
Umami's handleDeposit() which does not work under Anvil.
"""

import os

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract, get_function_selector
from eth_defi.deploy import deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.umami.vault import UmamiVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation


JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
CI = os.environ.get("CI") == "true"

pytestmark = pytest.mark.skipif(
    JSON_RPC_ARBITRUM is None,
    reason="Set JSON_RPC_ARBITRUM env",
)


@pytest.fixture
def large_usdc_holder() -> HexAddress:
    return HexAddress(HexStr(USDC_WHALE[42161]))


@pytest.fixture
def anvil_arbitrum_fork(request, large_usdc_holder) -> AnvilLaunch:
    """Create a testable fork of Arbitrum."""
    mainnet_rpc = os.environ["JSON_RPC_ARBITRUM"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_usdc_holder],
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture
def web3(anvil_arbitrum_fork: AnvilLaunch):
    web3 = create_multi_provider_web3(
        anvil_arbitrum_fork.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 42161
    return web3


@pytest.fixture
def umami_vault(web3) -> UmamiVault:
    """gmUSDC vault on Arbitrum."""
    vault = create_vault_instance(
        web3,
        address="0x959f3807f0aa7921e18c78b00b2819ba91e52fef",
        features={ERC4626Feature.umami_like},
    )
    assert isinstance(vault, UmamiVault)
    return vault


@pytest.fixture()
def deployer(web3) -> str:
    return web3.eth.accounts[0]


@pytest.fixture()
def owner(web3) -> str:
    return web3.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3) -> str:
    return web3.eth.accounts[2]


@pytest.fixture()
def third_party(web3) -> str:
    return web3.eth.accounts[3]


@pytest.fixture()
def vault(
    web3: Web3,
    deployer: str,
    owner: str,
    asset_manager: str,
    umami_vault: UmamiVault,
) -> Contract:
    """Create SimpleVaultV0 with GuardV0 and whitelist the Umami vault."""

    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager)

    tx_hash = vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())

    vault_address = umami_vault.vault_address
    note = "Allow Umami gmUSDC"
    tx_hash = guard.functions.whitelistERC4626(vault_address, note).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist our vault as an allowed receiver (shares go here)
    tx_hash = guard.functions.allowReceiver(vault.address, "Allow SimpleVault as receiver").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return vault


@pytest.fixture()
def guard(
    web3: Web3,
    vault: Contract,
) -> Contract:
    return get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_umami_vault_whitelisted(
    umami_vault: UmamiVault,
    vault: Contract,
    guard: Contract,
):
    """Verify Umami-specific call sites are whitelisted after whitelistERC4626()."""
    vault_address = umami_vault.vault_address

    # Standard ERC-4626 selectors
    standard_deposit_sel = Web3.keccak(text="deposit(uint256,address)")[:4]
    standard_redeem_sel = Web3.keccak(text="redeem(uint256,address,address)")[:4]
    standard_withdraw_sel = Web3.keccak(text="withdraw(uint256,address,address)")[:4]
    assert guard.functions.isAllowedCallSite(vault_address, standard_deposit_sel).call()
    assert guard.functions.isAllowedCallSite(vault_address, standard_redeem_sel).call()
    assert guard.functions.isAllowedCallSite(vault_address, standard_withdraw_sel).call()

    # Umami non-standard selectors
    umami_deposit_sel = Web3.keccak(text="deposit(uint256,uint256,address)")[:4]
    umami_redeem_sel = Web3.keccak(text="redeem(uint256,uint256,address,address)")[:4]
    assert guard.functions.isAllowedCallSite(vault_address, umami_deposit_sel).call()
    assert guard.functions.isAllowedCallSite(vault_address, umami_redeem_sel).call()

    # Vault is an allowed approval destination
    assert guard.functions.isAllowedApprovalDestination(vault_address).call()


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_umami_deposit_validates(
    web3: Web3,
    umami_vault: UmamiVault,
    asset_manager: str,
    vault: Contract,
    guard: Contract,
):
    """Valid Umami deposit passes guard validation.

    The deposit receiver is our whitelisted vault address.
    """
    vault_address = umami_vault.vault_address
    receiver = vault.address

    # Encode deposit(uint256 assets, uint256 minOutAfterFees, address receiver)
    deposit_calldata = web3.codec.encode(
        ["uint256", "uint256", "address"],
        [1200 * 10**6, 0, receiver],
    )
    selector = Web3.keccak(text="deposit(uint256,uint256,address)")[:4]
    full_calldata = selector + deposit_calldata

    # validateCall is a view function — should not revert
    guard.functions.validateCall(asset_manager, vault_address, full_calldata).call()


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_umami_deposit_malicious_receiver(
    web3: Web3,
    umami_vault: UmamiVault,
    asset_manager: str,
    vault: Contract,
    guard: Contract,
    third_party: str,
):
    """Umami deposit to a non-whitelisted receiver is rejected by the guard.

    A compromised trade executor should not be able to redirect deposited
    shares to an arbitrary address.
    """
    vault_address = umami_vault.vault_address

    # Encode deposit with a malicious receiver (third_party is not whitelisted)
    deposit_calldata = web3.codec.encode(
        ["uint256", "uint256", "address"],
        [1200 * 10**6, 0, third_party],
    )
    selector = Web3.keccak(text="deposit(uint256,uint256,address)")[:4]
    full_calldata = selector + deposit_calldata

    with pytest.raises(Exception, match="validate_UmamiDeposit"):
        guard.functions.validateCall(asset_manager, vault_address, full_calldata).call()


@pytest.mark.skipif(CI, reason="Flaky on CI due to Anvil fork block range errors")
def test_guard_umami_redeem_malicious_receiver(
    web3: Web3,
    umami_vault: UmamiVault,
    asset_manager: str,
    vault: Contract,
    guard: Contract,
    third_party: str,
):
    """Umami redeem to a non-whitelisted receiver is rejected by the guard.

    A compromised trade executor should not be able to redirect redeemed
    assets to an arbitrary address.
    """
    vault_address = umami_vault.vault_address

    # Encode redeem(uint256 shares, uint256 minOutAfterFees, address receiver, address owner)
    redeem_calldata = web3.codec.encode(
        ["uint256", "uint256", "address", "address"],
        [100 * 10**6, 0, third_party, vault.address],
    )
    selector = Web3.keccak(text="redeem(uint256,uint256,address,address)")[:4]
    full_calldata = selector + redeem_calldata

    with pytest.raises(Exception, match="validate_UmamiRedeem"):
        guard.functions.validateCall(asset_manager, vault_address, full_calldata).call()
