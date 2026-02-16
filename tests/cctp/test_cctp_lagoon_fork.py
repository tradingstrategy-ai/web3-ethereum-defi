"""Lagoon vault + CCTP V2 integration tests on Base fork.

Tests that a Lagoon vault with TradingStrategyModuleV0 can:

1. Execute ``depositForBurn`` through the guard to burn USDC cross-chain
2. Receive minted USDC via ``receiveMessage()`` using a forged attestation
"""

import logging
import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.cctp.constants import (
    CCTP_DOMAIN_BASE,
    CCTP_DOMAIN_ETHEREUM,
)
from eth_defi.cctp.receive import prepare_receive_message
from eth_defi.cctp.testing import (
    craft_cctp_message,
    forge_attestation,
    replace_attester_on_fork,
)
from eth_defi.cctp.transfer import (
    prepare_approve_for_burn,
    prepare_deposit_for_burn,
)
from eth_defi.cctp.whitelist import CCTPDeployment
from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
    LagoonAutomatedDeployment,
)
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDC_NATIVE_TOKEN, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(
    not JSON_RPC_BASE,
    reason="JSON_RPC_BASE environment variable required",
)

#: USDC on Ethereum mainnet (used as burn token in crafted CCTP messages)
USDC_ETHEREUM = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture()
def asset_manager() -> HexAddress:
    return "0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947"


@pytest.fixture()
def usdc_holder() -> HexAddress:
    return USDC_WHALE[8453]


