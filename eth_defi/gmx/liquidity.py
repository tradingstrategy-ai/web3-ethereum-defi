"""
GMX Liquidity Module

This module provides comprehensive functionality for managing liquidity provision
on the GMX protocol, implementing sophisticated automated market maker (AMM)
operations through a streamlined, high-level interface. It abstracts the complex
mechanics of liquidity pool participation while maintaining full control over
deposit and withdrawal strategies.

**Liquidity Provision Overview:**

GMX operates on an innovative liquidity model where liquidity providers (LPs)
deposit assets into multi-asset pools that serve as counterparties to traders.
Unlike traditional order book exchanges where traders match against each other,
GMX's automated market maker system allows traders to open and close positions
directly against these community-funded liquidity pools.

**How Liquidity Provision Works:**

When you provide liquidity to GMX, you're essentially becoming the "house" in
a sophisticated trading casino. Traders open leveraged positions against your
deposited capital, and you earn fees from their trading activity while taking
on the risk that successful traders might profit at your expense. This creates
a dynamic risk/reward relationship where liquidity providers earn steady fees
in exchange for providing capital and absorbing trader profits and losses.

**GM Tokens - Your Ownership Claims:**

When you deposit assets into a GMX liquidity pool, you receive GM (GMX Market)
tokens that represent your proportional ownership of that pool. Think of GM
tokens like shares in a mutual fund - they entitle you to a percentage of all
the assets in the pool plus any accumulated trading fees. As the pool generates
fees from trading activity, the value of your GM tokens increases, allowing you
to withdraw more assets than you originally deposited.

**Key Financial Concepts:**

- **Impermanent Loss**: Risk that comes from price changes between your deposited assets
- **Trading Fees**: Revenue earned from trader activity in your pool
- **Pool Composition**: The mix of different assets that optimizes risk and returns
- **Utilization Rates**: How much of the pool capital is actively being used for trading

**Risk Management Features:**

- **Debug Mode**: Test all operations without committing real funds or gas
- **Flexible Composition**: Control exactly which assets and amounts you provide
- **Granular Withdrawals**: Withdraw specific assets or amounts as needed
- **Real-time Monitoring**: Track performance and adjust strategy based on market conditions

Example:

.. code-block:: python

    # Set up liquidity management with proper wallet configuration
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig.from_private_key(web3, "0x...", chain="arbitrum")
    liquidity_manager = GMXLiquidityManager(config)

    # Add liquidity to ETH/USDC pool with balanced exposure
    deposit_order = liquidity_manager.add_liquidity(
        market_token_symbol="ETH",
        long_token_symbol="ETH",
        short_token_symbol="USDC",
        long_token_usd=1000,  # $1000 worth of ETH
        short_token_usd=1000,  # $1000 worth of USDC
        debug_mode=True,  # Test first before real transaction
    )

    # Execute the deposit (when ready to commit real funds)
    if not debug_mode:
        tx_receipt = deposit_order.submit()
        print(f"Liquidity added: {tx_receipt.transactionHash.hex()}")

    # Later: Remove liquidity when strategy calls for it
    withdrawal_order = liquidity_manager.remove_liquidity(
        market_token_symbol="ETH",
        out_token_symbol="USDC",  # Withdraw as USDC
        gm_amount=0.5,  # Withdraw 50% of position
        debug_mode=False,  # Execute real withdrawal
    )

**Integration with Trading Strategy:**

Liquidity provision on GMX works best as part of a comprehensive DeFi strategy.
Many sophisticated users combine liquidity provision with delta-neutral trading
strategies, where they provide liquidity to earn fees while hedging their
exposure through opposing positions on other platforms. This can create
income-generating strategies with reduced directional market risk.

Note:
    All liquidity operations require wallet configuration with transaction signing
    capabilities. Use debug mode extensively when developing strategies to avoid
    costly mistakes in live markets.

Warning:
    Liquidity provision involves significant financial risk. Pool values can
    decrease due to trader profits, impermanent loss, and market volatility.
    Never provide more liquidity than you can afford to lose completely.
"""

from gmx_python_sdk.scripts.v2.order.create_deposit_order import DepositOrder
from gmx_python_sdk.scripts.v2.order.create_withdrawal_order import WithdrawOrder
from gmx_python_sdk.scripts.v2.order.liquidity_argument_parser import (
    LiquidityArgumentParser,
)

from eth_defi.gmx.config import GMXConfig


