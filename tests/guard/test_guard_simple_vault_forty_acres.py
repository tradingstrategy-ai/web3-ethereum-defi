"""Certify guarded 40acres Aerodrome deposits and redemptions through GuardV0."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_account import Account
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.forty_acres.deposit_redeem import FortyAcresDepositManager
from eth_defi.erc_4626.vault_protocol.forty_acres.vault import FortyAcresVault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil, set_balance
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

#: A real 40acres vault whose direct USDC balance is preflighted before redemption.
AERODROME_USDC_VAULT: HexAddress = "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run this test"),
    pytest.mark.xdist_group("fork:base:midnight"),
]


@pytest.fixture
def anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared fixed Base fork without modifying vault liquidity."""
    return anvil_fork_pool.get_launch(JSON_RPC_BASE, BASE_MIDNIGHT_BLOCK)


@pytest.fixture
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared Base fork at its cacheable midnight block."""
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, BASE_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Restore all test mutations while preserving Base's real vault state."""
    yield from evm_snapshot_revert(anvil_fork)


@pytest.fixture
def protocol_vault(web3: Web3) -> FortyAcresVault:
    """Open the Aerodrome 40acres USDC supply vault."""
    vault = create_vault_instance_autodetect(web3, AERODROME_USDC_VAULT)
    assert isinstance(vault, FortyAcresVault)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, protocol_vault: FortyAcresVault) -> tuple[Contract, Contract, HotWallet]:
    """Deploy a SimpleVault with governance distinct from its asset manager."""
    deployer = HotWallet(Account.create())
    asset_manager = HotWallet(Account.create())
    set_balance(web3, deployer.address, Web3.to_wei(10, "ether"))
    set_balance(web3, asset_manager.address, Web3.to_wei(10, "ether"))
    deployer.sync_nonce(web3)
    asset_manager.sync_nonce(web3)
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager.address, libraries=GUARD_LIBRARIES)
    initialise_hash = _broadcast(web3, deployer, simple_vault.functions.initialiseOwnership(deployer.address))
    assert_transaction_success_with_explanation(web3, initialise_hash)

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = _broadcast(web3, deployer, guard.functions.whitelistERC4626(protocol_vault.address, "Allow liquid 40acres Aerodrome USDC"))
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, guard, asset_manager


def _broadcast(web3: Web3, control: HotWallet, func: ContractFunction, gas: int = 2_000_000) -> HexBytes:
    """Broadcast a call from an Anvil-funded private-key wallet."""
    signed = control.sign_bound_call_with_new_nonce(func, {"gas": gas}, web3=web3, fill_gas_price=True)
    return web3.eth.send_raw_transaction(signed.rawTransaction)


def _perform_guarded_call(web3: Web3, simple_vault: Contract, control: HotWallet, func: ContractFunction) -> HexBytes:
    """Run one manager-generated protocol call through SimpleVaultV0."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = _broadcast(web3, control, simple_vault.functions.performCall(target, call_data))
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def _assert_guarded_call_rejected(web3: Web3, simple_vault: Contract, control: HotWallet, func: ContractFunction, expected_error: str) -> None:
    """Assert that GuardV0 rejects an altered ERC-4626 receiver or owner."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = _broadcast(web3, control, simple_vault.functions.performCall(target, call_data))
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_guarded_forty_acres_aerodrome_deposit_and_redeem(
    web3: Web3,
    protocol_vault: FortyAcresVault,
    guarded_simple_vault: tuple[Contract, Contract, HotWallet],
) -> None:
    """Redeem a direct USDC contribution through GuardV0.

    1. Create the specialised 40acres redemption manager.
    2. Fund and deposit USDC through the guarded SimpleVault.
    3. Redeem the resulting shares while the direct contribution is available.
    """
    # 1. Create the specialised 40acres redemption manager.
    simple_vault, guard, asset_manager = guarded_simple_vault
    manager = protocol_vault.get_deposit_manager()
    assert isinstance(manager, FortyAcresDepositManager)

    # 2. Fund and deposit USDC through the guarded SimpleVault.
    raw_amount = protocol_vault.denomination_token.convert_to_raw(Decimal(10))
    # The test deposit supplies the direct USDC needed for this guarded redemption.
    fund_erc20_on_anvil(web3, protocol_vault.denomination_token.address, simple_vault.address, raw_amount)
    approval = protocol_vault.denomination_token.contract.functions.approve(protocol_vault.address, raw_amount)
    _perform_guarded_call(web3, simple_vault, asset_manager, approval)
    assert guard.functions.isAllowedApprovalDestination(protocol_vault.address).call() is True

    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    deposit_hash = _perform_guarded_call(web3, simple_vault, asset_manager, deposit_request.funcs[0])
    deposit_ticket = deposit_request.parse_deposit_transaction([deposit_hash])
    deposit_analysis = manager.analyse_deposit(deposit_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == Decimal(10)
    assert deposit_analysis.share_count > 0

    raw_shares = protocol_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares > 0
    assert manager.force_settle(None).settlement_required is False

    # 3. Redeem the resulting shares while the direct contribution is available.
    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    redemption_hash = _perform_guarded_call(web3, simple_vault, asset_manager, redemption_request.funcs[0])
    redemption_ticket = redemption_request.parse_redeem_transaction([redemption_hash])
    redemption_analysis = manager.analyse_redemption(redemption_hash, redemption_ticket)
    assert redemption_analysis.share_count == protocol_vault.share_token.convert_to_decimals(raw_shares)
    assert redemption_analysis.denomination_amount > 0
    assert protocol_vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert protocol_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == protocol_vault.denomination_token.convert_to_raw(redemption_analysis.denomination_amount)


def test_guarded_forty_acres_aerodrome_rejects_substituted_addresses(
    web3: Web3,
    protocol_vault: FortyAcresVault,
    guarded_simple_vault: tuple[Contract, Contract, HotWallet],
) -> None:
    """Reject substituted ERC-4626 addresses for 40acres.

    1. Use the guarded Aerodrome vault and its asset manager.
    2. Attempt deposit and redemption calls with substituted receiver or owner addresses.
    3. Verify GuardV0 rejects every altered address.
    """
    # 1. Use the guarded Aerodrome vault and its asset manager.
    simple_vault, _guard, asset_manager = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])

    # 2. Attempt deposit and redemption calls with substituted receiver or owner addresses.
    # 3. Verify GuardV0 rejects every altered address.
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        protocol_vault.vault_contract.functions.deposit(1, outsider),
        "Receiver not whitelisted",
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        protocol_vault.vault_contract.functions.redeem(1, outsider, simple_vault.address),
        "Receiver not whitelisted",
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        protocol_vault.vault_contract.functions.redeem(1, simple_vault.address, outsider),
        "Owner not whitelisted",
    )
