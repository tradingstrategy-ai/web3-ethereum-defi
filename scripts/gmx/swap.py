"""Example script for swapping tokens through GMX.

- When given SIMULATE environmet variable, runs the actions in an Anvil-forked mainnet environment
"""

import os
from decimal import Decimal

from web3 import Web3

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging


def create_fork_funded_wallet(web3: Web3) -> HotWallet:
    """On Anvil forked mainnet, create a wallet with some funds.

    - Topped up with simulated 199 USDC and 1 ETH
    """
    hot_wallet = HotWallet.create_for_testing(web3)
    # Picked on Etherscan
    # https://arbiscan.io/token/0xaf88d065e77c8cc2239327c5edb3a432268e5831#balances
    usdc_holder = "0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7"
    usdc = fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    tx_hash = usdc.transfer(hot_wallet.address, Decimal("199")).transact({"from": usdc_holder})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return hot_wallet


def main():

    setup_console_logging()

    SIMULATE = os.environ.get("SIMULATE") == "true"
    JSON_RPC_ARBITRUM = os.environ["JSON_RPC_ARBITRUM"]

    if SIMULATE:
        print("Simulation deployment with Anvil")
        anvil = fork_network_anvil(JSON_RPC_ARBITRUM)
        web3 = create_multi_provider_web3(anvil.json_rpc_url)
        hot_wallet = create_fork_funded_wallet(web3)
    else:
        print("Base production deployment")
        web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
        PRIVATE_KEY = os.environ["PRIVATE_KEY"]
        hot_wallet = None

    assert PRIVATE_KEY, "Private key must be set in environment variable PRIVATE_KEY"

    chain_id = web3.eth.chain_id
    assert chain_id == 42161, f"This example is for Arbitrum, got chain {chain_id}"

    gmx_config = GMXConfig(
        web3=web3,
        wallet=hot_wallet,

    )
    trading_manager = GMXTrading(gmx_config)

    usd_amount = 1.00  # Amount in USD to swap

    # Swap USDC for SOL (Wormhole)
    # GMX v2 supports token swaps for its collateral tokens.
    # https://docs.gmx.io/docs/trading/v2#swaps
    swap_order = trading_manager.swap_tokens(
        in_token_symbol="USDC",
        out_token_symbol="SOL",
        amount=1.00,
        slippage_percent=0.02,  # 0.2% slippage
        debug_mode=False,
    )






if __name__ == "__main__":
    main()