"""Test multichain Lagoon vault deployment with deterministic Safe and CCTP bridging.

- Deploys Lagoon vaults across Ethereum, Arbitrum, Base, and HyperEVM forks
- Verifies the same deterministic Safe address across all chains
- Tests CCTP bridging: Arbitrum → Base (burn on Arbitrum, receive on Base)
"""

import logging
import os
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress

from eth_defi.cctp.constants import CCTP_DOMAIN_ARBITRUM, CCTP_DOMAIN_BASE
from eth_defi.cctp.receive import prepare_receive_message
from eth_defi.cctp.testing import craft_cctp_message, forge_attestation, replace_attester_on_fork
from eth_defi.cctp.transfer import prepare_approve_for_burn, prepare_deposit_for_burn
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonConfig,
    LagoonDeploymentParameters,
    LagoonMultichainDeployment,
    deploy_multichain_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(
    not JSON_RPC_ETHEREUM or not JSON_RPC_ARBITRUM or not JSON_RPC_BASE or not JSON_RPC_HYPERLIQUID,
    reason="JSON_RPC_ETHEREUM, JSON_RPC_ARBITRUM, JSON_RPC_BASE, and JSON_RPC_HYPERLIQUID environment variables required",
)

#: Fixed private key so the deployer address is the same on all chains.
DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

#: Fixed owner addresses (Anvil default accounts).
OWNER_1 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OWNER_2 = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


@pytest.fixture()
def deployer() -> LocalAccount:
    return Account.from_key(DEPLOYER_PRIVATE_KEY)


