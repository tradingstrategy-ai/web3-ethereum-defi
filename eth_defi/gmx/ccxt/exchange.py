"""CCXT-Compatible Wrapper for GMX Protocol.

This module provides a CCXT-compatible synchronous interface for accessing GMX protocol
market data and trading functionality.

Example usage::

    from web3 import Web3
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.gmx.ccxt import GMX

    # Initialize
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    gmx = GMX(config)

    # Fetch OHLCV data (CCXT-style)
    ohlcv = gmx.fetch_ohlcv("ETH/USD", "1h", limit=100)

.. note::
    GMX protocol does not provide volume data in candlesticks, so volume
    will always be 0 in the returned OHLCV arrays.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from ccxt.base.errors import NotSupported, OrderNotFound
from eth_utils import to_checksum_address

from eth_defi.ccxt.exchange_compatible import ExchangeCompatible
from eth_defi.chain import get_chain_name
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError
from eth_defi.gmx.ccxt.properties import describe_gmx
from eth_defi.gmx.ccxt.validation import _validate_ohlcv_data_sufficiency
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized
from eth_defi.gmx.core import GetOpenPositions
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.utils import calculate_estimated_liquidation_price
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)


class GMX(ExchangeCompatible):
    """
    CCXT-compatible wrapper for GMX protocol market data and trading.

    This class provides a familiar CCXT-style interface for interacting with
    GMX protocol, implementing synchronous methods and data structures that match
    CCXT conventions. This allows traders to use GMX with minimal changes to
    existing CCXT-based trading systems.

    **Market Data Methods:**

    - ``load_markets()`` / ``fetch_markets()`` - Get all available markets
    - ``fetch_ticker(symbol)`` - Get current price and 24h stats for one market
    - ``fetch_tickers(symbols)`` - Get ticker data for multiple markets
    - ``fetch_ohlcv(symbol, timeframe)`` - Get candlestick/OHLCV data
    - ``fetch_trades(symbol, since, limit)`` - Get recent public trades
    - ``fetch_currencies()`` - Get token metadata (decimals, addresses)
    - ``fetch_time()`` - Get blockchain time
    - ``fetch_status()`` - Check API operational status

    **Open Interest & Funding:**

    - ``fetch_open_interest(symbol)`` - Current open interest
    - ``fetch_open_interest_history(symbol, timeframe, since, limit)`` - Historical OI
    - ``fetch_open_interests(symbols)`` - Batch OI fetch
    - ``fetch_funding_rate(symbol)`` - Current funding rate
    - ``fetch_funding_rate_history(symbol, since, limit)`` - Historical funding

    **Trading Methods:**

    - ``fetch_balance()`` - Get account token balances
    - ``fetch_open_orders(symbol)`` - List open positions as orders
    - ``fetch_my_trades(symbol, since, limit)`` - User trade history
    - ``create_order(symbol, type, side, amount, price, params)`` - Create and execute order (requires wallet)
    - ``create_market_buy_order(symbol, amount, params)`` - Open long position
    - ``create_market_sell_order(symbol, amount, params)`` - Open short position
    - ``create_limit_order(symbol, side, amount, price, params)`` - Create limit order (behaves as market)

    **Position Management:**

    - ``fetch_positions(symbols)`` - Get detailed position information with metrics
    - ``set_leverage(leverage, symbol)`` - Configure leverage settings
    - ``fetch_leverage(symbol)`` - Query leverage configuration

    **GMX Limitations:**

    - No ``fetch_order_book()`` - GMX uses liquidity pools, not order books
    - No ``cancel_order()`` - GMX orders execute immediately or revert
    - No ``fetch_order()`` - Orders execute immediately via keeper system
    - Volume data not available in OHLCV
    - 24h high/low calculated from recent OHLCV data
    - Trades derived from position change events
    - Balance "used" amount not calculated (shown as 0.0)
    - Order creation requires wallet parameter during initialization

    :ivar config: GMX configuration object
    :vartype config: GMXConfig
    :ivar api: GMX API client for market data
    :vartype api: GMXAPI
    :ivar web3: Web3 instance for blockchain queries
    :vartype web3: Web3
    :ivar subsquid: Subsquid GraphQL client for historical data
    :vartype subsquid: GMXSubsquidClient
    :ivar markets: Dictionary of available markets (populated by load_markets)
    :vartype markets: Dict[str, Any]
    :ivar timeframes: Supported timeframe intervals
    :vartype timeframes: Dict[str, str]
    :ivar markets_loaded: Flag indicating if markets have been loaded
    :vartype markets_loaded: bool
    """

    # GMX markets that should be skipped due to deprecated or unsupported feeds
    EXCLUDED_SYMBOLS: set[str] = {
        "AI16Z",
        "BTC2",
        "ETH2",
        "GMX2",
        "SOL2",
        "ARB2",
        "APE_DEPRECATED",
    }

    def __init__(
        self,
        config: GMXConfig | None = None,
        params: dict | None = None,
        subsquid_endpoint: str | None = None,
        wallet: HotWallet | None = None,
        **kwargs,
    ):
        """
        Initialize the CCXT wrapper with GMX configuration.

        Supports two initialization styles:

        1. CCXT-style (recommended)::

            gmx = GMX(
                params={
                    "rpcUrl": "https://arb1.arbitrum.io/rpc",
                    "privateKey": "0x...",  # Optional - for trading
                    "chainId": 42161,  # Optional - auto-detected from RPC
                    "subsquidEndpoint": "...",  # Optional
                    "wallet": wallet_object,  # Optional - alternative to privateKey
                    "verbose": True,  # Optional - enable debug logging
                }
            )

        2. Legacy-style (backward compatible)::

            gmx = GMX(config=config, wallet=wallet, subsquid_endpoint="...")

        :param config: GMXConfig object (legacy) or parameters dict (if passed as first arg)
        :type config: GMXConfig | dict | None
        :param params: CCXT-style parameters dictionary
        :type params: dict | None
        :param subsquid_endpoint: Optional Subsquid GraphQL endpoint URL (legacy only)
        :type subsquid_endpoint: str | None
        :param wallet: HotWallet for transaction signing (legacy only)
        :type wallet: HotWallet | None
        """
        # Handle positional arguments and mixed usage
        # If the first argument 'config' is actually a dict, treat it as params
        self.markets_loaded = None
        if isinstance(config, dict):
            params = config
            config = None

        # Prepare kwargs for CCXT base class
        # CCXT expects 'config' to be a dict of parameters if provided
        ccxt_kwargs = kwargs.copy()
        if params:
            ccxt_kwargs.update(params)

        # Initialize CCXT base class
        # We do NOT pass GMXConfig object to super().__init__ as it expects a dict
        super().__init__(config=ccxt_kwargs)

        # Detect initialization style and route to appropriate method
        if params:
            # CCXT-style: dictionary parameters
            self._init_from_parameters(params)
        elif config is not None:
            # Legacy style: GMXConfig object
            self._init_from_config(config, subsquid_endpoint, wallet)
        else:
            # No parameters - minimal initialization
            self._init_empty()

    def _init_from_parameters(self, parameters: dict):
        """Initialize from CCXT-style parameters dictionary.

        :param parameters: Dictionary with rpcUrl, privateKey, chainId, etc.
        :type parameters: dict
        """
        # Extract parameters
        self._rpc_url = parameters.get("rpcUrl", "")
        self._private_key = parameters.get("privateKey", "")
        self._subsquid_endpoint = parameters.get("subsquidEndpoint")
        self._chain_id_override = parameters.get("chainId")
        self._wallet = parameters.get("wallet")
        self._verbose = parameters.get("verbose", False)
        self.execution_buffer = parameters.get("executionBuffer", 2.2)

        # Configure verbose logging if requested
        if self._verbose:
            self._configure_verbose_logging()

        # Create web3 instance from RPC URL
        if not self._rpc_url:
            raise ValueError("rpcUrl is required in parameters")

        self.web3 = create_multi_provider_web3(self._rpc_url)

        # Detect chain from web3 or use override
        if self._chain_id_override:
            chain_id = self._chain_id_override
        else:
            chain_id = self.web3.eth.chain_id

        chain_name = get_chain_name(chain_id).lower()

        # Validate that GMX is supported on this chain
        supported_chains = ["arbitrum", "arbitrum_sepolia", "avalanche"]
        if chain_name not in supported_chains:
            raise ValueError(
                f"GMX not supported on chain {chain_name} (chain_id: {chain_id}). Supported chains: {supported_chains}",
            )

        # Create wallet if private key provided
        if self._private_key and not self._wallet:
            self._wallet = HotWallet.from_private_key(self._private_key)
            self._wallet.sync_nonce(self.web3)

        self.wallet = self._wallet
        wallet_address = self.wallet.address if self.wallet else None

        # Warn if no wallet (view-only mode)
        if not self.wallet:
            logger.warning(
                "GMX initialized without wallet or privateKey. Running in VIEW-ONLY mode. Order creation methods will fail.",
            )

        # Create GMX config from web3 and wallet
        # Pass wallet to config so BaseOrder can access it for auto-approval
        self.config = GMXConfig(self.web3, user_wallet_address=wallet_address, wallet=self.wallet)

        # Initialize API and trader
        self.api = GMXAPI(self.config)
        self.trader = GMXTrading(self.config) if self.wallet else None

        # Store wallet address
        self.wallet_address = wallet_address

        # Initialize Subsquid client
        chain = self.config.get_chain()
        self.subsquid = GMXSubsquidClient(
            chain=chain,
            custom_endpoint=self._subsquid_endpoint,
        )

        # Common initialization
        self._init_common()

    def _configure_verbose_logging(self):
        """Enable verbose logging for GMX SDK components."""
        # Set DEBUG level for all GMX-related loggers
        loggers_to_configure = [
            "eth_defi.gmx",
            "eth_defi.gmx.ccxt",
            "eth_defi.gmx.trading",
            "eth_defi.gmx.api",
            "eth_defi.gmx.config",
        ]

        for logger_name in loggers_to_configure:
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.DEBUG)

            # Add console handler if none exists
            if not logger.handlers:
                handler = logging.StreamHandler()
                handler.setLevel(logging.DEBUG)
                formatter = logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                )
                handler.setFormatter(formatter)
                logger.addHandler(handler)

    def _init_from_config(
        self,
        config: GMXConfig,
        subsquid_endpoint: str | None,
        wallet: HotWallet | None,
    ):
        """Initialize from legacy GMXConfig object.

        :param config: GMX configuration object
        :type config: GMXConfig
        :param subsquid_endpoint: Optional Subsquid endpoint
        :type subsquid_endpoint: str | None
        :param wallet: Optional wallet for trading
        :type wallet: HotWallet | None
        """
        self.config = config
        self.api = GMXAPI(config)
        self.web3 = config.web3
        self.wallet = wallet
        self.execution_buffer = 2.2  # Default execution buffer for legacy config

        # Initialize trading manager
        self.trader = GMXTrading(config) if wallet else None

        # Store wallet address
        self.wallet_address = (
            config.get_wallet_address()
            if hasattr(
                config,
                "get_wallet_address",
            )
            else None
        )

        # Initialize Subsquid client
        chain = config.get_chain()
        self.subsquid = GMXSubsquidClient(
            chain=chain,
            custom_endpoint=subsquid_endpoint,
        )

        # Common initialization
        self._init_common()

    def _load_markets_from_graphql(self) -> dict[str, Any]:
        """Load markets from GraphQL only (for backtesting - no RPC calls).

        Uses GMX API /tokens endpoint to fetch token metadata instead of hardcoding.

        :return: dictionary mapping unified symbols to market info
        :rtype: dict[str, Any]
        """
        try:
            market_infos = self.subsquid.get_market_infos(limit=200)
            logger.debug(f"Fetched {len(market_infos)} markets from GraphQL")

            # Fetch token data from GMX API
            tokens_data = self.api.get_tokens()
            logger.debug(f"Fetched tokens from GMX API, type: {type(tokens_data)}")

            # Build address->symbol mapping (lowercase addresses for matching)
            address_to_symbol = {}
            if isinstance(tokens_data, dict):
                # If tokens_data is a dict, extract the list of tokens
                tokens_list = tokens_data.get("tokens", [])
            elif isinstance(tokens_data, list):
                tokens_list = tokens_data
            else:
                logger.error(f"Unexpected tokens_data format: {type(tokens_data)}")
                tokens_list = []

            for token in tokens_list:
                if not isinstance(token, dict):
                    continue
                address = token.get("address", "").lower()
                symbol = token.get("symbol", "")
                if address and symbol:
                    address_to_symbol[address] = symbol

            logger.debug(f"Built address mapping for {len(address_to_symbol)} tokens")

            markets_dict = {}
            for market_info in market_infos:
                try:
                    index_token_addr = market_info.get("indexTokenAddress", "").lower()
                    market_token_addr = market_info.get("marketTokenAddress", "")

                    # Look up symbol from GMX API tokens data
                    symbol_name = address_to_symbol.get(index_token_addr)

                    if not symbol_name:
                        logger.debug(f"Skipping market with unknown index token: {index_token_addr}")
                        continue  # Skip unknown tokens

                    # Skip excluded symbols
                    if symbol_name in self.EXCLUDED_SYMBOLS:
                        continue

                    # Use Freqtrade futures format (consistent with regular load_markets)
                    unified_symbol = f"{symbol_name}/USDC:USDC"

                    # Calculate max leverage from minCollateralFactor
                    min_collateral_factor = market_info.get("minCollateralFactor")
                    max_leverage = 50.0  # Default
                    if min_collateral_factor:
                        max_leverage = GMXSubsquidClient.calculate_max_leverage(min_collateral_factor) or 50.0

                    maintenance_margin_rate = 1.0 / max_leverage if max_leverage > 0 else 0.02

                    markets_dict[unified_symbol] = {
                        "id": symbol_name,
                        "symbol": unified_symbol,
                        "base": symbol_name,
                        "quote": "USDC",
                        "baseId": symbol_name,
                        "quoteId": "USDC",
                        "settle": "USDC",
                        "settleId": "USDC",
                        "active": True,
                        "type": "swap",
                        "spot": False,
                        "swap": True,
                        "future": True,
                        "option": False,
                        "contract": True,
                        "linear": True,
                        "contractSize": self.parse_number("1"),
                        "precision": {
                            "amount": self.parse_number(self.parse_precision("8")),
                            "price": self.parse_number(self.parse_precision("8")),
                        },
                        "limits": {
                            "amount": {"min": None, "max": None},
                            "price": {"min": None, "max": None},
                            "cost": {"min": 10, "max": None},
                            "leverage": {"min": 1.1, "max": max_leverage},
                        },
                        "maintenanceMarginRate": maintenance_margin_rate,
                        "info": {
                            "market_token": market_token_addr,
                            "index_token": market_info.get("indexTokenAddress"),
                            "long_token": market_info.get("longTokenAddress"),
                            "short_token": market_info.get("shortTokenAddress"),
                            "graphql_only": True,  # Flag to indicate this was loaded from GraphQL
                        },
                    }
                except Exception as e:
                    logger.debug(f"Failed to process market {market_info.get('marketTokenAddress')}: {e}")
                    continue

            self.markets = markets_dict
            self.markets_loaded = True
            self.symbols = list(self.markets.keys())

            logger.info(f"Loaded {len(self.markets)} markets from GraphQL")
            logger.debug(f"Market symbols: {self.symbols}")
            return self.markets

        except Exception as e:
            logger.error(f"Failed to load markets from GraphQL: {e}")
            # Return empty markets rather than failing completely
            self.markets = {}
            self.markets_loaded = True
            self.symbols = []
            return self.markets

    def _init_common(self):
        """Initialize common attributes regardless of init method."""
        self.markets = {}
        self.markets_loaded = False
        self.symbols = []
        self._orders = {}  # Order cache - cleared on fresh runs to avoid stale data

        self.timeframes = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
        }

        # GMX trading fees (approximately 0.07% for most markets)
        # GMX uses perpetual swaps, so fees are defined under 'swap'
        self.fees = {
            "trading": {
                "tierBased": False,
                "percentage": True,
                "maker": 0.0007,  # 0.07% maker fee
                "taker": 0.0007,  # 0.07% taker fee
            },
            "swap": {
                "tierBased": False,
                "percentage": True,
                "maker": 0.0007,  # 0.07% maker fee
                "taker": 0.0007,  # 0.07% taker fee
            },
        }

        self.leverage = {}
        self._token_metadata = {}

    def _init_empty(self):
        """Initialize with minimal functionality (no RPC/config)."""
        self.config = None
        self.api = None
        self.web3 = None
        self.wallet = None
        self.trader = None
        self.subsquid = None
        self.wallet_address = None
        self._init_common()

    def calculate_fee(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float,
        takerOrMaker: str = "taker",
        params: dict = None,
    ) -> dict:
        """Calculate trading fee for GMX positions.

        GMX uses dynamic fees based on pool balancing:
        - Position open/close: 0.04% (balanced) or 0.06% (imbalanced)
        - Normal swaps: 0.05% (balanced) or 0.07% (imbalanced)
        - Stablecoin swaps: 0.005% (balanced) or 0.02% (imbalanced)

        For backtesting, we use a fixed 0.06% (0.0006) which represents
        a realistic middle ground for position trading.

        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")
            type: Order type (e.g., "market", "limit")
            side: Order side ("buy" or "sell")
            amount: Order amount in base currency
            price: Order price
            takerOrMaker: "taker" or "maker" (not used for GMX)
            params: Additional parameters

        Returns:
            Fee dictionary with rate and cost
        """
        if params is None:
            params = {}

        # GMX fee rate: 0.06% (0.0006) for positions
        rate = 0.0006

        # Get market to determine fee currency
        market = None
        if self.markets_loaded:
            normalized_symbol = self._normalize_symbol(symbol)
            if normalized_symbol in self.markets:
                market = self.markets[normalized_symbol]

        # Fee currency is the settlement currency (USDC for GMX)
        currency = market.get("settle", "USDC") if market else "USDC"

        # Calculate fee cost based on position notional value
        cost = amount * price * rate if price and amount else None

        return {
            "type": takerOrMaker,
            "currency": currency,
            "rate": rate,
            "cost": cost,
        }

    def describe(self):
        """Get CCXT exchange description."""
        return describe_gmx()

    def _load_token_metadata(self):
        """Load token metadata for price decimal conversion.

        Caches token decimals and synthetic flags needed for correct price parsing.
        """
        if self._token_metadata:
            return  # Already loaded

        tokens_data = self.api.get_tokens()
        token_list = tokens_data.get("tokens", []) if isinstance(tokens_data, dict) else tokens_data

        for token in token_list:
            address = token.get("address", "").lower()
            symbol = token.get("symbol", "")
            decimals = token.get("decimals")
            if address:
                if decimals is None:
                    raise ValueError(f"GMX API did not return decimals for token {symbol} ({address}). Cannot safely convert prices.")
                self._token_metadata[address] = {
                    "decimals": decimals,
                    "synthetic": token.get("synthetic", False),
                    "symbol": symbol,
                }

    def load_markets(
        self,
        reload: bool = False,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Load available markets from GMX protocol (synchronous version).

        This is the synchronous implementation for the sync GMX class.
        For async support, use the GMX class from eth_defi.gmx.ccxt.async_support.

        :param reload: If True, force reload markets even if already loaded
        :type reload: bool
        :param params: Additional parameters (for CCXT compatibility, not currently used)
        :type params: dict | None
        :return: dictionary mapping unified symbols (e.g. "ETH/USDC") to market info
        :rtype: dict[str, Any]
        """
        if self.markets_loaded and not reload:
            return self.markets

        # Use GraphQL by default for fast initialisation (avoids slow RPC calls to Markets/Oracle)
        # Only use RPC path if explicitly requested via graphql_only=False
        use_graphql_only = not (params and params.get("graphql_only") is False) and not self.options.get("graphql_only") is False

        if use_graphql_only and self.subsquid:
            logger.info("Loading markets from GraphQL")
            return self._load_markets_from_graphql()

        # Fetch available markets from GMX using Markets class (makes RPC calls)
        markets_instance = Markets(self.config)
        available_markets = markets_instance.get_available_markets()

        # Fetch leverage data from subsquid if available
        leverage_by_market = {}
        min_collateral_by_market = {}
        if self.subsquid:
            try:
                market_infos = self.subsquid.get_market_infos(limit=200)
                for market_info in market_infos:
                    market_addr = market_info.get("marketTokenAddress")
                    min_collateral_factor = market_info.get("minCollateralFactor")
                    if market_addr and min_collateral_factor:
                        # Normalise address to checksum format to match available_markets keys
                        market_addr = to_checksum_address(market_addr)
                        max_leverage = GMXSubsquidClient.calculate_max_leverage(
                            min_collateral_factor,
                        )
                        if max_leverage is not None:
                            leverage_by_market[market_addr] = max_leverage
                            min_collateral_by_market[market_addr] = min_collateral_factor
            except Exception as e:
                logger.warning(f"Failed to fetch leverage data from subsquid: {e}")

        # Process markets into CCXT-style format
        for market_address, market_data in available_markets.items():
            symbol_name = market_data.get("market_symbol", "")
            if not symbol_name or symbol_name == "UNKNOWN":
                continue

            if symbol_name in self.EXCLUDED_SYMBOLS:
                logger.debug(
                    "Skipping excluded GMX market %s (address %s)",
                    symbol_name,
                    market_address,
                )
                continue

            # Create unified symbol for Freqtrade futures (e.g., ETH/USDC:USDC)
            unified_symbol = f"{symbol_name}/USDC:USDC"

            # Get max leverage for this market
            max_leverage = leverage_by_market.get(market_address)
            min_collateral_factor = min_collateral_by_market.get(market_address)

            # Calculate maintenance margin rate from min collateral factor
            # maintenanceMarginRate = 1 / max_leverage (approximately)
            # If max_leverage is 50x, maintenance margin is ~2%
            # Default to 0.02 (2%, equivalent to 50x leverage) if not available
            maintenance_margin_rate = (1.0 / max_leverage) if max_leverage else 0.02

            self.markets[unified_symbol] = {
                "id": symbol_name,  # GMX market symbol
                "symbol": unified_symbol,  # CCXT unified symbol
                "base": symbol_name,  # Base currency (e.g., ETH)
                "quote": "USDC",  # Quote currency (settlement in USDC)
                "baseId": symbol_name,
                "quoteId": "USDC",
                "settle": "USDC",  # Settlement currency
                "settleId": "USDC",  # Settlement currency ID
                "active": True,
                "type": "swap",  # GMX provides perpetual swaps
                "spot": False,
                "margin": True,
                "swap": True,
                "future": True,  # Enable for Freqtrade futures backtesting
                "option": False,
                "contract": True,
                "linear": True,
                "inverse": False,
                "contractSize": self.parse_number("1"),
                "maker": 0.0003,
                "taker": 0.0006,
                "precision": {
                    "amount": self.parse_number(self.parse_precision("8")),
                    "price": self.parse_number(self.parse_precision("8")),
                },
                "limits": {
                    "amount": {"min": None, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": 10, "max": None},
                    "leverage": {"min": 1.1, "max": max_leverage},
                },
                "maintenanceMarginRate": maintenance_margin_rate,
                "info": {
                    "market_token": market_address,  # Market contract address
                    "index_token": market_data.get("index_token_address"),
                    "long_token": market_data.get("long_token_address"),
                    "short_token": market_data.get("short_token_address"),
                    "min_collateral_factor": min_collateral_factor,
                    "max_leverage": max_leverage,
                    **market_data,
                },
            }

        self.markets_loaded = True

        # Update symbols list (CCXT compatibility)
        self.symbols = list(self.markets.keys())

        return self.markets

    def fetch_markets(
        self,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch all available markets from GMX protocol.

        This method fetches market data from GMX and returns it as a list of market structures.
        Unlike load_markets(), this method does not cache the results and always fetches fresh data.

        :param params: Additional parameters (not used currently)
        :type params: Dict[str, Any | None]
        :returns: List of market structures
        :rtype: List[Dict[str, Any]]

        Example::

            markets = gmx.fetch_markets()
            for market in markets:
                print(f"{market['symbol']}: {market['base']}/{market['quote']}")
        """
        if params is None:
            params = {}

        # Fetch available markets from GMX using Markets class
        markets_instance = Markets(self.config)
        available_markets = markets_instance.get_available_markets()

        # Fetch leverage data from subsquid
        leverage_by_market = {}
        min_collateral_by_market = {}
        if self.subsquid:
            try:
                market_infos = self.subsquid.get_market_infos(limit=200)
                for market_info in market_infos:
                    market_addr = market_info.get("marketTokenAddress")
                    min_collateral_factor = market_info.get("minCollateralFactor")
                    if market_addr and min_collateral_factor:
                        # Normalize address to checksum format to match available_markets keys
                        market_addr = to_checksum_address(market_addr)
                        max_leverage = GMXSubsquidClient.calculate_max_leverage(
                            min_collateral_factor,
                        )
                        if max_leverage is not None:
                            leverage_by_market[market_addr] = max_leverage
                            min_collateral_by_market[market_addr] = min_collateral_factor
            except Exception as e:
                logger.warning(f"Failed to fetch leverage data from subsquid: {e}")

        markets = []

        # Process markets into CCXT-style format
        for market_address, market_data in available_markets.items():
            symbol_name = market_data.get("market_symbol", "")
            if not symbol_name or symbol_name == "UNKNOWN":
                continue

            if symbol_name in self.EXCLUDED_SYMBOLS:
                continue

            # Use Freqtrade futures format
            unified_symbol = f"{symbol_name}/USDC:USDC"

            # Get max leverage for this market
            max_leverage = leverage_by_market.get(market_address)
            min_collateral_factor = min_collateral_by_market.get(market_address)

            # Calculate maintenance margin rate from min collateral factor
            # maintenanceMarginRate = 1 / max_leverage (approximately)
            # If max_leverage is 50x, maintenance margin is ~2%
            # Default to 0.02 (2%, equivalent to 50x leverage) if not available
            maintenance_margin_rate = (1.0 / max_leverage) if max_leverage else 0.02

            market = {
                "id": symbol_name,
                "symbol": unified_symbol,
                "base": symbol_name,
                "quote": "USDC",
                "baseId": symbol_name,
                "quoteId": "USDC",
                "settle": "USDC",  # Settlement currency
                "settleId": "USDC",  # Settlement currency ID
                "active": True,
                "type": "swap",  # GMX provides perpetual swaps
                "spot": False,
                "margin": True,
                "swap": True,
                "future": True,  # Enable for Freqtrade futures backtesting
                "option": False,
                "contract": True,
                "linear": True,
                "inverse": False,
                "contractSize": self.parse_number("1"),
                "maker": 0.0003,
                "taker": 0.0006,
                "precision": {
                    "amount": self.parse_number(self.parse_precision("8")),
                    "price": self.parse_number(self.parse_precision("8")),
                },
                "limits": {
                    "amount": {"min": None, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": 10, "max": None},
                    "leverage": {"min": 1.1, "max": max_leverage},
                },
                "maintenanceMarginRate": maintenance_margin_rate,
                "info": {
                    "market_token": market_address,  # Market contract address
                    "index_token": market_data.get("index_token_address"),
                    "long_token": market_data.get("long_token_address"),
                    "short_token": market_data.get("short_token_address"),
                    "min_collateral_factor": min_collateral_factor,
                    "max_leverage": max_leverage,
                    **market_data,
                },
            }
            markets.append(market)

        return markets

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to Freqtrade futures format.

        GMX markets are stored with Freqtrade futures format (e.g., "ETH/USDC:USDC")
        but users may call methods with simpler format (e.g., "ETH/USDC").
        This method normalizes the symbol to the internal format.

        :param symbol: Symbol in either format ("ETH/USDC" or "ETH/USDC:USDC")
        :type symbol: str
        :return: Normalized symbol in Freqtrade futures format
        :rtype: str
        """
        # If already in futures format, return as-is
        if ":USDC" in symbol:
            return symbol

        # If in simple format, add :USDC suffix
        # ETH/USDC -> ETH/USDC:USDC
        if "/USDC" in symbol and ":USDC" not in symbol:
            return f"{symbol}:USDC"

        # Return as-is if not a USDC pair
        return symbol

    def market(self, symbol: str) -> dict[str, Any]:
        """Get market information for a specific trading pair.

        :param symbol: Unified symbol (e.g., "ETH/USD" or "ETH/USDC:USDC")
        :type symbol: str
        :return: Market information dictionary
        :rtype: dict[str, Any]
        :raises ValueError: If markets haven't been loaded or symbol not found
        """
        if not self.markets_loaded:
            raise ValueError("Markets not loaded. Call load_markets() first.")

        # Normalize symbol to internal format
        normalized_symbol = self._normalize_symbol(symbol)

        if normalized_symbol not in self.markets:
            raise ValueError(
                f"Market {symbol} not found. Available markets: {list(self.markets.keys())}",
            )

        return self.markets[normalized_symbol]

    def fetch_market_leverage_tiers(
        self,
        symbol: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch leverage tiers for a specific market.

        GMX uses a dynamic leverage system where minimum collateral requirements
        increase with open interest. This method returns discrete tiers approximating
        the continuous leverage model.

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD")
        :type symbol: str
        :param params: Optional parameters:

            - side: "long" or "short" (default: "long")
            - num_tiers: Number of tiers to generate (default: 5)

        :type params: dict[str, Any] | None
        :return: List of leverage tier dictionaries with fields:

            - tier: Tier number
            - minNotional: Minimum position size in USD
            - maxNotional: Maximum position size in USD
            - maxLeverage: Maximum leverage for this tier
            - minCollateralFactor: Required collateral factor

        :rtype: list[dict[str, Any]]

        Example::

            # Get leverage tiers for ETH/USD longs
            tiers = gmx.fetch_market_leverage_tiers("ETH/USD")

            # Get tiers for shorts
            tiers = gmx.fetch_market_leverage_tiers("ETH/USD", {"side": "short"})
        """
        if params is None:
            params = {}

        if not self.subsquid:
            raise NotSupported("Subsquid client not initialized - leverage tiers unavailable")

        # Ensure markets are loaded
        self.load_markets()

        # Get market info
        market_info_ccxt = self.market(symbol)
        market_address = market_info_ccxt["info"]["market_token"]

        # Get leverage tier parameters
        side = params.get("side", "long")
        is_long = side.lower() == "long"
        num_tiers = params.get("num_tiers", 5)

        # Fetch market info from subsquid
        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=1,
        )

        if not market_infos:
            return []

        market_info = market_infos[0]

        # Calculate tiers
        return GMXSubsquidClient.calculate_leverage_tiers(
            market_info,
            is_long=is_long,
            num_tiers=num_tiers,
        )

    def fetch_leverage_tiers(
        self,
        symbols: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch leverage tiers for multiple markets.

        :param symbols: List of symbols (e.g., ["ETH/USD", "BTC/USD"]). If None, fetches for all markets.
        :type symbols: list[str] | None
        :param params: Optional parameters (passed to fetch_market_leverage_tiers)
        :type params: dict[str, Any] | None
        :return: Dictionary mapping symbols to their leverage tiers
        :rtype: dict[str, list[dict[str, Any]]]

        Example::

            # Get leverage tiers for all markets
            all_tiers = gmx.fetch_leverage_tiers()

            # Get tiers for specific markets
            tiers = gmx.fetch_leverage_tiers(["ETH/USD", "BTC/USD"])
        """
        if params is None:
            params = {}

        # Ensure markets are loaded
        self.load_markets()

        # If no symbols specified, use all available markets
        if symbols is None:
            symbols = list(self.markets.keys())

        # Fetch tiers for each symbol
        result = {}
        for symbol in symbols:
            try:
                tiers = self.fetch_market_leverage_tiers(symbol, params)
                result[symbol] = tiers
            except Exception as e:
                logger.warning(f"Failed to fetch leverage tiers for {symbol}: {e}")
                result[symbol] = []

        return result

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[list]:
        """Fetch historical OHLCV (Open, High, Low, Close, Volume) candlestick data.

        This method follows CCXT conventions for fetching historical market data.
        It returns a list of OHLCV candles where each candle is a list of
        [timestamp, open, high, low, close, volume].

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD")
        :type symbol: str
        :param timeframe: Candlestick interval - "1m", "5m", "15m", "1h", "4h", "1d"
        :type timeframe: str
        :param since: Unix timestamp in milliseconds for the earliest candle to fetch (GMX API returns recent candles, filtering is done client-side)
        :type since: int | None
        :param limit: Maximum number of candles to return
        :type limit: int | None
        :param params: Additional parameters (e.g., {"until": timestamp_ms, "skip_validation": True})
        :type params: dict[str, Any] | None
        :return: list of OHLCV candles, each as [timestamp_ms, open, high, low, close, volume]
        :rtype: list[list]
        :raises ValueError: If invalid symbol or timeframe
        :raises InsufficientHistoricalDataError: If insufficient data for requested time range (when since is specified)

        .. note::
            Volume is always 0 as GMX API doesn't provide volume data

        Example::

            # Fetch last 100 hourly candles for ETH
            candles = gmx.fetch_ohlcv("ETH/USD", "1h", limit=100)

            # Fetch candles since specific time
            since = int(time.time() * 1000) - 86400000
            candles = gmx.fetch_ohlcv("ETH/USD", "1h", since=since)

            # Each candle: [timestamp, open, high, low, close, volume]
            for candle in candles:
                timestamp, o, h, l, c, v = candle
                print(f"{timestamp}: O:{o} H:{h} L:{l} C:{c} V:{v}")
        """
        # TODO: Keeping fot CCXT compatibility. Not using this parameter for now
        if params is None:
            params = {}

        # Ensure markets are loaded
        self.load_markets()

        # Get market info and extract GMX token symbol
        market_info = self.market(symbol)
        token_symbol = market_info["id"]  # GMX token symbol (e.g., "ETH")

        # Validate timeframe
        if timeframe not in self.timeframes:
            raise ValueError(
                f"Invalid timeframe: {timeframe}. Supported: {list(self.timeframes.keys())}",
            )

        gmx_period = self.timeframes[timeframe]

        # Fetch candlestick data from GMX API
        response = self.api.get_candlesticks(token_symbol, gmx_period)

        # Parse the response
        candles_data = response.get("candles", [])

        # Parse OHLCV data
        ohlcv = self.parse_ohlcvs(candles_data, market_info, timeframe, since, limit)

        # Validate data sufficiency for backtesting
        _validate_ohlcv_data_sufficiency(
            ohlcv=ohlcv,
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            params=params,
        )

        return ohlcv

    def parse_ohlcvs(
        self,
        ohlcvs: list[list],
        market: dict[str, Any] | None = None,
        timeframe: str = "1m",  # CCXT uses this format so adding this for interface compatibility
        since: int | None = None,
        limit: int | None = None,
        use_tail: bool = True,
    ) -> list[list]:
        """Parse multiple OHLCV candles from GMX format to CCXT format.

        Converts GMX candlestick data (5 fields) to CCXT format (6 fields with volume).
        Applies filtering based on 'since' timestamp and 'limit' parameters.

        :param ohlcvs: list of raw OHLCV data from GMX API (V will be always 0 for GMX)
        :type ohlcvs: list[list]
        :param market: Market information dictionary (optional)
        :type market: dict[str, Any] | None
        :param timeframe: Candlestick interval
        :type timeframe: str
        :param since: Filter candles after this timestamp (ms)
        :type since: int | None
        :param limit: Maximum number of candles to return
        :type limit: int | None
        :param use_tail: If True, return the most recent candles when limiting
        :type use_tail: bool
        :return: list of parsed OHLCV candles in CCXT format
        :rtype: list[list]
        """
        parsed = [self.parse_ohlcv(ohlcv, market) for ohlcv in ohlcvs]

        # Sort by timestamp (ascending)
        parsed = sorted(parsed, key=lambda x: x[0])

        # Filter by 'since' parameter if provided
        if since is not None:
            parsed = [candle for candle in parsed if candle[0] >= since]

        # Apply limit
        if limit is not None and len(parsed) > limit:
            if use_tail:
                # Return the most recent 'limit' candles
                parsed = parsed[-limit:]
            else:
                # Return the oldest 'limit' candles
                parsed = parsed[:limit]

        return parsed

    def parse_ticker(
        self,
        ticker: dict,
        market: dict = None,
    ) -> dict:
        """
        Parse GMX ticker data to CCXT format.

        :param ticker: Raw ticker data from GMX API
        :param market: Market structure from load_markets()
        :return: CCXT-formatted ticker::

            {
                "symbol": "ETH/USD",
                "timestamp": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "high": None,  # Calculated separately from OHLCV
                "low": None,  # Calculated separately from OHLCV
                "bid": last_price,  # GMX doesn't have order books
                "bidVolume": None,
                "ask": last_price,
                "askVolume": None,
                "vwap": None,
                "open": None,  # Calculated separately from OHLCV
                "close": 3350.0,  # Current price
                "last": 3350.0,  # Current price
                "previousClose": None,
                "change": None,
                "percentage": None,
                "average": None,
                "baseVolume": None,  # GMX doesn't provide volume
                "quoteVolume": None,
                "info": {...},  # Raw GMX ticker data
            }
        """
        # Get current timestamp
        timestamp = self.milliseconds()

        # Load token metadata if not already loaded (for decimal conversion)
        self._load_token_metadata()

        # Extract price from ticker
        # GMX API ticker structure: {"maxPrice": "339822976278", "minPrice": "339695402118", "tokenAddress": "0x..."}
        # Price decimal format depends on token type:
        # - Non-synthetic tokens: 12 decimals
        # - Synthetic tokens: (30 - token_decimals) decimals
        max_price = self.safe_string(ticker, "maxPrice")
        min_price = self.safe_string(ticker, "minPrice")
        token_address = self.safe_string(ticker, "tokenAddress", "").lower()

        # Get token metadata for correct decimal conversion
        token_meta = self._token_metadata.get(token_address)
        if not token_meta:
            raise ValueError(f"Token metadata not found for {token_address}. Ensure load_markets() was called and token exists in GMX API.")
        token_decimals = token_meta.get("decimals")
        if token_decimals is None:
            raise ValueError(f"Token decimals not found for {token_address}. Cannot safely convert prices.")

        # Convert from appropriate decimal format to float
        # GMX uses 30-decimal PRECISION for all prices
        # Formula: price_usd = raw_price / 10^(30 - token_decimals)
        # Examples: BTC (8 decimals) = 10^22, ETH (18 decimals) = 10^12
        last_price = None
        if max_price and min_price:
            price_decimals = 30 - token_decimals

            max_price_float = float(max_price) / (10**price_decimals)
            min_price_float = float(min_price) / (10**price_decimals)
            # Use midpoint as last price
            last_price = (max_price_float + min_price_float) / 2

        return {
            "symbol": self.safe_string(market, "symbol"),
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "high": None,  # Will calculate from OHLCV in fetch_ticker
            "low": None,  # Will calculate from OHLCV in fetch_ticker
            "bid": last_price,  # TEMPORARY: Using last price for testing
            "bidVolume": None,
            "ask": last_price,  # TEMPORARY: Using last price for testing
            "askVolume": None,
            "vwap": None,
            "open": None,  # Will calculate from OHLCV in fetch_ticker
            "close": last_price,
            "last": last_price,
            "previousClose": None,
            "change": None,
            "percentage": None,
            "average": None,
            "baseVolume": None,  # GMX doesn't track volume
            "quoteVolume": None,
            "info": ticker,
        }

    def fetch_open_interest(
        self,
        symbol: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch current open interest for a symbol.

        This method returns the current open interest data for both long and short
        positions on GMX protocol using the fast Subsquid GraphQL endpoint.

        Follows CCXT standard by aggregating long + short into openInterestValue,
        while preserving granular long/short breakdown in info field.

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD")
        :type symbol: str
        :param params: Additional parameters - can include "market_address" to query specific market
        :type params: dict[str, Any] | None
        :returns: dictionary with open interest information::

            {
                "symbol": "ETH/USD",
                "baseVolume": None,
                "quoteVolume": None,
                "openInterestAmount": 12345.67,  # Total OI in tokens (ETH)
                "openInterestValue": 37615898.78,  # Aggregated long + short OI in USD
                "timestamp": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "info": {
                    "longOpenInterest": 18807949.39,  # Long OI in USD (parsed)
                    "shortOpenInterest": 18807949.39,  # Short OI in USD (parsed)
                    "longOpenInterestTokens": 6172.835,  # Long OI in ETH (parsed)
                    "shortOpenInterestTokens": 6172.835,  # Short OI in ETH (parsed)
                    ...  # Raw Subsquid data + raw USD/token values
                }
            }

        :rtype: dict[str, Any]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get current open interest for ETH
            oi = gmx.fetch_open_interest("ETH/USD")
            print(f"Total OI: {oi['openInterestAmount']:.2f} ETH")
            print(f"Total OI: ${oi['openInterestValue']:,.0f}")

            # Access long/short breakdown from info field
            print(f"Long: {oi['info']['longOpenInterestTokens']:.2f} ETH")
            print(f"Short: {oi['info']['shortOpenInterestTokens']:.2f} ETH")
            print(f"Long: ${oi['info']['longOpenInterest']:,.0f}")
            print(f"Short: ${oi['info']['shortOpenInterest']:,.0f}")

        .. note::
            Data is fetched from Subsquid GraphQL endpoint for fast access.
            Long/short breakdown is available in the info field.
        """
        if params is None:
            params = {}

        # Ensure markets are loaded
        self.load_markets()

        # Get market info
        market_info = self.market(symbol)
        market_address = params.get(
            "market_address",
            market_info["info"]["market_token"],
        )

        # Fetch latest market info from Subsquid (fast)
        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=1,
            order_by="id_DESC",
        )

        if not market_infos:
            raise ValueError(f"No market info found for {symbol}")

        raw_info = market_infos[0]

        # Parse using helper method (pass market for symbol)
        return self.parse_open_interest(raw_info, market_info)

    def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch historical open interest data from Subsquid.

        Retrieves historical open interest snapshots from the GMX Subsquid GraphQL endpoint.
        Data includes long and short open interest values over time.

        :param symbol: Unified symbol (e.g., "ETH/USD")
        :type symbol: str
        :param timeframe: Time interval (note: data is snapshot-based, not aggregated)
        :type timeframe: str
        :param since: Start timestamp in milliseconds
        :type since: int | None
        :param limit: Maximum number of records (default: 100)
        :type limit: int | None
        :param params: Additional parameters (e.g., {"market_address": "0x..."})
        :type params: dict[str, Any] | None
        :returns: list of historical open interest snapshots
        :rtype: list[dict[str, Any]]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get historical OI for ETH
            history = exchange.fetch_open_interest_history("ETH/USD", limit=50)
            for snapshot in history:
                print(f"{snapshot['datetime']}: ${snapshot['openInterestValue']:,.0f}")

        .. note::
            Data is fetched from Subsquid GraphQL endpoint.
            Returns snapshots, not time-aggregated data.
        """
        if params is None:
            params = {}

        if limit is None:
            limit = 100

        # Ensure markets are loaded for market info
        self.load_markets()

        # Get market info for symbol
        market_info = self.market(symbol) if symbol else None
        market_address = params.get("market_address")
        if market_info and not market_address:
            market_address = market_info["info"]["market_token"]

        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=limit,
        )

        result = []
        for info in market_infos:
            parsed = self.parse_open_interest(info, market_info)
            result.append(parsed)

        return result

    def fetch_open_interests(
        self,
        symbols: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Fetch open interest for multiple symbols at once.

        :param symbols: List of symbols (e.g., ["ETH/USD", "BTC/USD"]). If None, fetch all markets.
        :type symbols: list[str] | None
        :param params: Additional parameters
        :type params: dict[str, Any] | None
        :return: Dictionary mapping symbols to open interest data
        :rtype: dict[str, dict[str, Any]]

        Example::

            # Fetch OI for multiple markets
            ois = gmx.fetch_open_interests(["ETH/USD", "BTC/USD", "ARB/USD"])
            for symbol, oi in ois.items():
                print(f"{symbol}: ${oi['openInterestValue']:,.0f}")

            # Fetch OI for all markets
            all_ois = gmx.fetch_open_interests()
        """
        if params is None:
            params = {}

        self.load_markets()

        # If no symbols specified, use all markets
        if symbols is None:
            symbols = list(self.markets.keys())

        result = {}
        for symbol in symbols:
            try:
                oi = self.fetch_open_interest(symbol, params)
                # Use canonical symbol from the returned data
                canonical_symbol = oi["symbol"]
                result[canonical_symbol] = oi
            except Exception as e:
                # Skip symbols that fail (e.g., swap-only markets without OI data)
                continue

        return result

    def parse_open_interest(
        self,
        interest: dict[str, Any],
        market: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Parse raw open interest data to CCXT format.

        Follows CCXT pattern: aggregates long + short into standard fields,
        preserves granular breakdown in info field.

        :param interest: Raw market info from Subsquid with fields:
            - longOpenInterestUsd: Long OI in USD (30 decimals)
            - shortOpenInterestUsd: Short OI in USD (30 decimals)
            - longOpenInterestInTokens: Long OI in tokens (token decimals)
            - shortOpenInterestInTokens: Short OI in tokens (token decimals)
        :type interest: dict[str, Any]
        :param market: Market information (CCXT market structure)
        :type market: dict[str, Any] | None
        :return: Parsed open interest in CCXT format with structure::

            {
                "symbol": "ETH/USD",
                "openInterestAmount": 12345.67,  # Total OI in tokens (ETH)
                "openInterestValue": 37615898.78,  # Total OI in USD
                "timestamp": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "info": {
                    "longOpenInterest": 18807949.39,  # Long OI in USD (parsed)
                    "shortOpenInterest": 18807949.39,  # Short OI in USD (parsed)
                    "longOpenInterestUsd": "...",  # Long OI in USD (raw 30 decimals)
                    "shortOpenInterestUsd": "...",  # Short OI in USD (raw 30 decimals)
                    "longOpenInterestTokens": 6172.835,  # Long OI in ETH (parsed)
                    "shortOpenInterestTokens": 6172.835,  # Short OI in ETH (parsed)
                    "longOpenInterestInTokens": "...",  # Long OI (raw with decimals)
                    "shortOpenInterestInTokens": "...",  # Short OI (raw with decimals)
                    ...  # Additional Subsquid fields
                }
            }

        :rtype: dict[str, Any]

        Example::

            raw_data = subsquid.get_market_infos(market_address, limit=1)[0]
            market_info = gmx.market("ETH/USD")
            parsed = gmx.parse_open_interest(raw_data, market_info)

            # Access aggregated values
            print(f"Total OI: {parsed['openInterestAmount']:.2f} ETH")
            print(f"Total OI: ${parsed['openInterestValue']:,.2f} USD")

            # Access long/short breakdown
            print(f"Long: {parsed['info']['longOpenInterestTokens']:.2f} ETH")
            print(f"Short: {parsed['info']['shortOpenInterestTokens']:.2f} ETH")
        """
        # Parse 30-decimal USD values
        long_oi_usd_raw = interest.get("longOpenInterestUsd", 0)
        short_oi_usd_raw = interest.get("shortOpenInterestUsd", 0)

        # Convert from 30 decimals to float
        long_oi_usd = float(long_oi_usd_raw) / 1e30 if long_oi_usd_raw else 0.0
        short_oi_usd = float(short_oi_usd_raw) / 1e30 if short_oi_usd_raw else 0.0
        total_oi_usd = long_oi_usd + short_oi_usd

        # Parse token amounts (if available)
        long_oi_tokens_raw = interest.get("longOpenInterestInTokens", 0)
        short_oi_tokens_raw = interest.get("shortOpenInterestInTokens", 0)

        # Convert token amounts to human-readable (need decimals from market info)
        long_oi_tokens = 0.0
        short_oi_tokens = 0.0
        total_oi_tokens = None

        if market and (long_oi_tokens_raw or short_oi_tokens_raw):
            try:
                # Get index token address and decimals
                index_token_address = market["info"]["index_token"]
                decimals = self.subsquid.get_token_decimals(index_token_address)

                # Convert to human-readable amounts
                long_oi_tokens = float(long_oi_tokens_raw) / (10**decimals) if long_oi_tokens_raw else 0.0
                short_oi_tokens = float(short_oi_tokens_raw) / (10**decimals) if short_oi_tokens_raw else 0.0
                total_oi_tokens = long_oi_tokens + short_oi_tokens
            except (KeyError, TypeError, ValueError):
                # If we can't get decimals, leave as None
                pass

        # Get timestamp (use current time as Subsquid doesn't provide snapshot timestamp)
        timestamp = self.milliseconds()

        # Build enriched info dict with both parsed and raw values
        info_dict = {
            # USD values (parsed from 30 decimals)
            "longOpenInterest": long_oi_usd,
            "shortOpenInterest": short_oi_usd,
            # USD values (raw 30 decimals)
            "longOpenInterestUsd": long_oi_usd_raw,
            "shortOpenInterestUsd": short_oi_usd_raw,
            # Token amounts (parsed, human-readable)
            "longOpenInterestTokens": long_oi_tokens,
            "shortOpenInterestTokens": short_oi_tokens,
            # Token amounts (raw with decimals)
            "longOpenInterestInTokens": long_oi_tokens_raw,
            "shortOpenInterestInTokens": short_oi_tokens_raw,
            **interest,  # Include all raw Subsquid data
        }

        return {
            "symbol": self.safe_string(market, "symbol"),
            "baseVolume": None,  # GMX doesn't provide volume data
            "quoteVolume": None,  # GMX doesn't provide volume data
            "openInterestAmount": total_oi_tokens,  # Total in tokens (raw)
            "openInterestValue": total_oi_usd,  # Total in USD
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "info": info_dict,
        }

    def fetch_funding_rate(
        self,
        symbol: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch current funding rate for a symbol.

        This method returns the current funding rate for both long and short
        positions on GMX protocol using the fast Subsquid GraphQL endpoint.

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD")
        :type symbol: str
        :param params: Additional parameters - can include "market_address" to query specific market
        :type params: dict[str, Any] | None
        :returns: dictionary with funding rate information::

            {
                "symbol": "ETH/USD",
                "fundingRate": 0.0001,  # Per-second rate (as decimal)
                "longFundingRate": 0.0001,  # Long position rate (per-second)
                "shortFundingRate": -0.0001,  # Short position rate (per-second)
                "fundingTimestamp": 1234567890000,
                "fundingDatetime": "2021-01-01T00:00:00.000Z",
                "timestamp": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "info": {...},  # Raw Subsquid data
            }

        :rtype: dict[str, Any]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get current funding rate for BTC
            fr = exchange.fetch_funding_rate("BTC/USD")
            # Convert per-second to hourly
            hourly_rate = fr["fundingRate"] * 3600
            print(f"Hourly funding: {hourly_rate:.6f}")

            # Positive rate = longs pay shorts
            # Negative rate = shorts pay longs

        .. note::
            Data is fetched from Subsquid GraphQL endpoint for fast access.
            Rates are per-second values. Multiply by 3600 for hourly rate.
        """
        if params is None:
            params = {}

        # Ensure markets are loaded
        self.load_markets()

        # Get market info
        market_info = self.market(symbol)
        market_address = params.get(
            "market_address",
            market_info["info"]["market_token"],
        )

        # Fetch latest market info from Subsquid (fast)
        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=1,
            order_by="id_DESC",
        )

        if not market_infos:
            raise ValueError(f"No market info found for {symbol}")

        info = market_infos[0]

        # Parse 30-decimal funding rate values
        funding_per_second = float(info.get("fundingFactorPerSecond", 0)) / 1e30
        longs_pay_shorts = info.get("longsPayShorts", True)

        # Determine direction based on longsPayShorts flag
        if longs_pay_shorts:
            long_funding = funding_per_second
            short_funding = -funding_per_second
        else:
            long_funding = -funding_per_second
            short_funding = funding_per_second

        timestamp = self.milliseconds()

        return {
            "symbol": market_info["symbol"],  # Use canonical symbol (ETH/USDC:USDC)
            "fundingRate": funding_per_second,  # Per-second rate
            "longFundingRate": long_funding,  # GMX-specific field
            "shortFundingRate": short_funding,  # GMX-specific field
            "fundingTimestamp": timestamp,
            "fundingDatetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
            "info": info,
        }

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch historical funding rate data from Subsquid.

        Retrieves historical funding rate snapshots from the GMX Subsquid GraphQL endpoint.
        Data includes funding rates per second and direction (longs pay shorts or vice versa).

        :param symbol: Unified symbol (e.g., "ETH/USD")
        :type symbol: str
        :param since: Start timestamp in milliseconds
        :type since: int | None
        :param limit: Maximum number of records (default: 100)
        :type limit: int | None
        :param params: Additional parameters (e.g., {"market_address": "0x..."})
        :type params: dict | None
        :returns: list of historical funding rate snapshots
        :rtype: list[dict[str, Any]]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get historical funding rates for BTC
            history = exchange.fetch_funding_rate_history("BTC/USD", limit=50)
            for snapshot in history:
                rate = snapshot["fundingRate"]
                print(f"{snapshot['datetime']}: {rate * 100:.6f}% per hour")

        .. note::
            Data is fetched from Subsquid GraphQL endpoint.
            Funding rates are per-second values, multiply by 3600 for hourly rate.
        """
        if params is None:
            params = {}

        if limit is None:
            limit = 100

        # Get canonical symbol from market
        market_info = self.market(symbol)
        canonical_symbol = market_info["symbol"]

        market_address = params.get("market_address")
        since_seconds = since // 1000 if since else None

        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=limit,
        )

        result = []
        for info in market_infos:
            funding_per_second = float(info.get("fundingFactorPerSecond", 0)) / 1e30
            longs_pay_shorts = info.get("longsPayShorts", True)

            # Try to extract timestamp from ID or use current time
            timestamp_ms = None
            try:
                info_id = info.get("id", "")
                # Subsquid IDs often contain block number or timestamp
                # For now, we'll use current time if no explicit timestamp field
                timestamp_ms = self.milliseconds()
            except Exception:
                timestamp_ms = self.milliseconds()

            datetime_str = datetime.fromtimestamp(timestamp_ms / 1000).isoformat() + "Z"

            result.append(
                {
                    "symbol": canonical_symbol,
                    "fundingRate": funding_per_second,
                    "longFundingRate": funding_per_second if longs_pay_shorts else -funding_per_second,
                    "shortFundingRate": -funding_per_second if longs_pay_shorts else funding_per_second,
                    "fundingTimestamp": timestamp_ms,
                    "fundingDatetime": datetime_str,
                    "timestamp": timestamp_ms,
                    "datetime": datetime_str,
                    "info": info,
                }
            )

        return result

    def fetch_funding_history(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch funding fee payment history for positions.

        GMX V2 does not track historical funding fee payments per position.
        This method returns an empty list to indicate no funding history is available.
        Freqtrade will calculate funding fees as 0.0 when summing the empty list.

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD") - not used
        :type symbol: str | None
        :param since: Timestamp in milliseconds - not used
        :type since: int | None
        :param limit: Maximum number of records - not used
        :type limit: int | None
        :param params: Additional parameters - not used
        :type params: dict[str, Any] | None
        :returns: Empty list (GMX doesn't provide funding history)
        :rtype: list[dict[str, Any]]

        .. note::
            GMX V2 does not track historical funding fee payments. Funding fees
            are continuously accrued and settled, but the protocol does not
            maintain a queryable history of past payments.

            If you need funding rate history (not payment history), use
            fetch_funding_rate_history() instead.
        """
        logger.warning("fetch_funding_history() called but GMX V2 does not track historical funding fee payments. Returning empty list (funding fees will be calculated as 0.0).")
        return []

    def fetch_ticker(
        self,
        symbol: str,
        params: dict = None,
    ) -> dict:
        """
        Fetch ticker data for a single market.

        Gets current price and 24h statistics for the specified market.
        Note: GMX doesn't provide 24h high/low, so these are calculated from recent OHLCV.

        :param symbol: CCXT symbol (e.g., "ETH/USD")
        :param params: Optional parameters (not used currently)
        :return: CCXT-formatted ticker (see parse_ticker for structure)

        Example::

            ticker = gmx.fetch_ticker("ETH/USD")
            print(f"Current price: ${ticker['last']}")
            print(f"24h high: ${ticker['high']}")
        """
        params = params or {}
        self.load_markets()

        # Get market info
        market = self.market(symbol)

        # Get index token address for this market
        index_token_address = market["info"]["index_token"]

        # Fetch ticker from GMX API
        all_tickers = self.api.get_tickers()

        # Find ticker for this token
        ticker = None
        for t in all_tickers:
            if t.get("tokenAddress", "").lower() == index_token_address.lower():
                ticker = t
                break

        if not ticker:
            raise ValueError(f"No ticker data found for {symbol}")

        # Parse to CCXT format
        result = self.parse_ticker(ticker, market)

        # Calculate 24h high/low from recent OHLCV (last 24 hours of 1h candles)
        try:
            since = self.milliseconds() - (24 * 60 * 60 * 1000)  # 24 hours ago
            ohlcv = self.fetch_ohlcv(symbol, "1h", since=since, limit=24)

            if ohlcv:
                # Extract highs and lows
                highs = [candle[2] for candle in ohlcv]  # Index 2 is high
                lows = [candle[3] for candle in ohlcv]  # Index 3 is low

                result["high"] = max(highs) if highs else None
                result["low"] = min(lows) if lows else None

                # Also get open from first candle
                result["open"] = ohlcv[0][1] if ohlcv else None  # Index 1 is open
        except Exception:
            # If OHLCV fetch fails, leave high/low as None
            pass

        return result

    def fetch_tickers(
        self,
        symbols: list[str] = None,
        params: dict = None,
    ) -> dict:
        """
        Fetch ticker data for multiple markets at once.

        :param symbols: List of CCXT symbols to fetch. If None, fetches all markets.
        :param params: Optional parameters (not used currently)
        :return: Dict mapping symbols to ticker data::

            {
                "ETH/USD": {...},
                "BTC/USD": {...},
                ...
            }

        Example::

            # Fetch all tickers
            tickers = gmx.fetch_tickers()

            # Fetch specific symbols
            tickers = gmx.fetch_tickers(["ETH/USD", "BTC/USD"])
        """
        params = params or {}
        self.load_markets()

        # Fetch all tickers from GMX API once
        all_tickers = self.api.get_tickers()

        # Build mapping of token address to ticker data
        ticker_by_address = {}
        for ticker in all_tickers:
            address = ticker.get("tokenAddress", "").lower()
            if address:
                ticker_by_address[address] = ticker

        # If symbols specified, filter to those; otherwise use all markets
        if symbols is not None:
            target_symbols = symbols
        else:
            target_symbols = list(self.markets.keys())

        # Parse ticker for each requested symbol
        result = {}
        for symbol in target_symbols:
            try:
                market = self.market(symbol)
                # Use canonical symbol from market (ETH/USDC:USDC)
                canonical_symbol = market["symbol"]
                index_token_address = market["info"]["index_token"].lower()

                if index_token_address in ticker_by_address:
                    ticker_data = ticker_by_address[index_token_address]
                    result[canonical_symbol] = self.parse_ticker(ticker_data, market)

                    # Calculate 24h high/low from OHLCV (same as fetch_ticker)
                    try:
                        since = self.milliseconds() - (24 * 60 * 60 * 1000)
                        ohlcv = self.fetch_ohlcv(canonical_symbol, "1h", since=since, limit=24)

                        if ohlcv:
                            highs = [candle[2] for candle in ohlcv]
                            lows = [candle[3] for candle in ohlcv]

                            result[canonical_symbol]["high"] = max(highs) if highs else None
                            result[canonical_symbol]["low"] = min(lows) if lows else None
                            result[canonical_symbol]["open"] = ohlcv[0][1] if ohlcv else None
                    except Exception:
                        pass
            except Exception:
                # Skip symbols we can't fetch
                pass

        return result

    def fetch_currencies(
        self,
        params: dict = None,
    ) -> dict:
        """
        Fetch currency/token metadata.

        Returns information about all tradeable tokens including decimals,
        addresses, and symbols.

        :param params: Optional parameters (not used currently)
        :return: Dict mapping currency codes to metadata::

            {
                "ETH": {
                    "id": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                    "code": "ETH",
                    "name": "Ethereum",
                    "active": True,
                    "fee": None,
                    "precision": 18,
                    "limits": {
                        "amount": {"min": None, "max": None},
                        "withdraw": {"min": None, "max": None}
                    },
                    "info": {...}
                },
                ...
            }

        Example::

            currencies = gmx.fetch_currencies()
            eth_decimals = currencies["ETH"]["precision"]
        """
        params = params or {}

        # Fetch token data from GMX API
        tokens_data = self.api.get_tokens()

        # Extract token list from response (structure is {"tokens": [...]})
        token_list = tokens_data.get("tokens", []) if isinstance(tokens_data, dict) else tokens_data

        result = {}
        for token in token_list:
            # Extract token info (keys are: symbol, address, decimals)
            address = token.get("address", "")
            symbol = token.get("symbol", "")
            decimals = token.get("decimals")
            name = symbol  # GMX API doesn't provide full names

            if symbol and address:
                if decimals is None:
                    raise ValueError(f"GMX API did not return decimals for token {symbol} ({address}). Cannot safely process currencies.")
                result[symbol] = {
                    "id": address,
                    "code": symbol,
                    "name": name,
                    "active": True,  # Assume all GMX tokens are active
                    "fee": None,
                    "precision": decimals,
                    "limits": {"amount": {"min": None, "max": None}, "withdraw": {"min": None, "max": None}},
                    "info": token,
                }

        return result

    def parse_trade(
        self,
        trade: dict,
        market: dict = None,
    ) -> dict:
        """
        Parse trade data to CCXT format.

        GMX doesn't have traditional public trades, so we derive this from
        position change events (opens and closes).

        :param trade: Position change event from Subsquid
        :param market: Market structure
        :return: CCXT-formatted trade::

            {
                "id": "0x123...",
                "order": None,
                "timestamp": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "symbol": "ETH/USD",
                "type": None,
                "side": "buy",  # or "sell"
                "takerOrMaker": None,
                "price": 3350.0,
                "amount": 10.5,
                "cost": 35175.0,
                "fee": {...},
                "info": {...},
            }
        """
        # Get timestamp from trade event
        timestamp = self.safe_integer(trade, "timestamp")
        if timestamp:
            timestamp = timestamp * 1000  # Convert to milliseconds if needed
        else:
            timestamp = self.milliseconds()

        # Determine side from position change action
        # Use explicit mapping for robustness
        action = self.safe_string(trade, "action")
        ACTION_TO_SIDE = {
            "PositionIncrease": "buy",
            "PositionDecrease": "sell",
            "IncreaseLong": "buy",
            "IncreaseShort": "sell",
            "DecreaseLong": "sell",  # Closing long = selling
            "DecreaseShort": "buy",  # Closing short = buying
        }
        side = ACTION_TO_SIDE.get(action, "buy" if "Increase" in str(action) else "sell")

        # Get price and amount
        price = self.safe_number(trade, "executionPrice")
        size_delta = self.safe_number(trade, "sizeDeltaUsd")

        # Calculate amount in base currency
        amount = None
        cost = None
        if price and size_delta:
            cost = abs(size_delta)
            amount = cost / price if price > 0 else None

        # Parse fee if available
        fee = None
        fee_amount = self.safe_number(trade, "feeUsd")
        if fee_amount:
            fee = {"cost": abs(fee_amount), "currency": "USD"}

        return {
            "id": self.safe_string(trade, "id"),
            "order": None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "symbol": self.safe_string(market, "symbol"),
            "type": None,
            "side": side,
            "takerOrMaker": None,
            "price": price,
            "amount": amount,
            "cost": cost,
            "fee": fee,
            "info": trade,
        }

    def fetch_trades(
        self,
        symbol: str,
        since: int = None,
        limit: int = None,
        params: dict = None,
    ) -> list[dict]:
        """
        Fetch recent public trades for a market.

        Note: GMX doesn't have traditional public trades. This method derives
        trade data from position change events via Subsquid GraphQL.

        :param symbol: CCXT symbol (e.g., "ETH/USD")
        :param since: Timestamp in milliseconds to fetch trades from
        :param limit: Maximum number of trades to return
        :param params: Optional parameters (not used currently)
        :return: List of CCXT-formatted trades

        Example::

            # Get last 50 trades
            trades = gmx.fetch_trades("ETH/USD", limit=50)

            # Get trades since yesterday
            since = int((datetime.now() - timedelta(days=1)).timestamp() * 1000)
            trades = gmx.fetch_trades("ETH/USD", since=since)
        """
        params = params or {}
        self.load_markets()

        # Get market info
        market = self.market(symbol)
        market_address = market["info"]["market_token"].lower()

        # Fetch position changes from Subsquid
        # Note: get_position_changes() doesn't support filtering by market or timestamp
        # so we fetch more and filter manually
        position_changes = self.subsquid.get_position_changes(limit=limit or 100)

        # Parse and filter position changes as trades
        trades = []
        for change in position_changes:
            try:
                # Filter by market address
                change_market = change.get("market", "").lower()
                if change_market != market_address:
                    continue

                # Filter by timestamp if specified
                if since:
                    change_timestamp = change.get("timestamp", 0) * 1000  # Convert to ms
                    if change_timestamp < since:
                        continue

                trade = self.parse_trade(change, market)
                trades.append(trade)
            except Exception:
                # Skip trades we can't parse
                pass

        # Sort by timestamp descending (most recent first)
        trades.sort(key=lambda x: x["timestamp"], reverse=True)

        # Apply limit if specified
        if limit:
            trades = trades[:limit]

        return trades

    def fetch_time(
        self,
        params: dict = None,
    ) -> int:
        """
        Fetch current server time.

        For GMX (blockchain-based), this returns the timestamp of the latest
        Arbitrum block.

        :param params: Optional parameters (not used currently)
        :return: Current timestamp in milliseconds

        Example::

            server_time = gmx.fetch_time()
            print(f"Server time: {server_time}")
        """
        params = params or {}

        # Get latest block timestamp from Arbitrum
        latest_block = self.web3.eth.get_block("latest")
        timestamp_seconds = latest_block["timestamp"]

        # Convert to milliseconds
        return timestamp_seconds * 1000

    def fetch_status(
        self,
        params: dict = None,
    ) -> dict:
        """
        Fetch API operational status.

        Checks if GMX API and Subsquid endpoints are responding.

        :param params: Optional parameters (not used currently)
        :return: Status information::

            {
                "status": "ok",  # or "maintenance"
                "updated": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "eta": None,
                "url": None,
                "info": {...},
            }

        Example::

            status = gmx.fetch_status()
            if status["status"] == "ok":
                print("API is operational")
        """
        params = params or {}

        timestamp = self.milliseconds()
        status_result = "ok"
        info = {}

        try:
            # Test GMX API by fetching tickers
            tickers = self.api.get_tickers()
            info["gmx_api"] = "ok"
            info["gmx_api_markets"] = len(tickers)
        except Exception as e:
            status_result = "maintenance"
            info["gmx_api"] = f"error: {str(e)}"

        try:
            # Test Subsquid by fetching markets
            markets = self.subsquid.get_markets()
            info["subsquid"] = "ok"
            info["subsquid_markets"] = len(markets)
        except Exception as e:
            status_result = "maintenance"
            info["subsquid"] = f"error: {str(e)}"

        try:
            # Test web3 connection
            latest_block = self.web3.eth.block_number
            info["web3"] = "ok"
            info["web3_block_number"] = latest_block
        except Exception as e:
            status_result = "maintenance"
            info["web3"] = f"error: {str(e)}"

        return {
            "status": status_result,
            "updated": timestamp,
            "datetime": self.iso8601(timestamp),
            "eta": None,
            "url": None,
            "info": info,
        }

    def fetch_balance(
        self,
        params: dict = None,
    ) -> dict:
        """
        Fetch account token balances.

        Returns wallet balances for all supported tokens.
        Requires user_wallet_address to be set in GMXConfig.

        :param params: Optional parameters
            - wallet_address: Override default wallet address from config
        :return: CCXT-formatted balance::

            {
                "ETH": {
                    "free": 1.5,  # Available balance
                    "used": 0.0,  # Locked in positions (not implemented yet)
                    "total": 1.5,  # Total balance
                },
                "USDC": {...},
                "free": {...},  # Summary of all free balances
                "used": {...},  # Summary of all used balances
                "total": {...},  # Summary of all total balances
                "info": {...},  # Raw balance data
            }

        Example::

            # Initialize with wallet address
            config = GMXConfig(web3, user_wallet_address="0x...")
            gmx = GMX(config)
            balance = gmx.fetch_balance()
            eth_balance = balance["ETH"]["free"]
        """
        params = params or {}

        # Get wallet address from params or stored wallet_address
        wallet = params.get("wallet_address", self.wallet_address)
        if not wallet:
            raise ValueError("wallet_address must be provided in GMXConfig or params")

        # Convert to checksum address
        wallet = self.web3.to_checksum_address(wallet)

        # Fetch currency metadata
        currencies = self.fetch_currencies()

        # Fetch open positions to calculate locked collateral
        collateral_locked = {}  # Maps token symbol to locked amount (in token units)
        try:
            from eth_defi.gmx.core import GetOpenPositions

            positions_manager = GetOpenPositions(self.config)
            positions = positions_manager.get_data(wallet)

            for position_key, position_data in positions.items():
                # Get collateral token and amount
                collateral_token = position_data.get("collateral_token", "")
                collateral_amount_raw = position_data.get("initial_collateral_amount", 0)

                if collateral_token and collateral_amount_raw:
                    # Get token decimals
                    token_decimals = currencies.get(collateral_token, {}).get("precision", 18)

                    # Convert to float
                    collateral_amount_float = float(collateral_amount_raw) / (10**token_decimals)

                    # Add to locked amounts
                    if collateral_token not in collateral_locked:
                        collateral_locked[collateral_token] = 0.0
                    collateral_locked[collateral_token] += collateral_amount_float

        except Exception:
            # If we can't fetch positions, just show all balance as free
            pass

        # Build balance dict
        result = {"free": {}, "used": {}, "total": {}, "info": {}}

        # Query balance for each token
        for code, currency in currencies.items():
            token_address = currency["id"]
            decimals = currency["precision"]

            try:
                # Fetch token details and contract
                token_details = fetch_erc20_details(self.web3, token_address, chain_id=self.web3.eth.chain_id)

                # Get balance
                balance_raw = token_details.contract.functions.balanceOf(wallet).call()
                balance_float = float(balance_raw) / (10**decimals)

                # Calculate used (locked in positions) and free amounts
                used_amount = collateral_locked.get(code, 0.0)
                free_amount = max(0.0, balance_float - used_amount)  # Ensure non-negative
                total_amount = balance_float

                result[code] = {"free": free_amount, "used": used_amount, "total": total_amount}

                result["free"][code] = free_amount
                result["used"][code] = used_amount
                result["total"][code] = total_amount

                result["info"][code] = {"address": token_address, "raw_balance": str(balance_raw), "decimals": decimals}

            except Exception as e:
                # Skip tokens we can't query
                result["info"][code] = {"error": str(e)}

        return result

    def _get_token_decimals(self, market: dict | None) -> int | None:
        """Get token decimals from market metadata.

        :param market: Market structure with info containing index_token address
        :return: Token decimals or None if not found
        """
        if not market or not isinstance(market, dict):
            return None

        # Ensure token metadata is loaded
        if not getattr(self, "_token_metadata", None):
            self._token_metadata = {}
        if not self._token_metadata:
            self._load_token_metadata()

        info = market.get("info", {}) or {}
        index_addr = (info.get("index_token") or info.get("indexTokenAddress") or "").lower()
        if not index_addr:
            raise ValueError(f"Market {market.get('symbol', 'unknown')} has no index_token address. Cannot determine decimals.")

        token_meta = self._token_metadata.get(index_addr)
        if not token_meta:
            raise ValueError(f"Token metadata not found for index token {index_addr}. Ensure load_markets() was called.")

        decimals = token_meta.get("decimals")
        if decimals is None:
            raise ValueError(f"Decimals not found for token {index_addr}. GMX API response is missing decimals.")

        return int(decimals)

    def _convert_price_to_usd(self, raw_price: float | int | None, market: dict | None) -> float | None:
        """Convert GMX raw price to USD using the standard formula.

        Uses the same conversion as open_positions.py:
            price_usd = raw_price / 10^(30 - token_decimals)

        :param raw_price: Raw price from GMX (may be in 30-decimal format or already USD)
        :param market: Market structure to get token decimals
        :return: Price in USD or None
        """
        from eth_defi.gmx.utils import convert_raw_price_to_usd

        if raw_price is None:
            return None

        try:
            v = float(raw_price)
        except Exception:
            return None

        # If already looks like a valid USD price, return as-is
        # This handles data that's already been converted (e.g., from GetOpenPositions)
        if 0.01 <= v <= 1_000_000:
            return v

        # Get token decimals from market metadata
        token_decimals = self._get_token_decimals(market)
        if token_decimals is None:
            # Cannot convert without knowing token decimals
            # Return as-is - caller should ensure proper conversion upstream
            return v

        return convert_raw_price_to_usd(v, token_decimals)

    def parse_order(
        self,
        order: dict,
        market: dict = None,
    ) -> dict:
        """
        Parse order/position data to CCXT format.

        :param order: Order/position data from GMX
        :param market: Market structure
        :return: CCXT-formatted order::

            {"id": "ETH_long", "clientOrderId": None, "timestamp": 1234567890000, "datetime": "2021-01-01T00:00:00.000Z", "lastTradeTimestamp": None, "symbol": "ETH/USD", "type": "market", "side": "buy", "price": 3350.0, "amount": 10.5, "cost": 35175.0, "average": 3350.0, "filled": 10.5, "remaining": 0.0, "status": "open", "fee": None, "trades": [], "info": {...}}
        """
        # Get timestamp (if available)
        timestamp = self.milliseconds()

        # Determine side from position
        is_long = order.get("is_long", True)
        side = "buy" if is_long else "sell"

        # Get position size and prices
        # Note: Data from GetOpenPositions is already converted to USD
        # _convert_price_to_usd handles both raw and pre-converted values
        position_size_usd = self.safe_number(order, "position_size")
        raw_entry_price = self.safe_number(order, "entry_price")
        raw_mark_price = self.safe_number(order, "mark_price")

        # Convert prices to USD (handles already-converted values gracefully)
        entry_price = self._convert_price_to_usd(raw_entry_price, market)
        mark_price = self._convert_price_to_usd(raw_mark_price, market)

        # Calculate amount in base currency
        amount = None
        if position_size_usd and entry_price and entry_price > 0:
            amount = position_size_usd / entry_price

        # Get position key as order ID
        order_id = order.get("position_key", "unknown")

        return {
            "id": order_id,
            "clientOrderId": None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "lastTradeTimestamp": timestamp,
            "symbol": self.safe_string(market, "symbol"),
            "type": "market",  # GMX primarily uses market orders
            "timeInForce": None,
            "postOnly": False,
            "side": side,
            "price": entry_price or mark_price,
            "stopPrice": None,
            "amount": amount,
            "cost": position_size_usd,
            "average": entry_price or mark_price,
            "filled": amount,  # Assume fully filled for market orders
            "remaining": 0.0,
            "status": "open",
            "fee": None,
            "trades": [],
            "info": order,
        }

    def parse_position(
        self,
        position: dict,
        market: dict = None,
    ) -> dict:
        """
        Parse position data to CCXT format.

        :param position: Position data from GMX
        :param market: Market structure
        :return: CCXT-formatted position::

            {"id": "ETH_long_0x123...", "symbol": "ETH/USD", "timestamp": 1234567890000, "datetime": "2021-01-01T00:00:00.000Z", "isolated": False, "hedged": False, "side": "long", "contracts": 10.5, "contractSize": 1, "entryPrice": 3350.0, "markPrice": 3400.0, "notional": 35700.0, "leverage": 5.0, "collateral": 7140.0, "initialMargin": 7140.0, "maintenanceMargin": 357.0, "initialMarginPercentage": 0.20, "maintenanceMarginPercentage": 0.01, "unrealizedPnl": 525.0, "liquidationPrice": 2680.0, "marginRatio": 0.05, "percentage": 7.35, "info": {...}}
        """
        # Get timestamp
        timestamp = self.milliseconds()

        # Determine side from position
        is_long = position.get("is_long", True)
        side = "long" if is_long else "short"

        # Get position size and prices
        position_size_usd = self.safe_number(position, "position_size")
        entry_price = self.safe_number(position, "entry_price")
        mark_price = self.safe_number(position, "mark_price")
        collateral_amount = self.safe_number(
            position,
            "initial_collateral_amount_usd",
        )

        # Calculate contracts (amount in base currency)
        contracts = None
        if position_size_usd and entry_price and entry_price > 0:
            contracts = position_size_usd / entry_price

        # Calculate notional (current position value)
        notional = None
        if contracts and mark_price:
            notional = contracts * mark_price

        # Calculate leverage
        leverage = None
        if position_size_usd and collateral_amount and collateral_amount > 0:
            leverage = position_size_usd / collateral_amount

        # Calculate unrealized PnL
        unrealized_pnl = None
        percentage = self.safe_number(position, "percent_profit")
        if position_size_usd and percentage is not None:
            unrealized_pnl = position_size_usd * (percentage / 100)

        # Calculate liquidation price including fees
        liquidation_price = None
        if entry_price and collateral_amount and position_size_usd and position_size_usd > 0:
            liquidation_price = calculate_estimated_liquidation_price(
                entry_price=entry_price,
                collateral_usd=collateral_amount,
                size_usd=position_size_usd,
                is_long=is_long,
                maintenance_margin=0.01,  # GMX typically uses 1%
                include_closing_fee=True,  # Include 0.07% closing fee
            )

        # Calculate margin ratio (used margin / total position value)
        margin_ratio = None
        if collateral_amount and notional and notional > 0:
            margin_ratio = collateral_amount / notional

        # Initial margin equals collateral for GMX
        initial_margin = collateral_amount

        # Maintenance margin (approximate - GMX uses ~1% of position size)
        maintenance_margin = None
        if position_size_usd:
            maintenance_margin = position_size_usd * 0.01

        # Margin percentages
        initial_margin_percentage = None
        if leverage:
            initial_margin_percentage = 1.0 / leverage

        maintenance_margin_percentage = 0.01  # GMX typically uses 1%

        # Get position key as ID
        position_id = position.get("position_key", "unknown")

        return {
            "id": position_id,
            "symbol": self.safe_string(market, "symbol") if market else None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "isolated": False,  # GMX uses cross margin
            "hedged": False,  # GMX doesn't support hedging mode
            "side": side,
            "contracts": contracts,
            "contractSize": self.parse_number("1"),
            "entryPrice": entry_price,
            "markPrice": mark_price,
            "notional": notional,
            "leverage": leverage,
            "collateral": collateral_amount,
            "initialMargin": initial_margin,
            "maintenanceMargin": maintenance_margin,
            "initialMarginPercentage": initial_margin_percentage,
            "maintenanceMarginPercentage": maintenance_margin_percentage,
            "unrealizedPnl": unrealized_pnl,
            "liquidationPrice": liquidation_price,
            "marginRatio": margin_ratio,
            "percentage": percentage,
            "info": position,
        }

    def fetch_open_orders(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = None,
        params: dict = None,
    ) -> list[dict]:
        """
        Fetch open orders (positions) for the account.

        In GMX, open positions are treated as "open orders".
        Requires user_wallet_address to be set in GMXConfig.

        :param symbol: Filter by symbol (optional)
        :param since: Not used (GMX returns current positions)
        :param limit: Maximum number of orders to return
        :param params: Optional parameters
            - wallet_address: Override default wallet address
        :return: List of CCXT-formatted orders

        Example::

            # Initialize with wallet address
            config = GMXConfig(web3, user_wallet_address="0x...")
            gmx = GMX(config)
            orders = gmx.fetch_open_orders()
            eth_orders = gmx.fetch_open_orders(symbol="ETH/USD")
        """
        params = params or {}
        self.load_markets()

        # Get wallet address
        wallet = params.get("wallet_address", self.wallet_address)
        if not wallet:
            raise ValueError("wallet_address must be provided in GMXConfig or params")

        # Import and use GetOpenPositions
        from eth_defi.gmx.core.open_positions import GetOpenPositions

        # Fetch open positions
        positions_manager = GetOpenPositions(self.config)
        positions = positions_manager.get_data(wallet)

        # Parse to CCXT orders
        result = []
        for position_key, position_data in positions.items():
            try:
                # Find matching market
                market_symbol = position_data.get("market_symbol", "")
                unified_symbol = f"{market_symbol}/USDC:USDC"

                # Skip if filtering by symbol
                if symbol:
                    # Normalize the input symbol for comparison
                    normalized_input_symbol = self._normalize_symbol(symbol)
                    if unified_symbol != normalized_input_symbol:
                        continue

                # Get market info
                if unified_symbol in self.markets:
                    market = self.markets[unified_symbol]
                else:
                    # Create minimal market if not found
                    market = {"symbol": unified_symbol}

                # Add position key to data for ID
                position_data["position_key"] = position_key

                # Parse position as order
                order = self.parse_order(position_data, market)
                result.append(order)

            except Exception:
                # Skip positions we can't parse
                pass

        # Apply limit
        if limit:
            result = result[:limit]

        return result

    def fetch_my_trades(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = None,
        params: dict = None,
    ) -> list[dict]:
        """
        Fetch user's trade history.

        Returns position changes (opens/closes) for the account.
        Requires user_wallet_address to be set in GMXConfig.

        :param symbol: Filter by symbol (optional)
        :param since: Timestamp in milliseconds to fetch trades from
        :param limit: Maximum number of trades to return
        :param params: Optional parameters
            - wallet_address: Override default wallet address
        :return: List of CCXT-formatted trades

        Example::

            config = GMXConfig(web3, user_wallet_address="0x...")
            gmx = GMX(config)
            trades = gmx.fetch_my_trades(limit=50)

            # Filter by symbol
            eth_trades = gmx.fetch_my_trades(symbol="ETH/USD")
        """
        params = params or {}
        self.load_markets()

        # Get wallet address
        wallet = params.get("wallet_address", self.wallet_address)
        if not wallet:
            raise ValueError("wallet_address must be provided in GMXConfig or params")

        # Fetch position changes from Subsquid
        # NOTE: get_position_changes() only accepts account, position_key, and limit parameters
        # We need to filter by timestamp manually
        position_changes = self.subsquid.get_position_changes(
            account=wallet,
            limit=limit or 100,
        )

        # Parse each position change as a trade
        trades = []
        for change in position_changes:
            try:
                # Filter by timestamp if specified
                if since:
                    change_timestamp = change.get("timestamp", 0) * 1000  # Convert to milliseconds
                    if change_timestamp < since:
                        continue

                # Find market
                market_address = change.get("market")
                market = None

                # Search for matching market
                for symbol_key, market_info in self.markets.items():
                    if market_info["info"]["market_token"].lower() == market_address.lower():
                        market = market_info
                        break

                if market is None:
                    continue

                # Skip if filtering by symbol
                if symbol and market["symbol"] != symbol:
                    continue

                # Parse trade
                trade = self.parse_trade(change, market)
                trades.append(trade)

            except Exception:
                # Skip trades we can't parse
                pass

        # Sort by timestamp descending
        trades.sort(key=lambda x: x["timestamp"], reverse=True)

        # Apply limit
        if limit:
            trades = trades[:limit]

        return trades

    def fetch_positions(
        self,
        symbols: list[str] = None,
        params: dict = None,
    ) -> list[dict]:
        """
        Fetch all open positions for the account.

        Returns detailed position information with full metrics (leverage, PnL, liquidation price, etc.).
        Requires user_wallet_address to be set in GMXConfig.

        :param symbols: Filter by list of symbols (optional)
        :param params: Optional parameters
            - wallet_address: Override default wallet address
        :return: List of CCXT-formatted positions

        Example::

            config = GMXConfig(web3, user_wallet_address="0x...")
            gmx = GMX(config)

            # Fetch all positions
            positions = gmx.fetch_positions()

            # Filter specific symbols
            positions = gmx.fetch_positions(symbols=["ETH/USD", "BTC/USD"])

            # Access position details
            for pos in positions:
                print(f"{pos['symbol']}: {pos['side']} {pos['contracts']} @ {pos['entryPrice']}")
                print(f"  Leverage: {pos['leverage']}x")
                print(f"  PnL: ${pos['unrealizedPnl']:.2f} ({pos['percentage']:.2f}%)")
                print(f"  Liquidation: ${pos['liquidationPrice']:.2f}")
        """
        params = params or {}
        self.load_markets()

        # Get wallet address
        wallet = params.get("wallet_address", self.wallet_address)
        if not wallet:
            raise ValueError("wallet_address must be provided in GMXConfig or params")

        # Fetch open positions
        positions_manager = GetOpenPositions(self.config)
        positions = positions_manager.get_data(wallet)

        # Parse to CCXT positions
        result = []
        for position_key, position_data in positions.items():
            try:
                # Find matching market
                market_symbol = position_data.get("market_symbol", "")
                unified_symbol = f"{market_symbol}/USDC:USDC"

                # Skip if filtering by symbols
                if symbols:
                    # Normalize input symbols for comparison
                    normalized_symbols = [self._normalize_symbol(s) for s in symbols]
                    if unified_symbol not in normalized_symbols:
                        continue

                # Get market info
                if unified_symbol in self.markets:
                    market = self.markets[unified_symbol]
                else:
                    # Create minimal market if not found
                    market = {"symbol": unified_symbol}

                # Add position key to data for ID
                position_data["position_key"] = position_key

                # Parse position
                position = self.parse_position(position_data, market)
                result.append(position)

            except Exception:
                # Skip positions we can't parse
                pass

        return result

    def set_leverage(
        self,
        leverage: float,
        symbol: str = None,
        params: dict = None,
    ) -> dict:
        """
        Set leverage for a symbol (or all symbols if not specified).

        Note: This only stores leverage settings locally for future order creation.
        GMX leverage is set per-position when creating the order, not globally.

        :param leverage: Leverage multiplier (e.g., 5.0 for 5x leverage)
        :param symbol: Symbol to set leverage for (e.g., "ETH/USD"). If None, sets default for all symbols
        :param params: Optional parameters (reserved for future use)
        :return: Leverage info dictionary

        Example::

            gmx = GMX(config)

            # Set leverage for specific symbol
            gmx.set_leverage(5.0, "ETH/USD")

            # Set default leverage for all symbols
            gmx.set_leverage(10.0)
        """
        params = params or {}

        # TODO: GMX now supports leverage less than 1. Handle this new update
        # Validate leverage
        if leverage < 1.0:
            raise ValueError(f"Leverage must be >= 1.0, got {leverage}")
        if leverage > 100.0:
            raise ValueError(f"Leverage cannot exceed 100x, got {leverage}")

        if symbol:
            # Set leverage for specific symbol
            self.leverage[symbol] = leverage
            return {"symbol": symbol, "leverage": leverage, "info": {"message": f"Leverage set to {leverage}x for {symbol}"}}
        else:
            # Set default leverage (stored with key '*')
            self.leverage["*"] = leverage
            return {
                "symbol": "*",
                "leverage": leverage,
                "info": {"message": f"Default leverage set to {leverage}x for all symbols"},
            }

    def fetch_leverage(
        self,
        symbol: str = None,
        params: dict = None,
    ) -> dict | list[dict]:
        """
        Get current leverage setting(s).

        Returns stored leverage configuration. If no leverage has been set,
        returns default of 1.0 (no leverage).

        :param symbol: Symbol to get leverage for. If None, returns all leverage settings
        :param params: Optional parameters (reserved for future use)
        :return: Leverage info dictionary or list of dictionaries

        Example::

            gmx = GMX(config)

            # Get leverage for specific symbol
            info = gmx.fetch_leverage("ETH/USD")
            print(f"ETH/USD leverage: {info['leverage']}x")

            # Get all leverage settings
            all_leverage = gmx.fetch_leverage()
        """
        params = params or {}

        if symbol:
            # Get leverage for specific symbol
            leverage = self.leverage.get(symbol)
            if leverage is None:
                # Try to get default leverage
                leverage = self.leverage.get("*", 1.0)

            return {"symbol": symbol, "leverage": leverage, "info": {}}
        else:
            # Return all leverage settings
            result = []
            for sym, lev in self.leverage.items():
                result.append({"symbol": sym, "leverage": lev, "info": {}})

            # If no settings, return default
            if not result:
                result.append(
                    {"symbol": "*", "leverage": 1.0, "info": {"message": "No leverage settings configured, using default 1.0x"}},
                )

            return result

    def add_margin(
        self,
        symbol: str,
        amount: float,
        params: dict = None,
    ) -> dict:
        """
        Add margin to an existing position.

        Note: This method is not yet implemented and requires GMX contract integration.

        :param symbol: Symbol of the position (e.g., "ETH/USD")
        :param amount: Amount of collateral to add (in USD)
        :param params: Optional parameters
        :raises NotImplementedError: Method requires GMX contract integration

        Example::

            # This will raise NotImplementedError
            gmx.add_margin("ETH/USD", 1000.0)
        """
        raise NotImplementedError(
            "add_margin() requires GMX smart contract integration. This method will be implemented in a future update when GMX trading contract methods are added to the library.",
        )

    def reduce_margin(
        self,
        symbol: str,
        amount: float,
        params: dict = None,
    ) -> dict:
        """
        Remove margin from an existing position.

        Note: This method is not yet implemented and requires GMX contract integration.

        :param symbol: Symbol of the position (e.g., "ETH/USD")
        :param amount: Amount of collateral to remove (in USD)
        :param params: Optional parameters
        :raises NotImplementedError: Method requires GMX contract integration

        Example::

            # This will raise NotImplementedError
            gmx.reduce_margin("ETH/USD", 500.0)
        """
        raise NotImplementedError(
            "reduce_margin() requires GMX smart contract integration. This method will be implemented in a future update when GMX trading contract methods are added to the library.",
        )

    def parse_ohlcv(
        self,
        ohlcv: list,
        market: dict[str, Any] | None = None,  # CCXT uses this format so adding this for interface compatibility
    ) -> list:
        """Parse a single OHLCV candle from GMX format to CCXT format.

        GMX returns: [timestamp_seconds, open, high, low, close]
        CCXT expects: [timestamp_ms, open, high, low, close, volume]

        :param ohlcv: Single candle data from GMX [timestamp_s, open, high, low, close]
        :type ohlcv: list
        :param market: Market information dictionary (optional)
        :type market: dict[str, Any] | None
        :return: Parsed candle in CCXT format [timestamp_ms, open, high, low, close, volume]
        :rtype: list

        .. note::
            Volume is set to 0 as GMX doesn't provide it
        """
        # GMX format: [timestamp (seconds), open, high, low, close]
        # CCXT format: [timestamp (milliseconds), open, high, low, close, volume]

        if len(ohlcv) < 5:
            raise ValueError(f"Invalid OHLCV data: expected at least 5 fields, got {len(ohlcv)}")

        timestamp_seconds = ohlcv[0]
        timestamp_ms = int(timestamp_seconds * 1000)  # Convert to milliseconds

        return [
            timestamp_ms,  # Timestamp in milliseconds
            float(ohlcv[1]),  # Open
            float(ohlcv[2]),  # High
            float(ohlcv[3]),  # Low
            float(ohlcv[4]),  # Close
            1.0,  # Volume (GMX doesn't provide volume data, use dummy value to avoid Freqtrade filtering)
        ]

    def parse_timeframe(self, timeframe: str) -> int:
        """Convert timeframe string to duration in seconds.

        :param timeframe: Timeframe string (e.g., "1m", "1h", "1d")
        :type timeframe: str
        :return: Duration in seconds
        :rtype: int

        Example::

            seconds = gmx.parse_timeframe("1h")  # Returns 3600
            seconds = gmx.parse_timeframe("1d")  # Returns 86400
        """
        timeframe_mapping = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }

        if timeframe not in timeframe_mapping:
            raise ValueError(f"Invalid timeframe: {timeframe}")

        return timeframe_mapping[timeframe]

    def milliseconds(self) -> int:
        """Get current Unix timestamp in milliseconds.

        :return: Current timestamp in milliseconds
        :rtype: int

        Example::

            now = gmx.milliseconds()
            print(f"Current time: {now} ms")
        """
        return int(time.time() * 1000)

    def safe_integer(
        self,
        dictionary: dict[str, Any],
        key: str,
        default: int | None = None,
    ) -> int | None:
        """Safely extract an integer value from a dictionary.

        :param dictionary: dictionary to extract from
        :type dictionary: dict[str, Any]
        :param key: Key to look up
        :type key: str
        :param default: Default value if key not found
        :type default: int | None
        :return: Integer value or default
        :rtype: int | None
        """
        value = dictionary.get(key, default)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def safe_string(
        self,
        dictionary: dict[str, Any],
        key: str,
        default: str | None = None,
    ) -> str | None:
        """Safely extract a string value from a dictionary.

        :param dictionary: dictionary to extract from
        :type dictionary: dict[str, Any]
        :param key: Key to look up
        :type key: str
        :param default: Default value if key not found
        :type default: str | None
        :return: String value or default
        :rtype: str | None
        """
        value = dictionary.get(key, default)
        if value is None:
            return default
        return str(value)

    def safe_number(
        self,
        dictionary: dict[str, Any],
        key: str,
        default: float | None = None,
    ) -> float | None:
        """Safely extract a numeric value from a dictionary.

        :param dictionary: dictionary to extract from
        :type dictionary: dict[str, Any]
        :param key: Key to look up
        :type key: str
        :param default: Default value if key not found
        :type default: float | None
        :return: Float value or default
        :rtype: float | None
        """
        value = dictionary.get(key, default)
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def safe_timestamp(
        self,
        dictionary: dict[str, Any],
        key: str,
        default: int | None = None,
    ) -> int | None:
        """Safely extract a timestamp and convert to milliseconds.

        :param dictionary: dictionary to extract from
        :type dictionary: dict[str, Any]
        :param key: Key to look up
        :type key: str
        :param default: Default value if key not found
        :type default: int | None
        :return: Timestamp in milliseconds or default
        :rtype: int | None
        """
        value = dictionary.get(key, default)
        if value is None:
            return default
        try:
            # Convert to int and ensure it's in milliseconds
            timestamp = int(value)
            # If timestamp is in seconds (< year 2100 in seconds), convert to ms
            if timestamp < 4102444800:
                timestamp = timestamp * 1000
            return timestamp
        except (ValueError, TypeError):
            return default

    def iso8601(
        self,
        timestamp: int | None,
    ) -> str | None:
        """Convert timestamp in milliseconds to ISO8601 string.

        :param timestamp: Timestamp in milliseconds
        :type timestamp: int | None
        :return: ISO8601 formatted datetime string
        :rtype: str | None
        """
        if timestamp is None:
            return None
        try:
            return datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z"
        except (ValueError, OSError):
            return None

    def sum(self, a: float, b: float) -> float:
        """Add two numbers safely.

        :param a: First number
        :type a: float
        :param b: Second number
        :type b: float
        :return: Sum of a and b
        :rtype: float
        """
        return a + b

    def omit(self, dictionary: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        """Create a new dictionary excluding specified keys.

        :param dictionary: Source dictionary
        :type dictionary: dict[str, Any]
        :param keys: list of keys to exclude
        :type keys: list[str]
        :return: New dictionary without the specified keys
        :rtype: dict[str, Any]
        """
        return {k: v for k, v in dictionary.items() if k not in keys}

    # Order Creation Methods
    def _convert_ccxt_to_gmx_params(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None,
        params: dict,
    ) -> dict:
        """Convert CCXT order parameters to GMX trading parameters.

        Maps standard CCXT order creation parameters to the GMX protocol's
        parameter structure for position opening/closing.

        :param symbol: CCXT symbol (e.g., 'ETH/USD')
        :type symbol: str
        :param type: Order type ('market' or 'limit')
        :type type: str
        :param side: Order side ('buy' or 'sell')
        :type side: str
        :param amount: Order size in base currency contracts (e.g., BTC for BTC/USD)
        :type amount: float
        :param price: Price in quote currency (USD). Used to convert amount to USD. Fetched from ticker if None.
        :type price: float | None
        :param params: Additional parameters (leverage, collateral_symbol, etc.)
        :type params: dict
        :return: GMX-compatible parameter dictionary
        :rtype: dict
        :raises ValueError: If symbol is invalid or parameters are incompatible
        """
        # Ensure markets are loaded before processing
        if not self.markets_loaded or not self.markets:
            self.load_markets()

        # Normalize symbol to internal format (ETH/USDC -> ETH/USDC:USDC)
        normalized_symbol = self._normalize_symbol(symbol)

        # Validate symbol exists
        if normalized_symbol not in self.markets:
            raise ValueError(f"Market {symbol} not found. Call load_markets() first.")

        market = self.markets[normalized_symbol]
        base_currency = market["base"]  # e.g., 'ETH' from 'ETH/USD'

        # Extract GMX-specific parameters from params
        collateral_symbol = params.get("collateral_symbol", "USDC")  # Default to USDC
        leverage = params.get("leverage", self.leverage.get(normalized_symbol, 1.0))
        slippage_percent = params.get("slippage_percent", 0.003)  # 0.3% default

        # Determine if this is opening or closing a position
        # Check if user has an existing position
        is_long = side == "buy"

        # Convert amount from base currency (BTC/ETH) to USD
        # For CCXT linear perpetuals, amount is in base currency contracts
        # GMX needs size_delta_usd in actual USD
        if price:
            size_delta_usd = amount * price
        else:
            # For market orders, fetch current price
            ticker = self.fetch_ticker(symbol)
            current_price = ticker["last"]
            size_delta_usd = amount * current_price

        gmx_params = {
            "market_symbol": base_currency,
            "collateral_symbol": collateral_symbol,
            "start_token_symbol": collateral_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,
            "leverage": leverage,
            "slippage_percent": slippage_percent,
        }

        # Add any additional parameters
        if "execution_buffer" in params:
            gmx_params["execution_buffer"] = params["execution_buffer"]
        if "auto_cancel" in params:
            gmx_params["auto_cancel"] = params["auto_cancel"]

        return gmx_params

    def _ensure_token_approval(
        self,
        collateral_symbol: str,
        size_delta_usd: float,
        leverage: float,
    ):
        """Ensure token approval for order creation.

        Checks if the collateral token has sufficient allowance for the GMX router.
        If not, automatically approves the token with a large allowance to avoid
        repeated approval transactions.

        Based on reference implementation from tests/gmx/debug_deploy.py

        :param collateral_symbol: Symbol of collateral token (e.g., 'USDC', 'WETH')
        :param size_delta_usd: Position size in USD
        :param leverage: Leverage multiplier
        """
        # Skip approval for ETH (native token)
        if collateral_symbol in ["ETH", "AVAX"]:
            logger.debug(f"Using native {collateral_symbol} - no approval needed")
            return

        # Get token address
        chain = self.config.get_chain()
        collateral_token_address = get_token_address_normalized(chain, collateral_symbol)

        if not collateral_token_address:
            # If token address not found, assume it's OK (might be native or not need approval)
            logger.debug(f"Token address not found for {collateral_symbol}, skipping approval")
            return

        # Get contract addresses (for router address)
        contract_addresses = get_contract_addresses(chain)
        spender_address = contract_addresses.syntheticsrouter

        # Get token details and contract
        token_details = fetch_erc20_details(self.web3, collateral_token_address, chain_id=self.web3.eth.chain_id)
        token_contract = token_details.contract

        # Check current allowance
        wallet_address = self.wallet.address
        current_allowance = token_contract.functions.allowance(to_checksum_address(wallet_address), spender_address).call()

        # Calculate required collateral amount (position size / leverage)
        # Add 10% buffer for fees
        required_collateral_usd = (size_delta_usd / leverage) * 1.1
        required_amount = int(required_collateral_usd * (10**token_details.decimals))

        logger.debug(f"Token approval check: {collateral_symbol} allowance={current_allowance / (10**token_details.decimals):.4f}, required={required_amount / (10**token_details.decimals):.4f}")

        # If allowance is sufficient, no action needed
        if current_allowance >= required_amount:
            logger.debug(f"Sufficient {collateral_symbol} allowance exists")
            return

        # Need to approve - use a large amount to avoid repeated approvals
        # Approve 1 billion tokens (same pattern as debug_deploy.py)
        approve_amount = 1_000_000_000 * (10**token_details.decimals)

        logger.info(f"Insufficient {collateral_symbol} allowance. Current: {current_allowance / (10**token_details.decimals):.4f}, Required: {required_amount / (10**token_details.decimals):.4f}. Approving {approve_amount / (10**token_details.decimals):.0f} {collateral_symbol}...")

        # Build approval transaction
        approve_tx = token_contract.functions.approve(spender_address, approve_amount).build_transaction(
            {
                "from": to_checksum_address(wallet_address),
                "gas": 100_000,
                "gasPrice": self.web3.eth.gas_price,
            }
        )

        # CRITICAL: Remove nonce before calling sign_transaction_with_new_nonce
        # The wallet will manage the nonce automatically
        if "nonce" in approve_tx:
            del approve_tx["nonce"]

        # Sign and send approval transaction
        signed_approve_tx = self.wallet.sign_transaction_with_new_nonce(approve_tx)
        approve_tx_hash = self.web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)

        logger.info(f"Approval transaction sent: {approve_tx_hash.hex()}. Waiting for confirmation...")

        # Wait for confirmation
        approve_receipt = self.web3.eth.wait_for_transaction_receipt(approve_tx_hash, timeout=120)

        if approve_receipt["status"] == 1:
            logger.info(f"Token approval successful! Approved {approve_amount / (10**token_details.decimals):.0f} {collateral_symbol} for {spender_address}")
        else:
            raise Exception(f"Token approval transaction failed: {approve_tx_hash.hex()}")

    def _parse_order_result_to_ccxt(
        self,
        order_result,
        symbol: str,
        side: str,
        type: str,
        amount: float,
        tx_hash: str,
        receipt: dict,
    ) -> dict:
        """Convert GMX OrderResult to CCXT order structure.

        :param order_result: GMX OrderResult from trading module
        :param symbol: CCXT symbol
        :type symbol: str
        :param side: Order side ('buy' or 'sell')
        :type side: str
        :param type: Order type ('market' or 'limit')
        :type type: str
        :param amount: Order size in USD
        :type amount: float
        :param tx_hash: Transaction hash
        :type tx_hash: str
        :param receipt: Transaction receipt
        :type receipt: dict
        :return: CCXT-compatible order structure
        :rtype: dict
        """
        timestamp = self.milliseconds()

        # Determine status from receipt
        # GMX orders execute immediately in the transaction, so if successful, the order is filled
        tx_success = receipt.get("status") == 1
        status = "closed" if tx_success else "failed"

        # Build info dict with all GMX-specific data
        info = {
            "tx_hash": tx_hash,
            "receipt": receipt,
            "block_number": receipt.get("blockNumber"),
            "gas_used": receipt.get("gasUsed"),
            "execution_fee": order_result.execution_fee,
            "acceptable_price": order_result.acceptable_price,
            "mark_price": order_result.mark_price,
            "gas_limit": order_result.gas_limit,
        }

        if order_result.estimated_price_impact is not None:
            info["estimated_price_impact"] = order_result.estimated_price_impact

        # Calculate fee in ETH
        fee_cost = order_result.execution_fee / 1e18

        # OrderResult.mark_price is already converted to USD in base_order.py
        # No additional conversion needed here
        mark_price = order_result.mark_price

        # GMX orders execute immediately - filled/remaining based on transaction success
        filled_amount = amount if tx_success else 0.0
        remaining_amount = 0.0 if tx_success else amount

        order = {
            "id": tx_hash,
            "clientOrderId": None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "lastTradeTimestamp": timestamp if tx_success else None,
            "symbol": symbol,
            "type": type,
            "side": side,
            "price": mark_price if type == "market" else None,
            "amount": amount,
            "cost": amount if tx_success else None,  # Cost equals amount for GMX (amount is in USD)
            "average": mark_price if tx_success else None,  # Average fill price
            "filled": filled_amount,  # GMX orders execute immediately in the transaction
            "remaining": remaining_amount,
            "status": status,
            "fee": {
                "cost": fee_cost,
                "currency": "ETH",
            },
            "trades": [],
            "info": info,
        }

        # Store order for backtesting
        self._orders[tx_hash] = order

        return order

    def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        """Create and execute a GMX order.

        This method creates orders on GMX protocol with CCXT-compatible interface.
        Orders are automatically signed with the wallet provided during initialization
        and submitted to the Arbitrum blockchain.

        **Example Usage:**

        .. code-block:: python

            from eth_defi.hotwallet import HotWallet
            from eth_defi.gmx.ccxt import GMX
            from eth_defi.gmx.config import GMXConfig
            from web3 import Web3

            # Initialize with wallet
            web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
            config = GMXConfig(web3=web3)
            wallet = HotWallet.from_private_key("0x...")
            gmx = GMX(config, wallet=wallet)

            # Create market buy order (long position)
            order = gmx.create_order(
                "ETH/USD",
                "market",
                "buy",
                1000,
                params={
                    "leverage": 3.0,
                    "collateral_symbol": "USDC",
                },
            )

            print(f"Order created: {order['id']}")  # Transaction hash
            print(f"Status: {order['status']}")  # 'open' or 'failed'

        :param symbol: Market symbol (e.g., 'ETH/USD', 'BTC/USD')
        :type symbol: str
        :param type: Order type ('market' or 'limit')
        :type type: str
        :param side: Order side ('buy' for long, 'sell' for short)
        :type side: str
        :param amount: Order size in USD
        :type amount: float
        :param price: Limit price (currently unused, GMX uses market orders)
        :type price: float | None
        :param params: Additional parameters:
            - leverage (float): Leverage multiplier (default: 1.0)
            - collateral_symbol (str): Collateral token (default: 'USDC')
            - slippage_percent (float): Slippage tolerance (default: 0.003)
            - execution_buffer (float): Gas buffer multiplier (default: 2.2)
            - auto_cancel (bool): Auto-cancel if execution fails (default: False)
        :type params: dict | None
        :return: CCXT-compatible order structure with transaction hash and status
        :rtype: dict
        :raises ValueError: If wallet not provided, parameters invalid, or market doesn't exist
        """
        if params is None:
            params = {}

        # Require wallet for order creation
        if not self.wallet:
            raise ValueError(
                "Wallet required for order creation. GMX is running in VIEW-ONLY mode. Provide 'privateKey' or 'wallet' in constructor parameters. Example: GMX({'rpcUrl': '...', 'privateKey': '0x...'})",
            )

        # Sync wallet nonce before creating/closing order (required for Freqtrade)
        self.wallet.sync_nonce(self.web3)

        # Ensure markets are loaded and populated
        if not self.markets_loaded or not self.markets:
            self.load_markets()

        # Convert CCXT parameters to GMX parameters
        gmx_params = self._convert_ccxt_to_gmx_params(
            symbol,
            type,
            side,
            amount,
            price,
            params,
        )

        # Ensure token approval before creating order
        self._ensure_token_approval(
            collateral_symbol=gmx_params["collateral_symbol"],
            size_delta_usd=gmx_params["size_delta_usd"],
            leverage=gmx_params["leverage"],
        )

        if side == "buy":
            # Create the order using GMXTrading
            order_result = self.trader.open_position(**gmx_params)
        elif side == "sell":
            # For closing positions, use on-chain position data from GetOpenPositions
            # to derive the correct decrease size and collateral delta.
            #
            # This mirrors the recommendation from the GMX SDK: always base the
            # decrease on the actual open position instead of the user-requested
            # amount to avoid "invalid decrease order size" reverts.

            normalized_symbol = self._normalize_symbol(symbol)
            market = self.markets[normalized_symbol]
            base_currency = market["base"]

            # Get existing positions to determine the position we're closing
            positions_manager = GetOpenPositions(self.config)
            existing_positions = positions_manager.get_data(self.wallet.address)

            # Find the matching long position for this market + collateral
            position_to_close = None
            for position_key, position_data in existing_positions.items():
                position_market = position_data.get("market_symbol", "")
                position_is_long = position_data.get("is_long", None)
                position_collateral = position_data.get("collateral_token", "")

                # Match market, collateral, and must be a long position
                if position_market == base_currency and position_collateral == gmx_params["collateral_symbol"] and position_is_long:
                    position_to_close = position_data
                    break

            if not position_to_close:
                raise ValueError(
                    f"No long position found for {symbol} with collateral {gmx_params['collateral_symbol']} to close",
                )

            # Derive actual on-chain position size in USD.
            # Preferred source is the already-converted "position_size" field.
            position_size_usd = position_to_close.get("position_size")

            # Fallback: convert raw 30-decimal size if available
            if position_size_usd is None:
                raw_size = position_to_close.get("position_size_usd_raw") or position_to_close.get(
                    "position_size_usd",
                )
                if raw_size:
                    try:
                        position_size_usd = float(raw_size) / 10**30
                    except Exception:
                        position_size_usd = None

            if not position_size_usd or position_size_usd <= 0:
                raise ValueError(
                    f"Cannot determine position size for {symbol} to close. Position data: {position_to_close}",
                )

            # User-requested size (from CCXT amount / strategy).
            requested_size_usd = float(gmx_params["size_delta_usd"])

            # Clamp requested size to the actual position size to avoid protocol
            # reverts when trying to close more than is open.
            size_delta_usd = min(requested_size_usd, position_size_usd)

            if size_delta_usd <= 0:
                raise ValueError(
                    f"Requested close size {requested_size_usd} is not positive for position size {position_size_usd} on {symbol}",
                )

            # Derive collateral delta proportionally from the original collateral.
            # This matches GMX semantics better than guessing from leverage.
            collateral_amount_usd = position_to_close.get("initial_collateral_amount_usd")
            if collateral_amount_usd is None:
                # Fallback: approximate from leverage if USD value is missing
                leverage = float(position_to_close.get("leverage", 1.0) or 1.0)
                if leverage > 0:
                    collateral_amount_usd = position_size_usd / leverage
                else:
                    collateral_amount_usd = position_size_usd

            # Pro-rata collateral for partial closes, full amount for full close
            close_fraction = min(1.0, size_delta_usd / position_size_usd)
            initial_collateral_delta = collateral_amount_usd * close_fraction

            # Safety floor – avoid tiny dust values that can cause rounding issues
            if initial_collateral_delta <= 0:
                initial_collateral_delta = collateral_amount_usd

            # Call close_position with the derived parameters
            order_result = self.trader.close_position(
                market_symbol=gmx_params["market_symbol"],
                collateral_symbol=gmx_params["collateral_symbol"],
                start_token_symbol=gmx_params["start_token_symbol"],
                is_long=True,  # We're closing a long position
                size_delta_usd=size_delta_usd,
                initial_collateral_delta=initial_collateral_delta,
                slippage_percent=gmx_params.get("slippage_percent", 0.003),
                execution_buffer=gmx_params.get("execution_buffer", self.execution_buffer),
                auto_cancel=gmx_params.get("auto_cancel", False),
            )
        else:
            raise ValueError("Side must be 'buy' or 'sell'")

        # Sign transaction (remove nonce if present, wallet will manage it)
        transaction = order_result.transaction
        if "nonce" in transaction:
            del transaction["nonce"]
        signed_tx = self.wallet.sign_transaction_with_new_nonce(transaction)

        # Submit to blockchain
        tx_hash_bytes = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(tx_hash_bytes)  # Use to_hex to include "0x" prefix

        # Wait for confirmation
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

        # Convert to CCXT format
        return self._parse_order_result_to_ccxt(
            order_result,
            symbol,
            side,
            type,
            amount,
            tx_hash,
            receipt,
        )

    def create_market_buy_order(
        self,
        symbol: str,
        amount: float,
        params: dict | None = None,
    ) -> dict:
        """Create a market buy order (long position).

        Convenience wrapper around create_order() for market buy orders.

        :param symbol: Market symbol (e.g., 'ETH/USD')
        :type symbol: str
        :param amount: Order size in USD
        :type amount: float
        :param params: Additional parameters (see create_order)
        :type params: dict | None
        :return: CCXT-compatible order structure
        :rtype: dict
        """
        return self.create_order(
            symbol,
            "market",
            "buy",
            amount,
            None,
            params,
        )

    def create_market_sell_order(
        self,
        symbol: str,
        amount: float,
        params: dict | None = None,
    ) -> dict:
        """Create a market sell order (short position).

        Convenience wrapper around create_order() for market sell orders.

        :param symbol: Market symbol (e.g., 'ETH/USD')
        :type symbol: str
        :param amount: Order size in USD
        :type amount: float
        :param params: Additional parameters (see create_order)
        :type params: dict | None
        :return: CCXT-compatible order structure
        :rtype: dict
        """
        return self.create_order(
            symbol,
            "market",
            "sell",
            amount,
            None,
            params,
        )

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        params: dict | None = None,
    ) -> dict:
        """Create a limit order.

        Note: GMX currently uses market orders with acceptable price limits.
        This method exists for CCXT compatibility but behaves like a market order.

        :param symbol: Market symbol (e.g., 'ETH/USD')
        :type symbol: str
        :param side: Order side ('buy' or 'sell')
        :type side: str
        :param amount: Order size in USD
        :type amount: float
        :param price: Limit price (informational, GMX uses market orders)
        :type price: float
        :param params: Additional parameters (see create_order)
        :type params: dict | None
        :return: CCXT-compatible order structure
        :rtype: dict
        """
        return self.create_order(symbol, "limit", side, amount, price, params)

    # Unsupported methods (GMX protocol limitations)

    def clear_order_cache(self):
        """Clear the in-memory order cache.

        Call this when switching strategies or starting a fresh session
        to avoid stale order data from previous runs.
        """
        self._orders = {}
        logger.info("Cleared order cache")

    def cancel_order(
        self,
        id: str,
        symbol: str | None = None,
        params: dict | None = None,
    ):
        """Cancel an order.

        Not supported by GMX - orders execute immediately via keeper system.

        :raises NotSupported: GMX doesn't support order cancellation
        """
        raise NotSupported(
            self.id + " cancel_order() is not supported - GMX orders are executed immediately by keepers and cannot be cancelled. Orders either execute or revert if conditions aren't met.",
        )

    def fetch_order(
        self,
        id: str,
        symbol: str | None = None,
        params: dict | None = None,
    ):
        """Fetch order by ID (transaction hash).

        Returns the order that was created with the given transaction hash.
        Queries the blockchain to get the current transaction status.

        :param id: Order ID (transaction hash)
        :type id: str
        :param symbol: Symbol (not used, for CCXT compatibility)
        :type symbol: str | None
        :param params: Additional parameters (not used)
        :type params: dict | None
        :return: CCXT-compatible order structure
        :rtype: dict
        :raises OrderNotFound: If order with given ID doesn't exist
        """
        # Check if order exists in stored orders
        if id in self._orders:
            order = self._orders[id].copy()

            # Fetch current transaction status from blockchain
            try:
                if id.startswith("0x"):
                    receipt = self.web3.eth.get_transaction_receipt(id)
                    # Update status based on receipt
                    tx_success = receipt.get("status") == 1
                    order["status"] = "closed" if tx_success else "failed"

                    # GMX orders execute immediately, so update filled/remaining
                    if tx_success:
                        order["filled"] = order["amount"]
                        order["remaining"] = 0.0
                    else:
                        order["filled"] = 0.0
                        order["remaining"] = order["amount"]

                    # Update info with latest receipt data
                    if "info" not in order:
                        order["info"] = {}
                    order["info"]["receipt"] = receipt
                    order["info"]["block_number"] = receipt.get("blockNumber")
                    order["info"]["gas_used"] = receipt.get("gasUsed")
            except Exception as e:
                logger.warning(f"Could not fetch transaction receipt for {id}: {e}")

            return order

        # Order not in cache - try to fetch from blockchain directly
        # This handles orders from previous sessions or other strategies
        # Normalize ID: add "0x" prefix if missing (for backwards compatibility with old order IDs)
        normalized_id = id if id.startswith("0x") else f"0x{id}"

        if len(normalized_id) == 66:  # Valid tx hash length (0x + 64 hex chars)
            try:
                receipt = self.web3.eth.get_transaction_receipt(normalized_id)
                tx = self.web3.eth.get_transaction(normalized_id)

                # Build minimal order structure from transaction data
                tx_success = receipt.get("status") == 1
                order = {
                    "id": id,
                    "clientOrderId": None,
                    "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                    "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                    "lastTradeTimestamp": None,
                    "status": "closed" if tx_success else "failed",
                    "symbol": symbol if symbol else None,
                    "type": "market",
                    "side": None,  # Can't determine from tx alone
                    "price": None,
                    "amount": None,  # Can't determine from tx alone
                    "filled": None,  # Can't determine from tx alone
                    "remaining": 0.0 if tx_success else None,
                    "cost": None,
                    "trades": [],  # Empty list, not None - freqtrade expects a list
                    "fee": {
                        "currency": "ETH",
                        "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                    },
                    "info": {
                        "receipt": receipt,
                        "transaction": tx,
                        "block_number": receipt.get("blockNumber"),
                        "gas_used": receipt.get("gasUsed"),
                    },
                    "average": None,
                    "fees": [],
                }

                logger.info(f"Fetched order {id} from blockchain (not in cache)")
                return order

            except Exception as e:
                logger.warning(f"Could not fetch transaction {id} from blockchain: {e}")
                # Fall through to raise OrderNotFound

        # Order not found anywhere
        raise OrderNotFound(f"{self.id} order {id} not found in stored orders or on blockchain")

    def fetch_order_book(
        self,
        symbol: str,
        limit: int | None = None,
        params: dict | None = None,
    ):
        """Fetch order book.

        Not supported by GMX - uses liquidity pools instead of order books.

        :raises NotSupported: GMX uses liquidity pools, not order books
        """
        raise NotSupported(
            self.id + " fetch_order_book() is not supported - GMX uses liquidity pools instead of traditional order books. Use fetch_ticker() for current prices or fetch_open_interest() for market depth.",
        )

    def fetch_closed_orders(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: dict | None = None,
    ):
        """Fetch closed orders.

        Not supported by GMX - use fetch_my_trades() instead.

        :raises NotSupported: GMX doesn't track closed orders
        """
        raise NotSupported(
            self.id + " fetch_closed_orders() is not supported - Use fetch_my_trades() to see your trading history or fetch_positions() for current positions.",
        )

    def fetch_orders(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: dict | None = None,
    ):
        """Fetch all orders.

        Not supported by GMX - use fetch_positions() and fetch_my_trades() instead.

        :raises NotSupported: GMX doesn't track pending orders
        """
        raise NotSupported(
            self.id + " fetch_orders() is not supported - GMX orders execute immediately. Use fetch_positions() for open positions.",
        )

    async def close(self) -> None:
        """Close exchange connection and clean up resources.

        GMX exchange doesn't maintain persistent WebSocket connections or
        HTTP sessions that need cleanup, but this method is provided for
        compatibility with the async CCXT exchange interface.

        This method can be called in async cleanup code or context managers.
        """
        # GMX doesn't maintain persistent connections, so this is a no-op
        # If future implementations add connection pooling or caching,
        # cleanup logic should be added here
        pass
