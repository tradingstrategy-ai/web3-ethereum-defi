"""Test Orderly vault"""

import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.vault import OrderlyVault, deposit
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation

pytestmark = pytest.mark.skipif(
    not any(
        [
            os.environ.get("JSON_RPC_ARBITRUM_SEPOLIA"),
            os.environ.get("HOT_WALLET_PRIVATE_KEY"),
        ]
    ),
    reason="No JSON_RPC_ARBITRUM_SEPOLIA or HOT_WALLET_PRIVATE_KEY environment variable",
)


def test_orderly_deposit(
    web3: Web3,
    orderly_vault: OrderlyVault,
    hot_wallet: HotWallet,
    usdc: TokenDetails,
    broker_id: str,
    orderly_account_id: HexAddress,
):
    # supply USDC to Aave
    approve_fn, get_deposit_fee_fn, deposit_fn = deposit(
        vault=orderly_vault,
        token=usdc.contract,
        amount=100 * 10**6,
        wallet_address=hot_wallet.address,
        orderly_account_id=orderly_account_id,
        broker_id=broker_id,
        token_id="USDC",
    )

    tx = approve_fn.build_transaction({"from": hot_wallet.address, "gas": 200_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    deposit_fee = get_deposit_fee_fn.call()

    tx = deposit_fn.build_transaction({"from": hot_wallet.address, "value": deposit_fee})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # nothing left in USDC balance
    assert usdc.functions.balanceOf(hot_wallet.address).call() == pytest.approx((1008 - 100) * 10**6)

    # NOTE: There is no easy to way to verify the USDC balance in the vault
    # it can be done via offchain API: https://orderly.network/docs/build-on-omnichain/evm-api/restful-api/private/get-current-holding
    # but this is not feasible for testing
