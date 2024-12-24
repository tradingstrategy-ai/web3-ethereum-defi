"""Test Uniswap Universal Router swaps.

NOTE: Debug revert reason bytecode with: https://calldata.swiss-knife.xyz/decoder
"""

import os
from decimal import Decimal
from datetime import datetime

import flaky
import pytest
from eth_typing import HexAddress
from web3 import Web3, HTTPProvider
from web3.types import Wei

from uniswap_universal_router_decoder import RouterCodec, FunctionRecipient, TransactionSpeed

from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_universal_router.deployment import fetch_uniswap_universal_router_deployment, UniswapUniversalRouterDeployment
from eth_defi.uniswap_universal_router.swap import swap_uniswap_v3, approve_token
from eth_defi.gas import estimate_gas_fees, apply_gas

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE", "https://mainnet.base.org")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def usdc_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"


@pytest.fixture()
def anvil_base_fork(request, usdc_holder) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[usdc_holder],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    # web3 = Web3(HTTPProvider(anvil_base_fork.json_rpc_url))
    web3 = create_multi_provider_web3(anvil_base_fork.json_rpc_url)
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    )


@pytest.fixture
def weth(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x4200000000000000000000000000000000000006",
    )


@pytest.fixture
def benji(web3):
    return fetch_erc20_details(
        web3,
        "0xBC45647eA894030a4E9801Ec03479739FA2485F0",
    )


@pytest.fixture()
def hot_wallet(web3, usdc, usdc_holder) -> HotWallet:
    """A test account with USDC balance."""

    hw = HotWallet.create_for_testing(web3, test_account_n=1, eth_amount=10)
    hw.sync_nonce(web3)

    # give hot wallet some native token
    web3.eth.send_transaction(
        {
            "from": web3.eth.accounts[9],
            "to": hw.address,
            "value": 1 * 10**18,
        }
    )

    # Top up with 999 USDC
    tx_hash = usdc.contract.functions.transfer(hw.address, 10_000 * 10**6).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return hw


@pytest.fixture()
def universal_router(web3: Web3) -> UniswapUniversalRouterDeployment:
    return fetch_uniswap_universal_router_deployment(
        web3,
        "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD",
        "0x000000000022D473030F116dDEE9F6B43aC78Ba3",
    )


def test_fetch_deployment(web3: Web3):
    """Read vault metadata from private Velvet endpoint."""
    deployment = fetch_uniswap_universal_router_deployment(web3, "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD")
    assert deployment.router.address == "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"


def test_universal_router_swap_v3(web3: Web3, hot_wallet: HotWallet, usdc: TokenDetails, weth: TokenDetails, universal_router: UniswapUniversalRouterDeployment):
    usdc_to_spend = 1000 * 10**6
    approve_fn, data, signable_message = approve_token(universal_router, token=usdc.contract, amount=usdc_to_spend)

    # approve permit2
    tx = approve_fn.build_transaction({"from": hot_wallet.address})
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx, gas_fees)
    signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # sign permit2 message
    signed_message = hot_wallet.account.sign_message(signable_message)

    # swap
    swap_fn = swap_uniswap_v3(
        universal_router,
        path=[usdc.address, 500, weth.address],
        permit2_data=data,
        permit2_signed_message=signed_message,
        amount_in=usdc_to_spend,
        amount_out_min=int(0.1 * 10**18),
    )

    # NOTE: we can't use hot_wallet.sign_transaction_with_new_nonce() for some reason
    tx = swap_fn.build_transaction(
        hot_wallet.address,
        nonce=hot_wallet.allocate_nonce(),
    )
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx, gas_fees)
    signed_tx = hot_wallet.account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    usdc_balance = usdc.contract.functions.balanceOf(hot_wallet.address).call()
    weth_balance = weth.contract.functions.balanceOf(hot_wallet.address).call()
    assert usdc_balance == pytest.approx(9000 * 10**6)
    assert weth_balance > int(0.2 * 10**18)


