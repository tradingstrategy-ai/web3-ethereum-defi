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
def usdt(web3) -> TokenDetails:
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
def hot_wallet_user(web3, usdt, usdt_holder) -> HotWallet:
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
    tx_hash = usdc.contract.functions.transfer(hw.address, 2 * 10**6).transact({"from": usdt_holder, "gas": 100_000})
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


def test_fetch_vault_portfolio(vault: VelvetVault):
    """Read vault token balances."""
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > 0
    assert portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"] > 0


def test_vault_swap_partially(
    vault: VelvetVault,
    vault_owner: HexAddress,
    slippage: float,
):
    """Simulate swap tokens using Enzo.

    - Swap 1 SUDC to DogInMe

    - See balances update in the vault
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)

    existing_dogmein_balance = portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    existing_usdc_balance = portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        swap_amount=1_000_000,  # 1 USDC
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
    assert portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"] > existing_dogmein_balance
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] < existing_usdc_balance


@pytest.mark.skip(reason="Enso is just random piece of shit")
def test_vault_swap_very_little(
    vault: VelvetVault,
    vault_owner: HexAddress,
    slippage: float,
):
    """Simulate swap tokens using Enzo.

    - Do a very small amount of USDC
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    #  code 500: {"message":"Could not quote shortcuts for route 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913 -> 0x6921b130d297cc43754afba22e5eac0fbf8db75b on network 8453, please make sure your amountIn (1) is within an acceptable range","description":"failed enso request"}
    with pytest.raises(VelvetSwapError):
        vault.prepare_swap_with_enso(
            token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
            swap_amount=1,  # 1 USDC
            slippage=slippage,
            remaining_tokens=universe.spot_token_addresses,
            swap_all=False,
            from_=vault_owner,
            retries=0,
        )