@pytest.fixture()
def anvil_base_fork(request, usdc_holder, asset_manager) -> AnvilLaunch:
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[usdc_holder, asset_manager],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    web3 = create_multi_provider_web3(
        anvil_base_fork.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def base_usdc(web3) -> TokenDetails:
    return fetch_erc20_details(web3, USDC_NATIVE_TOKEN[8453])


@pytest.fixture()
def deployer_hot_wallet(web3) -> HotWallet:
    return HotWallet.create_for_testing(web3, eth_amount=1)


@pytest.fixture()
def multisig_owners(web3) -> list[HexAddress]:
    return [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]


@pytest.fixture()
def topped_up_asset_manager(web3, asset_manager) -> HexAddress:
    tx_hash = web3.eth.send_transaction(
        {
            "to": asset_manager,
            "from": web3.eth.accounts[0],
            "value": 9 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return asset_manager


@pytest.fixture()
def depositor(web3, base_usdc, usdc_holder) -> HexAddress:
    address = web3.eth.accounts[5]
    tx_hash = base_usdc.contract.functions.transfer(
        address,
        999 * 10**6,
    ).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return address


@pytest.fixture()
def deploy_info(
    web3,
    deployer_hot_wallet,
    topped_up_asset_manager,
    multisig_owners,
) -> LagoonAutomatedDeployment:
    """Deploy a Lagoon vault with CCTP whitelisted for Arbitrum."""
    chain_id = web3.eth.chain_id

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="CCTP Test Vault",
        symbol="CTV",
    )

    cctp = CCTPDeployment.create_for_chain(
        chain_id=chain_id,
        allowed_destinations=[42161],  # Arbitrum
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=topped_up_asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        cctp_deployment=cctp,
        any_asset=True,
    )

    return deploy_info


def _fund_vault(web3, vault, base_usdc, depositor, asset_manager, amount_usdc=100):
    """Deposit USDC into the vault and settle so the Safe holds funds."""
    raw_amount = amount_usdc * 10**6

    # Initial valuation at 0
    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit
    tx_hash = base_usdc.contract.functions.approve(
        vault.address,
        raw_amount,
    ).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.request_deposit(depositor, raw_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Post valuation and settle
    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = vault.settle_via_trading_strategy_module(Decimal(0)).transact(
        {
            "from": asset_manager,
            "gas": 1_000_000,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert vault.underlying_token.fetch_balance_of(vault.safe_address) == amount_usdc


def test_lagoon_cctp_deposit_for_burn(
    web3: Web3,
    deploy_info: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    depositor: HexAddress,
):
    """Lagoon vault burns USDC cross-chain via CCTP depositForBurn.

    1. Deploy Lagoon vault with CCTP whitelisted
    2. Fund vault with USDC via deposit + settle
    3. Approve USDC to TokenMessengerV2 through the vault
    4. Call depositForBurn through the vault
    5. Assert USDC balance decreased
    """
    vault = deploy_info.vault
    asset_manager = topped_up_asset_manager
    amount = 50 * 10**6  # 50 USDC

    # Fund the vault so Safe holds USDC
    _fund_vault(web3, vault, base_usdc, depositor, asset_manager, amount_usdc=100)

    safe_balance_before = base_usdc.contract.functions.balanceOf(vault.safe_address).call()
    assert safe_balance_before >= amount

    # Approve USDC to TokenMessengerV2 through the vault
    approve_fn = prepare_approve_for_burn(web3, amount)
    moduled_tx = vault.transact_via_trading_strategy_module(approve_fn)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Burn USDC cross-chain through the vault
    burn_fn = prepare_deposit_for_burn(
        web3,
        amount=amount,
        destination_chain_id=42161,
        mint_recipient=vault.safe_address,
    )
    moduled_tx = vault.transact_via_trading_strategy_module(burn_fn)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify USDC was burned
    safe_balance_after = base_usdc.contract.functions.balanceOf(vault.safe_address).call()
    assert safe_balance_after == safe_balance_before - amount


def test_lagoon_cctp_receive_minted_usdc(
    web3: Web3,
    deploy_info: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    depositor: HexAddress,
):
    """Lagoon vault receives minted USDC via CCTP receiveMessage.

    Simulates an incoming cross-chain transfer by:

    1. Replacing the CCTP attester with a test account on the fork
    2. Crafting a valid CCTP message (as if burned on Ethereum)
    3. Signing it with the test attester to forge an attestation
    4. Calling receiveMessage() to mint USDC to the vault Safe
    5. Verifying the Safe balance increased
    """
    vault = deploy_info.vault
    asset_manager = topped_up_asset_manager
    bridge_amount = 100 * 10**6  # 100 USDC

    # Fund the vault with a small initial deposit so it is properly settled
    _fund_vault(web3, vault, base_usdc, depositor, asset_manager, amount_usdc=10)

    safe_balance_before = base_usdc.contract.functions.balanceOf(vault.safe_address).call()

    # Replace the CCTP attester so we can forge attestations on this fork
    test_attester = replace_attester_on_fork(web3)

    # Use a unique nonce unlikely to have been used on the source domain.
    # CCTP nonces are per source-domain, so a large value is safe.
    nonce = 999_999_999

    # Craft a CCTP message as if USDC was burned on Ethereum
    message = craft_cctp_message(
        source_domain=CCTP_DOMAIN_ETHEREUM,
        destination_domain=CCTP_DOMAIN_BASE,
        nonce=nonce,
        mint_recipient=vault.safe_address,
        amount=bridge_amount,
        burn_token=USDC_ETHEREUM,
    )

    # Forge the attestation with our test attester
    attestation = forge_attestation(message, test_attester)

    # Call receiveMessage() — anyone can relay, no guard check needed
    receive_fn = prepare_receive_message(web3, message, attestation)
    relayer = web3.eth.accounts[9]
    tx_hash = receive_fn.transact({"from": relayer, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify USDC was minted to the Safe
    safe_balance_after = base_usdc.contract.functions.balanceOf(vault.safe_address).call()
    assert safe_balance_after == safe_balance_before + bridge_amount

    # Post new valuation reflecting the bridged USDC and settle
    new_nav = Decimal(safe_balance_after) / Decimal(10**6)
    vault.post_valuation_and_settle(new_nav, asset_manager)

    assert vault.fetch_total_assets(web3.eth.block_number) == pytest.approx(new_nav, rel=Decimal("0.01"))