def test_raw_universal_router_swap_v3(web3: Web3, hot_wallet: HotWallet, usdc: TokenDetails, weth: TokenDetails, universal_router: UniswapUniversalRouterDeployment):
    from uniswap_universal_router_decoder import RouterCodec, FunctionRecipient, TransactionSpeed
    from datetime import datetime
    from web3.types import Wei

    codec = RouterCodec(web3)

    tx = usdc.contract.functions.approve(Web3.to_checksum_address("0x000000000022D473030F116dDEE9F6B43aC78BA3"), 2**256 - 1).build_transaction({"from": hot_wallet.address})
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx, gas_fees)

    signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)
    signed_bytes = signed_tx.rawTransaction
    assert len(signed_bytes) > 0

    tx_hash = web3.eth.send_raw_transaction(signed_bytes)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Permit signature
    nonce = hot_wallet.allocate_nonce()
    data, signable_message = codec.create_permit2_signable_message(
        usdc.address,
        Wei(1000 * 10**6),  # max = 2**160 - 1
        int(datetime.now().timestamp() + 180),
        0,  # Permit2 nonce
        universal_router.router.address,  # The UR checksum address
        int(datetime.now().timestamp() + 180),
        web3.eth.chain_id,  # chain id
    )
    # print(hot_wallet.allocate_nonce(), nonce)

    # Then you need to sign the message:
    signed_message = hot_wallet.account.sign_message(signable_message)  # where acc is your LocalAccount

    # Permit + v2 swap encoding
    encoded_data = (
        codec.encode.chain().permit2_permit(data, signed_message)
        # .v2_swap_exact_in(FunctionRecipient.SENDER, Wei(800 * 10**6), Wei(0), [usdc.address, weth.address])
        .v3_swap_exact_in(
            function_recipient=FunctionRecipient.SENDER,
            amount_in=Wei(800 * 10**6),
            amount_out_min=Wei(1 * 10**17),
            path=[
                usdc.address,
                500,
                weth.address,
            ],
            payer_is_sender=True,
        )
        # .build(codec.get_default_deadline())
        # .build(int(datetime.now().timestamp() + 180))
    )

    print(usdc.contract.functions.balanceOf(hot_wallet.address).call() / 10**6)

    tx = encoded_data.build_transaction(
        hot_wallet.address,  # 'from'
        nonce=nonce,
    )

    # sign and broadcast
    signed_tx = hot_wallet.account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash, timeout=20)


def test_raw_universal_router_mix_swap_v2_v3(
    web3: Web3,
    hot_wallet: HotWallet,
    usdc: TokenDetails,
    weth: TokenDetails,
    benji: TokenDetails,
    universal_router: UniswapUniversalRouterDeployment,
):
    codec = RouterCodec(web3)

    tx = usdc.contract.functions.approve(Web3.to_checksum_address("0x000000000022D473030F116dDEE9F6B43aC78BA3"), 2**256 - 1).build_transaction({"from": hot_wallet.address})
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx, gas_fees)

    signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)
    signed_bytes = signed_tx.rawTransaction
    assert len(signed_bytes) > 0

    tx_hash = web3.eth.send_raw_transaction(signed_bytes)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Permit signature
    nonce = hot_wallet.allocate_nonce()
    data, signable_message = codec.create_permit2_signable_message(
        usdc.address,
        Wei(4000 * 10**6),  # max = 2**160 - 1
        int(datetime.now().timestamp() + 180),
        0,  # Permit2 nonce: not sure why it is 0 here
        universal_router.router.address,  # The UR checksum address
        int(datetime.now().timestamp() + 180),
        web3.eth.chain_id,  # chain id
    )

    # Then you need to sign the message:
    signed_message = hot_wallet.account.sign_message(signable_message)  # where acc is your LocalAccount

    weth_amount = int(0.23 * 10**18)

    # Permit + v3 swap + v2 swap chain
    encoded_data = (
        codec.encode.chain()
        .permit2_permit(data, signed_message)
        .v3_swap_exact_out(
            function_recipient=FunctionRecipient.ROUTER,
            amount_out=Wei(weth_amount),
            amount_in_max=Wei(1000 * 10**6),
            path=[
                usdc.address,
                100,
                weth.address,
            ],
            payer_is_sender=True,
        )
        .v2_swap_exact_in(
            function_recipient=FunctionRecipient.SENDER,
            amount_in=Wei(weth_amount),
            amount_out_min=Wei(0),
            path=[
                weth.address,
                benji.address,
            ],
            payer_is_sender=False,
        )
    )

    tx = encoded_data.build_transaction(
        hot_wallet.address,  # 'from'
        deadline=int(datetime.now().timestamp() + 180),  # the swap deadline
        # gas_limit=350_000,
        nonce=nonce,
    )

    # sign and broadcast
    signed_tx = hot_wallet.account.sign_transaction(tx)
    # signed_tx = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    # tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert_transaction_success_with_explanation(web3, tx_hash, timeout=20)

    print(benji.contract.functions.balanceOf(hot_wallet.address).call() / 10**18)

    # receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=20)

    # assert_transaction_success_with_explanation()
