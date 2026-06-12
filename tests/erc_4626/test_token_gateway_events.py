"""Test TokenGateway (ForgeYieldsUSDC / fyUSDC) event discovery.

TokenGateway uses non-standard ERC-4626 flow events with extra parameters.
These tests verify that:

1. Custom 5-argument Deposit topic maps to VaultEventKind.deposit
2. RedeemRequested topic maps to VaultEventKind.withdraw
3. RedeemTokenGatewayDepreciated topic maps to VaultEventKind.withdraw
4. Standard ERC-4626 Deposit topic remains distinct from TokenGateway Deposit
5. (Integration) JSONRPCVaultDiscover detects the vault on Ethereum mainnet
"""

import os

import pytest
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.discovery_base import (
    VaultEventKind,
    get_standard_erc_4626_vault_discovery_events,
    get_token_gateway_discovery_events,
    get_vault_event_topic_map,
)

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Create a throwaway Web3 instance for ABI loading (no RPC needed)."""
    return Web3()


def test_token_gateway_deposit_topic_is_deposit(web3: Web3):
    """Verify that the TokenGateway 5-argument Deposit event is classified as a deposit.

    1. Load TokenGateway events
    2. Compute topic0 for the custom Deposit event
    3. Look it up in the vault event topic map
    4. Assert it maps to VaultEventKind.deposit
    """
    # 1. Load TokenGateway events
    tg_events = get_token_gateway_discovery_events(web3)

    # 2. Compute topic0
    deposit_topic = get_topic_signature_from_event(tg_events[0])

    # 3. Look up in topic map
    topic_map = get_vault_event_topic_map(web3)

    # 4. Assert deposit classification
    assert topic_map[deposit_topic] == VaultEventKind.deposit


def test_token_gateway_redeem_requested_topic_is_withdraw(web3: Web3):
    """Verify that the TokenGateway RedeemRequested event is classified as a withdrawal.

    1. Load TokenGateway events
    2. Compute topic0 for RedeemRequested
    3. Look it up in the vault event topic map
    4. Assert it maps to VaultEventKind.withdraw
    """
    # 1. Load TokenGateway events
    tg_events = get_token_gateway_discovery_events(web3)

    # 2. Compute topic0
    redeem_requested_topic = get_topic_signature_from_event(tg_events[1])

    # 3. Look up in topic map
    topic_map = get_vault_event_topic_map(web3)

    # 4. Assert withdrawal classification
    assert topic_map[redeem_requested_topic] == VaultEventKind.withdraw


def test_token_gateway_redeem_depreciated_topic_is_withdraw(web3: Web3):
    """Verify that the TokenGateway RedeemTokenGatewayDepreciated event is classified as a withdrawal.

    1. Load TokenGateway events
    2. Compute topic0 for RedeemTokenGatewayDepreciated
    3. Look it up in the vault event topic map
    4. Assert it maps to VaultEventKind.withdraw
    """
    # 1. Load TokenGateway events
    tg_events = get_token_gateway_discovery_events(web3)

    # 2. Compute topic0
    redeem_depreciated_topic = get_topic_signature_from_event(tg_events[2])

    # 3. Look up in topic map
    topic_map = get_vault_event_topic_map(web3)

    # 4. Assert withdrawal classification
    assert topic_map[redeem_depreciated_topic] == VaultEventKind.withdraw


def test_token_gateway_deposit_distinct_from_erc4626(web3: Web3):
    """Verify that the TokenGateway Deposit topic is distinct from standard ERC-4626 Deposit.

    The TokenGateway Deposit has an extra referralCode parameter, so its
    Keccak-256 topic signature must differ from the standard 4-argument
    ERC-4626 Deposit(address,address,uint256,uint256).

    1. Load standard ERC-4626 events
    2. Load TokenGateway events
    3. Compute topic0 for both Deposit events
    4. Assert they are different
    """
    # 1. Load standard ERC-4626 events
    erc4626_events = get_standard_erc_4626_vault_discovery_events(web3)

    # 2. Load TokenGateway events
    tg_events = get_token_gateway_discovery_events(web3)

    # 3. Compute topic0 for both
    erc4626_deposit_topic = get_topic_signature_from_event(erc4626_events[0])
    tg_deposit_topic = get_topic_signature_from_event(tg_events[0])

    # 4. Assert distinct
    assert erc4626_deposit_topic != tg_deposit_topic, f"TokenGateway Deposit topic should differ from standard ERC-4626 Deposit, both are {erc4626_deposit_topic}"


@pytest.mark.slow
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed")
def test_token_gateway_vault_discovery_ethereum():
    """Verify that JSONRPCVaultDiscover detects the ForgeYieldsUSDC vault on Ethereum.

    Uses known on-chain events:

    1. Set up JSONRPCVaultDiscover for Ethereum mainnet
    2. Scan a block range containing a known TokenGateway Deposit at block 24,972,408
    3. Verify the vault address appears in the leads
    4. Assert non-zero deposit count

    `Etherscan link <https://etherscan.io/address/0x943109DC7C950da4592d85ebd4Cfed007Af64670>`_.
    """
    from eth_defi.erc_4626.rpc_discovery import JSONRPCVaultDiscover
    from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory

    VAULT_ADDRESS = "0x943109DC7C950da4592d85ebd4Cfed007Af64670"
    DEPOSIT_BLOCK = 24_972_408

    # 1. Set up discovery
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_ETHEREUM)

    vault_discover = JSONRPCVaultDiscover(
        web3,
        web3factory,
        # Keep each eth_getLogs request small so providers with a strict
        # max block range (-32012 errors) accept the query
        max_getlogs_range=100,
    )

    # 2. Scan a narrow block range around the known deposit
    start_block = DEPOSIT_BLOCK - 50
    end_block = DEPOSIT_BLOCK + 50

    report = vault_discover.fetch_leads(start_block, end_block, display_progress=False)

    # 3. Verify vault appears in leads
    vault_lead = report.leads.get(VAULT_ADDRESS.lower())
    assert vault_lead is not None, f"TokenGateway vault {VAULT_ADDRESS} not found in leads"

    # 4. Assert non-zero deposit count
    assert vault_lead.deposit_count >= 1, f"Expected deposits, got {vault_lead.deposit_count}"
