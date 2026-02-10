"""A manual test script to show how to execute an automated Velora trade from a Lagoon vault.

- Uses Velora (ParaSwap) with Lagoon vaults
- You need an Arbitrum wallet funded with minimum ETH for gas fees, initial deposit and such
- 0.005 ETH needed on Arbitrum for gas fees and wrapping WETH

Unlike CowSwap which uses an offchain order book and presigning, Velora executes
swaps atomically in a single transaction.
"""

import logging
import os
import time
from decimal import Decimal
from pprint import pformat

from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.chain import get_chain_name
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.gas import estimate_gas_price, apply_gas
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce
from eth_defi.erc_4626.vault_protocol.lagoon.velora import (
    approve_velora,
    execute_velora_swap,
)
from eth_defi.velora.quote import fetch_velora_quote
from eth_defi.velora.swap import fetch_velora_swap_transaction
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import deploy_automated_lagoon_vault, LagoonDeploymentParameters
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import BRIDGED_USDC_TOKEN, USDC_NATIVE_TOKEN, USDT_NATIVE_TOKEN, WRAPPED_NATIVE_TOKEN, get_weth_contract, fetch_erc20_details
from eth_defi.utils import setup_console_logging


def deploy_lagoon_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    etherscan_api_key: str,
):
    """Deploy a Lagoon vault with Velora trade automation integration"""

    chain_id = web3.eth.chain_id

    parameters = LagoonDeploymentParameters(
        underlying=WRAPPED_NATIVE_TOKEN[chain_id],  # WETH nominated vault
        name="https://github.com/tradingstrategy-ai/web3-ethereum-defi",
        symbol="TradingStrategy.ai",
    )

    # Whitelist Arbitrum mainstream stablecoins + WETH
    assets = [
        BRIDGED_USDC_TOKEN[chain_id],  # USDC.e
        USDC_NATIVE_TOKEN[chain_id],  # USDC
        USDT_NATIVE_TOKEN[chain_id],  # USDT
        WRAPPED_NATIVE_TOKEN[chain_id],  # WETH
    ]

    asset_manager = hot_wallet.address
    multisig_owners = [hot_wallet.address]  # For simplicity, just use single owner multisig

    # Create a new Lagoon vault with TradingStrategyModuleV0, with Velora integration enabled
    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=1,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        velora=True,
        from_the_scratch=False,
        use_forge=True,
        assets=assets,
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=15.0,  # Some hack seconds to work around Gnosis Safe lib issues
    )

    print(f"Deployed Lagoon vault at {deploy_info.vault.address} with Velora integration")
    print(deploy_info.pformat())
    return deploy_info.vault


_tx_count = 0


def broadcast_tx(
    hot_wallet: HotWallet,
    bound_func: ContractFunction,
    value: int | None = None,
    tx_params: dict | None = None,
    default_gas_limit: int = 1_000_000,
) -> SignedTransactionWithNonce:
    """Craft a transaction payload to a smart contract function and broadcast it from our hot wallet.

    :param value:
        ETH attached to the transaction
    """
    global _tx_count

    _tx_count += 1

    # Create signed transactions from Web3.py contract calls
    # and use our broadcast waiter function to send out these txs onchain
    web3 = bound_func.w3
    gas_price_suggestion = estimate_gas_price(web3)
    tx_params = apply_gas(tx_params or {}, gas_price_suggestion)

    if "gas" not in tx_params:
        # Use default gas limit if not specified,
        # don't try to estimate
        tx_params["gas"] = default_gas_limit

    tx = hot_wallet.sign_bound_call_with_new_nonce(bound_func, value=value, tx_params=tx_params)
    print(f"Broadcasting tx #{_tx_count}: {tx.hash.hex()}, calling {bound_func.fn_name or '<unknown>'}() with account nonce {tx.nonce}")
    # Raises if the tx reverts
    broadcast_and_wait_transactions_to_complete(
        web3,
        [tx],
    )
    return tx


