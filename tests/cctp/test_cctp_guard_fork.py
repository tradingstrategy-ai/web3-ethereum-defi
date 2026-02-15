"""Guard integration tests for CCTP V2 on Ethereum mainnet fork.

Tests validate that:
1. CCTP whitelisting works in SimpleVaultV0 guard
2. depositForBurn is allowed when properly whitelisted
3. depositForBurn is rejected when not whitelisted (wrong domain, wrong token, wrong recipient)
"""

import os

import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.cctp.constants import (
    CCTP_DOMAIN_ARBITRUM,
    CCTP_DOMAIN_BASE,
    TOKEN_MESSENGER_V2,
)
from eth_defi.cctp.transfer import (
    encode_mint_recipient,
    prepare_approve_for_burn,
    prepare_deposit_for_burn,
)
from eth_defi.deploy import deploy_contract
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ETHEREUM"),
    reason="JSON_RPC_ETHEREUM environment variable not set",
)


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
def safe_address(web3) -> str:
    """Simulated Safe address that receives funds."""
    return web3.eth.accounts[3]


@pytest.fixture()
def vault(
    web3: Web3,
    deployer: str,
    owner: str,
    asset_manager: str,
) -> Contract:
    """Deploy SimpleVaultV0 with guard."""
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager)
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    return vault


@pytest.fixture()
def guard(
    web3: Web3,
    vault: Contract,
    owner: str,
    safe_address: str,
    usdc: Contract,
) -> Contract:
    """Get guard contract with CCTP whitelisted."""
    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())

    # Whitelist CCTP TokenMessengerV2
    guard.functions.whitelistCCTP(
        Web3.to_checksum_address(TOKEN_MESSENGER_V2),
        "Allow CCTP",
    ).transact({"from": owner})

    # Whitelist Arbitrum as destination
    guard.functions.whitelistCCTPDestination(
        CCTP_DOMAIN_ARBITRUM,
        "Allow Arbitrum",
    ).transact({"from": owner})

    # Whitelist USDC as allowed asset
    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})

    # Whitelist safe as receiver (this address will be the mintRecipient)
    guard.functions.allowReceiver(safe_address, "Allow Safe as receiver").transact({"from": owner})

    # Whitelist asset_manager as sender
    guard.functions.allowSender(
        web3.eth.accounts[2],
        "Allow asset manager",
    ).transact({"from": owner})

    return guard


@pytest.fixture()
def funded_vault(
    web3: Web3,
    vault: Contract,
    usdc: Contract,
    ethereum_usdc_whale: HexAddress,
) -> Contract:
    """Fund the vault with USDC."""
    amount = 1000 * 10**6  # 1000 USDC
    usdc.functions.transfer(vault.address, amount).transact({"from": ethereum_usdc_whale})
    assert usdc.functions.balanceOf(vault.address).call() >= amount
    return vault


def test_cctp_guard_whitelist_status(
    guard: Contract,
    safe_address: str,
):
    """Verify CCTP whitelisting state is correct."""
    assert guard.functions.isAllowedCCTPMessenger(Web3.to_checksum_address(TOKEN_MESSENGER_V2)).call() is True
    assert guard.functions.isAllowedCCTPDestination(CCTP_DOMAIN_ARBITRUM).call() is True
    assert guard.functions.isAllowedCCTPDestination(CCTP_DOMAIN_BASE).call() is False


def test_cctp_deposit_for_burn_through_vault(
    web3: Web3,
    funded_vault: Contract,
    guard: Contract,
    asset_manager: str,
    safe_address: str,
    usdc: Contract,
):
    """Test that depositForBurn succeeds through a guarded vault.

    The vault acts as the caller, so we encode the transaction
    and execute it through the vault's performCall.
    """
    amount = 100 * 10**6  # 100 USDC

    vault_balance_before = usdc.functions.balanceOf(funded_vault.address).call()

    # First: approve USDC to TokenMessengerV2 through the vault
    approve_fn = prepare_approve_for_burn(web3, amount)
    target, call_data = encode_simple_vault_transaction(approve_fn)
    tx_hash = funded_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Second: call depositForBurn through the vault
    burn_fn = prepare_deposit_for_burn(
        web3,
        amount=amount,
        destination_chain_id=42161,  # Arbitrum
        mint_recipient=safe_address,
    )
    target, call_data = encode_simple_vault_transaction(burn_fn)
    tx_hash = funded_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify USDC was burned
    vault_balance_after = usdc.functions.balanceOf(funded_vault.address).call()
    assert vault_balance_after == vault_balance_before - amount


def test_cctp_wrong_destination_rejected(
    web3: Web3,
    funded_vault: Contract,
    guard: Contract,
    asset_manager: str,
    safe_address: str,
    usdc: Contract,
):
    """Test that depositForBurn to a non-whitelisted domain is rejected."""
    amount = 100 * 10**6

    # Approve first
    approve_fn = prepare_approve_for_burn(web3, amount)
    target, call_data = encode_simple_vault_transaction(approve_fn)
    funded_vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    # Try to burn to Base (domain 6) which is not whitelisted
    burn_fn = prepare_deposit_for_burn(
        web3,
        amount=amount,
        destination_chain_id=8453,  # Base - not whitelisted
        mint_recipient=safe_address,
    )
    target, call_data = encode_simple_vault_transaction(burn_fn)

    with pytest.raises(TransactionAssertionError, match="CCTP destination not allowed"):
        tx_hash = funded_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_cctp_wrong_recipient_rejected(
    web3: Web3,
    funded_vault: Contract,
    guard: Contract,
    asset_manager: str,
    usdc: Contract,
):
    """Test that depositForBurn to a non-whitelisted recipient is rejected."""
    amount = 100 * 10**6
    attacker = "0x0000000000000000000000000000000000000Bad"

    # Approve first
    approve_fn = prepare_approve_for_burn(web3, amount)
    target, call_data = encode_simple_vault_transaction(approve_fn)
    funded_vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    # Try to burn with non-whitelisted recipient
    burn_fn = prepare_deposit_for_burn(
        web3,
        amount=amount,
        destination_chain_id=42161,
        mint_recipient=attacker,
    )
    target, call_data = encode_simple_vault_transaction(burn_fn)

    with pytest.raises(TransactionAssertionError, match="CCTP mint recipient not allowed"):
        tx_hash = funded_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_cctp_destination_removal(
    web3: Web3,
    guard: Contract,
    owner: str,
):
    """Test that CCTP destinations can be removed."""
    assert guard.functions.isAllowedCCTPDestination(CCTP_DOMAIN_ARBITRUM).call() is True

    guard.functions.removeCCTPDestination(
        CCTP_DOMAIN_ARBITRUM,
        "Remove Arbitrum",
    ).transact({"from": owner})

    assert guard.functions.isAllowedCCTPDestination(CCTP_DOMAIN_ARBITRUM).call() is False
