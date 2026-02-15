"""Two-chain CCTP emulation test using dual Anvil forks.

Tests the conceptual cross-chain flow:
1. Source (Ethereum fork): depositForBurn through a guarded vault
2. Destination (Arbitrum fork): emulate USDC minting via Anvil impersonation

The attestation/relay step is spoofed since Circle's Iris service
does not work on forked chains. Instead, we directly transfer USDC
on the destination chain to simulate the mint.
"""

import logging
import os

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.cctp.constants import (
    CCTP_DOMAIN_ARBITRUM,
    TOKEN_MESSENGER_V2,
)
from eth_defi.cctp.transfer import (
    prepare_approve_for_burn,
    prepare_deposit_for_burn,
)
from eth_defi.deploy import deploy_contract
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import fetch_erc20_details, USDC_NATIVE_TOKEN, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation


JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = pytest.mark.skipif(
    not JSON_RPC_ETHEREUM or not JSON_RPC_ARBITRUM,
    reason="JSON_RPC_ETHEREUM and JSON_RPC_ARBITRUM environment variables required",
)

#: Circle/Centre USDC deployer on Ethereum
ETHEREUM_USDC_WHALE = HexAddress(HexStr("0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"))

#: Arbitrum USDC whale
ARBITRUM_USDC_WHALE = USDC_WHALE[42161]


@pytest.fixture()
def anvil_ethereum(request) -> AnvilLaunch:
    """Ethereum mainnet fork."""
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        unlocked_addresses=[ETHEREUM_USDC_WHALE],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_arbitrum(request) -> AnvilLaunch:
    """Arbitrum mainnet fork."""
    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        unlocked_addresses=[ARBITRUM_USDC_WHALE],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_ethereum(anvil_ethereum) -> Web3:
    """Web3 connected to Ethereum fork."""
    web3 = create_multi_provider_web3(anvil_ethereum.json_rpc_url)
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture()
def web3_arbitrum(anvil_arbitrum) -> Web3:
    """Web3 connected to Arbitrum fork."""
    web3 = create_multi_provider_web3(anvil_arbitrum.json_rpc_url)
    assert web3.eth.chain_id == 42161
    return web3


@pytest.fixture()
def usdc_ethereum(web3_ethereum) -> Contract:
    """USDC on Ethereum."""
    return fetch_erc20_details(web3_ethereum, USDC_NATIVE_TOKEN[1]).contract


@pytest.fixture()
def usdc_arbitrum(web3_arbitrum) -> Contract:
    """USDC on Arbitrum."""
    return fetch_erc20_details(web3_arbitrum, USDC_NATIVE_TOKEN[42161]).contract


@pytest.fixture()
def deployer(web3_ethereum) -> str:
    return web3_ethereum.eth.accounts[0]


@pytest.fixture()
def owner(web3_ethereum) -> str:
    return web3_ethereum.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3_ethereum) -> str:
    return web3_ethereum.eth.accounts[2]


@pytest.fixture()
def recipient_address(web3_ethereum) -> str:
    """Address that receives USDC on both chains."""
    return web3_ethereum.eth.accounts[3]


@pytest.fixture()
def vault(
    web3_ethereum: Web3,
    deployer: str,
    owner: str,
    asset_manager: str,
    usdc_ethereum: Contract,
) -> Contract:
    """Deploy guarded vault on Ethereum fork with CCTP whitelisted."""
    vault = deploy_contract(web3_ethereum, "guard/SimpleVaultV0.json", deployer, asset_manager)
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})

    guard = get_deployed_contract(web3_ethereum, "guard/GuardV0.json", vault.functions.guard().call())

    # Whitelist CCTP
    guard.functions.whitelistCCTP(
        Web3.to_checksum_address(TOKEN_MESSENGER_V2),
        "Allow CCTP",
    ).transact({"from": owner})

    guard.functions.whitelistCCTPDestination(
        CCTP_DOMAIN_ARBITRUM,
        "Allow Arbitrum",
    ).transact({"from": owner})

    # Whitelist USDC and receiver
    guard.functions.whitelistToken(usdc_ethereum.address, "Allow USDC").transact({"from": owner})
    guard.functions.allowReceiver(
        web3_ethereum.eth.accounts[3],
        "Allow recipient",
    ).transact({"from": owner})
    guard.functions.allowSender(asset_manager, "Allow asset manager").transact({"from": owner})

    # Fund vault with USDC
    amount = 500 * 10**6  # 500 USDC
    usdc_ethereum.functions.transfer(vault.address, amount).transact({"from": ETHEREUM_USDC_WHALE})

    return vault


def test_dual_fork_cctp_transfer(
    web3_ethereum: Web3,
    web3_arbitrum: Web3,
    vault: Contract,
    usdc_ethereum: Contract,
    usdc_arbitrum: Contract,
    asset_manager: str,
    recipient_address: str,
):
    """Test conceptual cross-chain USDC transfer across two Anvil forks.

    1. Burns USDC on Ethereum via guarded vault
    2. Emulates USDC arrival on Arbitrum via spoofed transfer
    """
    amount = 100 * 10**6  # 100 USDC

    # --- Source chain (Ethereum): burn USDC ---

    vault_balance_before = usdc_ethereum.functions.balanceOf(vault.address).call()

    # Approve through vault
    approve_fn = prepare_approve_for_burn(web3_ethereum, amount)
    target, call_data = encode_simple_vault_transaction(approve_fn)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3_ethereum, tx_hash)

    # Burn through vault
    burn_fn = prepare_deposit_for_burn(
        web3_ethereum,
        amount=amount,
        destination_chain_id=42161,
        mint_recipient=recipient_address,
    )
    target, call_data = encode_simple_vault_transaction(burn_fn)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3_ethereum, tx_hash)

    # Verify burn on source chain
    vault_balance_after = usdc_ethereum.functions.balanceOf(vault.address).call()
    assert vault_balance_after == vault_balance_before - amount

    # --- Destination chain (Arbitrum): emulate USDC mint ---
    #
    # In production, Circle's attestation service would attest the burn
    # and a relayer would call receiveMessage() to mint. On forks, we
    # emulate this by directly transferring USDC from a whale.

    recipient_balance_before = usdc_arbitrum.functions.balanceOf(recipient_address).call()

    # Spoof USDC transfer from whale to recipient on Arbitrum
    usdc_arbitrum.functions.transfer(
        recipient_address,
        amount,
    ).transact({"from": ARBITRUM_USDC_WHALE})

    # Verify recipient received USDC on Arbitrum
    recipient_balance_after = usdc_arbitrum.functions.balanceOf(recipient_address).call()
    assert recipient_balance_after == recipient_balance_before + amount