def main():
    # Turn on coloured console logging if we can
    logger = setup_console_logging()

    json_rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    assert json_rpc_url, "You need to give JSON_RPC_ARBITRUM environment variable pointing to your full node"

    private_key = os.environ.get("PRIVATE_KEY_SWAP_TEST")
    assert private_key, "You need to give PRIVATE_KEY_SWAP_TEST environment variable with a prefunded ETH account"

    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    assert etherscan_api_key, "You need to give ETHERSCAN_API_KEY environment variable to verify the deployed contracts"

    # How much ETH we convert to WETH and deposit to the vault for trading
    # Assume we aim for ~$1 swap: at $3000 per ETH, so this is about $1.
    # The amount should not be too small as we could be hitting all kind of dust limitations.
    test_amount = Decimal("0.0003333333333333333")

    web3 = create_multi_provider_web3(json_rpc_url)

    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id)

    print(f"Connected to {chain_name} (chain ID: {chain_id}), last block is {web3.eth.block_number:,}")

    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)
    balance = web3.eth.get_balance(hot_wallet.address)
    print(f"Hot wallet address: {hot_wallet.address}, ETH balance: {web3.from_wei(balance, 'ether')} ETH, current nonce is {hot_wallet.current_nonce}")

    weth_contract = get_weth_contract(web3)
    weth = fetch_erc20_details(web3, WRAPPED_NATIVE_TOKEN[chain_id])
    stablecoin = fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id])

    # Check "Ethereum weather"
    gas_estimate = estimate_gas_price(web3)
    print(f"Current gas price estimate:\n{gas_estimate.pformat()}")

    #
    # Before we start let's ask for a quote so we know Velora can fulfill
    # our swap before starting swapping, and we know there is a route
    # available.
    #
    quote = fetch_velora_quote(
        from_=hot_wallet.address,  # Not deployed vault address yet, so use our hot wallet as a placeholder
        buy_token=stablecoin,
        sell_token=weth,
        amount_in=test_amount,
    )
    print(f"Our Velora quote data is:\n{quote.pformat()}")

    print(f"Target price is {quote.get_price():.6f} {weth.symbol}/{stablecoin.symbol}")

    #
    # 1. Wrap some WETH which we use as the initial deposit to the vault
    #
    weth_balance = weth.fetch_balance_of(hot_wallet.address)

    if weth_balance < test_amount:
        assert web3.eth.get_balance(hot_wallet.address) >= Web3.to_wei(test_amount, "ether"), f"Not enough ETH to wrap to WETH, need at least {test_amount} ETH"

        broadcast_tx(hot_wallet, weth_contract.functions.deposit(), value=Web3.to_wei(test_amount, "ether"), tx_params={"gas": 200_000})
        time.sleep(30.0)  # Give some time to crappy RPC nodes to reach internal consistency

    weth_balance = weth.fetch_balance_of(hot_wallet.address)
    print(f"After wrapping our WETH balance is {weth_balance} WETH")

    #
    # 2. Deploy a new Lagoon vault with our TradingStrategyModuleV0 trading automation integration Safe module
    #

    # Verbose logging for deployment as it takes some time and outputs tons of stuff
    logger.setLevel(logging.INFO)
    vault = deploy_lagoon_vault(
        web3,
        hot_wallet,
        etherscan_api_key,
    )
    logger.setLevel(logging.WARNING)
    # Re-sync nonce after deployment, as it has been changed outside our wallet object
    # by Gnosis Safe library
    hot_wallet.sync_nonce(web3)

    #
    # 3. Request deposit to the vault with WETH
    #

    # 3.a) Approve WETH transfer to the vault
    deposit_amount = weth_balance  # Deposit all of our WETH
    broadcast_tx(
        hot_wallet,
        weth.approve(vault.address, deposit_amount),
    )

    # 3.b) Request deposit
    broadcast_tx(
        hot_wallet,
        vault.request_deposit(hot_wallet.address, weth.convert_to_raw(weth_balance)),
    )

    #
    # 4. Settle the vault.
    #
    # Do the initial vault valuation and settlement,
    # so our deposit gets correctly processed into Safe multisig backing up the vault
    #
    valuation = weth.fetch_balance_of(vault.safe_address)
    broadcast_tx(
        hot_wallet,
        vault.post_new_valuation(valuation),
    )
    broadcast_tx(
        hot_wallet,
        vault.settle_via_trading_strategy_module(valuation),
    )

    #
    # 5. Perform an automated Velora trade with the assets from the vault.
    # Swap all of our WETH to USDC.e via Velora integration.
    #

    # 5.a) Get a quote for the swap
    quote = fetch_velora_quote(
        from_=vault.safe_address,
        buy_token=stablecoin,
        sell_token=weth,
        amount_in=weth_balance,
    )
    print(f"Velora quote for vault swap:\n{quote.pformat()}")

    # 5.b) Build the swap transaction with 2.5% slippage
    slippage_bps = 250
    swap_tx = fetch_velora_swap_transaction(
        quote=quote,
        user_address=vault.safe_address,
        slippage_bps=slippage_bps,
    )
    print(f"Velora swap tx built: Augustus {swap_tx.to}, min out {swap_tx.min_amount_out} {stablecoin.symbol}")

    # 5.c) Approve TokenTransferProxy to spend WETH from the Safe
    broadcast_tx(
        hot_wallet,
        approve_velora(
            vault=vault,
            token=weth,
            amount=weth_balance,
        ),
    )

    # 5.d) Execute the swap via TradingStrategyModuleV0.swapAndValidateVelora()
    _velora_broadcast_callback = lambda _web3, _hot_wallet, _bound_func: broadcast_tx(_hot_wallet, _bound_func).hash

    logger.setLevel(logging.INFO)
    result = execute_velora_swap(
        asset_manager=hot_wallet,
        vault=vault,
        buy_token=stablecoin,
        sell_token=weth,
        amount_in=swap_tx.amount_in,
        min_amount_out=swap_tx.min_amount_out,
        augustus_calldata=swap_tx.calldata,
        broadcast_callback=_velora_broadcast_callback,
    )
    logger.setLevel(logging.WARNING)

    print(f"Velora swap completed!")
    print(f"  TX hash: {result.tx_hash.hex()}")
    print(f"  Sold: {result.get_amount_sold_decimal()} {weth.symbol}")
    print(f"  Bought: {result.get_amount_bought_decimal()} {stablecoin.symbol}")
    print(f"All ok, check the vault at https://routescan.io/{vault.address}")


if __name__ == "__main__":
    main()
