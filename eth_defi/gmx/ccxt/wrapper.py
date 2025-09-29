"""
CCXT Compatibility Wrapper for GMX Orders

This wrapper provides a CCXT-like interface for GMX orders to minimize migration
overhead for users coming from CCXT-based trading systems.
"""

import logging
from typing import Optional, Dict, Any, Union
from decimal import Decimal

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderType, OrderSide, TransactionResult


class GMXCCXTWrapper:
    """
    CCXT-compatible wrapper for GMX trading operations

    Provides familiar CCXT method names and interfaces for GMX protocol trading
    while returning unsigned transactions for external signing.

    Example usage:
        ```python
        from web3 import Web3
        from eth_defi.gmx.config import GMXConfig
        from eth_defi.gmx.order.ccxt_wrapper import GMXCCXTWrapper

        # Setup
        web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
        config = GMXConfig(web3, user_wallet_address="0x...")
        exchange = GMXCCXTWrapper(config)

        # Create market buy order
        result = exchange.create_market_buy_order("ETH/USD", 100.0)  # $100 ETH long

        # Sign and send the transaction
        signed_tx = account.sign_transaction(result.transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        ```
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize GMX CCXT wrapper

        Args:
            config: GMXConfig instance with Web3 and chain configuration
        """
        self.config = config
        self.base_order = BaseOrder(config)
        self.logger = logging.getLogger(f"{self.__class__.__name__}")

        # CCXT-like properties
        self.id = "gmx"
        self.name = "GMX"
        self.countries = ["US", "Global"]  # GMX is global
        self.version = "v2"
        self.rateLimit = 1000  # Conservative rate limit
        self.has = {
            "spot": False,
            "margin": False,
            "future": True,
            "option": False,
            "swap": True,
            "createMarketOrder": True,
            "createLimitOrder": True,
            "createStopOrder": True,
            "fetchTicker": True,
            "fetchTickers": True,
            "fetchMarkets": True,
            "fetchBalance": True,
            "fetchPositions": True,
            "fetchOrders": False,  # Would require event parsing
            "fetchOpenOrders": False,
            "fetchClosedOrders": False,
            "cancelOrder": False,  # Would require separate implementation
        }

        self.logger.info(f"Initialized GMX CCXT wrapper for {config.get_chain()}")

    # Core CCXT trading methods

    def create_market_buy_order(self, symbol: str, amount: Union[float, int], price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> TransactionResult:
        """
        Create market buy order (long position)

        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")
            amount: Position size in USD
            price: Ignored for market orders
            params: Additional parameters

        Returns:
            TransactionResult with unsigned transaction
        """
        self.logger.info(f"Creating market buy order: {symbol} ${amount}")

        return self.base_order.create_market_buy_order(symbol=symbol, amount=amount, price=price, params=params)

    def create_market_sell_order(self, symbol: str, amount: Union[float, int], price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> TransactionResult:
        """
        Create market sell order (close long or open short)

        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")
            amount: Position size in USD
            price: Ignored for market orders
            params: Additional parameters (use 'side': 'short' for short position)

        Returns:
            TransactionResult with unsigned transaction
        """
        self.logger.info(f"Creating market sell order: {symbol} ${amount}")

        return self.base_order.create_market_sell_order(symbol=symbol, amount=amount, price=price, params=params)

    def create_limit_buy_order(self, symbol: str, amount: Union[float, int], price: float, params: Optional[Dict[str, Any]] = None) -> TransactionResult:
        """
        Create limit buy order

        Args:
            symbol: Trading pair symbol
            amount: Position size in USD
            price: Limit price
            params: Additional parameters

        Returns:
            TransactionResult with unsigned transaction
        """
        self.logger.info(f"Creating limit buy order: {symbol} ${amount} @ ${price}")

        return self.base_order.create_limit_buy_order(symbol=symbol, amount=amount, price=price, params=params)

    def create_limit_sell_order(self, symbol: str, amount: Union[float, int], price: float, params: Optional[Dict[str, Any]] = None) -> TransactionResult:
        """
        Create limit sell order

        Args:
            symbol: Trading pair symbol
            amount: Position size in USD
            price: Limit price
            params: Additional parameters

        Returns:
            TransactionResult with unsigned transaction
        """
        self.logger.info(f"Creating limit sell order: {symbol} ${amount} @ ${price}")

        return self.base_order.create_limit_sell_order(symbol=symbol, amount=amount, price=price, params=params)

    def create_order(self, symbol: str, type: str, side: str, amount: Union[float, int], price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> TransactionResult:
        """
        Generic order creation method (CCXT compatible)

        Args:
            symbol: Trading pair symbol
            type: Order type ('market', 'limit')
            side: Order side ('buy', 'sell', 'long', 'short')
            amount: Position size in USD
            price: Order price (required for limit orders)
            params: Additional parameters

        Returns:
            TransactionResult with unsigned transaction
        """
        self.logger.info(f"Creating {type} {side} order: {symbol} ${amount}")

        # Convert string types to enums
        order_type = OrderType.MARKET if type.lower() == "market" else OrderType.LIMIT

        # Determine if it's an increase or decrease order
        if side.lower() in ["buy", "long"]:
            order_side = OrderSide.BUY
            if order_type == OrderType.MARKET:
                order_type = OrderType.MARKET_INCREASE
        else:
            order_side = OrderSide.SELL
            if order_type == OrderType.MARKET:
                order_type = OrderType.MARKET_DECREASE

        order_params = OrderParams(symbol=symbol, type=order_type, side=order_side, amount=amount, price=price, is_long=side.lower() in ["buy", "long"], **(params or {}))

        return self.base_order.create_order(order_params)

    # Data fetching methods (CCXT compatible)

    def fetch_markets(self, params: Optional[Dict] = None) -> Dict[str, Dict]:
        """
        Fetch available trading markets

        Args:
            params: Additional parameters (unused)

        Returns:
            Dictionary of available markets in CCXT format
        """
        self.logger.debug("Fetching markets")
        return self.base_order.fetch_markets()

    def fetch_ticker(self, symbol: str, params: Optional[Dict] = None) -> Dict:
        """
        Fetch ticker information for a symbol

        Args:
            symbol: Trading pair symbol
            params: Additional parameters (unused)

        Returns:
            Ticker information dictionary
        """
        self.logger.debug(f"Fetching ticker for {symbol}")
        return self.base_order.fetch_ticker(symbol)

    def fetch_tickers(self, symbols: Optional[list] = None, params: Optional[Dict] = None) -> Dict[str, Dict]:
        """
        Fetch ticker information for multiple symbols

        Args:
            symbols: List of symbols (if None, fetch all)
            params: Additional parameters (unused)

        Returns:
            Dictionary of ticker information
        """
        self.logger.debug("Fetching tickers")

        if symbols is None:
            # Fetch all available markets
            markets = self.fetch_markets()
            symbols = list(markets.keys())

        tickers = {}
        for symbol in symbols:
            try:
                tickers[symbol] = self.fetch_ticker(symbol)
            except Exception as e:
                self.logger.warning(f"Failed to fetch ticker for {symbol}: {e}")
                continue

        return tickers

    # Utility methods

    def symbol_to_market(self, symbol: str) -> Dict:
        """
        Convert symbol to market information

        Args:
            symbol: Trading pair symbol

        Returns:
            Market information
        """
        markets = self.fetch_markets()
        if symbol not in markets:
            raise ValueError(f"Symbol {symbol} not found")
        return markets[symbol]

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        """
        Round amount to market precision

        Args:
            symbol: Trading pair symbol
            amount: Amount to round

        Returns:
            Rounded amount
        """
        market = self.symbol_to_market(symbol)
        precision = market.get("precision", {}).get("amount", 8)
        return round(amount, precision)

    def price_to_precision(self, symbol: str, price: float) -> float:
        """
        Round price to market precision

        Args:
            symbol: Trading pair symbol
            price: Price to round

        Returns:
            Rounded price
        """
        market = self.symbol_to_market(symbol)
        precision = market.get("precision", {}).get("price", 2)
        return round(price, precision)

    # Helper methods for transaction execution

    def sign_and_send_transaction(self, result: TransactionResult, private_key: str) -> str:
        """
        Sign and send transaction (helper method)

        Args:
            result: TransactionResult from order creation
            private_key: Private key for signing

        Returns:
            Transaction hash

        Note:
            This is a convenience method. For production use, handle signing
            externally for better security.
        """
        from eth_account import Account

        # Add nonce if not present
        if "nonce" not in result.transaction:
            wallet_address = self.config.get_wallet_address()
            nonce = self.config.web3.eth.get_transaction_count(wallet_address)
            result.transaction["nonce"] = nonce

        # Sign transaction
        signed_tx = Account.sign_transaction(result.transaction, private_key)

        # Send transaction
        tx_hash = self.config.web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        self.logger.info(f"Transaction sent: {tx_hash.hex()}")
        return tx_hash.hex()

    def estimate_gas(self, result: TransactionResult) -> Dict[str, int]:
        """
        Estimate gas for transaction

        Args:
            result: TransactionResult from order creation

        Returns:
            Gas estimation dictionary
        """
        try:
            # Estimate gas if not already set
            tx_params = result.transaction.copy()
            if "from" not in tx_params:
                tx_params["from"] = self.config.get_wallet_address()

            estimated_gas = self.config.web3.eth.estimate_gas(tx_params)

            return {"gas_limit": result.transaction.get("gas", estimated_gas), "gas_price": result.transaction.get("gasPrice", self.config.web3.eth.gas_price), "estimated_gas": estimated_gas, "max_fee_per_gas": result.transaction.get("maxFeePerGas"), "max_priority_fee_per_gas": result.transaction.get("maxPriorityFeePerGas")}
        except Exception as e:
            self.logger.error(f"Gas estimation failed: {e}")
            raise

    # Status and info methods

    def describe(self) -> Dict[str, Any]:
        """
        Get exchange information (CCXT compatible)

        Returns:
            Exchange description dictionary
        """
        return {
            "id": self.id,
            "name": self.name,
            "countries": self.countries,
            "version": self.version,
            "rateLimit": self.rateLimit,
            "has": self.has,
            "chain": self.config.get_chain(),
            "chain_id": self.config.web3.eth.chain_id,
            "wallet_address": self.config.get_wallet_address(),
        }

    def load_markets(self, reload: bool = False, params: Optional[Dict] = None) -> Dict[str, Dict]:
        """
        Load markets (CCXT compatible)

        Args:
            reload: Whether to reload market cache
            params: Additional parameters

        Returns:
            Markets dictionary
        """
        # For GMX, markets are loaded dynamically, so we just return current markets
        return self.fetch_markets(params)

    # Error handling helper

    def handle_errors(self, code: int, reason: str, url: str, method: str, headers: Dict, body: str):
        """
        Handle API errors (CCXT compatible placeholder)

        Args:
            code: HTTP status code
            reason: Error reason
            url: Request URL
            method: HTTP method
            headers: Request headers
            body: Response body
        """
        # GMX doesn't use traditional HTTP APIs for trading, so this is mostly for compatibility
        if code >= 400:
            raise Exception(f"GMX API Error {code}: {reason}")
