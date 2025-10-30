"""A manual test script to show how to execute an automated Cowswap trade from a Lagoon vault.

- Uses CowSwap and Safe "presign" integration with Lagoon vaults
- You need an Arbitrum wallet funded with minimum ETH for gas fees, initial deposit and such
- 0.005 ETH needed on Arbitrum for gas fees and wrapping WETH

"""

import logging
import os
import time
from decimal import Decimal
from pprint import pformat

from web3 import Web3
from web3.contract.contract import ContractFunction
from hexbytes import HexBytes

from eth_defi.chain import get_chain_name
from eth_defi.confirmation import broadcast_and_wait_transactions_to_complete
from eth_defi.gas import estimate_gas_price, apply_gas
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce
from eth_defi.lagoon.cowswap import presign_and_execute_cowswap, fetch_quote
from eth_defi.lagoon.deployment import deploy_automated_lagoon_vault, LagoonDeploymentParameters
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import BRIDGED_USDC_TOKEN, USDC_NATIVE_TOKEN, USDT_NATIVE_TOKEN, WRAPPED_NATIVE_TOKEN, get_weth_contract, fetch_erc20_details
from eth_defi.utils import setup_console_logging


def deploy_lagoon_vault(
    web3: Web3,
    hot_wallet: HotWallet,
    etherscan_api_key: str,
):
    """ "Deploy a Lagoon vault with Cowswap trade automation integration"""

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

    # Create a new Lagoon vault with TradingStrategyModuleV0, with Cowswap integration enabled
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
        cowswap=True,
        from_the_scratch=False,
        use_forge=True,
        assets=assets,
        etherscan_api_key=etherscan_api_key,
        between_contracts_delay_seconds=15.0,  # Some hack seconds to work around Gnosis Safe lib issues
    )

    print(f"Deployed Lagoon vault at {deploy_info.vault.address} with Cowswap integration")
    print(deploy_info.pformat())
    return deploy_info.vault


_tx_count = 0


def broadcast_tx(
    hot_wallet: HotWallet,
    bound_func: ContractFunction,
    value: int | None = None,
    tx_params: dict | None = None,
    defautl_gas_limit: int = 1_000_000,
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

    if not "gas" in tx_params:
        # Use default gas limit if not specified,
        # don't try to estimate
        tx_params["gas"] = defautl_gas_limit

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
    assert json_rpc_url, f"You need to give JSON_RPC_ARBITRUM environment variable pointing ot your full node"

    private_key = os.environ.get("PRIVATE_KEY")
    assert private_key, f"You need to give PRIVATE_KEY environment variable with a prefunded ETH account"

    etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY")
    assert etherscan_api_key, f"You need to give ETHERSCAN_API_KEY environment variable to verify the deployed contracts"

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

    # Check "Ethereum weather"
    gas_estimate = estimate_gas_price(web3)
    print(f"Current gas price estimate:\n{gas_estimate.pformat()}")

    #
    # Before we start let's ask for a quote so we know CowSwap can fulfill
    # our swap before starting.
    #
    quoted_data = fetch_quote(
        buy_token=fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id]),
        sell_token=weth,
        amount_in=Decimal("0.1"),
        min_amount_out=Decimal("0.01"),
    )
    import ipdb

    ipdb.set_trace()

    #
    # 1. Wrap some WETH which we use as the initial deposit to the vault
    #
    test_amount = Decimal(0.0001)
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
    # 5. Perform an automated Cowswap trade with the assets from the vault.
    # Swap all of out WETH to USDC.e via Cowswap integration.
    #
    _cowswap_broadcast_callback = lambda _web3, _hot_wallet, _bound_func: broadcast_tx(_hot_wallet, _bound_func).hash

    # 10% slippage max
    # We are doing swaps with very small amounts so we are getting
    # massive cost impact because fees are proportional to the swap size.
    max_slippage = 0.10

    cowswap_result = presign_and_execute_cowswap(
        asset_manager_wallet=hot_wallet,
        vault=vault,
        buy_token=fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id]),
        sell_token=weth,
        amount_in=weth_balance,
        min_amount_out=weth_balance * Decimal(1 - max_slippage),
        broadcast_callback=_cowswap_broadcast_callback,
    )

    print(f"Cowswap order executed, order UID: {cowswap_result.order_uid.hex()}")
    print(f"Order status:\n{pformat(cowswap_result.order_status)}")

    print(f"All ok, check the vault at https://routescan.io/{vault.address}")


if __name__ == "__main__":
    main()
