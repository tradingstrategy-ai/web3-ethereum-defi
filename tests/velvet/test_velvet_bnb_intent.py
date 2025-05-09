"""Velvet Capital BNB + intent API tests.

- Test against mainnet fork of live deployed vault on Binance chain

- Vault meta ...

- Vault UI https://dapp.velvet.capital/VaultDetails/0x806b760f99ce80fa01bf9b3a8de6dd3590d4d1a9
"""

import os
import time
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, make_anvil_custom_rpc_request
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDT_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.trade import TradeSuccess
from eth_defi.vault.base import VaultSpec, TradingUniverse
from eth_defi.velvet import VelvetVault
from eth_defi.velvet.analysis import analyse_trade_by_receipt_generic
from eth_defi.velvet.enso import VelvetSwapError

JSON_RPC_BINANCE = os.environ.get("JSON_RPC_BINANCE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BINANCE or CI, reason="JSON_RPC_BINANCE needed to run these tests")


@pytest.fixture()
def vault_owner() -> HexAddress:
    # Vaut owner
    return "0xc9EDbb9F5b3f55B7Cc87a8Af6A695f18200E47Af"


@pytest.fixture()
def usdt_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0xEc4B945380CDFFaf79A938a65FF9A8B20c89eA1b"


@pytest.fixture()
def existing_shareholder() -> HexAddress:
    """A user that has shares for the vault that can be redeemed.

    - This user has a pre-approved approve() to withdraw all shares

    https://basescan.org/token/0x205e80371f6d1b33dff7603ca8d3e92bebd7dc25#balances
    """
    return "0xc9EDbb9F5b3f55B7Cc87a8Af6A695f18200E47Af"


@pytest.fixture()
def slippage() -> float:
    """Slippage value to be used in tests.

    - Deal with mysterious Enso failures

    - Random TooMuchSlippage "2Po" errors
    """
    return 0.10


@pytest.fixture()
def anvil_bnb_fork(request, vault_owner, usdt_holder, existing_shareholder) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BINANCE is not None, "JSON_RPC_BINANCE not set"
    launch = fork_network_anvil(
        JSON_RPC_BINANCE,
        unlocked_addresses=[vault_owner, usdt_holder, existing_shareholder],
        #  fork_block_number=23261311,  # Cannot use forked state because Enso has its own state
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_bnb_fork) -> Web3:
    """Create Web3 instance.

    - Use mainnet fork with Anvil for local testing

    - If symbolic transaction debugging is needed, you can override
      Anvil manually with a Tenderly virtual testnet
    """
    # Debug using Tenderly debugger
    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
        snapshot = make_anvil_custom_rpc_request(web3, "evm_snapshot")
        try:
            yield web3
        finally:
            # Revert Tenderly testnet back to its original state
            # https://docs.tenderly.co/forks/guides/testing/reset-transactions-after-completing-the-test
            make_anvil_custom_rpc_request(web3, "evm_revert", [snapshot])
    else:
        # Anvil
        web3 = create_multi_provider_web3(
            anvil_bnb_fork.json_rpc_url,
            default_http_timeout=(2, 90),
            retries=0,  # Tests will fail if we need to retry eth_sendTransaction
        )
        assert web3.eth.chain_id == 56
        yield web3


@pytest.fixture()
def bnb_usdt_token(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        USDT_NATIVE_TOKEN[web3.eth.chain_id]
    )


@pytest.fixture()
def bnb_cake_token(web3) -> TokenDetails:
    """Cake.

    https://tradingstrategy.ai/trading-view/binance/tokens/0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82
    """
    return fetch_erc20_details(web3, "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82")



@pytest.fixture()
def hot_wallet_user(web3, bnb_usdt_token, usdt_holder) -> HotWallet:
    """A test account with USDC balance."""

    hw = HotWallet.create_for_testing(
        web3,
        test_account_n=1,
        eth_amount=10
    )
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
    tx_hash = bnb_usdt_token.contract.functions.transfer(hw.address, 2 * 10**6).transact({"from": usdt_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return hw



@pytest.fixture()
def vault(web3) -> VelvetVault:
    return VelvetVault(
        web3,
        VaultSpec(56, "0x806b760f99ce80fa01bf9b3a8de6dd3590d4d1a9"),
    )


def test_velvet_bnb_fetch_info(vault: VelvetVault):
    """Read vault metadata from the Velvet endpoint.


    """
    data = vault.fetch_info()
    assert data["owner"] == "0xc9edbb9f5b3f55b7cc87a8af6a695f18200e47af"
    vault.check_valid_contract()


def test_velvet_bnb_fetch_vault_portfolio(
    vault: VelvetVault,
    bnb_cake_token,
    bnb_usdt_token,
):
    """Read vault token balances."""
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            bnb_cake_token.address,
            bnb_usdt_token.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20[bnb_cake_token.address] == 0
    assert portfolio.spot_erc20[bnb_usdt_token.address] > 0


def test_velvet_bnb_swap_partially(
    vault: VelvetVault,
    vault_owner: HexAddress,
    slippage: float,
    bnb_cake_token,
    bnb_usdt_token,
):
    """Simulate swap tokens using Enzo.

    - Swap 1 USDT to Cake

    - See balances update in the vault
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            bnb_cake_token.address,
            bnb_usdt_token.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)

    existing_target_token_balance = portfolio.spot_erc20[bnb_cake_token.address]
    assert existing_target_token_balance == 0

    existing_stable_balance = portfolio.spot_erc20[bnb_usdt_token.address]
    assert existing_stable_balance > Decimal(1.0)

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_intent(
        token_in=bnb_usdt_token.address,
        token_out=bnb_cake_token.address,
        swap_amount=bnb_usdt_token.convert_to_raw(Decimal(1)),  # 1 USDC
        slippage=slippage,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
        from_=vault_owner,
    )

    # Perform swap
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check our balances updated
    latest_block = web3.eth.block_number
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20[bnb_cake_token.address] > existing_target_token_balance
    assert portfolio.spot_erc20[bnb_usdt_token.address] < existing_stable_balance


@pytest.mark.xfail(reason="Depends on daily Enso weather whether this pases or not")
def test_velvet_bnb_swap_very_little(
    vault: VelvetVault,
    vault_owner: HexAddress,
    slippage: float,
    bnb_cake_token,
    bnb_usdt_token,
):
    """Simulate swap tokens using Velvet intent API.

    - Do a very small amount of stable
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            bnb_cake_token.address,
            bnb_usdt_token.address,
        }
    )

    #  code 500: {"message":"Could not quote shortcuts for route 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913 -> 0x6921b130d297cc43754afba22e5eac0fbf8db75b on network 8453, please make sure your amountIn (1) is within an acceptable range","description":"failed enso request"}
    #with pytest.raises(VelvetSwapError):
    tx_data = vault.prepare_swap_with_enso(
        token_in=bnb_usdt_token.address,
        token_out=bnb_cake_token.address,
        swap_amount=1,  # Raw 1 unit
        slippage=slippage,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
        from_=vault_owner,
        retries=0,
    )

    # Perform swap
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    receipt = web3.eth.get_transaction_receipt(tx_hash)



def test_velvet_bnb_swap_analyse(
    vault: VelvetVault,
    vault_owner: HexAddress,
    slippage: float,
    bnb_cake_token,
    bnb_usdt_token
):
    """Analyse the receipt of Velvet swap transaction

    - Swap 1 SUDC to DogInMe

    - Figure out the actual price executed
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            bnb_cake_token.address,
            bnb_usdt_token.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)

    existing_target_token_balance = portfolio.spot_erc20[bnb_cake_token.address]
    assert existing_target_token_balance == 0

    existing_usdc_balance = portfolio.spot_erc20[bnb_usdt_token.address]
    assert existing_usdc_balance > Decimal(1.0)

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_intent(
        token_in=bnb_usdt_token.address,
        token_out=bnb_cake_token.address,
        swap_amount=bnb_usdt_token.convert_to_raw(Decimal(1)),  # 1 USDT
        slippage=slippage,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
        from_=vault_owner,
    )

    # Perform swap
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    receipt = web3.eth.get_transaction_receipt(tx_hash)

    analysis = analyse_trade_by_receipt_generic(
        web3,
        tx_hash,
        receipt,
    )

    assert isinstance(analysis, TradeSuccess)
    assert analysis.intent_based
    assert analysis.token0.symbol == "USDT"
    assert analysis.token1.symbol == "Cake"
    assert analysis.amount_in == 1 * 10**18
    assert analysis.amount_out > 0
    # assert
    # https://tradingstrategy.ai/trading-view/binance/pancakeswap-v2/cake-bnb
    price = analysis.get_human_price(reverse_token_order=True)
    assert 0.001 < price < 10.00