def test_vault_swap_sell_to_usdc(
    vault: VelvetVault,
    vault_owner: HexAddress,
    slippage: float,
):
    """Simulate swap tokens using Enzo.

    - Sell base token to get more USDC
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    existing_usdc_balance = portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        token_out="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        swap_amount=500 * 10**18,
        slippage=slippage,
        remaining_tokens=universe.spot_token_addresses,
        swap_all=False,
        from_=vault_owner,
    )

    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    latest_block = web3.eth.block_number
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"] > existing_usdc_balance


def test_velvet_api_deposit(
    vault: VelvetVault,
    vault_owner: HexAddress,
    deposit_user: HexAddress,
    usdc: TokenDetails,
    slippage: float,
    base_doginme_token: TokenDetails,
):
    """Use Velvet API to perform deposit"""

    web3 = vault.web3

    # Velvet vault tracked assets
    universe = TradingUniverse(
        spot_token_addresses={
            base_doginme_token.address,  # DogInMe
            usdc.address,  # USDC on Base
        }
    )

    # Check the existing portfolio USDC balance before starting the
    # the deposit process
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    existing_usdc_balance = portfolio.spot_erc20[usdc.address]
    assert existing_usdc_balance > Decimal(1.0)

    # Velvet deposit manager on Base,
    # the destination of allowance
    deposit_manager = "0xe4e23120a38c4348D7e22Ab23976Fa0c4Bf6e2ED"  # vault.deposit_manager_address

    # Check there is ready-made manual approve() waiting onchain
    allowance = usdc.contract.functions.allowance(
        Web3.to_checksum_address(deposit_user),
        Web3.to_checksum_address(deposit_manager),
        ).call()
    raw_amount = 4999999
    assert allowance == raw_amount

    # E               eth_defi.trace.TransactionAssertionError: Revert reason: execution reverted: revert: TransferHelper::transferFrom: transferFrom failed
    # E               Solidity stack trace:
    # E               CALL: [reverted] 0xe4e23120a38c4348D7e22Ab23976Fa0c4Bf6e2ED.0x9136d415(<unknown>) [29803 gas]
    # E               └── CALL: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913.transferFrom(sender=0x7612a94aaff7a552c373e3124654c1539a4486a8, recipient=0x6e3e0fe13dae2c42cca7ae2e849b0976e2e63e05, amount=5000000) [18763 gas]
    # E                   └── DELEGATECALL: 0x2Ce6311ddAE708829bc0784C967b7d77D19FD779.0x23b872dd(<unknown>) [11573 gas]
    # E               Transaction details:

    # Prepare the deposit tx payload
    tx_data = vault.prepare_deposit_with_enso(
        from_=deposit_user,
        deposit_token_address=usdc.address,
        amount=raw_amount,
        slippage=slippage,
    )
    assert tx_data["to"] == deposit_manager
    started_at = time.time()
    tx_hash = web3.eth.send_transaction(tx_data)
    try:
        assert_transaction_success_with_explanation(web3, tx_hash)
    except Exception as e:
        # Double check allowance - Anvil bug
        duration = time.time() - started_at
        allowance = usdc.contract.functions.allowance(
            Web3.to_checksum_address(deposit_user),
            Web3.to_checksum_address(deposit_manager),
        ).call()
        raise RuntimeError(f"transferFrom() failed, allowance after broadcast {allowance / 10**6} USDC: {e}, crash took {duration} seconds") from e

    # USDC balance has increased after the deposit
    portfolio = vault.fetch_portfolio(universe, web3.eth.block_number)
    assert portfolio.spot_erc20[usdc.address] > existing_usdc_balance


def test_velvet_api_redeem(
    vault: VelvetVault,
    vault_owner: HexAddress,
    existing_shareholder: HexAddress,
    usdc: TokenDetails,
    base_doginme_token: TokenDetails,
    slippage: float,
):
    """Use Velvet API to perform redemption.

    - Do autosell redemption
    """

    web3 = vault.web3

    # Check we have our shares
    share_token = vault.share_token
    assert share_token.name == "Example 2"
    assert share_token.symbol == "EXA2"
    assert share_token.total_supply == 1000 * 10**18
    shares = share_token.fetch_balance_of(existing_shareholder)
    assert shares > 0

    withdrawal_manager = vault.withdraw_manager_address

    # Check there is ready-made manual approve() waiting onchain
    allowance = share_token.contract.functions.allowance(
        Web3.to_checksum_address(existing_shareholder),
        Web3.to_checksum_address(withdrawal_manager),
        ).call()
    assert allowance == pytest.approx(1000 * 10**18)

    tx_hash = share_token.contract.functions.approve(
        Web3.to_checksum_address(vault.portfolio_address),
        share_token.convert_to_raw(shares)
    ).transact({
        "from": Web3.to_checksum_address(existing_shareholder),
    })
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Velvet vault tracked assets
    universe = TradingUniverse(
        spot_token_addresses={
            base_doginme_token.address,  # DogInMe
            usdc.address,  # USDC on Base
        }
    )

    # Check the existing portfolio USDC balance before starting the
    # the deposit process
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    existing_usdc_balance = portfolio.spot_erc20[usdc.address]
    assert existing_usdc_balance > Decimal(1.0)

    # Prepare the redemption tx payload
    tx_data = vault.prepare_redemption(
        from_=existing_shareholder,
        amount=share_token.convert_to_raw(shares),
        withdraw_token_address=usdc.address,
        slippage=slippage,
    )
    assert tx_data["to"] == "0x99e9C4d3171aFAA3075D0d1aE2Bb42B5E53aEdAB"
    # TODO: Not sure why times out
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Vault balances are zero after redeeming everything
    portfolio = vault.fetch_portfolio(universe, web3.eth.block_number)
    assert portfolio.spot_erc20[usdc.address] == pytest.approx(0)
    assert portfolio.spot_erc20[base_doginme_token.address] == pytest.approx(0)


def test_vault_swap_analyse(
    vault: VelvetVault,
    vault_owner: HexAddress,
    slippage: float,
):
    """Analyse the receipt of Enso swap transaction

    - Swap 1 SUDC to DogInMe

    - Figure out the actual price executed
    """
    web3 = vault.web3
    universe = TradingUniverse(
        spot_token_addresses={
            "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)

    existing_dogmein_balance = portfolio.spot_erc20["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
    assert existing_dogmein_balance > 0

    existing_usdc_balance = portfolio.spot_erc20["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert existing_usdc_balance > Decimal(1.0)

    # Build tx using Velvet API
    tx_data = vault.prepare_swap_with_enso(
        token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
        swap_amount=1_000_000,  # 1 USDC
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
    assert analysis.token0.symbol == "USDC"
    assert analysis.token1.symbol == "doginme"
    assert analysis.amount_in == 1 * 10**6
    assert analysis.amount_out > 0
    # https://www.coingecko.com/en/coins/doginme
    price = analysis.get_human_price(reverse_token_order=True)
    assert 0 < price < 0.01