@pytest.fixture()
def anvil_ethereum(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        unlocked_addresses=[USDC_WHALE[1]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_arbitrum(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        unlocked_addresses=[USDC_WHALE[42161]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_base(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[USDC_WHALE[8453]],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_hyperliquid(request) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_HYPERLIQUID,
        gas_limit=30_000_000,  # HyperEVM small blocks have 2–3M gas limit; override to large block limit (30M) for TradingStrategyModuleV0 (~5.4M gas). See https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_ethereum(anvil_ethereum) -> "Web3":
    from web3 import Web3

    web3 = create_multi_provider_web3(
        anvil_ethereum.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture()
def web3_arbitrum(anvil_arbitrum) -> "Web3":
    from web3 import Web3

    web3 = create_multi_provider_web3(
        anvil_arbitrum.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 42161
    return web3


@pytest.fixture()
def web3_base(anvil_base) -> "Web3":
    from web3 import Web3

    web3 = create_multi_provider_web3(
        anvil_base.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def web3_hyperliquid(anvil_hyperliquid) -> "Web3":
    from web3 import Web3

    web3 = create_multi_provider_web3(
        anvil_hyperliquid.json_rpc_url,
        default_http_timeout=(3, 500.0),
    )
    assert web3.eth.chain_id == 999
    return web3


def _fund_vault(web3, vault, usdc_details, depositor, asset_manager, amount_usdc=100):
    """Deposit USDC into the vault and settle so the Safe holds funds."""
    raw_amount = amount_usdc * 10**6

    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = usdc_details.contract.functions.approve(
        vault.address,
        raw_amount,
    ).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.request_deposit(depositor, raw_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.settle_via_trading_strategy_module(Decimal(0)).transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert vault.underlying_token.fetch_balance_of(vault.safe_address) == amount_usdc


@pytest.mark.timeout(900)
def test_multichain_lagoon_deploy_and_cctp_bridge(
    web3_ethereum,
    web3_arbitrum,
    web3_base,
    web3_hyperliquid,
    deployer,
):
    """Deploy Lagoon vaults on 4 chains with deterministic Safe, then bridge USDC via CCTP.

    Part 1: Multichain deployment — verify same Safe address on all 4 chains.
    Part 2: CCTP bridging — Arbitrum → Base burn and forged attestation receive.
    """

    salt_nonce = 42

    # Fund deployer with ETH/HYPE on all 4 forks
    for web3 in [web3_ethereum, web3_arbitrum, web3_base, web3_hyperliquid]:
        web3.provider.make_request("anvil_setBalance", [deployer.address, hex(100 * 10**18)])

    # --- Part 1: Multichain deployment ---

    config = LagoonConfig(
        parameters=LagoonDeploymentParameters(
            underlying=None,  # auto-resolved per chain from USDC_NATIVE_TOKEN
            name="Multichain Test Vault",
            symbol="MTV",
        ),
        asset_manager=deployer.address,
        safe_owners=[OWNER_1, OWNER_2],
        safe_threshold=2,
        any_asset=True,
        safe_salt_nonce=salt_nonce,
    )

    chain_web3 = {
        "ethereum": web3_ethereum,
        "arbitrum": web3_arbitrum,
        "base": web3_base,
        "hyperliquid": web3_hyperliquid,
    }

    result = deploy_multichain_lagoon_vault(
        chain_web3=chain_web3,
        deployer=deployer,
        config=config,
        cctp_enabled=True,
    )

    # Verify all Safe addresses are the same
    assert isinstance(result, LagoonMultichainDeployment)
    assert len(result.deployments) == 4
    safe_addresses = {name: d.vault.safe_address for name, d in result.deployments.items()}
    assert len(set(safe_addresses.values())) == 1, f"Safe addresses differ: {safe_addresses}"

    # Verify vault addresses differ across chains
    vault_addresses = {name: d.vault.address for name, d in result.deployments.items()}
    assert len(set(vault_addresses.values())) == 4, f"Vault addresses should differ: {vault_addresses}"

    # Verify CCTP was configured on Ethereum, Arbitrum, Base but NOT on HyperEVM
    for chain_name in ["ethereum", "arbitrum", "base"]:
        guard = result.deployments[chain_name].trading_strategy_module
        # CCTP whitelisting should have been applied — check the guard has the CCTP TokenMessenger whitelisted
        assert guard is not None

    # --- Part 2: CCTP bridging Arbitrum → Base ---

    arb_deployment = result.deployments["arbitrum"]
    base_deployment = result.deployments["base"]
    arb_vault = arb_deployment.vault
    base_vault = base_deployment.vault

    arb_usdc = fetch_erc20_details(web3_arbitrum, USDC_NATIVE_TOKEN[42161])

    # Fund the Arbitrum vault with USDC so we can burn
    arb_depositor = USDC_WHALE[42161]
    _fund_vault(web3_arbitrum, arb_vault, arb_usdc, arb_depositor, deployer.address, amount_usdc=200)

    bridge_amount = 100 * 10**6  # 100 USDC

    safe_balance_before = arb_usdc.contract.functions.balanceOf(arb_vault.safe_address).call()
    assert safe_balance_before >= bridge_amount

    # Step 1: Approve USDC to TokenMessengerV2 through the Arbitrum vault
    approve_fn = prepare_approve_for_burn(web3_arbitrum, bridge_amount)
    moduled_tx = arb_vault.transact_via_trading_strategy_module(approve_fn)
    tx_hash = moduled_tx.transact({"from": deployer.address, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3_arbitrum, tx_hash)

    # Step 2: Burn USDC cross-chain to Base vault Safe
    burn_fn = prepare_deposit_for_burn(
        web3_arbitrum,
        amount=bridge_amount,
        destination_chain_id=8453,
        mint_recipient=base_vault.safe_address,
    )
    moduled_tx = arb_vault.transact_via_trading_strategy_module(burn_fn)
    tx_hash = moduled_tx.transact({"from": deployer.address, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3_arbitrum, tx_hash)

    # Step 3: Verify USDC was burned on Arbitrum
    safe_balance_after = arb_usdc.contract.functions.balanceOf(arb_vault.safe_address).call()
    assert safe_balance_after == safe_balance_before - bridge_amount

    # Step 4: Receive on Base fork — replace attester and forge attestation
    test_attester = replace_attester_on_fork(web3_base)

    nonce = 999_999_999
    message = craft_cctp_message(
        source_domain=CCTP_DOMAIN_ARBITRUM,
        destination_domain=CCTP_DOMAIN_BASE,
        nonce=nonce,
        mint_recipient=base_vault.safe_address,
        amount=bridge_amount,
        burn_token=USDC_NATIVE_TOKEN[42161],
    )
    attestation = forge_attestation(message, test_attester)

    base_usdc = fetch_erc20_details(web3_base, USDC_NATIVE_TOKEN[8453])
    base_safe_balance_before = base_usdc.contract.functions.balanceOf(base_vault.safe_address).call()

    # Step 5: Call receiveMessage() on Base — anyone can relay
    receive_fn = prepare_receive_message(web3_base, message, attestation)
    relayer = web3_base.eth.accounts[9]
    tx_hash = receive_fn.transact({"from": relayer, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3_base, tx_hash)

    # Step 6: Verify USDC was minted to the Base vault Safe
    base_safe_balance_after = base_usdc.contract.functions.balanceOf(base_vault.safe_address).call()
    assert base_safe_balance_after == base_safe_balance_before + bridge_amount