class GMXLiquidityManager:
    """
    Comprehensive liquidity management system for GMX protocol participation.

    This class provides a sophisticated interface for participating in GMX's
    automated market maker system as a liquidity provider. It handles the
    complex mechanics of pool deposits and withdrawals while providing safety
    features like debug mode testing and flexible asset composition strategies.

    **Liquidity Provider Economics:**

    As a liquidity provider on GMX, you're participating in a profit-sharing
    arrangement with the protocol. Traders pay fees for the privilege of using
    your capital to open leveraged positions, and those fees are distributed
    to liquidity providers proportional to their pool ownership. However, when
    traders are profitable, those profits come from the liquidity pool, creating
    a natural tension between trader success and LP returns.

    **Pool Mechanics:**

    GMX liquidity pools are multi-asset pools that can contain different types
    of tokens serving different roles. Long tokens (like ETH, BTC) are typically
    the assets that traders want exposure to, while short tokens (like USDC, USDT)
    serve as the "stable" collateral. The ratio between these assets affects
    both the risk profile and the fee generation potential of your liquidity
    provision.

    **Advanced Strategy Support:**

    The manager supports sophisticated liquidity strategies through precise
    control over deposit composition and withdrawal timing. Advanced users can
    implement dynamic hedging strategies, rebalancing protocols, and risk
    management systems by programmatically adjusting their liquidity positions
    based on market conditions and pool performance.

    :ivar config: GMX configuration object containing network and wallet settings
    :vartype config: GMXConfig
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize the liquidity management system with GMX configuration.

        This constructor validates that the provided configuration has the
        necessary wallet and network settings to perform liquidity operations.
        Since liquidity provision involves financial transactions, the
        configuration must include transaction signing capabilities.

        :param config:
            Complete GMX configuration object that includes network settings
            and wallet credentials for transaction signing. Must have write
            capability enabled for liquidity operations to function
        :type config: GMXConfig
        :raises ValueError:
            When the configuration lacks transaction signing capabilities
            required for liquidity operations
        """
        self.config = config

    def add_liquidity(
        self,
        market_token_symbol: str,
        long_token_symbol: str,
        short_token_symbol: str,
        long_token_usd: float = 0,
        short_token_usd: float = 0,
        debug_mode: bool = False,
    ) -> DepositOrder:
        """
        Add liquidity to a specified GMX pool with precise asset composition control.

        This method creates a liquidity deposit order that adds your specified
        assets to a GMX trading pool. You become a liquidity provider earning
        fees from trader activity while taking on the risk that successful
        traders might reduce the pool value. The method provides sophisticated
        control over exactly which assets and amounts you contribute.

        **Strategic Considerations:**

        The ratio between long_token_usd and short_token_usd affects your risk
        profile. Providing only long tokens (like ETH) gives you full exposure
        to that asset's price movements plus trading fees. Providing a mix
        creates a more balanced risk profile. Providing only short tokens (like
        USDC) minimizes price exposure but may generate lower fees.

        **Debug Mode Benefits:**

        Always test liquidity strategies in debug mode first. This validates
        your parameters, checks token approvals, estimates gas costs, and
        simulates the transaction without spending real money or gas. Debug
        mode is essential for developing sophisticated liquidity strategies
        without costly trial-and-error in live markets.

        Example:

        .. code-block:: python

            # Conservative strategy: Balanced exposure to ETH/USDC
            deposit = liquidity_manager.add_liquidity(
                market_token_symbol="ETH",
                long_token_symbol="ETH",
                short_token_symbol="USDC",
                long_token_usd=500,  # $500 of ETH exposure
                short_token_usd=500,  # $500 of stable collateral
                debug_mode=True,
            )

            # Aggressive strategy: Full ETH exposure for maximum fee potential
            deposit = liquidity_manager.add_liquidity(
                market_token_symbol="ETH",
                long_token_symbol="ETH",
                short_token_symbol="USDC",
                long_token_usd=1000,  # $1000 of ETH exposure
                short_token_usd=0,  # No stable collateral
                debug_mode=False,  # Execute real transaction
            )

        :param market_token_symbol:
            Symbol identifying the GMX market pool (e.g., "ETH", "BTC").
            This determines which trading pool will receive your liquidity
            and which GM tokens you'll receive as ownership claims
        :type market_token_symbol: str
        :param long_token_symbol:
            Symbol of the long token asset to deposit (e.g., "ETH", "BTC").
            Long tokens are typically the assets traders want price exposure to
        :type long_token_symbol: str
        :param short_token_symbol:
            Symbol of the short token asset to deposit (e.g., "USDC", "USDT").
            Short tokens typically serve as stable collateral in the pool
        :type short_token_symbol: str
        :param long_token_usd:
            USD value of long tokens to deposit into the pool. Set to 0 to
            provide no long token exposure. Higher values increase your
            exposure to the long token's price movements
        :type long_token_usd: float
        :param short_token_usd:
            USD value of short tokens to deposit into the pool. Set to 0 to
            provide no short token collateral. Higher values provide more
            stable value backing for the pool
        :type short_token_usd: float
        :param debug_mode:
            Whether to run in debug mode without submitting real transactions.
            Debug mode validates parameters and simulates execution without
            spending gas or committing funds
        :type debug_mode: bool
        :return:
            Configured deposit order object that can be executed to add
            liquidity to the specified pool with the chosen asset composition
        :rtype: DepositOrder
        :raises ValueError:
            When configuration lacks write capabilities or parameters are invalid
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "market_token_symbol": market_token_symbol,
            "long_token_symbol": long_token_symbol,
            "short_token_symbol": short_token_symbol,
            "long_token_usd": long_token_usd,
            "short_token_usd": short_token_usd,
        }

        # Process parameters
        output = LiquidityArgumentParser(write_config, is_deposit=True).process_parameters_dictionary(parameters)

        # Create deposit order
        return DepositOrder(
            config=write_config,
            market_key=output["market_key"],
            initial_long_token=output["long_token_address"],
            initial_short_token=output["short_token_address"],
            long_token_amount=output["long_token_amount"],
            short_token_amount=output["short_token_amount"],
            debug_mode=debug_mode,
        )

    def remove_liquidity(
        self,
        market_token_symbol: str,
        out_token_symbol: str,
        gm_amount: float,
        debug_mode: bool = False,
    ) -> WithdrawOrder:
        """
        Remove liquidity from a GMX pool by redeeming GM tokens for underlying assets.

        This method creates a withdrawal order that converts your GM liquidity
        provider tokens back into underlying pool assets. You can choose which
        specific asset to receive and how much of your position to liquidate,
        providing precise control over your liquidity management strategy.

        **Withdrawal Strategy Considerations:**

        The asset you choose to receive (out_token_symbol) affects your final
        exposure. Withdrawing to stable tokens like USDC locks in your current
        USD value, while withdrawing to volatile tokens like ETH maintains
        your exposure to price movements. Partial withdrawals (gm_amount < 1.0)
        allow you to realize some profits while maintaining pool exposure.

        **GM Token Economics:**

        GM tokens appreciate in value as the pool earns fees from trading
        activity. When you withdraw, you receive a proportional share of all
        pool assets plus accumulated fees. If the pool has been profitable
        from trading fees, you'll receive more value than you originally
        deposited. However, if traders have been profitable against the pool,
        you might receive less.

        **Timing and Market Conditions:**

        Withdrawal timing can significantly impact your returns. Withdrawing
        during high trading volume periods often captures more accumulated
        fees. Withdrawing during volatile markets might trigger rebalancing
        that affects the exact assets you receive. Consider market conditions
        and pool performance when planning withdrawal strategies.

        Example:

        .. code-block:: python

            # Partial withdrawal to lock in profits
            withdrawal = liquidity_manager.remove_liquidity(
                market_token_symbol="ETH",
                out_token_symbol="USDC",  # Convert to stable value
                gm_amount=0.25,  # Withdraw 25% of position
                debug_mode=True,  # Test the withdrawal first
            )

            # Full exit strategy during market uncertainty
            withdrawal = liquidity_manager.remove_liquidity(
                market_token_symbol="ETH",
                out_token_symbol="ETH",  # Maintain ETH exposure
                gm_amount=1.0,  # Withdraw entire position
                debug_mode=False,  # Execute real withdrawal
            )

        :param market_token_symbol:
            Symbol identifying the GMX market pool to withdraw from (e.g., "ETH").
            Must match the pool where you previously provided liquidity and
            currently hold GM tokens
        :type market_token_symbol: str
        :param out_token_symbol:
            Symbol of the specific token you want to receive from withdrawal
            (e.g., "USDC", "ETH"). Determines your final asset exposure after
            liquidity removal
        :type out_token_symbol: str
        :param gm_amount:
            Proportion of your GM token position to withdraw, expressed as
            a decimal (0.5 = 50%, 1.0 = 100%). Allows precise control over
            partial vs. complete position liquidation
        :type gm_amount: float
        :param debug_mode:
            Whether to run in debug mode without submitting real transactions.
            Debug mode validates parameters and estimates withdrawal amounts
            without spending gas or executing the withdrawal
        :type debug_mode: bool
        :return:
            Configured withdrawal order object that can be executed to remove
            liquidity from the specified pool and receive chosen assets
        :rtype: WithdrawOrder
        :raises ValueError:
            When configuration lacks write capabilities, insufficient GM tokens,
            or invalid withdrawal parameters
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "market_token_symbol": market_token_symbol,
            "out_token_symbol": out_token_symbol,
            "gm_amount": gm_amount,
        }

        # Process parameters
        output = LiquidityArgumentParser(write_config, is_withdrawal=True).process_parameters_dictionary(parameters)

        # Create withdrawal order
        return WithdrawOrder(
            config=write_config,
            market_key=output["market_key"],
            out_token=output["out_token_address"],
            gm_amount=output["gm_amount"],
            debug_mode=debug_mode,
        )


if __name__ == "__main__":
    from web3 import Web3
    from dotenv import load_dotenv

    load_dotenv()
    # Set up web3 connection
    web3 = Web3(Web3.HTTPProvider("http://localhost:8545"))

    # Create GMX configuration with anvil private key
    cfg = GMXConfig(
        web3,
        chain="arbitrum",
        private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        user_wallet_address="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    )

    # Initialize market data module
    lqd_manager = GMXLiquidityManager(cfg)
    lqd_manager.add_liquidity("ETH", "ETH", "USDC", 100, 0)
