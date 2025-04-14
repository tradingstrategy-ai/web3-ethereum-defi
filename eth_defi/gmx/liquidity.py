"""
GMX Liquidity Module

This module provides functionality for managing liquidity on GMX.
"""

from gmx_python_sdk.scripts.v2.order.create_deposit_order import DepositOrder
from gmx_python_sdk.scripts.v2.order.create_withdrawal_order import WithdrawOrder
from gmx_python_sdk.scripts.v2.order.liquidity_argument_parser import LiquidityArgumentParser

from eth_defi.gmx.config import GMXConfig


class GMXLiquidityManager:
    """
    Liquidity management functionality for GMX protocol.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize liquidity manager module.

        Args:
            config: GMX configuration object
        """
        self.config = config

    def add_liquidity(self, market_token_symbol: str, long_token_symbol: str, short_token_symbol: str, long_token_usd: float = 0, short_token_usd: float = 0, debug_mode: bool = False) -> DepositOrder:
        """
        Add liquidity to a GMX pool.

        Args:
            market_token_symbol: Symbol of the market (e.g., "ETH")
            long_token_symbol: Symbol of the long token (e.g., "ETH")
            short_token_symbol: Symbol of the short token (e.g., "USDC")
            long_token_usd: USD value of long tokens to deposit
            short_token_usd: USD value of short tokens to deposit
            debug_mode: Run in debug mode without submitting transaction

        Returns:
            Transaction receipt or debug information
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Prepare parameters dictionary
        parameters = {"chain": self.config.get_chain(), "market_token_symbol": market_token_symbol, "long_token_symbol": long_token_symbol, "short_token_symbol": short_token_symbol, "long_token_usd": long_token_usd, "short_token_usd": short_token_usd}

        # Process parameters
        output = LiquidityArgumentParser(write_config, is_deposit=True).process_parameters_dictionary(parameters)

        # Create deposit order
        return DepositOrder(config=write_config, market_key=output["market_key"], initial_long_token=output["long_token_address"], initial_short_token=output["short_token_address"], long_token_amount=output["long_token_amount"], short_token_amount=output["short_token_amount"], debug_mode=debug_mode)

    def remove_liquidity(self, market_token_symbol: str, out_token_symbol: str, gm_amount: float, debug_mode: bool = False) -> WithdrawOrder:
        """
        Remove liquidity from a GMX pool.

        Args:
            market_token_symbol: Symbol of the market (e.g., "ETH")
            out_token_symbol: Symbol of the token to receive
            gm_amount: Amount of GM (liquidity) tokens to withdraw
            debug_mode: Run in debug mode without submitting transaction

        Returns:
            Transaction receipt or debug information
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Prepare parameters dictionary
        parameters = {"chain": self.config.get_chain(), "market_token_symbol": market_token_symbol, "out_token_symbol": out_token_symbol, "gm_amount": gm_amount}

        # Process parameters
        output = LiquidityArgumentParser(write_config, is_withdrawal=True).process_parameters_dictionary(parameters)

        # Create withdrawal order
        return WithdrawOrder(config=write_config, market_key=output["market_key"], out_token=output["out_token_address"], gm_amount=output["gm_amount"], debug_mode=debug_mode)

"""
Example output of the deposit order:
04/14/2025 12:23:07 PM INFO: Getting Available Markets..
04/14/2025 12:25:44 PM INFO: Getting Available Markets..
04/14/2025 12:27:04 PM INFO: Creating order...
Checking coins for approval..
Approving contract "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6" to spend 3077390718026081 tokens belonging to token address: 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
Txn submitted!
Check status: https://arbiscan.io/tx/235ba676853b25801a66ba1f2c5fe27e526f2515c7e204ecd71ad62629ee2625
Contract "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6" approved to spend 3077390718026081 tokens belonging to token address: 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
Coins Approved for spend!
04/14/2025 12:27:06 PM INFO: Building transaction...
04/14/2025 12:27:06 PM INFO: Txn submitted!
04/14/2025 12:27:06 PM INFO: Check status: https://arbiscan.io/tx/3337db1f0004a5fc06e07eb2d154c486ed0041ef17f18dbca0f57284656d702d
04/14/2025 12:27:06 PM INFO: Transaction submitted!
"""

if __name__ == "__main__":
    from web3 import Web3
    from dotenv import load_dotenv

    load_dotenv()
    # Set up web3 connection
    web3 = Web3(Web3.HTTPProvider("http://localhost:8545"))

    # Create GMX configuration with anvil private key
    cfg = GMXConfig(web3, chain="arbitrum", private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80", user_wallet_address="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

    # Initialize market data module
    lqd_manager = GMXLiquidityManager(cfg)
    lqd_manager.add_liquidity("ETH", "ETH", "USDC", 100, 0)

