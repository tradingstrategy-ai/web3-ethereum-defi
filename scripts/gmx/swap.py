"""Example script for swapping tokens through GMX.

- Takes in JSON_RPC_ARBITRUM env variable for your Arbitrum node

- When given SIMULATE environmet variable, runs the actions in an Anvil-forked mainnet environment,
  archive Arbitrum node needed

- When given JSON_RPC_TENDERLY, use Tenderly virtual testnet for the simulation
"""

import logging
import os
from decimal import Decimal

from web3 import Web3

from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.testing import emulate_keepers
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import setup_console_logging

#: Arbitrum address holding large USDC balance, used to seed accounts in simulation
LARGE_USDC_HOLDER = "0xF977814e90dA44bFA03b6295A0616a897441aceC"

#: GMX keeper address, used in simulations
GMX_KEEPER = "0xE47b36382DC50b90bCF6176Ddb159C4b9333A7AB"

#: GMX controller address, used in simulations
GMX_CONTROLLER = "0xf5F30B10141E1F63FC11eD772931A8294a591996"

#: Block number to fork from in Anvil simulation
SIMULATION_ARBITUM_FORK_BLOCK_NUMBER = 341_830_407


logger = logging.getLogger(__name__)


def create_fork_funded_wallet(web3: Web3) -> HotWallet:
    """On Anvil forked mainnet, create a wallet with some funds.

    - Topped up with simulated 199 USDC and 1 ETH
    """
    hot_wallet = HotWallet.create_for_testing(web3)
    # Picked on Etherscan
    # https://arbiscan.io/token/0xaf88d065e77c8cc2239327c5edb3a432268e5831#balances
    usdc = fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    tx_hash = usdc.transfer(hot_wallet.address, Decimal("199")).transact({"from": LARGE_USDC_HOLDER})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Inject web3 middleware for signign
    # GMX code uses legacy signer infrastructure
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(hot_wallet.account))

    assert usdc.fetch_balance_of(hot_wallet.address) > 0, "Simulated wallet did not receive USDC"
    assert web3.eth.get_balance(hot_wallet.address) > 0, "Simulated wallet did not receive ETH"

    logger.info(
        "Simulated wallet %s has %s ETH",
        hot_wallet.address,
        web3.eth.get_balance(hot_wallet.address) / 10**18,
    )

    return hot_wallet


def main():
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    SIMULATE = os.environ.get("SIMULATE") == "true"
    JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
    JSON_RPC_TENDERLY = os.environ.get("JSON_RPC_TENDERLY")

    if SIMULATE:
        # Addresses we need to take control to simulate GMX offchain Keeper fuctionality
        unlocked_addresses = [
            LARGE_USDC_HOLDER,
            GMX_KEEPER,
            GMX_CONTROLLER,
        ]
        if JSON_RPC_TENDERLY:
            logger.info("Using Tenderly virtual testnet for simulation: %s", JSON_RPC_TENDERLY)
            web3 = create_multi_provider_web3(
                JSON_RPC_TENDERLY,
                default_http_timeout=(10.0, 60.0),  # Increase default timeouts if your Anvil is slow
                retries=0,  # If Anvil RPC call fails, retries won't help
            )
        else:
            logger.info("Forking Arbitrum with Anvil")
            anvil = fork_network_anvil(
                JSON_RPC_ARBITRUM,
                unlocked_addresses=unlocked_addresses,
                fork_block_number=SIMULATION_ARBITUM_FORK_BLOCK_NUMBER,  # Always simulate against a fixed state
            )
            web3 = create_multi_provider_web3(
                anvil.json_rpc_url,
                default_http_timeout=(10.0, 60.0),  # Increase default timeouts if your Anvil is slow
                retries=0,  # If Anvil RPC call fails, retries won't help
            )

        hot_wallet = create_fork_funded_wallet(web3)

        logger.info("Using simulated wallet %s", hot_wallet.address)
        logger.info("GMX keeper address is %s", GMX_KEEPER)
        logger.info("GMX controller address is %s", GMX_CONTROLLER)
    else:
        logger.info("Base production deployment")
        assert JSON_RPC_ARBITRUM
        web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
        PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
        assert PRIVATE_KEY, "Private key must be set in environment variable PRIVATE_KEY"
        hot_wallet = None

    logger.info(
        "Using JSON RPC %s",
        get_provider_name(web3.provider),
    )

    chain_id = web3.eth.chain_id
    assert chain_id == 42161, f"This example is for Arbitrum, got chain {chain_id}"

    gmx_config = GMXConfig(
        web3=web3,
        wallet=hot_wallet,
    )
    trading_manager = GMXTrading(gmx_config)

    usd_amount = 1.00  # In token amount in USD to swap. The input is trade size in USD, not token quantity.
    in_token_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    out_token_address = "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a"  # AAVE on Arbitrum

    in_token = fetch_erc20_details(web3, in_token_address)
    out_token = fetch_erc20_details(web3, out_token_address)

    # Swap USDC for SOL (Wormhole)
    # GMX v2 supports token swaps for its collateral tokens.
    # https://docs.gmx.io/docs/trading/v2#swaps
    swap_order = trading_manager.swap_tokens(
        in_token_symbol=in_token.symbol,
        out_token_symbol=out_token.symbol,
        amount=usd_amount,
        slippage_percent=0.02,  # 0.2% slippage tolerance
        debug_mode=False,
    )

    tx_hash = swap_order.tx_info.hex()

    logger.info("Swap transaction created, transaction hash is %s", tx_hash)

    assert_transaction_success_with_explanation(web3, tx_hash)

    if SIMULATE:
        # GMX Keepers are offchain oracles resposnible for maintaining GMX markets.
        # In live execution, Keepers will automatically execute fulfilling the swap order
        # when they see the swap order onchain.
        # In mainnet fork, we need to emulate their actions, because naturally
        # keepers cannot see what's going on in the forked environment.
        tx_hash = emulate_keepers(
            gmx_config,
            in_token.symbol,
            out_token.symbol,
            web3,
            hot_wallet.address,
            in_token_address,
            out_token_address,
            deployer_address=hot_wallet.address,
        )
        logger.info(
            "Emulated GMX keeper executed the swap, transaction hash is %s",
            tx_hash.hex(),
        )
        assert_transaction_success_with_explanation(web3, tx_hash)

    out_token_amount = out_token.fetch_balance_of(hot_wallet.address)

    logger.info(
        "Swapped %s worth of %s USD for %s %s",
        in_token.symbol,
        usd_amount,
        out_token_amount,
        out_token.symbol,
    )

    assert out_token.fetch_balance_of(hot_wallet.address) > 0, f"Swap did not result in any output tokens for {out_token}"
    logger.info("All ok")


if __name__ == "__main__":
    main()
