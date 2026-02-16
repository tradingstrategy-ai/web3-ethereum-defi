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

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from ccxt.base.errors import BaseError, ExchangeError, InvalidOrder, NotSupported, OrderNotFound
from eth_utils import to_checksum_address

from eth_defi.ccxt.exchange_compatible import ExchangeCompatible
from eth_defi.chain import get_chain_name
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.cache import GMXMarketCache
from eth_defi.gmx.ccxt.properties import describe_gmx
from eth_defi.gmx.ccxt.validation import _validate_ohlcv_data_sufficiency
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import DEFAULT_GAS_CRITICAL_THRESHOLD_USD, DEFAULT_GAS_ESTIMATE_BUFFER, DEFAULT_GAS_MONITOR_ENABLED, DEFAULT_GAS_RAISE_ON_CRITICAL, DEFAULT_GAS_WARNING_THRESHOLD_USD, GMX_MIN_COST_USD, PRECISION
from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.events import decode_gmx_event, extract_order_execution_result, extract_order_key_from_receipt
from eth_defi.gmx.gas_monitor import GasMonitorConfig, GMXGasMonitor, InsufficientGasError
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.gmx.order import SLTPEntry, SLTPOrder, SLTPParams
from eth_defi.gmx.order_tracking import check_order_status
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.utils import calculate_estimated_liquidation_price, convert_raw_price_to_usd
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.fallback import get_fallback_provider
from eth_defi.provider.log_block_range import get_logs_max_block_range
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)


def _scan_logs_chunked_for_trade_action(
    web3,
    event_emitter: str,
    order_key: bytes,
    order_key_hex: str,
    from_block: int,
    to_block: int,
) -> dict | None:
    """Scan EventEmitter logs in chunks for order execution event.

    Uses chunked queries to avoid RPC timeouts on large block ranges.

    :param web3:
        Web3 instance
    :param event_emitter:
        EventEmitter contract address
    :param order_key:
        The 32-byte order key to search for
    :param order_key_hex:
        The order key as hex string (with 0x prefix)
    :param from_block:
        Start block for scanning
    :param to_block:
        End block for scanning
    :return:
        trade_action dict if found, None otherwise
    """
    chunk_size = get_logs_max_block_range(web3)
    total_blocks = to_block - from_block + 1

    if total_blocks <= 0:
        return None

    logger.debug(
        "Scanning %d blocks for order %s in chunks of %d",
        total_blocks,
        order_key_hex[:18],
        chunk_size,
    )

    for chunk_start in range(from_block, to_block + 1, chunk_size):
        chunk_end = min(chunk_start + chunk_size - 1, to_block)

        logger.debug(
            "Scanning blocks %d-%d for order %s",
            chunk_start,
            chunk_end,
            order_key_hex[:18],
        )

        try:
            logs = web3.eth.get_logs(
                {
                    "address": event_emitter,
                    "fromBlock": chunk_start,
                    "toBlock": chunk_end,
                }
            )

            for log in logs:
                try:
                    event = decode_gmx_event(web3, log)
                    if not event:
                        continue

                    if event.event_name not in ("OrderExecuted", "OrderCancelled", "OrderFrozen"):
                        continue

                    # Check if this event matches our order_key
                    event_order_key = event.topic1 or event.get_bytes32("key")
                    if event_order_key != order_key:
                        continue

                    # Found our order's execution event
                    logger.debug(
                        "Found %s event for order %s in block %d via chunked scan",
                        event.event_name,
                        order_key_hex[:18],
                        log["blockNumber"],
                    )

                    # Build trade_action dict from event
                    order_type = event.get_uint("orderType")
                    trade_action = {
                        "eventName": event.event_name,
                        "orderKey": order_key_hex,
                        "isLong": event.get_bool("isLong"),
                        "orderType": order_type,
                        "reason": event.get_string("reasonBytes") if event.event_name == "OrderCancelled" else None,
                        "transaction": {
                            "hash": log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"],
                        },
                    }

                    logger.info(
                        "EventEmitter trade_action for order %s: eventName=%s, orderType=%s, isLong=%s, available_uint_keys=%s, available_bool_keys=%s",
                        order_key_hex[:18],
                        event.event_name,
                        order_type,
                        event.get_bool("isLong"),
                        list(event.uint_items.keys()),
                        list(event.bool_items.keys()),
                    )

                    # Get execution price if available
                    if event.event_name == "OrderExecuted":
                        exec_price = event.get_uint("executionPrice")
                        if exec_price:
                            trade_action["executionPrice"] = str(exec_price)

                    return trade_action

                except Exception as e:
                    logger.debug("Error decoding log: %s", e)
                    continue

        except Exception as e:
            logger.warning(
                "Error scanning blocks %d-%d for order %s: %s",
                chunk_start,
                chunk_end,
                order_key_hex[:18],
                e,
            )
            # Continue to next chunk - partial failure is acceptable

    return None


def _derive_side_from_trade_action(trade_action: dict) -> str | None:
    """Derive CCXT order side from trade action data.

    Uses ``orderType`` and ``isLong`` fields to reverse-map the side
    that was used when the order was originally created.

    Mapping (reverse of ``create_order()`` logic):

    - MarketIncrease (2) + long  -> ``"buy"``  (opening long)
    - MarketIncrease (2) + short -> ``"sell"`` (opening short)
    - MarketDecrease (3) + long  -> ``"sell"`` (closing long)
    - MarketDecrease (3) + short -> ``"buy"``  (closing short)

    :param trade_action:
        Dict from Subsquid or EventEmitter with ``orderType`` and ``isLong`` fields.
    :return:
        ``"buy"`` or ``"sell"`` if derivable, ``None`` otherwise.
    """
    order_type = trade_action.get("orderType")
    is_long = trade_action.get("isLong")

    if order_type is None or is_long is None:
        return None

    # Coerce to int in case it comes as string from some sources
    try:
        order_type = int(order_type)
    except (ValueError, TypeError):
        return None

    if order_type == 2:  # MarketIncrease
        return "buy" if is_long else "sell"
    elif order_type == 3:  # MarketDecrease
        return "sell" if is_long else "buy"

    return None


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
        price_sanity_config: "PriceSanityCheckConfig | None" = None,
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
                    "requireMultipleProviders": True,  # Optional - enforce fallback support
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
        :param price_sanity_config: Configuration for price sanity checks (optional)
        :type price_sanity_config: PriceSanityCheckConfig | None
        """
        # Handle positional arguments and mixed usage
        # If the first argument 'config' is actually a dict, treat it as params
        self.markets_loaded = None
        if isinstance(config, dict):
            params = config
            config = None

        # Store price sanity config (will be initialized in _init_common if None)
        self._price_sanity_config = price_sanity_config

        # Initialize oracle prices instance (will be lazily created when needed)
        self._oracle_prices_instance = None

        # Prepare kwargs for CCXT base class
        # CCXT expects 'config' to be a dict of parameters if provided
        ccxt_kwargs = kwargs.copy()
        if params:
            ccxt_kwargs.update(params)

        # Initialize CCXT base class
        # We do NOT pass GMXConfig object to super().__init__ as it expects a dict
        # Note: CCXT may try to access properties during init, so we need config set first
        try:
            super().__init__(config=ccxt_kwargs)
        except ValueError as e:
            # CCXT tries to access oracle_prices property during init, which may fail
            # if config isn't set yet. This is OK - we'll set it properly below.
            if "Cannot access oracle prices" not in str(e):
                raise

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
        self.default_slippage = parameters.get("defaultSlippage", 0.003)  # 0.3% default
        self._require_multiple_providers = parameters.get("requireMultipleProviders", False)
        self._oracle_prices_instance = None

        # Configure verbose logging if requested
        if self._verbose:
            self._configure_verbose_logging()

        # Create web3 instance from RPC URL
        if not self._rpc_url:
            keys = parameters.keys()
            raise ValueError(f"rpcUrl is required in parameters - we got keys: {keys}")

        # Log detected RPC providers (space-separated format)
        rpc_urls = [u for u in self._rpc_url.split() if u and not u.startswith("mev+")]
        logger.info("RPC configuration: %d provider(s) detected", len(rpc_urls))

        # Create web3 with multi-provider support
        self.web3 = create_multi_provider_web3(self._rpc_url)

        # Validate provider count if required
        if self._require_multiple_providers:
            fallback = get_fallback_provider(self.web3)
            if len(fallback.providers) < 2:
                raise ValueError(f"GMX CCXT requires at least 2 providers for proper fallback functionality, but only {len(fallback.providers)} provider(s) configured. Set requireMultipleProviders=False to allow single-provider mode.")

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

        # Parse gas monitoring config from CCXT options
        options = parameters.get("options", {})
        self._gas_monitor_config = GasMonitorConfig(
            warning_threshold_usd=options.get("gasWarningThresholdUsd", DEFAULT_GAS_WARNING_THRESHOLD_USD),
            critical_threshold_usd=options.get("gasCriticalThresholdUsd", DEFAULT_GAS_CRITICAL_THRESHOLD_USD),
            enabled=options.get("gasMonitorEnabled", DEFAULT_GAS_MONITOR_ENABLED),
            gas_estimate_buffer=options.get("gasEstimateBuffer", DEFAULT_GAS_ESTIMATE_BUFFER),
            raise_on_critical=options.get("gasRaiseOnCritical", DEFAULT_GAS_RAISE_ON_CRITICAL),
        )

        # Initialize API and trader with gas monitoring config
        self.api = GMXAPI(self.config)
        self.trader = GMXTrading(self.config, gas_monitor_config=self._gas_monitor_config) if self.wallet else None
        self._gas_monitor: GMXGasMonitor | None = None

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
        self.default_slippage = 0.003  # Default 0.3% slippage
        self._oracle_prices_instance = None

        # Use default gas monitoring config for legacy initialization
        self._gas_monitor_config = GasMonitorConfig()

        # Initialize trading manager with gas monitoring config
        self.trader = GMXTrading(config, gas_monitor_config=self._gas_monitor_config) if wallet else None
        self._gas_monitor: GMXGasMonitor | None = None

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
        """Load markets from GraphQL.

        Fast market loading using SubSquid GraphQL endpoint.
        Significantly faster than RPC loading (1-2s vs 87-217s).

        Uses GMX API /tokens endpoint to fetch token metadata instead of hardcoding.

        :return: dictionary mapping unified symbols to market info
        :rtype: dict[str, Any]
        """
        try:
            market_infos = self.subsquid.get_market_infos(limit=200)
            logger.debug("Fetched %s markets from GraphQL", len(market_infos))

            # Fetch token data from GMX API
            tokens_data = self.api.get_tokens()
            logger.debug("Fetched tokens from GMX API, type: %s", type(tokens_data))

            # Build address->symbol mapping (lowercase addresses for matching)
            address_to_symbol = {}
            if isinstance(tokens_data, dict):
                # If tokens_data is a dict, extract the list of tokens
                tokens_list = tokens_data.get("tokens", [])
            elif isinstance(tokens_data, list):
                tokens_list = tokens_data
            else:
                logger.error("Unexpected tokens_data format: %s", type(tokens_data))
                tokens_list = []

            for token in tokens_list:
                if not isinstance(token, dict):
                    continue
                address = token.get("address", "").lower()
                symbol = token.get("symbol", "")
                if address and symbol:
                    address_to_symbol[address] = symbol

            logger.debug("Built address mapping for %s tokens", len(address_to_symbol))

            markets_dict = {}
            # Special wstETH market address (has WETH as index but should be treated as wstETH market)
            _special_wsteth_address = "0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5".lower()

            for market_info in market_infos:
                try:
                    index_token_addr = market_info.get("indexTokenAddress", "").lower()
                    market_token_addr = market_info.get("marketTokenAddress", "")
                    market_token_addr_lower = market_token_addr.lower()
                    long_token_addr = market_info.get("longTokenAddress", "").lower()
                    short_token_addr = market_info.get("shortTokenAddress", "").lower()

                    # Special case for wstETH market
                    # This market has WETH as index token but should be treated as wstETH
                    if market_token_addr_lower == _special_wsteth_address:
                        symbol_name = "wstETH"
                        # Override index token to wstETH token for correct identification
                        index_token_addr = "0x5979D7b546E38E414F7E9822514be443A4800529".lower()
                    else:
                        # Look up symbol from GMX API tokens data
                        symbol_name = address_to_symbol.get(index_token_addr)

                    if not symbol_name:
                        logger.debug("Skipping market with unknown index token: %s", index_token_addr)
                        continue  # Skip unknown tokens

                    # Handle synthetic markets (where long_token == short_token)
                    # These are marked with "2" suffix (e.g., ETH2, BTC2)
                    if long_token_addr == short_token_addr:
                        symbol_name = f"{symbol_name}2"

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
                    # don't ask why it works
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
                            "cost": {"min": GMX_MIN_COST_USD, "max": None},
                            "leverage": {"min": 1.1, "max": max_leverage},
                        },
                        "maintenanceMarginRate": maintenance_margin_rate,
                        "info": {
                            "market_token": market_token_addr,
                            "index_token": index_token_addr,  # Use overridden value (e.g., wstETH token for wstETH market)
                            "long_token": market_info.get("longTokenAddress"),
                            "short_token": market_info.get("shortTokenAddress"),
                            "graphql_only": True,  # Flag to indicate this was loaded from GraphQL
                        },
                    }
                except Exception as e:
                    logger.debug("Failed to process market %s: %s", market_info.get("marketTokenAddress"), e)
                    continue

            self.markets = markets_dict
            self.markets_loaded = True
            self.symbols = list(self.markets.keys())

            logger.info("Loaded %s markets from GraphQL", len(self.markets))
            logger.debug("Market symbols: %s", self.symbols)
            return self.markets

        except Exception as e:
            logger.error("Failed to load markets from GraphQL: %s", e)
            # Return empty markets rather than failing completely
            self.markets = {}
            self.markets_loaded = True
            self.symbols = []
            return self.markets

    def _load_markets_from_rest_api(self) -> dict[str, Any]:
        """Load markets from GMX REST API.

        Fast market loading using GMX REST API /markets/info endpoint.
        Performance similar to GraphQL (1-2s) but uses official GMX API.

        This is now the DEFAULT loading mode as it provides:
        - Fast performance (1-2s vs 87-217s for RPC)
        - Official GMX-maintained endpoint
        - Comprehensive market data including rates and liquidity
        - isListed status for filtering

        :return: dictionary mapping unified symbols to market info
        :rtype: dict[str, Any]
        """
        try:
            # Check disk cache first
            if self._market_cache:
                cached_markets = self._market_cache.get_markets("rest_api")
                if cached_markets is not None:
                    logger.info(
                        "Loaded %d markets from disk cache",
                        len(cached_markets),
                    )
                    self.markets = cached_markets
                    self.markets_loaded = True
                    self.symbols = list(self.markets.keys())
                    return self.markets

            # Fetch comprehensive market data from REST API
            markets_info = self.api.get_markets_info(market_tokens_data=True)
            logger.debug("Fetched markets info from REST API")

            # Fetch token metadata for symbol mapping
            tokens_data = self.api.get_tokens()
            logger.debug("Fetched tokens from GMX API")

            # Build address->symbol mapping (lowercase for matching)
            address_to_symbol = {}
            if isinstance(tokens_data, dict):
                tokens_list = tokens_data.get("tokens", [])
            elif isinstance(tokens_data, list):
                tokens_list = tokens_data
            else:
                logger.error("Unexpected tokens_data format: %s", type(tokens_data))
                tokens_list = []

            for token in tokens_list:
                if not isinstance(token, dict):
                    continue
                address = token.get("address", "").lower()
                symbol = token.get("symbol", "")
                if address and symbol:
                    address_to_symbol[address] = symbol

            logger.debug("Built address mapping for %d tokens", len(address_to_symbol))

            # Process markets from /markets/info response
            markets_dict = {}
            markets_list = markets_info.get("markets", [])

            # Special wstETH market address (has WETH as index but should be wstETH)
            _special_wsteth_address = "0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5".lower()

            for market_info in markets_list:
                try:
                    # Extract addresses
                    index_token_addr = market_info.get("indexToken", "").lower()
                    market_token_addr = market_info.get("marketToken", "")
                    market_token_addr_lower = market_token_addr.lower()
                    long_token_addr = market_info.get("longToken", "").lower()
                    short_token_addr = market_info.get("shortToken", "").lower()

                    # Check if market is listed
                    is_listed = market_info.get("isListed", False)
                    if not is_listed:
                        logger.debug(
                            "Skipping unlisted market: %s",
                            market_info.get("name", market_token_addr),
                        )
                        continue

                    # Special case for wstETH market
                    if market_token_addr_lower == _special_wsteth_address:
                        symbol_name = "wstETH"
                        index_token_addr = "0x5979D7b546E38E414F7E9822514be443A4800529".lower()
                    else:
                        # Look up symbol from token metadata
                        symbol_name = address_to_symbol.get(index_token_addr)

                    if not symbol_name:
                        logger.debug(
                            "Skipping market with unknown index token: %s",
                            index_token_addr,
                        )
                        continue

                    # Handle synthetic markets (where long_token == short_token)
                    # Marked with "2" suffix (e.g., ETH2, BTC2)
                    if long_token_addr == short_token_addr:
                        symbol_name = f"{symbol_name}2"

                    # Skip excluded symbols
                    if symbol_name in self.EXCLUDED_SYMBOLS:
                        logger.debug("Skipping excluded symbol: %s", symbol_name)
                        continue

                    # Use Freqtrade futures format
                    unified_symbol = f"{symbol_name}/USDC:USDC"

                    # Calculate max leverage (default to 50x if not available)
                    max_leverage = 50.0
                    min_collateral_factor = None

                    # Try to get leverage from subsquid if available
                    # For now, use default - can enhance later with subsquid integration
                    maintenance_margin_rate = 1.0 / max_leverage if max_leverage > 0 else 0.02

                    # Extract additional data from REST API response
                    listing_date = market_info.get("listingDate")
                    open_interest_long = market_info.get("openInterestLong")
                    open_interest_short = market_info.get("openInterestShort")
                    funding_rate_long = market_info.get("fundingRateLong")
                    funding_rate_short = market_info.get("fundingRateShort")
                    borrowing_rate_long = market_info.get("borrowingRateLong")
                    borrowing_rate_short = market_info.get("borrowingRateShort")
                    net_rate_long = market_info.get("netRateLong")
                    net_rate_short = market_info.get("netRateShort")
                    available_liquidity_long = market_info.get("availableLiquidityLong")
                    available_liquidity_short = market_info.get("availableLiquidityShort")
                    pool_amount_long = market_info.get("poolAmountLong")
                    pool_amount_short = market_info.get("poolAmountShort")

                    # Build CCXT-compatible market structure
                    markets_dict[unified_symbol] = {
                        "id": symbol_name,
                        "symbol": unified_symbol,
                        "base": symbol_name,
                        "quote": "USDC",
                        "baseId": symbol_name,
                        "quoteId": "USDC",
                        "settle": "USDC",
                        "settleId": "USDC",
                        "active": is_listed,  # Use isListed from API
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
                            "cost": {"min": GMX_MIN_COST_USD, "max": None},
                            "leverage": {"min": 1.1, "max": max_leverage},
                        },
                        "maintenanceMarginRate": maintenance_margin_rate,
                        "info": {
                            # Original fields (backwards compatible)
                            "market_token": market_token_addr,
                            "index_token": index_token_addr,
                            "long_token": long_token_addr,
                            "short_token": short_token_addr,
                            "min_collateral_factor": min_collateral_factor,
                            "max_leverage": max_leverage,
                            "rest_api_mode": True,  # Flag to indicate REST API mode
                            # New fields from REST API
                            "is_listed": is_listed,
                            "listing_date": listing_date,
                            "open_interest_long": open_interest_long,
                            "open_interest_short": open_interest_short,
                            "funding_rate_long": funding_rate_long,
                            "funding_rate_short": funding_rate_short,
                            "borrowing_rate_long": borrowing_rate_long,
                            "borrowing_rate_short": borrowing_rate_short,
                            "net_rate_long": net_rate_long,
                            "net_rate_short": net_rate_short,
                            "available_liquidity_long": available_liquidity_long,
                            "available_liquidity_short": available_liquidity_short,
                            "pool_amount_long": pool_amount_long,
                            "pool_amount_short": pool_amount_short,
                        },
                    }

                except Exception as e:
                    logger.debug(
                        "Failed to process market %s: %s",
                        market_info.get("marketToken"),
                        e,
                    )
                    continue

            self.markets = markets_dict
            self.markets_loaded = True
            self.symbols = list(self.markets.keys())

            # Save to disk cache
            if self._market_cache:
                try:
                    self._market_cache.set_markets(
                        markets_dict,
                        "rest_api",
                        ttl=3600,  # 1 hour TTL for market metadata
                    )
                except Exception as e:
                    logger.warning("Failed to save markets to cache: %s", e)

            logger.info(
                "Loaded %d markets from REST API (%d excluded)",
                len(self.markets),
                len(markets_list) - len(self.markets),
            )
            logger.debug("Market symbols: %s", self.symbols)

            return self.markets

        except Exception as e:
            logger.error("Failed to load markets from REST API: %s", e)
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

        # Consecutive failure tracking for safety
        self._consecutive_failures = 0  # Track consecutive transaction failures
        self._max_consecutive_failures = 3  # Threshold to pause trading
        self._trading_paused = False  # Flag to indicate if trading is paused
        self._trading_paused_reason = None  # Store reason for pause

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

        # Initialize price sanity config if not already set
        if self._price_sanity_config is None:
            from eth_defi.gmx.price_sanity import PriceSanityCheckConfig

            self._price_sanity_config = PriceSanityCheckConfig()

        # Initialise disk cache for markets
        # Can be disabled via options or environment variable
        cache_disabled = self.options.get("disable_market_cache") is True or os.environ.get("GMX_DISABLE_MARKET_CACHE", "").lower() == "true"

        cache_dir = self.options.get("market_cache_dir")
        if cache_dir:
            cache_dir = Path(cache_dir)

        try:
            chain = self.config.get_chain() if hasattr(self, "config") and self.config else "arbitrum"
            self._market_cache = GMXMarketCache.get_cache(
                chain=chain,
                cache_dir=cache_dir,
                disabled=cache_disabled,
            )
        except Exception as e:
            logger.warning("Failed to initialise market cache: %s", e)
            self._market_cache = None

    def _init_empty(self):
        """Initialize with minimal functionality (no RPC/config)."""
        self.config = None
        self.api = None
        self.web3 = None
        self.wallet = None
        self.trader = None
        self.subsquid = None
        self.wallet_address = None
        self.default_slippage = 0.003  # Default 0.3% slippage
        self._oracle_prices_instance = None
        self._init_common()

    @property
    def oracle_prices(self) -> OraclePrices:
        """Oracle prices instance for retrieving current prices.

        Uses lazy initialization pattern for efficiency. Only creates
        the OraclePrices instance when first accessed.

        :return: OraclePrices instance for this exchange's chain
        :rtype: OraclePrices
        :raises ValueError: If accessed before config is set
        """
        # Check if the instance exists using getattr to avoid AttributeError during initialization
        instance = getattr(self, "_oracle_prices_instance", None)
        if instance is None:
            # Use getattr to safely check if config exists (it may not during initialization)
            config = getattr(self, "config", None)
            if config is None:
                # During initialization, config might not be set yet
                # This is OK - the instance will be created when actually needed
                raise ValueError("Cannot access oracle prices without a config. Initialize with config or params.")
            self._oracle_prices_instance = OraclePrices(config.get_chain())
            instance = self._oracle_prices_instance
        return instance

    @property
    def gas_monitor(self) -> GMXGasMonitor | None:
        """Gas monitor instance for checking balance and estimating gas costs.

        Uses lazy initialisation pattern for efficiency. Only creates
        the GMXGasMonitor instance when first accessed.

        :return: GMXGasMonitor instance for this exchange's chain, or None during initialisation
        :rtype: GMXGasMonitor | None
        """
        # Use getattr to avoid AttributeError during parent class initialization
        instance = getattr(self, "_gas_monitor", None)
        if instance is None:
            config = getattr(self, "config", None)
            # Return None during CCXT base class init (config not set yet)
            if config is None:
                return None
            web3 = getattr(self, "web3", None)
            if web3 is None:
                return None
            gas_config = getattr(self, "_gas_monitor_config", None) or GasMonitorConfig()
            self._gas_monitor = GMXGasMonitor(
                web3=web3,
                chain=config.get_chain(),
                config=gas_config,
            )
            instance = self._gas_monitor
        return instance

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

        :param symbol: Trading pair symbol (e.g., "ETH/USD")
        :param type: Order type (e.g., "market", "limit")
        :param side: Order side ("buy" or "sell")
        :param amount: Order amount in base currency
        :param price: Order price
        :param takerOrMaker: "taker" or "maker" (not used for GMX)
        :param params: Additional parameters
        :return: Fee dictionary with rate and cost
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

    def _build_trading_fee(self, symbol: str, size_delta_usd: float) -> dict:
        """Build a CCXT fee dict for GMX trading fees.

        GMX charges 0.04-0.07% position fees depending on price impact direction.
        We use 0.06% as the standard rate (the conservative/common case).

        Fee is denominated in the settlement/quote currency (typically USDC).

        :param symbol:
            Trading pair symbol
        :param size_delta_usd:
            Position size in USD
        :return:
            CCXT fee dict with cost, currency, and rate

        See Also:
            - https://docs.gmx.io/docs/trading#fees-and-rebates
        """
        rate = 0.0006  # 0.06% - matches calculate_fee()
        market = self.markets.get(symbol) if self.markets_loaded else None
        currency = self.safe_string(market, "settle", "USDC") if market else "USDC"
        cost = abs(size_delta_usd) * rate if size_delta_usd else 0.0
        return {"cost": cost, "currency": currency, "rate": rate}

    def _convert_token_fee_to_usd(
        self,
        fee_tokens: int,
        market: dict,
        is_long: bool,
        collateral_token: str | None = None,
        collateral_token_price: int | None = None,
    ) -> float:
        """Convert raw token fee amount to USD.

        GMX fees are denominated in the collateral token. The actual collateral
        token is determined dynamically from event data (users can choose USDC
        as collateral even for long positions).

        For stablecoin collateral (USDC), fee_in_tokens ~ fee_in_usd.
        For non-stablecoin collateral (WETH, WBTC), multiply by collateral price.

        :param fee_tokens:
            Raw fee amount in token's native decimals
        :param market:
            CCXT market dict
        :param is_long:
            Whether position is long (True) or short (False)
        :param collateral_token:
            Actual collateral token address from event data. If not provided,
            falls back to market's long_token/short_token.
        :param collateral_token_price:
            Collateral token price in raw 30-decimal GMX format from event data.
            Used for USD conversion of non-stablecoin fees.
        :return:
            Fee amount in USD
        """
        if not fee_tokens or not market:
            return 0.0

        # Get collateral token address - prefer event data, fall back to market assumption
        if not collateral_token:
            collateral_token = self.safe_string(market.get("info", {}), "long_token" if is_long else "short_token")

        if not collateral_token:
            return 0.0

        # Look up token decimals from _token_metadata (keyed by lowercase address)
        if not getattr(self, "_token_metadata", None):
            self._token_metadata = {}
        if not self._token_metadata:
            self._load_token_metadata()

        token_meta = self._token_metadata.get(collateral_token.lower())
        if not token_meta:
            logger.warning("Token metadata not found for collateral %s, cannot convert fee", collateral_token)
            return 0.0

        token_decimals = token_meta.get("decimals")
        if token_decimals is None:
            return 0.0

        # Convert to token amount
        fee_in_tokens = fee_tokens / (10 ** int(token_decimals))

        token_symbol = token_meta.get("symbol", "?")

        # Check if collateral is a stablecoin (USDC, USDT, DAI - 6 decimals typical)
        # If we have a collateral token price from events, use it for accurate conversion
        if collateral_token_price is not None:
            collateral_price_usd = convert_raw_price_to_usd(collateral_token_price, int(token_decimals))
            if collateral_price_usd and collateral_price_usd > 0:
                fee_usd = fee_in_tokens * collateral_price_usd
                logger.info(
                    "Fee conversion (event price): %s raw -> %s %s * $%s = $%s USD",
                    fee_tokens,
                    fee_in_tokens,
                    token_symbol,
                    collateral_price_usd,
                    fee_usd,
                )
                return fee_usd

        # Non-stablecoin collateral without price data - cannot convert accurately
        if is_long:
            logger.warning(
                "Fee conversion: no collateral price for long position, fee_tokens=%s %s cannot be converted to USD. Returning token amount as approximate USD (may be inaccurate for non-stablecoin collateral).",
                fee_in_tokens,
                token_symbol,
            )

        # Stablecoin path: token amount ≈ USD amount
        logger.info(
            "Fee conversion (stablecoin): %s raw -> %s %s ≈ $%s USD",
            fee_tokens,
            fee_in_tokens,
            token_symbol,
            fee_in_tokens,
        )
        return fee_in_tokens

    def _extract_actual_fee(self, verification, market: dict, is_long: bool, size_delta_usd: float) -> dict:
        """Extract actual fees from order verification.

        Calculates total trading fee (position + borrowing + funding) from
        on-chain events and computes the actual fee rate.

        Returns CCXT-compliant fee structure with cost, currency, and rate.

        :param verification:
            GMXOrderVerificationResult with fee data
        :param market:
            CCXT market dict
        :param is_long:
            Whether position is long
        :param size_delta_usd:
            Position size in USD
        :return:
            CCXT fee dict with actual cost, currency, and rate

        CCXT Fee Structure::

            {
                "cost": float,  # Fee amount in currency units
                "currency": str,  # Fee denomination (USDC)
                "rate": float,  # Fee percentage (e.g., 0.0006 = 0.06%)
            }
        """
        if not verification or not verification.fees:
            # Fallback to fixed rate if no fee data available
            fallback = self._build_trading_fee(market.get("symbol", ""), size_delta_usd)
            logger.info(
                "Fee extract: no verification fees, using estimated fee: %s",
                fallback,
            )
            return fallback

        # Sum all fee components (in collateral token amounts)
        total_fee_tokens = verification.fees.position_fee + verification.fees.borrowing_fee + verification.fees.funding_fee + verification.fees.liquidation_fee

        logger.info(
            "Fee extract: position_fee=%s, borrowing_fee=%s, funding_fee=%s, liquidation_fee=%s, total_tokens=%s, is_long=%s",
            verification.fees.position_fee,
            verification.fees.borrowing_fee,
            verification.fees.funding_fee,
            verification.fees.liquidation_fee,
            total_fee_tokens,
            is_long,
        )

        # Convert to USD using actual collateral token from events
        fee_usd = self._convert_token_fee_to_usd(
            total_fee_tokens,
            market,
            is_long,
            collateral_token=getattr(verification, "collateral_token", None),
            collateral_token_price=getattr(verification, "collateral_token_price", None),
        )

        # Calculate actual rate
        actual_rate = fee_usd / size_delta_usd if size_delta_usd > 0 else 0.0

        # Get currency from market using CCXT safe access
        currency = self.safe_string(market, "settle", "USDC") if market else "USDC"

        fee_result = {"cost": fee_usd, "currency": currency, "rate": actual_rate}
        logger.info(
            "Fee extract: fee_usd=$%s, rate=%s%%, currency=%s -> %s",
            fee_usd,
            actual_rate * 100 if actual_rate else 0,
            currency,
            fee_result,
        )

        # Return CCXT-compliant structure
        return fee_result

    def _build_fee_breakdown(self, verification, market: dict, is_long: bool, execution_fee_eth: float) -> dict:
        """Build comprehensive fee breakdown for order info.

        Creates detailed breakdown of all fee components:
        - Trading fees (position, borrowing, funding, liquidation)
        - Execution fees (ETH gas paid to keepers)

        :param verification:
            GMXOrderVerificationResult with fee data
        :param market:
            CCXT market
        :param is_long:
            Position direction
        :param execution_fee_eth:
            Execution fee in ETH
        :return:
            Detailed fee breakdown dict
        """
        breakdown = {}

        if verification and verification.fees:
            coll_token = getattr(verification, "collateral_token", None)
            coll_price = getattr(verification, "collateral_token_price", None)
            # Convert each fee component to USD using actual collateral data
            position_fee_usd = self._convert_token_fee_to_usd(verification.fees.position_fee, market, is_long, collateral_token=coll_token, collateral_token_price=coll_price)
            borrowing_fee_usd = self._convert_token_fee_to_usd(verification.fees.borrowing_fee, market, is_long, collateral_token=coll_token, collateral_token_price=coll_price)
            funding_fee_usd = self._convert_token_fee_to_usd(verification.fees.funding_fee, market, is_long, collateral_token=coll_token, collateral_token_price=coll_price)
            liquidation_fee_usd = self._convert_token_fee_to_usd(verification.fees.liquidation_fee, market, is_long, collateral_token=coll_token, collateral_token_price=coll_price) if verification.fees.liquidation_fee else 0.0

            total_trading_fee_usd = position_fee_usd + borrowing_fee_usd + funding_fee_usd + liquidation_fee_usd

            breakdown["trading_fees"] = {
                "position_fee_usd": position_fee_usd,
                "borrowing_fee_usd": borrowing_fee_usd,
                "funding_fee_usd": funding_fee_usd,
                "liquidation_fee_usd": liquidation_fee_usd,
                "total_usd": total_trading_fee_usd,
            }

        # Execution fees (keeper gas)
        if execution_fee_eth:
            breakdown["execution_fees"] = {
                "eth": execution_fee_eth,
                "usd": None,
            }

        return breakdown

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

        Loading modes (in priority order):
        1. REST API (DEFAULT) - Fast (1-2s), official GMX endpoint, comprehensive data
        2. GraphQL - Fast (1-2s), requires subsquid
        3. RPC - Slow (87-217s), most comprehensive on-chain data

        Use options or params to control loading mode:
        - options={'rest_api_mode': False} - Disable REST API mode
        - options={'graphql_only': True} - Force GraphQL mode
        - params={'graphql_only': True} - Force GraphQL mode (CCXT style)

        :param reload: If True, force reload markets even if already loaded
        :type reload: bool
        :param params: Additional parameters (for CCXT compatibility)
        :type params: dict | None
        :return: dictionary mapping unified symbols (e.g. "ETH/USDC") to market info
        :rtype: dict[str, Any]
        """
        if self.markets_loaded and not reload:
            return self.markets

        # Determine loading mode based on configuration
        rest_api_disabled = (params and params.get("rest_api_mode") is False) or self.options.get("rest_api_mode") is False

        use_graphql_only = (params and params.get("graphql_only") is True) or self.options.get("graphql_only") is True

        # Check if we're on a testnet - REST API only supports mainnet
        # Testnets must use RPC mode for accurate on-chain market data
        is_testnet = self.config and self.config.chain in ("arbitrum_sepolia", "avalanche_fuji")
        if is_testnet:
            rest_api_disabled = True
            logger.info("Testnet detected (%s) - REST API not available, using RPC mode", self.config.chain)

        # Loading mode selection:
        # 1. If REST API not disabled and not forcing GraphQL -> REST API (NEW DEFAULT)
        # 2. If GraphQL explicitly requested -> GraphQL
        # 3. Otherwise -> RPC (fallback)

        if not rest_api_disabled and not use_graphql_only:
            logger.info("Loading markets from REST API (default mode)")
            return self._load_markets_from_rest_api()

        if use_graphql_only and self.subsquid:
            logger.info("Loading markets from GraphQL (graphql_only=True)")
            return self._load_markets_from_graphql()

        # RPC mode (fallback)
        # Fetch available markets from GMX using Markets class (makes RPC calls)
        # Fetches complete market data from on-chain sources
        logger.info("Loading markets from RPC (Core Markets module)")

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
                logger.warning("Failed to fetch leverage data from subsquid: %s", e)

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
                    "cost": {"min": GMX_MIN_COST_USD, "max": None},
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
                logger.warning("Failed to fetch leverage data from subsquid: %s", e)

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
                    "cost": {"min": GMX_MIN_COST_USD, "max": None},
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
                logger.warning("Failed to fetch leverage tiers for %s: %s", symbol, e)
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
            "info": ticker.copy(),  # Copy to avoid mutating cached ticker
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

        # Perform price sanity check if enabled
        if self._price_sanity_config.enabled:
            try:
                from eth_defi.gmx.price_sanity import PriceSanityAction, PriceSanityException, check_price_sanity

                # Get oracle prices (lazy initialization)
                if self._oracle_prices_instance is None:
                    self._oracle_prices_instance = OraclePrices(self.config.get_chain())
                oracle_prices = self._oracle_prices_instance.get_recent_prices()

                # Get token decimals from market info
                # Try multiple sources for compatibility with different loading modes
                token_decimals = None

                # First try market_metadata (RPC mode)
                market_metadata = market["info"].get("market_metadata")
                if market_metadata:
                    token_decimals = market_metadata.get("decimals")

                # Fall back to _token_metadata (REST API mode, loaded by parse_ticker)
                if not token_decimals and self._token_metadata:
                    token_meta = self._token_metadata.get(index_token_address.lower(), {})
                    token_decimals = token_meta.get("decimals")

                # Last resort: direct field in info (if added by some loading mode)
                if not token_decimals:
                    token_decimals = market["info"].get("index_token_decimals")

                if not token_decimals:
                    logger.debug(
                        "Cannot get decimals for %s, skipping price sanity check",
                        index_token_address,
                    )
                    return result  # Return ticker without sanity check

                # Normalise token address for lookup
                # Oracle prices use checksum addresses, so we need to normalise
                try:
                    oracle_address = to_checksum_address(index_token_address)
                except (ValueError, AttributeError):
                    oracle_address = index_token_address

                # Handle testnet address translation
                if hasattr(self.config, "get_chain") and self.config.get_chain() in [
                    "arbitrum_sepolia",
                    "avalanche_fuji",
                ]:
                    from eth_defi.gmx.core.oracle import _TESTNET_TO_MAINNET_ADDRESSES

                    testnet_mappings = _TESTNET_TO_MAINNET_ADDRESSES.get(self.config.get_chain(), {})
                    oracle_address = testnet_mappings.get(oracle_address, oracle_address)

                # Get oracle price for this token
                oracle_price = oracle_prices.get(oracle_address)

                if oracle_price:
                    # Perform sanity check
                    sanity_result = check_price_sanity(
                        oracle_price=oracle_price,
                        ticker_price=ticker,
                        token_address=index_token_address,
                        token_decimals=token_decimals,
                        config=self._price_sanity_config,
                    )

                    # Store result in ticker info
                    result["info"]["price_sanity_check"] = {
                        "passed": sanity_result.passed,
                        "deviation_percent": sanity_result.deviation_percent,
                        "oracle_price_usd": sanity_result.oracle_price_usd,
                        "ticker_price_usd": sanity_result.ticker_price_usd,
                        "action_taken": sanity_result.action_taken.value,
                        "timestamp": sanity_result.timestamp.isoformat(),
                        "reason": sanity_result.reason,
                    }

                    # Apply action if check failed
                    if not sanity_result.passed:
                        if sanity_result.action_taken == PriceSanityAction.use_oracle_warn:
                            # Use oracle price instead of ticker price
                            result["last"] = sanity_result.oracle_price_usd
                            result["close"] = sanity_result.oracle_price_usd
                            result["bid"] = sanity_result.oracle_price_usd
                            result["ask"] = sanity_result.oracle_price_usd
                        # use_ticker_warn and raise_exception are already handled by check_price_sanity

            except PriceSanityException:
                # Re-raise price sanity exceptions
                raise
            except Exception as e:
                # Log but don't fail on sanity check errors
                logger.warning(
                    "Price sanity check failed for %s: %s. Continuing with ticker price.",
                    symbol,
                    str(e),
                )

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

        # Parse ticker for each requested symbol (first pass - basic ticker data)
        result = {}
        symbols_needing_ohlcv = []
        since = self.milliseconds() - (24 * 60 * 60 * 1000)

        for symbol in target_symbols:
            try:
                market = self.market(symbol)
                canonical_symbol = market["symbol"]
                index_token_address = market["info"]["index_token"].lower()

                if index_token_address in ticker_by_address:
                    ticker_data = ticker_by_address[index_token_address]
                    result[canonical_symbol] = self.parse_ticker(ticker_data, market)
                    symbols_needing_ohlcv.append(canonical_symbol)
            except Exception:
                pass

        # Fetch OHLCV data in parallel for 24h high/low calculation
        if symbols_needing_ohlcv:
            max_workers = int(os.environ.get("MAX_WORKERS", "4"))
            ohlcv_map = {}

            try:
                with ThreadPoolExecutor(max_workers=min(max_workers, len(symbols_needing_ohlcv))) as executor:
                    future_to_symbol = {executor.submit(self._fetch_ohlcv_for_ticker, sym, since): sym for sym in symbols_needing_ohlcv}

                    for future in as_completed(future_to_symbol, timeout=60):
                        symbol, ohlcv = future.result()
                        if ohlcv:
                            ohlcv_map[symbol] = ohlcv

            except Exception as e:
                logger.warning("Parallel OHLCV fetch failed, falling back to sequential: %s", e)
                for sym in symbols_needing_ohlcv:
                    _, ohlcv = self._fetch_ohlcv_for_ticker(sym, since)
                    if ohlcv:
                        ohlcv_map[sym] = ohlcv

            # Apply OHLCV data to tickers
            for symbol, ohlcv in ohlcv_map.items():
                if symbol in result and ohlcv:
                    highs = [candle[2] for candle in ohlcv]
                    lows = [candle[3] for candle in ohlcv]
                    result[symbol]["high"] = max(highs) if highs else None
                    result[symbol]["low"] = min(lows) if lows else None
                    result[symbol]["open"] = ohlcv[0][1] if ohlcv else None

        return result

    def _fetch_ohlcv_for_ticker(
        self,
        symbol: str,
        since: int,
    ) -> tuple[str, list | None]:
        """Fetch 24h OHLCV data for a single ticker.

        :param symbol: Market symbol (e.g., "ETH/USDC:USDC")
        :param since: Start timestamp in milliseconds
        :return: Tuple of (symbol, ohlcv_data) or (symbol, None) on error
        """
        try:
            ohlcv = self.fetch_ohlcv(symbol, "1h", since=since, limit=24)
            return symbol, ohlcv
        except Exception as e:
            logger.debug("Failed to fetch OHLCV for %s: %s", symbol, e)
            return symbol, None

    def fetch_apy(
        self,
        symbol: str | None = None,
        period: str = "30d",
        params: dict | None = None,
    ) -> dict[str, Any] | float | None:
        """Fetch APY (Annual Percentage Yield) data for GMX markets.

        Retrieves yield data from GMX REST API with disk caching support.
        Can fetch APY for a specific market or all markets at once.

        :param symbol:
            CCXT market symbol (e.g., "ETH/USDC:USDC").
            If None, returns APY for all markets as a dictionary.
        :param period:
            Time period for APY calculation.
            Valid values: '1d', '7d', '30d', '90d', '180d', '1y', 'total'
            Default: '30d'
        :param params:
            Optional parameters (not used currently)
        :return:
            If symbol is specified: float APY value or None if not found
            If symbol is None: dict mapping symbols to APY values
        :raises ValueError: If period is invalid

        Example::

            # Fetch 30-day APY for specific market
            apy = gmx.fetch_apy("ETH/USDC:USDC", period="30d")
            print(f"ETH/USDC APY: {apy * 100:.2f}%")

            # Fetch APY for all markets
            all_apy = gmx.fetch_apy(period="7d")
            for symbol, apy_value in all_apy.items():
                print(f"{symbol}: {apy_value * 100:.2f}%")
        """
        params = params or {}

        # Ensure markets are loaded
        self.load_markets()

        # Try disk cache first
        cached_apy = None
        if self._market_cache:
            try:
                cached_apy = self._market_cache.get_apy(period=period, check_expiry=True)
                if cached_apy:
                    logger.debug("Using cached APY data for period %s", period)
            except Exception as e:
                logger.warning("Failed to read APY from disk cache: %s", e)

        # Fetch from API if cache miss
        if cached_apy is None:
            try:
                apy_response = self.api.get_apy(period=period, use_cache=True)
                cached_apy = apy_response.get("markets", {})

                # Save to disk cache
                if self._market_cache and cached_apy:
                    try:
                        self._market_cache.set_apy(
                            data=cached_apy,
                            period=period,
                            ttl=None,  # Use default TTL from constants
                        )
                        logger.debug("Saved APY data to disk cache for period %s", period)
                    except Exception as e:
                        logger.warning("Failed to save APY to disk cache: %s", e)

            except Exception as e:
                logger.error("Failed to fetch APY data from API: %s", e)
                return None if symbol else {}

        # Build mapping of market token address to CCXT symbol
        market_token_to_symbol = {}
        for ccxt_symbol, market_info in self.markets.items():
            market_token = market_info["info"].get("market_token", "").lower()
            if market_token:
                market_token_to_symbol[market_token] = ccxt_symbol

        # If symbol specified, return APY for that market only
        if symbol is not None:
            # Get market info for this symbol
            market = self.market(symbol)
            market_token = market["info"].get("market_token", "").lower()

            # Look up APY by market token address (case-insensitive)
            for addr, apy_data in cached_apy.items():
                if addr.lower() == market_token:
                    # Return base APY value
                    return apy_data.get("apy", 0.0)

            # Market not found in APY data
            logger.warning("No APY data found for %s (market token: %s)", symbol, market_token)
            return None

        # Return APY for all markets (map from market token addresses to CCXT symbols)
        result = {}
        for market_token_addr, apy_data in cached_apy.items():
            market_token_lower = market_token_addr.lower()

            # Find CCXT symbol for this market token
            if market_token_lower in market_token_to_symbol:
                ccxt_symbol = market_token_to_symbol[market_token_lower]
                result[ccxt_symbol] = apy_data.get("apy", 0.0)

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
            fee_cost = abs(fee_amount)
            # Calculate rate if we have the cost
            fee_rate = fee_cost / cost if cost and cost > 0 else 0.0006
            fee = {"cost": fee_cost, "currency": "USD", "rate": fee_rate}

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

        logger.debug("=" * 80)
        logger.debug("BALANCE_TRACE: fetch_balance() CALLED, wallet=%s", wallet)

        # Fetch currency metadata
        currencies = self.fetch_currencies()

        # Fetch open positions to calculate locked collateral
        collateral_locked = {}  # Maps token symbol to locked amount (in token units)
        try:
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

        except Exception as e:
            # If we can't fetch positions, we cannot reliably calculate locked collateral
            # Raising exception prevents incorrect balance from being used
            logger.error("Failed to fetch positions for balance calculation: %s", e)
            raise ExchangeError(f"Cannot calculate balance: position fetch failed: {e}") from e

        logger.debug("BALANCE_TRACE: collateral_locked=%s", collateral_locked)

        # Build balance dict
        result = {"free": {}, "used": {}, "total": {}, "info": {}}

        # Query balance for each token in parallel
        max_workers = int(os.environ.get("MAX_WORKERS", "4"))
        currency_items = list(currencies.items())

        if currency_items:
            balance_results = []
            try:
                with ThreadPoolExecutor(max_workers=min(max_workers, len(currency_items))) as executor:
                    future_to_code = {}
                    for code, currency in currency_items:
                        future = executor.submit(self._fetch_single_token_balance, code, currency, wallet)
                        future_to_code[future] = code

                    for future in as_completed(future_to_code, timeout=30):
                        balance_results.append(future.result())

            except Exception as e:
                logger.warning("Parallel balance fetch failed, falling back to sequential: %s", e)
                # Fallback to sequential execution
                for code, currency in currency_items:
                    balance_results.append(self._fetch_single_token_balance(code, currency, wallet))

            # Process results
            for code, balance_float, balance_raw, error in balance_results:
                currency = currencies.get(code, {})
                token_address = currency.get("id", "")
                decimals = currency.get("precision", 18)

                if error:
                    result["info"][code] = {"error": error}
                else:
                    # Calculate used (locked in positions) and free amounts
                    used_amount = collateral_locked.get(code, 0.0)
                    free_amount = max(0.0, balance_float - used_amount)  # Ensure non-negative
                    total_amount = balance_float

                    result[code] = {"free": free_amount, "used": used_amount, "total": total_amount}

                    result["free"][code] = free_amount
                    result["used"][code] = used_amount
                    result["total"][code] = total_amount

                    result["info"][code] = {
                        "address": token_address,
                        "raw_balance": str(balance_raw),
                        "decimals": decimals,
                    }

        # Log final balance state
        logger.debug("BALANCE_TRACE: fetch_balance() RETURNING")
        for code in ["USDC", "ETH", "WETH"]:
            if code in result and isinstance(result[code], dict):
                logger.debug(
                    "BALANCE_TRACE: %s: free=%.8f, used=%.8f, total=%.8f",
                    code,
                    result[code].get("free", 0),
                    result[code].get("used", 0),
                    result[code].get("total", 0),
                )
        logger.debug("=" * 80)

        return result

    def _fetch_single_token_balance(
        self,
        code: str,
        currency: dict,
        wallet: str,
    ) -> tuple[str, float | None, int | None, str | None]:
        """Fetch balance for a single token.

        :param code: Token code (e.g., "ETH", "USDC")
        :param currency: Currency metadata with id (address) and precision (decimals)
        :param wallet: Wallet address to check balance for
        :return: Tuple of (code, balance_float, raw_balance, error_message)
        """
        # Skip synthetic tokens - they don't exist as ERC20 contracts on this chain
        if currency.get("info", {}).get("synthetic", False):
            return code, None, None, "synthetic_token"

        token_address = currency["id"]
        decimals = currency["precision"]

        try:
            token_details = fetch_erc20_details(self.web3, token_address, chain_id=self.web3.eth.chain_id)
            balance_raw = token_details.contract.functions.balanceOf(wallet).call()
            balance_float = float(balance_raw) / (10**decimals)
            return code, balance_float, balance_raw, None
        except Exception as e:
            logger.warning("Failed to fetch balance for %s: %s", code, e)
            return code, None, None, str(e)

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

    def _get_trades_from_order_cache(
        self,
        symbol: str = None,
        since: int = None,
    ) -> list[dict]:
        """
        Convert cached orders to trade format.

        This provides immediate trade data from recent orders without waiting for
        Subsquid indexer to process them. This solves the race condition where
        Freqtrade tries to fetch trade details immediately after order execution.

        :param symbol: Filter by symbol (optional)
        :param since: Timestamp in milliseconds to fetch trades from
        :return: List of CCXT-formatted trades from cached orders
        """
        trades = []

        for order_id, order in self._orders.items():
            # Only process closed (filled) orders
            # IMPORTANT: Failed transactions have status="failed" and are skipped here.
            # GMX orders execute atomically on-chain - they either succeed completely
            # (receipt.status=1 → order.status="closed") or revert completely
            # (receipt.status=0 → order.status="failed"). This ensures failed orders
            # in the cache never appear as trades, preventing conflicts.
            if order.get("status") != "closed":
                continue

            # Filter by symbol if specified
            if symbol:
                normalized_symbol = self._normalize_symbol(symbol)
                if order.get("symbol") != normalized_symbol:
                    continue

            # Filter by timestamp if specified
            if since and order.get("timestamp", 0) < since:
                continue

            # Convert order to trade format
            # CCXT trade format: https://docs.ccxt.com/#/?id=trade-structure
            trade = {
                "id": order_id,  # Transaction hash
                "order": order_id,  # Link to order
                "timestamp": order.get("timestamp"),
                "datetime": order.get("datetime"),
                "symbol": order.get("symbol"),
                "type": order.get("type"),
                "side": order.get("side"),
                "takerOrMaker": None,  # GMX doesn't distinguish
                "price": order.get("average") or order.get("price"),
                "amount": order.get("filled") or order.get("amount"),
                "cost": order.get("cost"),
                "fee": order.get("fee"),
                "fees": [order.get("fee")] if order.get("fee") else [],
                "info": order.get("info", {}),
            }
            trades.append(trade)

        return trades

    def fetch_my_trades(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = None,
        params: dict = None,
    ) -> list[dict]:
        """
        Fetch user's trade history.

        Uses RPC-first approach:
        1. First checks local order cache for recent trades (immediate blockchain data)
        2. Then fetches historical trades from Subsquid GraphQL
        3. Merges and deduplicates results

        This solves the race condition where Freqtrade fetches trades immediately
        after order execution, before the Subsquid indexer has processed them.

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

        # Step 1: Get trades from local order cache (RPC-based, immediate)
        cache_trades = self._get_trades_from_order_cache(symbol=symbol, since=since)

        # Step 2: Fetch position changes from Subsquid (historical data)
        # NOTE: get_position_changes() only accepts account, position_key, and limit parameters
        # We need to filter by timestamp manually
        position_changes = self.subsquid.get_position_changes(
            account=wallet,
            limit=limit or 100,
        )

        # Parse each position change as a trade
        subsquid_trades = []
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
            except (KeyError, ValueError, TypeError) as e:
                # Skip trades we can't parse due to missing/invalid data
                logger.debug("Skipping unparseable trade: %s", str(e))
            except Exception as e:
                # Unexpected error - log at warning level for investigation
                logger.warning("Unexpected error parsing trade: %s", str(e), exc_info=True)

        # Step 3: Merge results, deduplicating by transaction hash (id)
        # Cache trades take precedence (more recent/accurate)
        seen_ids = {trade["id"] for trade in cache_trades if trade.get("id")}
        trades = cache_trades.copy()

        # Add Subsquid trades that aren't in cache
        for trade in subsquid_trades:
            trade_id = trade.get("id")
            if trade_id and trade_id not in seen_ids:
                trades.append(trade)
                seen_ids.add(trade_id)

        # Step 4: Sort by timestamp descending
        trades.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

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
        logger.debug("ORDER_TRACE: fetch_positions() CALLED - symbols=%s", symbols)
        params = params or {}
        self.load_markets()

        # Get wallet address
        wallet = params.get("wallet_address", self.wallet_address)
        if not wallet:
            raise ValueError(
                "wallet_address must be provided in GMXConfig or params",
            )

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

            except Exception as e:
                # Skip positions we can't parse
                logger.debug("Skipping unparseable position: %s", str(e))
                pass

        # Log summary of positions found
        logger.info(
            "ORDER_TRACE: fetch_positions() RETURNING %d position(s)",
            len(result),
        )
        for pos in result:
            logger.info(
                "ORDER_TRACE:   - Position: symbol=%s, side=%s, size=%.8f, entry_price=%s, unrealized_pnl=%s, leverage=%.1fx",
                pos.get("symbol"),
                pos.get("side"),
                pos.get("contracts", 0),
                pos.get("entryPrice", 0),
                pos.get("unrealizedPnl", 0),
                pos.get("leverage", 0),
            )

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
        gmx_position: dict | None = None,
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
        :param gmx_position: Optional GMX position data from GetOpenPositions.
            When provided (for closes), use exact GMX values instead of calculating
            from amount × price. This prevents remnants from calculation mismatches.
        :type gmx_position: dict | None
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
        slippage_percent = params.get("slippage_percent", self.default_slippage)

        # Determine position direction based on side and reduceOnly
        # Following Freqtrade/CCXT standard pattern:
        # - Opening: buy=LONG, sell=SHORT
        # - Closing: sell=close LONG, buy=close SHORT
        reduceOnly = params.get("reduceOnly", False)

        if not reduceOnly:
            # Opening a position
            is_long = side == "buy"  # buy = LONG, sell = SHORT
        else:
            # Closing a position
            is_long = side == "sell"  # sell = close LONG, buy = close SHORT

        # ============================================
        # SIZE CALCULATION - EXACT GMX SIZE LOGIC
        # ============================================
        # For closes: Use exact GMX position size to prevent remnants
        # For opens: Calculate from amount × price as usual

        if reduceOnly and gmx_position:
            # ============================================
            # CLOSING WITH GMX POSITION DATA
            # Use exact on-chain values to prevent remnants
            # ============================================

            actual_size_usd = gmx_position.get("position_size", 0.0)

            # Validate position size is positive
            if actual_size_usd <= 0:
                logger.warning("CLOSE: GMX position has invalid size %.2f, falling back to calculated size", actual_size_usd)
                # Fall through to standard CCXT calculation by setting gmx_position to None
                gmx_position = None

        # Only proceed with GMX position logic if we still have a valid position
        if reduceOnly and gmx_position:
            # Check if this is a partial close (sub_trade_amt provided)
            sub_trade_amt = params.get("sub_trade_amt")

            if sub_trade_amt is None:
                # ============================================
                # FULL CLOSE: Use GMX's exact position size
                # ============================================
                size_delta_usd = actual_size_usd

                logger.info("FULL CLOSE: Using exact GMX position size %.2f USD (freqtrade amount %.2f tokens ignored to prevent remnants)", size_delta_usd, amount)

            else:
                # ============================================
                # PARTIAL CLOSE: Calculate requested, clamp to actual
                # ============================================
                if price:
                    requested_size_usd = sub_trade_amt * price
                else:
                    ticker = self.fetch_ticker(symbol)
                    current_price = ticker["last"]
                    requested_size_usd = sub_trade_amt * current_price

                # Clamp to actual position size
                size_delta_usd = min(requested_size_usd, actual_size_usd)

                if size_delta_usd < requested_size_usd:
                    logger.warning("PARTIAL CLOSE: Clamping size from %.2f to %.2f USD (actual GMX position)", requested_size_usd, size_delta_usd)

                logger.info("PARTIAL CLOSE: size_delta_usd=%.2f (requested %.2f, actual position %.2f)", size_delta_usd, requested_size_usd, actual_size_usd)

        elif "size_usd" in params:
            # GMX Extension: Direct USD sizing via size_usd parameter
            # Validate: size_usd and non-zero amount should not be used together
            if amount and amount > 0:
                from ccxt.base.errors import InvalidOrder

                raise InvalidOrder(f"Cannot use both 'size_usd' ({params['size_usd']}) and non-zero 'amount' ({amount}) together. Use either: (1) 'size_usd' in params for direct USD sizing (recommended), or (2) 'amount' for base currency sizing (will be multiplied by price). Recommendation: Use 'size_usd' with amount=0 for precise USD-denominated positions.")
            size_delta_usd = params["size_usd"]
            logger.debug("ORDER_TRACE: Using size_usd=%s from params", size_delta_usd)

        else:
            # Standard CCXT: amount in base currency, convert to USD
            if price:
                size_delta_usd = amount * price
                logger.debug("ORDER_TRACE: Using amount=%.8f * price=%s = size_delta_usd=%s", amount, price, size_delta_usd)
            else:
                # Market orders: fetch current price
                ticker = self.fetch_ticker(symbol)
                current_price = ticker["last"]
                size_delta_usd = amount * current_price
                logger.debug("ORDER_TRACE: Using amount=%.8f * current_price=%s = size_delta_usd=%s", amount, current_price, size_delta_usd)

        # Build GMX params dict with calculated/exact size
        gmx_params = {
            "market_symbol": base_currency,
            "collateral_symbol": collateral_symbol,
            "start_token_symbol": collateral_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,  # Now uses exact GMX value for closes
            "leverage": leverage,
            "slippage_percent": slippage_percent,
        }

        # Add any additional parameters
        if "execution_buffer" in params:
            gmx_params["execution_buffer"] = params["execution_buffer"]
        if "auto_cancel" in params:
            gmx_params["auto_cancel"] = params["auto_cancel"]

        return gmx_params

    def _parse_sltp_params(self, params: dict) -> tuple[SLTPEntry | None, SLTPEntry | None]:
        """Parse CCXT-style SL/TP params into GMX SLTPEntry objects.

        Supports both CCXT unified (stopLossPrice/takeProfitPrice) and object (stopLoss/takeProfit) styles.

        :param params: CCXT parameters dict containing SL/TP configuration
        :type params: dict
        :return: Tuple of (stop_loss_entry, take_profit_entry)
        :rtype: tuple[SLTPEntry | None, SLTPEntry | None]
        """
        stop_loss_entry = None
        take_profit_entry = None

        # Parse Stop Loss
        if "stopLossPrice" in params:
            # CCXT unified style - simple price
            stop_loss_entry = SLTPEntry(
                trigger_price=params["stopLossPrice"],
                close_percent=1.0,
                auto_cancel=True,
            )
        elif "stopLoss" in params:
            # CCXT object style or GMX extensions
            sl = params["stopLoss"]
            if isinstance(sl, dict):
                stop_loss_entry = SLTPEntry(
                    trigger_price=sl.get("triggerPrice"),
                    trigger_percent=sl.get("triggerPercent"),  # GMX extension
                    close_percent=sl.get("closePercent", 1.0),  # GMX extension
                    close_size_usd=sl.get("closeSizeUsd"),  # GMX extension
                    auto_cancel=sl.get("autoCancel", True),
                )
            else:
                # Backwards compat: stopLoss as price value
                stop_loss_entry = SLTPEntry(trigger_price=sl, close_percent=1.0)

        # Parse Take Profit (same logic)
        if "takeProfitPrice" in params:
            # CCXT unified style
            take_profit_entry = SLTPEntry(
                trigger_price=params["takeProfitPrice"],
                close_percent=1.0,
                auto_cancel=True,
            )
        elif "takeProfit" in params:
            # CCXT object style or GMX extensions
            tp = params["takeProfit"]
            if isinstance(tp, dict):
                take_profit_entry = SLTPEntry(
                    trigger_price=tp.get("triggerPrice"),
                    trigger_percent=tp.get("triggerPercent"),  # GMX extension
                    close_percent=tp.get("closePercent", 1.0),  # GMX extension
                    close_size_usd=tp.get("closeSizeUsd"),  # GMX extension
                    auto_cancel=tp.get("autoCancel", True),
                )
            else:
                # Backwards compat: takeProfit as price value
                take_profit_entry = SLTPEntry(trigger_price=tp, close_percent=1.0)

        return stop_loss_entry, take_profit_entry

    def _create_order_with_sltp(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None,
        params: dict,
        sl_entry: SLTPEntry | None,
        tp_entry: SLTPEntry | None,
    ) -> dict:
        """Create order with bundled SL/TP (atomic transaction).

        Opens position + SL + TP in a single multicall transaction.

        :param symbol: Market symbol (e.g., 'ETH/USD')
        :param type: Order type ('market' or 'limit')
        :param side: Order side ('buy' for long, 'sell' for short)
        :param amount: Order size in USD
        :param price: Price (for limit orders, or None for market)
        :param params: Additional CCXT parameters
        :param sl_entry: Stop loss configuration
        :param tp_entry: Take profit configuration
        :return: CCXT-compatible order structure
        """
        # Only support bundled SL/TP for opening positions (reduceOnly=False)
        reduceOnly = params.get("reduceOnly", False)
        if reduceOnly:
            raise ValueError("Bundled SL/TP only supported for opening positions (reduceOnly=False). Use standalone SL/TP for closing positions.")

        # Convert CCXT params to GMX params (no position query needed for opening positions)
        gmx_params = self._convert_ccxt_to_gmx_params(symbol, type, side, amount, price, params, gmx_position=None)

        # Get market and token info
        normalized_symbol = self._normalize_symbol(symbol)
        market = self.markets[normalized_symbol]
        base_currency = market["base"]

        collateral_symbol = gmx_params["collateral_symbol"]
        leverage = gmx_params["leverage"]
        size_delta_usd = gmx_params["size_delta_usd"]
        slippage_percent = gmx_params.get("slippage_percent", self.default_slippage)
        execution_buffer = gmx_params.get("execution_buffer", self.execution_buffer)

        # Get token addresses from self.markets
        # Note: self.markets is now correctly loaded (both GraphQL and RPC paths handle wstETH special case)
        # This respects the user's loading preference (GraphQL vs RPC) and avoids unnecessary RPC calls
        chain = self.config.get_chain()
        market_address = market["info"]["market_token"]  # Market contract address

        # Determine position direction from gmx_params (set by _convert_ccxt_to_gmx_params)
        is_long = gmx_params["is_long"]

        # For GMX, we need to use the appropriate token based on position direction
        # GMX markets have specific tokens for long/short positions
        # Long positions use long_token, short positions use short_token (typically stablecoin)
        if is_long:
            collateral_address = market["info"]["long_token"]  # Use market's long token for long positions
        else:
            collateral_address = market["info"]["short_token"]  # Use market's short token for short positions
        index_token_address = market["info"]["index_token"]  # Use market's index token

        if not collateral_address or not index_token_address:
            raise ValueError(f"Could not resolve token addresses for {symbol} market")

        # Calculate collateral amount from size and leverage
        collateral_usd = size_delta_usd / leverage
        token_details = fetch_erc20_details(self.web3, collateral_address, chain_id=self.web3.eth.chain_id)

        # Get the price of the collateral token to convert USD to token amount
        # For GMX markets, we need the actual price of the long_token (e.g., wstETH price, not ETH price)
        oracle = OraclePrices(self.config.chain)

        # Get price for token (handles testnet address translation)
        price_data = oracle.get_price_for_token(collateral_address)
        if price_data is None:
            raise ValueError(f"No oracle price available for collateral token {collateral_address}. This may indicate the token is not supported by GMX oracle feeds.")
        raw_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])

        # Convert from 30-decimal precision to USD price
        collateral_token_price = raw_price / (10 ** (PRECISION - token_details.decimals))

        # Calculate token amount: collateral_usd / token_price_usd = tokens
        # Then convert to smallest unit (wei-equivalent)
        collateral_tokens = collateral_usd / collateral_token_price
        collateral_amount = int(collateral_tokens * (10**token_details.decimals))

        # Ensure token approval for the actual collateral token
        # Use token symbol from token_details since we might be using a different token
        # (e.g., market uses wstETH but user specified "ETH")
        actual_collateral_symbol = token_details.symbol
        self._ensure_token_approval(actual_collateral_symbol, size_delta_usd, leverage)

        # Create SLTPOrder instance
        sltp_order = SLTPOrder(
            config=self.config,
            market_key=to_checksum_address(market_address),
            collateral_address=to_checksum_address(collateral_address),
            index_token_address=to_checksum_address(index_token_address),
            is_long=is_long,  # Use actual position direction from gmx_params
        )

        # Build SLTPParams
        sltp_params = SLTPParams(
            stop_loss=sl_entry,
            take_profit=tp_entry,
        )

        # Create bundled order
        sltp_result = sltp_order.create_increase_order_with_sltp(
            size_delta_usd=size_delta_usd,
            initial_collateral_delta_amount=collateral_amount,
            sltp_params=sltp_params,
            slippage_percent=slippage_percent,
            execution_buffer=execution_buffer,
        )

        logger.info("SL/TP result created: entry_price=%s, sl_trigger=%s, tp_trigger=%s, sl_fee=%s, tp_fee=%s", sltp_result.entry_price, sltp_result.stop_loss_trigger_price, sltp_result.take_profit_trigger_price, sltp_result.stop_loss_fee, sltp_result.take_profit_fee)

        # Gas estimation and logging (if gas monitoring enabled)
        gas_config = getattr(self, "_gas_monitor_config", None)

        # Log position size details (if gas monitoring enabled)
        if gas_config and gas_config.enabled:
            position_type = "LONG" if is_long else "SHORT"
            logger.info(
                "Opening %s position with SL/TP: size=$%.2f, collateral=$%.2f (%.6f %s), leverage=%.1fx",
                position_type,
                size_delta_usd,
                collateral_usd,
                collateral_tokens,
                actual_collateral_symbol,
                leverage,
            )
        monitor = self.gas_monitor
        gas_estimate = None
        native_price_usd = None
        if gas_config and gas_config.enabled and monitor:
            try:
                gas_estimate = monitor.estimate_transaction_gas(
                    tx=sltp_result.transaction,
                    from_addr=self.wallet.address,
                )
                monitor.log_gas_estimate(gas_estimate, "GMX SL/TP order")
                native_price_usd = gas_estimate.native_price_usd
            except Exception as e:
                logger.warning("Gas estimation failed: %s - using order gas_limit", e)

        # Sign transaction
        transaction = dict(sltp_result.transaction)
        if "nonce" in transaction:
            del transaction["nonce"]

        # Use estimated gas if available
        if gas_estimate:
            transaction["gas"] = gas_estimate.gas_limit

        signed_tx = self.wallet.sign_transaction_with_new_nonce(transaction)

        # Submit to blockchain
        tx_hash_bytes = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(tx_hash_bytes)

        # Wait for confirmation
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

        # Log actual gas usage (if gas monitoring enabled)
        if gas_config and gas_config.enabled and monitor:
            try:
                monitor.log_gas_usage(
                    receipt=dict(receipt),
                    native_price_usd=native_price_usd,
                    operation="GMX SL/TP order",
                    estimated_gas=gas_estimate.gas_limit if gas_estimate else None,
                )
            except Exception as e:
                logger.warning("Failed to log gas usage: %s", e)

        # Convert to CCXT format with SL/TP info
        order = self._parse_sltp_result_to_ccxt(
            sltp_result,
            symbol,
            side,
            type,
            amount,
            tx_hash,
            receipt,
        )

        return order

    def _parse_sltp_result_to_ccxt(
        self,
        sltp_result,
        symbol: str,
        side: str,
        type: str,
        amount: float,
        tx_hash: str,
        receipt: dict,
    ) -> dict:
        """Convert SLTPOrderResult to CCXT order structure.

        :param sltp_result: SLTPOrderResult from SLTPOrder
        :param symbol: CCXT symbol
        :param side: Order side
        :param type: Order type
        :param amount: Order size
        :param tx_hash: Transaction hash
        :param receipt: Transaction receipt
        :return: CCXT-compatible order structure with SL/TP info
        """
        timestamp = self.milliseconds()
        tx_success = receipt.get("status") == 1
        status = "closed" if tx_success else "failed"

        # Build info dict with SL/TP data
        info = {
            "tx_hash": tx_hash,
            "receipt": receipt,
            "block_number": receipt.get("blockNumber"),
            "gas_used": receipt.get("gasUsed"),
            "total_execution_fee": sltp_result.total_execution_fee,
            "main_order_fee": sltp_result.main_order_fee,
            "stop_loss_fee": sltp_result.stop_loss_fee,
            "take_profit_fee": sltp_result.take_profit_fee,
            "entry_price": sltp_result.entry_price,
            "has_stop_loss": sltp_result.stop_loss_trigger_price is not None,
            "has_take_profit": sltp_result.take_profit_trigger_price is not None,
        }

        # Add trigger prices if they exist
        if sltp_result.stop_loss_trigger_price is not None:
            info["stop_loss_trigger_price"] = sltp_result.stop_loss_trigger_price
        if sltp_result.take_profit_trigger_price is not None:
            info["take_profit_trigger_price"] = sltp_result.take_profit_trigger_price

        # Store execution fee in info (ETH gas paid to keeper)
        info["execution_fee_eth"] = sltp_result.total_execution_fee / 1e18

        # Use entry price from result
        mark_price = sltp_result.entry_price

        # GMX orders execute immediately
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
            "price": mark_price,
            "amount": amount,
            "cost": amount if tx_success else None,
            "average": mark_price if tx_success else None,
            "filled": filled_amount,
            "remaining": remaining_amount,
            "status": status,
            "fee": self._build_trading_fee(symbol, amount),
            "trades": None,
            "info": info,
        }

        return order

    def _create_standalone_sltp_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        params: dict,
    ) -> dict:
        """Create standalone SL/TP order for existing position.

        This method creates a standalone stop-loss or take-profit order for an
        existing GMX position. Unlike bundled orders (created with position opening),
        these are created separately after a position is already open.

        Used by Freqtrade's ``create_stoploss()`` method when
        ``stoploss_on_exchange=True``.

        :param symbol: Market symbol (e.g., "ETH/USDC:USDC")
        :param type: Order type ("stop_loss" or "take_profit")
        :param side: Order side ("sell" to close long, "buy" to close short)
        :param amount: Position size in USD to close
        :param params: Parameters dict containing:

            - ``stopLossPrice`` or ``takeProfitPrice``: Trigger price (float)
            - ``leverage``: Position leverage (required)
            - ``collateral_symbol``: Collateral token (optional, inferred from symbol)
            - ``slippage_percent``: Slippage tolerance (default: 0.003)
            - ``execution_buffer``: Execution fee buffer multiplier (default: 2.5)

        :return: CCXT-compatible order structure
        :raises NotSupported: If trying to create SL/TP without existing position
        :raises InvalidOrder: If required parameters are missing
        """

        # Parse parameters
        trigger_price = params.get("stopLossPrice" if type == "stop_loss" else "takeProfitPrice")
        if not trigger_price:
            # Try alternative parameter names
            if type == "stop_loss" and "stopLoss" in params:
                sl_param = params["stopLoss"]
                trigger_price = sl_param.get("triggerPrice") if isinstance(sl_param, dict) else sl_param
            elif type == "take_profit" and "takeProfit" in params:
                tp_param = params["takeProfit"]
                trigger_price = tp_param.get("triggerPrice") if isinstance(tp_param, dict) else tp_param

        if not trigger_price:
            raise InvalidOrder(f"Trigger price required for standalone {type} order")

        leverage = params.get("leverage", 1.0)
        slippage_percent = params.get("slippage_percent", self.default_slippage)
        execution_buffer = params.get("execution_buffer", 2.5)

        # Parse symbol to get market info
        market_info = self.market(symbol)
        market_symbol = market_info["base"]  # e.g., "BTC" from "BTC/USDC:USDC"
        collateral_symbol = params.get("collateral_symbol", "USDC")

        # Determine position direction from side
        # For SL/TP: sell = closing long position, buy = closing short position
        is_long = side == "sell"

        # We need to know the entry price to calculate percentage
        # For now, use trigger price to calculate entry price
        # This assumes the user passed absolute trigger price
        # In reality, we should fetch the position from chain, but that's expensive

        # Create GMX trading instance
        trading = GMXTrading(self.config)

        # Create standalone SL or TP order
        try:
            if type == "stop_loss":
                result = trading.create_stop_loss(
                    market_symbol=market_symbol,
                    collateral_symbol=collateral_symbol,
                    is_long=is_long,
                    position_size_usd=amount,
                    entry_price=None,  # Will be inferred from position
                    stop_loss_price=trigger_price,
                    close_percent=1.0,  # Close entire position
                    slippage_percent=slippage_percent,
                    execution_buffer=execution_buffer,
                )
            else:  # take_profit
                result = trading.create_take_profit(
                    market_symbol=market_symbol,
                    collateral_symbol=collateral_symbol,
                    is_long=is_long,
                    position_size_usd=amount,
                    entry_price=None,  # Will be inferred from position
                    take_profit_price=trigger_price,
                    close_percent=1.0,
                    slippage_percent=slippage_percent,
                    execution_buffer=execution_buffer,
                )
        except ValueError as e:
            if "entry_price is required" in str(e):
                raise InvalidOrder(
                    f"Cannot create {type} order without entry price: {e}. If using percentage trigger, you must provide entry_price or fetch position first. If using absolute price, ensure parameter name is correct.",
                ) from e
            raise

        # Gas estimation and logging (if gas monitoring enabled)
        gas_config = getattr(self, "_gas_monitor_config", None)
        monitor = self.gas_monitor

        # Log position size details for SL/TP order (if gas monitoring enabled)
        if gas_config and gas_config.enabled:
            position_type = "LONG" if is_long else "SHORT"
            order_type_display = "Stop Loss" if type == "stop_loss" else "Take Profit"
            logger.info(
                "Creating %s for %s position: size=$%.2f, trigger=$%.2f, collateral=%s",
                order_type_display,
                position_type,
                amount,
                trigger_price,
                collateral_symbol,
            )

        gas_estimate = None
        native_price_usd = None
        if gas_config and gas_config.enabled and monitor:
            try:
                gas_estimate = monitor.estimate_transaction_gas(
                    tx=result.transaction,
                    from_addr=self.wallet.address,
                )
                monitor.log_gas_estimate(gas_estimate, f"GMX {type} order")
                native_price_usd = gas_estimate.native_price_usd
            except Exception as e:
                logger.warning("Gas estimation failed: %s - using order gas_limit", e)

        # Sign and submit transaction
        transaction = dict(result.transaction)
        if "nonce" in transaction:
            del transaction["nonce"]

        # Use estimated gas if available
        if gas_estimate:
            transaction["gas"] = gas_estimate.gas_limit

        signed_tx = self.wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)

        # Log actual gas usage (if gas monitoring enabled)
        if gas_config and gas_config.enabled and monitor:
            try:
                monitor.log_gas_usage(
                    receipt=dict(receipt),
                    native_price_usd=native_price_usd,
                    operation=f"GMX {type} order",
                    estimated_gas=gas_estimate.gas_limit if gas_estimate else None,
                )
            except Exception as e:
                logger.warning("Failed to log gas usage: %s", e)

        # Build CCXT-compatible order structure
        timestamp = self.milliseconds()
        tx_success = receipt.get("status") == 1

        order = {
            "id": tx_hash.hex(),
            "clientOrderId": None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "lastTradeTimestamp": timestamp if tx_success else None,
            "symbol": symbol,
            "type": type,
            "side": side,
            "price": trigger_price,
            "amount": amount,
            "cost": amount if tx_success else None,
            "average": trigger_price if tx_success else None,
            "filled": amount if tx_success else 0.0,
            "remaining": 0.0 if tx_success else amount,
            "status": "closed" if tx_success else "canceled",
            "fee": self._build_trading_fee(symbol, amount),
            "trades": None,
            "stopPrice": trigger_price,  # Freqtrade expects this field
            "info": {
                "tx_hash": tx_hash.hex(),
                "block_number": receipt.get("blockNumber"),
                "gas_used": receipt.get("gasUsed"),
                "execution_fee": result.execution_fee,
                "execution_fee_eth": result.execution_fee / 10**18,
                "trigger_price": trigger_price,
                "order_type": type,
                "receipt": receipt,
            },
        }

        logger.info(
            "Created standalone %s order for %s: trigger=%.2f, amount=%.2f USD, tx=%s",
            type,
            symbol,
            trigger_price,
            amount,
            tx_hash.hex(),
        )

        return order

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
            logger.debug("Using native %s - no approval needed", collateral_symbol)
            return

        # Get token address
        chain = self.config.get_chain()
        collateral_token_address = get_token_address_normalized(chain, collateral_symbol)

        if not collateral_token_address:
            # If token address not found, assume it's OK (might be native or not need approval)
            logger.debug("Token address not found for %s, skipping approval", collateral_symbol)
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

        # Get token price to convert USD to token amount
        oracle = OraclePrices(self.config.chain)

        # Get price for token (handles testnet address translation)
        price_data = oracle.get_price_for_token(collateral_token_address)
        if price_data is None:
            raise ValueError(f"No oracle price available for collateral token {collateral_token_address}. This may indicate the token is not supported by GMX oracle feeds.")
        raw_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])

        # Convert from 30-decimal precision to USD price
        token_price = raw_price / (10 ** (PRECISION - token_details.decimals))

        # Convert USD to token amount: usd / price = tokens
        required_tokens = required_collateral_usd / token_price
        required_amount = int(required_tokens * (10**token_details.decimals))

        logger.debug("Token approval check: %s allowance=%.4f, required=%.4f, token_price=$%.2f", collateral_symbol, current_allowance / (10**token_details.decimals), required_amount / (10**token_details.decimals), token_price)

        # If allowance is sufficient, no action needed
        if current_allowance >= required_amount:
            logger.debug("Sufficient %s allowance exists", collateral_symbol)
            return

        # Need to approve - use a large amount to avoid repeated approvals
        # Approve 1 billion tokens (same pattern as debug_deploy.py)
        approve_amount = 1_000_000_000 * (10**token_details.decimals)

        logger.info("Insufficient %s allowance. Current: %.4f, Required: %.4f. Approving %.0f %s...", collateral_symbol, current_allowance / (10**token_details.decimals), required_amount / (10**token_details.decimals), approve_amount / (10**token_details.decimals), collateral_symbol)

        # Build approval transaction
        approve_tx = token_contract.functions.approve(spender_address, approve_amount).build_transaction(
            {
                "from": to_checksum_address(wallet_address),
                "gas": 100_000,
            }
        )

        # Apply EIP-1559 gas pricing with safety buffer to avoid
        # "max fee per gas less than block base fee" race condition on L2s
        from eth_defi.gas import estimate_gas_price, apply_gas

        gas_fees = estimate_gas_price(self.web3)
        apply_gas(approve_tx, gas_fees)

        # CRITICAL: Remove nonce before calling sign_transaction_with_new_nonce
        # The wallet will manage the nonce automatically
        if "nonce" in approve_tx:
            del approve_tx["nonce"]

        # Sign and send approval transaction
        signed_approve_tx = self.wallet.sign_transaction_with_new_nonce(approve_tx)
        approve_tx_hash = self.web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)

        logger.info("Approval transaction sent: %s. Waiting for confirmation...", approve_tx_hash.hex())

        # Wait for confirmation
        approve_receipt = self.web3.eth.wait_for_transaction_receipt(approve_tx_hash, timeout=120)

        if approve_receipt["status"] == 1:
            logger.info("Token approval successful! Approved %.0f %s for %s", approve_amount / (10**token_details.decimals), collateral_symbol, spender_address)
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
        order_key: bytes | None = None,
    ) -> dict:
        """Convert GMX OrderResult to CCXT order structure.

        This method is called after the ORDER CREATION transaction succeeds.
        The order is returned with status "open" because GMX uses a two-phase
        execution model:

        1. Order Creation - User submits order, receives OrderCreated event
        2. Keeper Execution - Keeper executes order in separate tx

        The actual order status (closed/cancelled) is determined later by
        fetch_order() which polls the DataStore and EventEmitter.

        :param order_result: GMX OrderResult from trading module
        :param symbol: CCXT symbol
        :type symbol: str
        :param side: Order side ('buy' or 'sell')
        :type side: str
        :param type: Order type ('market' or 'limit')
        :type type: str
        :param amount: Order size in USD
        :type amount: float
        :param tx_hash: Transaction hash of order creation
        :type tx_hash: str
        :param receipt: Transaction receipt of order creation
        :type receipt: dict
        :param order_key: Order key from OrderCreated event (for tracking)
        :type order_key: bytes | None
        :return: CCXT-compatible order structure with status "open"
        :rtype: dict
        """
        timestamp = self.milliseconds()

        # Order is "open" (pending keeper execution)
        # Status will be updated to "closed" or "cancelled" by fetch_order()
        status = "open"

        # Build info dict with all GMX-specific data
        info = {
            "tx_hash": tx_hash,
            "creation_receipt": receipt,
            "block_number": receipt.get("blockNumber"),
            "gas_used": receipt.get("gasUsed"),
            "execution_fee": order_result.execution_fee,
            "acceptable_price": order_result.acceptable_price,
            "mark_price": order_result.mark_price,
            "gas_limit": order_result.gas_limit,
        }

        # Store order_key for fetch_order() to track execution
        if order_key:
            info["order_key"] = order_key.hex()

        if order_result.estimated_price_impact is not None:
            info["estimated_price_impact"] = order_result.estimated_price_impact

        # Store execution fee in info (ETH gas paid to keeper)
        info["execution_fee_eth"] = order_result.execution_fee / 1e18

        # Use mark_price as initial price estimate
        mark_price = order_result.mark_price

        # Order is not yet filled - waiting for keeper
        filled_amount = 0.0
        remaining_amount = amount

        order = {
            "id": tx_hash,
            "clientOrderId": None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "lastTradeTimestamp": None,  # Not executed yet
            "symbol": symbol,
            "type": type,
            "side": side,
            "price": mark_price if type == "market" else None,
            "amount": amount,
            "cost": None,  # Unknown until executed
            "average": None,  # Unknown until executed
            "filled": filled_amount,
            "remaining": remaining_amount,
            "status": status,
            "fee": self._build_trading_fee(symbol, amount),
            "trades": [],
            "info": info,
        }

        # Store order for fetch_order() to retrieve
        self._orders[tx_hash] = order

        logger.debug(
            "ORDER_TRACE: Created order id=%s with status='open' (pending keeper execution), order_key=%s",
            tx_hash,
            order_key.hex()[:16] if order_key else "unknown",
        )

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

            # Approach 1: CCXT standard (amount in base currency)
            order = gmx.create_order(
                "ETH/USD",
                "market",
                "buy",
                0.5,  # 0.5 ETH
                params={
                    "leverage": 3.0,
                    "collateral_symbol": "USDC",
                },
            )

            # Approach 2: GMX extension (size_usd in USD)
            order = gmx.create_order(
                "ETH/USD",
                "market",
                "buy",
                0,  # Ignored when size_usd is provided
                params={
                    "size_usd": 1000,  # $1000 position
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
        :param amount: Order size in base currency contracts (e.g., ETH for ETH/USD). Use params['size_usd'] for USD-based sizing.
        :type amount: float
        :param price: Price for limit orders. For market orders, used to convert amount to USD if provided.
        :type price: float | None
        :param params: Additional parameters:
            - size_usd (float): GMX Extension - Order size in USD (alternative to amount parameter)
            - leverage (float): Leverage multiplier (default: 1.0)
            - collateral_symbol (str): Collateral token (default: 'USDC')
            - slippage_percent (float): Slippage tolerance (default: 0.003)
            - execution_buffer (float): Gas buffer multiplier (default: 2.2)
            - auto_cancel (bool): Auto-cancel if execution fails (default: False)
            - wait_for_execution (bool): Wait for keeper execution via Subsquid/EventEmitter (default: True).
              Set to False for fork tests where Subsquid won't have the order data.
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

        # Check if trading is paused due to consecutive failures
        if self._trading_paused:
            # Get current wallet balance for detailed error message
            try:
                eth_balance = self.web3.eth.get_balance(self.wallet.address)
                eth_balance_eth = eth_balance / 1e18
                # Estimate USD value (using $2000/ETH as approximation)
                eth_balance_usd = eth_balance_eth * 2000
                balance_str = f"{eth_balance_eth:.6f} ETH (${eth_balance_usd:.2f})"
            except Exception as e:
                balance_str = f"<failed to fetch: {e}>"

            # Get the last failed transaction hash if available
            last_tx_hash = getattr(self, "_last_failed_tx_hash", None)
            tx_link = f"TX: https://arbiscan.io/tx/{last_tx_hash}" if last_tx_hash else ""

            # Build detailed error message
            error_msg = f"🛑 GMX BOT PAUSED: {self._consecutive_failures} order failures detected.\n\n{self._trading_paused_reason}\n\nWallet balance: {balance_str}\n{tx_link}\n\nACTION REQUIRED: Increase executionBuffer in config (try 2.0x or higher), top up wallet if needed, then restart bot."
            logger.error(error_msg)
            # Raise BaseError which becomes OperationalException (stops bot, sends EXCEPTION to Telegram)
            raise BaseError(error_msg)

        # Nonce is managed by the HotWallet's internal counter:
        # - Initialised once via sync_nonce() at wallet creation (line ~406)
        # - Incremented locally by each sign_transaction_with_new_nonce() call
        # Do NOT re-sync from chain here — the RPC may return a stale count
        # (e.g. load-balanced nodes 1 block behind), causing "nonce too low".

        # Check gas balance before creating order (if gas monitoring enabled)
        gas_config = getattr(self, "_gas_monitor_config", None)
        monitor = self.gas_monitor
        if gas_config and gas_config.enabled and monitor:
            gas_check = monitor.check_gas_balance(self.wallet.address)
            if gas_check.status == "critical":
                monitor.log_gas_check_warning(gas_check)
                if gas_config.raise_on_critical:
                    raise InsufficientGasError(gas_check.message, gas_check)
                # Return failed order dict instead of crashing
                return {
                    "id": None,
                    "clientOrderId": None,
                    "datetime": self.iso8601(self.milliseconds()),
                    "timestamp": self.milliseconds(),
                    "lastTradeTimestamp": None,
                    "status": "rejected",
                    "symbol": symbol,
                    "type": type,
                    "side": side,
                    "price": price,
                    "amount": amount,
                    "filled": 0.0,
                    "remaining": amount,
                    "cost": 0.0,
                    "trades": [],
                    "fee": None,
                    "info": {
                        "error": "insufficient_gas",
                        "message": gas_check.message,
                        "gas_check": {
                            "balance_native": str(gas_check.native_balance),
                            "balance_usd": gas_check.balance_usd,
                            "critical_threshold_usd": gas_config.critical_threshold_usd,
                        },
                    },
                    "average": None,
                    "fees": [],
                }
            elif gas_check.status == "warning":
                monitor.log_gas_check_warning(gas_check)

        logger.info("=" * 80)
        logger.info(
            "ORDER_TRACE: create_order() CALLED - symbol=%s, type=%s, side=%s, amount=%.8f",
            symbol,
            type,
            side,
            amount,
        )
        logger.info(
            "ORDER_TRACE:   params - reduceOnly=%s, leverage=%s, collateral_symbol=%s",
            params.get("reduceOnly", False) if params else False,
            params.get("leverage") if params else None,
            params.get("collateral_symbol") if params else None,
        )

        # Ensure markets are loaded and populated
        if not self.markets_loaded or not self.markets:
            self.load_markets()

        # Parse SL/TP parameters (CCXT standard)
        sl_entry, tp_entry = self._parse_sltp_params(params)

        # Check for standalone SL/TP order types
        if type in ["stop_loss", "take_profit"]:
            return self._create_standalone_sltp_order(
                symbol,
                type,
                side,
                amount,
                params,
            )

        # Bundled approach: SL/TP with position opening
        if sl_entry or tp_entry:
            return self._create_order_with_sltp(symbol, type, side, amount, price, params, sl_entry, tp_entry)

        # ============================================
        # NEW: Query GMX position BEFORE size calculation for closes
        # ============================================
        gmx_position = None
        reduceOnly = params.get("reduceOnly", False)

        if reduceOnly:
            try:
                # Parse symbol to get market details
                normalized_symbol = self._normalize_symbol(symbol)
                market = self.markets[normalized_symbol]
                base_currency = market["base"]

                # Determine position direction from side
                is_closing_long = side == "sell"  # sell = close LONG
                is_closing_short = side == "buy"  # buy = close SHORT

                # Extract collateral symbol
                collateral_symbol = params.get("collateral_symbol")
                if not collateral_symbol:
                    if "collateral_token" in params:
                        collateral_symbol = params["collateral_token"]
                    else:
                        # Default to quote currency (strip :USDC suffix if present)
                        collateral_symbol = market["quote"].replace(":USDC", "").replace(":USDT", "")

                # Query all positions for this wallet
                logger.info("CLOSE ORDER: Querying GMX for actual position (market=%s, collateral=%s, direction=%s)", base_currency, collateral_symbol, "LONG" if is_closing_long else "SHORT")

                positions_manager = GetOpenPositions(self.config)
                existing_positions = positions_manager.get_data(self.wallet.address)

                # Find matching position: market + collateral + direction
                for position_key, position_data in existing_positions.items():
                    position_market = position_data.get("market_symbol", "")
                    position_is_long = position_data.get("is_long", None)
                    position_collateral = position_data.get("collateral_token", "")

                    if position_market == base_currency and position_collateral == collateral_symbol:
                        if is_closing_long and position_is_long:
                            gmx_position = position_data
                            break
                        elif is_closing_short and not position_is_long:
                            gmx_position = position_data
                            break

                if gmx_position:
                    logger.info("CLOSE ORDER: Found GMX position - size_usd=%.2f, collateral_usd=%.2f, tokens=%s", gmx_position.get("position_size", 0), gmx_position.get("initial_collateral_amount_usd", 0), gmx_position.get("size_in_tokens", 0))
                else:
                    logger.warning("CLOSE ORDER: No GMX position found - may already be closed")

            except Exception as e:
                logger.error("Failed to query GMX position for close: %s", e)
                # Continue with calculated size as fallback
                gmx_position = None

        # ============================================
        # Convert CCXT params to GMX params
        # NOW PASSING gmx_position for accurate sizing
        # ============================================
        gmx_params = self._convert_ccxt_to_gmx_params(
            symbol=symbol,
            type=type,
            side=side,
            amount=amount,
            price=price,
            params=params,
            gmx_position=gmx_position,  # NEW: Pass actual position data
        )

        # Ensure token approval before creating order
        self._ensure_token_approval(
            collateral_symbol=gmx_params["collateral_symbol"],
            size_delta_usd=gmx_params["size_delta_usd"],
            leverage=gmx_params["leverage"],
        )

        # Note: reduceOnly already extracted earlier (before position query)

        # Log position size details (if gas monitoring enabled)
        if gas_config and gas_config.enabled:
            size_usd = gmx_params.get("size_delta_usd", 0)
            leverage = gmx_params.get("leverage", 1.0)
            collateral_usd = size_usd / leverage if leverage > 0 else 0
            collateral_symbol = gmx_params.get("collateral_symbol", "unknown")
            is_long = gmx_params.get("is_long", True)
            position_type = "LONG" if is_long else "SHORT"
            action = "Closing" if reduceOnly else "Opening"

            # Try to get raw token amount using oracle price
            collateral_tokens = None
            try:
                from statistics import median

                from eth_defi.gmx.contracts import get_token_address_normalized
                from eth_defi.gmx.core.oracle import OraclePrices

                chain = self.config.get_chain()
                collateral_address = get_token_address_normalized(chain, collateral_symbol)
                if collateral_address:
                    oracle = OraclePrices(chain)
                    price_data = oracle.get_price_for_token(collateral_address)
                    if price_data:
                        token_details = fetch_erc20_details(self.web3, collateral_address)
                        raw_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])
                        token_price_usd = raw_price / (10 ** (PRECISION - token_details.decimals))
                        if token_price_usd > 0:
                            collateral_tokens = collateral_usd / token_price_usd
            except Exception as e:
                logger.debug("Could not calculate raw token amount: %s", e)

            if collateral_tokens is not None:
                logger.info(
                    "%s %s position: size=$%.2f, collateral=$%.2f (%.6f %s), leverage=%.1fx",
                    action,
                    position_type,
                    size_usd,
                    collateral_usd,
                    collateral_tokens,
                    collateral_symbol,
                    leverage,
                )
            else:
                logger.info(
                    "%s %s position: size=$%.2f, collateral=$%.2f %s, leverage=%.1fx",
                    action,
                    position_type,
                    size_usd,
                    collateral_usd,
                    collateral_symbol,
                    leverage,
                )

        if not reduceOnly:
            # ============================================
            # OPENING POSITIONS (buy=LONG, sell=SHORT)
            # ============================================
            if type == "limit":
                # Limit order - triggers at specified price
                if price is None:
                    raise ValueError("Limit orders require a price parameter")
                order_result = self.trader.open_limit_position(
                    trigger_price=price,
                    **gmx_params,
                )
            else:
                # Market order - executes immediately
                order_result = self.trader.open_position(**gmx_params)

        else:
            # ============================================
            # CLOSING POSITIONS (sell=close LONG, buy=close SHORT)
            # ============================================
            # For closing positions, use on-chain position data from GetOpenPositions
            # to derive the correct decrease size and collateral delta.
            #
            # This mirrors the recommendation from the GMX SDK: always base the
            # decrease on the actual open position instead of the user-requested
            # amount to avoid "invalid decrease order size" reverts.

            # RACE CONDITION FIX: When stop-loss executes, it closes the position on-chain.
            # If Freqtrade's main loop then tries to close the same position (e.g., via ROI),
            # we need to handle this gracefully instead of raising an error.

            # Determine which type of position we're closing
            is_closing_long = side == "sell"  # sell = close LONG
            is_closing_short = side == "buy"  # buy = close SHORT

            normalized_symbol = self._normalize_symbol(symbol)
            market = self.markets[normalized_symbol]
            base_currency = market["base"]

            # Get existing positions to determine the position we're closing
            positions_manager = GetOpenPositions(self.config)

            try:
                existing_positions = positions_manager.get_data(self.wallet.address)

                # Log existing positions for debugging
                # logger.info("ORDER_TRACE: Fetched %d existing positions for close operation", len(existing_positions))
                # for key, pos in existing_positions.items():
                #     logger.info(
                #         "ORDER_TRACE: Position %s - market=%s, is_long=%s, size_usd=%.2f, collateral_usd=%.2f, percent_profit=%.4f%%",
                #         key,
                #         pos.get("market_symbol"),
                #         pos.get("is_long"),
                #         pos.get("position_size", 0),
                #         pos.get("initial_collateral_amount_usd", 0),
                #         pos.get("percent_profit", 0),
                #     )

                # Find the matching position for this market + collateral + direction
                position_to_close = None
                for position_key, position_data in existing_positions.items():
                    position_market = position_data.get("market_symbol", "")
                    position_is_long = position_data.get("is_long", None)
                    position_collateral = position_data.get("collateral_token", "")

                    # Match market and collateral
                    if position_market == base_currency and position_collateral == gmx_params["collateral_symbol"]:
                        # Check if position direction matches what we're trying to close
                        if is_closing_long and position_is_long:
                            position_to_close = position_data
                            break
                        elif is_closing_short and not position_is_long:
                            position_to_close = position_data
                            break

                if not position_to_close:
                    # Position not found - likely already closed (e.g., by stop-loss)
                    position_type = "long" if is_closing_long else "short"
                    logger.warning("No %s position found for %s with collateral %s. This likely means the position was already closed (e.g., by stop-loss execution). Returning synthetic 'closed' order response.", position_type, symbol, gmx_params["collateral_symbol"])

                    # Get current mark price for cost calculation
                    try:
                        ticker = self.fetch_ticker(symbol)
                        mark_price = ticker.get("last") or ticker.get("close")
                    except Exception as e:
                        logger.warning("Failed to fetch ticker for synthetic order price: %s", e)
                        mark_price = None

                    # Return a synthetic "closed" order to allow Freqtrade to reconcile state
                    timestamp = self.milliseconds()
                    synthetic_order = {
                        "id": f"already_closed_{timestamp}",
                        "clientOrderId": None,
                        "timestamp": timestamp,
                        "datetime": self.iso8601(timestamp),
                        "lastTradeTimestamp": timestamp,
                        "symbol": symbol,
                        "type": type,
                        "side": side,
                        "price": mark_price,  # Current mark price (approximation)
                        "amount": amount,
                        "cost": amount * mark_price if mark_price else 0,  # Cost in stake currency = amount * price
                        "average": mark_price,  # Average fill price (approximation)
                        "filled": amount,  # Mark as fully filled
                        "remaining": 0.0,
                        "status": "closed",  # Position already closed
                        "fee": {"cost": 0.0, "currency": "ETH", "rate": None},  # No additional fee for this synthetic order
                        "trades": [],
                        "info": {
                            "reason": "position_already_closed",
                            "message": f"Position for {symbol} was already closed (likely by stop-loss)",
                            "requested_close_size_usd": gmx_params["size_delta_usd"],
                        },
                    }

                    # Add synthetic order to cache for consistency
                    self._orders[synthetic_order["id"]] = synthetic_order

                    logger.info("Position for %s already closed - returning synthetic order id=%s", symbol, synthetic_order["id"])
                    return synthetic_order

                # ============================================
                # NEW SIMPLIFIED LOGIC
                # Size already calculated correctly in _convert_ccxt_to_gmx_params
                # with exact GMX values - no clamping needed here
                # ============================================

                size_delta_usd = gmx_params["size_delta_usd"]

                # Validation: Ensure size is positive
                if size_delta_usd <= 0:
                    raise ValueError(f"Invalid close size {size_delta_usd} for {symbol}. Position data: {position_to_close}")

                logger.info("CLOSE: Using size_delta_usd=%.2f from exact GMX position data", size_delta_usd)

                # Derive collateral delta proportionally from the original collateral.
                # Since size is now exact from GMX, this calculation is accurate.
                collateral_amount_usd = position_to_close.get("initial_collateral_amount_usd")
                if collateral_amount_usd is None:
                    # Fallback: approximate from leverage if USD value is missing
                    leverage = float(position_to_close.get("leverage", 1.0) or 1.0)
                    if leverage > 0:
                        collateral_amount_usd = size_delta_usd / leverage
                    else:
                        collateral_amount_usd = size_delta_usd

                # Pro-rata collateral for partial closes, full amount for full close
                position_size_usd = position_to_close.get("position_size", size_delta_usd)
                close_fraction = min(1.0, size_delta_usd / position_size_usd) if position_size_usd > 0 else 1.0
                initial_collateral_delta = collateral_amount_usd * close_fraction

                # Safety floor – avoid tiny dust values
                if initial_collateral_delta <= 0:
                    initial_collateral_delta = collateral_amount_usd

                logger.info("CLOSE: size_delta=%.2f (%.1f%%), collateral_delta=%.2f (%.1f%%)", size_delta_usd, close_fraction * 100, initial_collateral_delta, close_fraction * 100)

                # Call close_position with the derived parameters
                # Use the actual position direction from the found position
                order_result = self.trader.close_position(
                    market_symbol=gmx_params["market_symbol"],
                    collateral_symbol=gmx_params["collateral_symbol"],
                    start_token_symbol=gmx_params["start_token_symbol"],
                    is_long=position_to_close.get("is_long"),  # Use actual position direction
                    size_delta_usd=size_delta_usd,
                    initial_collateral_delta=initial_collateral_delta,
                    slippage_percent=gmx_params.get("slippage_percent", self.default_slippage),
                    execution_buffer=gmx_params.get("execution_buffer", self.execution_buffer),
                    auto_cancel=gmx_params.get("auto_cancel", False),
                )

            except ValueError as e:
                # Check if this is a "position not found" error (our code above)
                # or some other ValueError that should be re-raised
                error_msg = str(e)
                if "No long position found" in error_msg or "position was already closed" in error_msg:
                    # This is the race condition - position was already closed
                    logger.warning("Caught position-not-found error for %s: %s. Position likely closed by stop-loss. Returning synthetic 'closed' order.", symbol, error_msg)

                    # Get current mark price for cost calculation
                    try:
                        ticker = self.fetch_ticker(symbol)
                        mark_price = ticker.get("last") or ticker.get("close")
                    except Exception as ticker_error:
                        logger.warning("Failed to fetch ticker for synthetic order price: %s", ticker_error)
                        mark_price = None

                    # Return synthetic closed order
                    timestamp = self.milliseconds()
                    synthetic_order = {
                        "id": f"already_closed_{timestamp}",
                        "clientOrderId": None,
                        "timestamp": timestamp,
                        "datetime": self.iso8601(timestamp),
                        "lastTradeTimestamp": timestamp,
                        "symbol": symbol,
                        "type": type,
                        "side": side,
                        "price": mark_price,  # Current mark price (approximation)
                        "amount": amount,
                        "cost": amount * mark_price if mark_price else 0,  # Cost in stake currency = amount * price
                        "average": mark_price,  # Average fill price (approximation)
                        "filled": amount,
                        "remaining": 0.0,
                        "status": "closed",
                        "fee": {"cost": 0.0, "currency": "ETH", "rate": None},
                        "trades": [],
                        "info": {
                            "reason": "position_already_closed",
                            "message": error_msg,
                            "requested_close_size_usd": gmx_params.get("size_delta_usd"),
                        },
                    }
                    # Add synthetic order to cache for consistency
                    self._orders[synthetic_order["id"]] = synthetic_order
                    return synthetic_order
                else:
                    # Some other ValueError - re-raise it
                    raise

        # Gas estimation and logging (if gas monitoring enabled)
        gas_estimate = None
        native_price_usd = None
        if gas_config and gas_config.enabled and monitor:
            try:
                gas_estimate = monitor.estimate_transaction_gas(
                    tx=order_result.transaction,
                    from_addr=self.wallet.address,
                )
                monitor.log_gas_estimate(gas_estimate, "GMX order")
                native_price_usd = gas_estimate.native_price_usd
            except Exception as e:
                logger.warning("Gas estimation failed: %s - using order gas_limit", e)

        # Sign transaction (remove nonce if present, wallet will manage it)
        transaction = dict(order_result.transaction)
        if "nonce" in transaction:
            del transaction["nonce"]

        # Use estimated gas if available, otherwise use order's gas_limit
        if gas_estimate:
            transaction["gas"] = gas_estimate.gas_limit

        signed_tx = self.wallet.sign_transaction_with_new_nonce(transaction)

        # Submit to blockchain
        tx_hash_bytes = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(tx_hash_bytes)  # Use to_hex to include "0x" prefix

        # Wait for confirmation
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

        # Log actual gas usage (if gas monitoring enabled)
        if gas_config and gas_config.enabled and monitor:
            try:
                monitor.log_gas_usage(
                    receipt=dict(receipt),
                    native_price_usd=native_price_usd,
                    operation="GMX order",
                    estimated_gas=gas_estimate.gas_limit if gas_estimate else None,
                )
            except Exception as e:
                logger.warning("Failed to log gas usage: %s", e)

        # logger.info(
        #     "ORDER_TRACE: Transaction submitted tx_hash=%s, status=%s",
        #     tx_hash,
        #     receipt.get("status"),
        # )

        # Check if transaction reverted on-chain
        if receipt.get("status") == 0:
            logger.error(
                "Order creation transaction REVERTED on-chain: tx_hash=%s, block=%s",
                tx_hash,
                receipt.get("blockNumber"),
            )

            # Try to get revert reason using transaction replay
            # If that fails, use gas usage patterns for diagnostics
            revert_reason = "Transaction reverted on-chain"

            try:
                from eth_defi.revert_reason import fetch_transaction_revert_reason

                revert_reason = fetch_transaction_revert_reason(self.web3, tx_hash_bytes, unknown_error_message="<revert reason not available>")

                # Check if extraction failed - use gas diagnostics
                if "<revert reason not available>" in revert_reason:
                    logger.debug("Transaction replay could not extract revert reason, using gas diagnostics")

                    # Fetch transaction to get gas limit (already done below, but needed here for diagnostics)
                    try:
                        tx = self.web3.eth.get_transaction(tx_hash_bytes)
                        gas_limit = tx.get("gas", 0)
                    except Exception:
                        gas_limit = 0

                    gas_used = receipt.get("gasUsed", 0)

                    # Check for out of gas
                    if gas_limit > 0 and gas_used >= gas_limit:
                        revert_reason = f"Transaction ran out of gas (used {gas_used:,} / {gas_limit:,}). Increase gas limit or check for infinite loops."
                    # Check for low gas (used > 95% of limit)
                    elif gas_limit > 0 and (gas_used / gas_limit) > 0.95:
                        revert_reason = f"Transaction used {gas_used:,} of {gas_limit:,} gas ({gas_used * 100 // gas_limit}%). Likely insufficient gas."
                    # Check for very low gas usage (likely failed early)
                    elif gas_used < 50000:
                        revert_reason = f"Transaction failed early (used only {gas_used:,} gas). Likely invalid parameters or contract state."
                    else:
                        # Generic message with gas info
                        revert_reason = f"Transaction reverted (used {gas_used:,} of {gas_limit:,} gas). Revert reason could not be extracted."

                    logger.error(
                        "Could not extract revert reason via replay. Diagnostic: %s. Block: %s",
                        revert_reason,
                        receipt.get("blockNumber"),
                    )
                else:
                    # fetch_transaction_revert_reason succeeded
                    logger.error("Transaction revert reason: %s", revert_reason)

            except Exception as fetch_error:
                # fetch_transaction_revert_reason raised exception - use gas diagnostics
                logger.debug("Transaction replay failed: %s", fetch_error)

                # Fetch transaction to get gas limit
                try:
                    tx = self.web3.eth.get_transaction(tx_hash_bytes)
                    gas_limit = tx.get("gas", 0)
                except Exception:
                    gas_limit = 0

                gas_used = receipt.get("gasUsed", 0)

                if gas_limit > 0 and gas_used >= gas_limit:
                    revert_reason = f"Transaction ran out of gas (used {gas_used:,} / {gas_limit:,})."
                elif gas_limit > 0 and (gas_used / gas_limit) > 0.95:
                    revert_reason = f"Transaction likely ran out of gas (used {gas_used:,} of {gas_limit:,}, {gas_used * 100 // gas_limit}%)."
                elif gas_used < 50000:
                    revert_reason = f"Transaction failed early (used only {gas_used:,} gas). Likely invalid parameters."
                else:
                    revert_reason = f"Transaction reverted (gas: {gas_used:,} / {gas_limit:,}). Reason could not be extracted."

                logger.error("Transaction replay failed. Diagnostic: %s", revert_reason)

            # Increment consecutive failure counter
            self._consecutive_failures += 1
            # Store last failed tx hash for pause message
            self._last_failed_tx_hash = tx_hash

            # Get gas and balance info for detailed notification
            gas_used = receipt.get("gasUsed", 0)

            # Fetch transaction to get gas limit (receipts don't have gas limit, only gasUsed)
            try:
                tx = self.web3.eth.get_transaction(tx_hash_bytes)
                gas_limit = tx.get("gas", 0)
            except Exception as e:
                logger.warning("Could not fetch transaction for gas limit: %s", e)
                gas_limit = 0

            gas_left = gas_limit - gas_used if gas_limit > 0 else 0

            # Get current ETH balance
            try:
                eth_balance = self.web3.eth.get_balance(self.wallet.address)
                eth_balance_formatted = f"{eth_balance / 1e18:.6f} ETH (${eth_balance / 1e18 * 2000:.2f} @ $2000/ETH)"
            except Exception as e:
                eth_balance_formatted = f"<failed to fetch: {e}>"

            # Build error message for Freqtrade/Telegram notification
            # Use f-string to avoid % formatting issues with logging
            gas_pct = (gas_used * 100 / gas_limit) if gas_limit > 0 else 0
            separator = "=" * 80
            error_msg = f"\n{separator}\nCRITICAL: Order creation transaction REVERTED on-chain\n{separator}\nTransaction Hash: {tx_hash}\nBlock Number: {receipt.get('blockNumber')}\nRevert Reason: {revert_reason}\nGas Used: {gas_used:,} / {gas_limit:,} ({gas_pct:.1f}%)\nGas Left: {gas_left:,}\nWallet Balance: {eth_balance_formatted}\nConsecutive Failures: {self._consecutive_failures} / {self._max_consecutive_failures}\n{separator}"
            logger.error(error_msg)

            # Check if threshold reached - PAUSE TRADING
            if self._consecutive_failures >= self._max_consecutive_failures:
                self._trading_paused = True

                # Calculate gas cost in USD
                try:
                    eth_balance = self.web3.eth.get_balance(self.wallet.address)
                    eth_balance_eth = eth_balance / 1e18
                    eth_balance_usd = eth_balance_eth * 2000  # Estimate USD value
                    gas_cost_eth = (gas_used * 2) / 1e9  # Estimate at 2 gwei
                    gas_cost_usd = gas_cost_eth * 2000

                    # Build detailed pause reason with gas costs
                    if "out of gas" in revert_reason.lower() or gas_used >= gas_limit * 0.95:
                        self._trading_paused_reason = f"LOW GAS: {eth_balance_eth:.6f} ETH (${eth_balance_usd:.2f})! Last tx used {gas_used:,} / {gas_limit:,} gas (${gas_cost_usd:.2f}). Last failure: {revert_reason[:150]}"
                    else:
                        self._trading_paused_reason = f"Last tx: {gas_used:,} gas used (${gas_cost_usd:.2f}). Wallet: {eth_balance_eth:.6f} ETH (${eth_balance_usd:.2f}). Reason: {revert_reason[:150]}"
                except Exception as e:
                    # Fallback if gas calculation fails
                    self._trading_paused_reason = f"Reached {self._consecutive_failures} consecutive transaction failures. Last failure: {revert_reason[:200]}"

                # Critical alert for auto-pause
                pause_separator = "!" * 80
                pause_msg = f"\n{pause_separator}\nTRADING PAUSED - MANUAL INTERVENTION REQUIRED\n{pause_separator}\nReason: {self._consecutive_failures} consecutive transaction failures\n{self._trading_paused_reason}\nTo resume trading, call: gmx.reset_failure_counter()\n{pause_separator}"
                logger.error(pause_msg)

            # Return cancelled order - don't store as "open"
            # Match the pattern from commit 2e2a8757 for cancelled orders
            timestamp = self.milliseconds()
            failed_order = {
                "id": tx_hash,
                "clientOrderId": None,
                "timestamp": timestamp,
                "datetime": self.iso8601(timestamp),
                "lastTradeTimestamp": timestamp,
                "symbol": symbol,
                "type": type,
                "side": side,
                "price": None,
                "amount": amount,
                "cost": None,
                "average": None,
                "filled": 0.0,
                "remaining": amount,
                "status": "cancelled",  # Mark as cancelled, not open
                "fee": {
                    "cost": order_result.execution_fee / 1e18,
                    "currency": "ETH",
                    "rate": None,
                },
                "trades": [],
                "info": {
                    "tx_hash": tx_hash,
                    "creation_receipt": receipt,
                    "block_number": receipt.get("blockNumber"),
                    "gas_used": receipt.get("gasUsed"),
                    "revert_reason": revert_reason,
                    "event_name": "TransactionReverted",
                    "cancel_reason": f"Order creation transaction reverted: {revert_reason}",
                },
            }

            # Store in cache for consistency
            self._orders[tx_hash] = failed_order

            logger.info(
                "Order creation FAILED - returning cancelled order id=%s, reason=%s",
                tx_hash[:18],
                revert_reason[:100],
            )

            return failed_order

        # Transaction succeeded - reset consecutive failure counter
        if self._consecutive_failures > 0:
            logger.info(
                "Transaction succeeded - resetting consecutive failure counter (was %d)",
                self._consecutive_failures,
            )
            self._consecutive_failures = 0

        # Extract order_key from OrderCreated or OrderExecuted event for tracking
        try:
            order_key = extract_order_key_from_receipt(self.web3, receipt)
            # logger.info(
            #     "ORDER_TRACE: Extracted order_key=%s from receipt",
            #     order_key.hex()[:16] if order_key else "none",
            # )
        except ValueError as e:
            logger.warning("Could not extract order_key from receipt: %s", e)
            order_key = None

        # Check if order was immediately executed (single-phase market order)
        # GMX market orders execute atomically - OrderExecuted is in same receipt
        immediate_execution = extract_order_execution_result(self.web3, receipt, order_key)
        if immediate_execution and immediate_execution.status == "executed":
            # logger.info(
            #     "ORDER_TRACE: Order was immediately executed in same tx (single-phase), execution_price=%s, size_delta_usd=%s",
            #     immediate_execution.execution_price,
            #     immediate_execution.size_delta_usd,
            # )
            # Order is already executed - return with status="closed"
            order = self._format_order(
                symbol=symbol,
                order_type=order_type,
                side=side,
                amount=amount,
                price=price,
                tx_hash=tx_hash,
                receipt=receipt,
                order_key=order_key,
            )
            order["status"] = "closed"
            order["filled"] = order["amount"]
            order["remaining"] = 0.0

            # Set execution price if available
            # Convert using token-specific decimals (30 - token_decimals)
            if immediate_execution.execution_price:
                market = self.markets[symbol]
                order["average"] = self._convert_price_to_usd(immediate_execution.execution_price, market)

            self._orders[order["id"]] = order
            return order

        # Check if we should wait for keeper execution
        # For fork tests, Subsquid won't have the order data, so skip waiting
        wait_for_execution = params.get("wait_for_execution", True) if params else True

        # Wait for keeper execution before returning
        # GMX uses two-phase execution: order creation → keeper execution
        # We must verify the keeper result to avoid reporting phantom trades
        if order_key and wait_for_execution:
            order_key_hex = "0x" + order_key.hex()
            trade_action = None
            execution_price = None
            execution_tx_hash = None

            # Try Subsquid first (fast indexed query)
            # logger.info(
            #     "ORDER_TRACE: Waiting for keeper execution via Subsquid (order_key=%s)...",
            #     order_key_hex[:18],
            # )

            try:
                subsquid = GMXSubsquidClient(chain=self.config.get_chain())
                trade_action = subsquid.get_trade_action_by_order_key(
                    order_key_hex,
                    timeout_seconds=30,
                    poll_interval=0.5,
                )

                # Debug: Print full trade_action response
                # import json
                # print(f"DEBUG: trade_action response = {json.dumps(trade_action, indent=2, default=str)}")

                # if trade_action:
                # logger.debug(
                #     "ORDER_TRACE: Subsquid returned trade action: eventName=%s",
                #     trade_action.get("eventName"),
                # )

            except Exception as e:
                logger.debug(
                    "ORDER_TRACE: Subsquid query failed, falling back to EventEmitter logs: %s",
                    e,
                )

            # Fallback: Query EventEmitter logs directly if Subsquid failed
            if trade_action is None:
                logger.debug(
                    "ORDER_TRACE: Falling back to EventEmitter log search...",
                )

                addresses = get_contract_addresses(self.config.get_chain())
                event_emitter = addresses.eventemitter
                creation_block = receipt.get("blockNumber", 0)

                # Poll EventEmitter logs for up to 60 seconds using chunked scanning
                max_wait_seconds = 60
                poll_interval = 2
                start_time = time.time()

                while time.time() - start_time < max_wait_seconds:
                    try:
                        current_block = self.web3.eth.block_number

                        # Use chunked scanning to avoid RPC timeouts on large block ranges
                        trade_action = _scan_logs_chunked_for_trade_action(
                            self.web3,
                            event_emitter,
                            order_key,
                            order_key_hex,
                            creation_block,
                            current_block,
                        )

                        if trade_action:
                            logger.debug(
                                "ORDER_TRACE: Found %s event in EventEmitter logs",
                                trade_action.get("eventName"),
                            )
                            break

                    except Exception as e:
                        logger.debug("Error fetching EventEmitter logs: %s", e)

                    time.sleep(poll_interval)

            # Process the trade action result
            if trade_action is None:
                # Timeout - no execution event found
                logger.debug(
                    "ORDER_TRACE: Keeper execution timeout, order_key=%s",
                    order_key_hex[:18],
                )
                # Return with status "open" - let fetch_order() handle later
                order = self._parse_order_result_to_ccxt(
                    order_result,
                    symbol,
                    side,
                    type,
                    amount,
                    tx_hash,
                    receipt,
                    order_key=order_key,
                )
                return order

            # Check if order was cancelled or frozen
            event_name = trade_action.get("eventName", "")
            if event_name in ("OrderCancelled", "OrderFrozen"):
                error_reason = trade_action.get("reason") or f"Order {event_name.lower()}"
                logger.debug(
                    "ORDER_TRACE: Order %s by keeper - reason=%s",
                    event_name,
                    error_reason,
                )
                # Return cancelled order - don't raise exception
                # Freqtrade expects order dict, not exception
                timestamp = self.milliseconds()
                order = {
                    "id": tx_hash,
                    "clientOrderId": None,
                    "timestamp": timestamp,
                    "datetime": self.iso8601(timestamp),
                    "lastTradeTimestamp": timestamp,
                    "symbol": symbol,
                    "type": type,
                    "side": side,
                    "price": None,
                    "amount": amount,
                    "cost": None,
                    "average": None,
                    "filled": 0.0,
                    "remaining": amount,
                    "status": "cancelled",
                    "fee": {
                        "cost": order_result.execution_fee / 1e18,
                        "currency": "ETH",
                        "rate": None,
                    },
                    "trades": [],
                    "info": {
                        "tx_hash": tx_hash,
                        "creation_receipt": receipt,
                        "order_key": order_key.hex(),
                        "event_name": event_name,
                        "cancel_reason": error_reason,
                    },
                }

                # Store in cache
                self._orders[tx_hash] = order

                logger.debug(
                    "ORDER_TRACE: create_order() RETURNING cancelled order_id=%s, reason=%s",
                    tx_hash[:18],
                    error_reason,
                )

                return order

            # Order executed successfully
            # Parse execution price from Subsquid (30 decimals) or event
            # Convert using token-specific decimals (30 - token_decimals)
            raw_exec_price = trade_action.get("executionPrice")
            if raw_exec_price:
                market = self.markets[symbol]
                execution_price = self._convert_price_to_usd(float(raw_exec_price), market)
            else:
                # Use mark price as fallback
                execution_price = order_result.mark_price

            execution_tx_hash = trade_action.get("transaction", {}).get("hash")
            is_long = trade_action.get("isLong")

            logger.info(
                "ORDER_TRACE: create_order() - Order EXECUTED successfully - price=%s, size_usd=%s",
                execution_price or 0,
                float(trade_action.get("sizeDeltaUsd", 0)) / 1e30 if trade_action.get("sizeDeltaUsd") else 0,
            )

            timestamp = self.milliseconds()
            order = {
                "id": tx_hash,
                "clientOrderId": None,
                "timestamp": timestamp,
                "datetime": self.iso8601(timestamp),
                "lastTradeTimestamp": timestamp,
                "symbol": symbol,
                "type": type,
                "side": side,
                "price": execution_price,
                "amount": amount,
                "cost": (execution_price or 0) * amount if execution_price else None,
                "average": execution_price,
                "filled": amount,
                "remaining": 0.0,
                "status": "closed",
                "fee": {
                    "cost": order_result.execution_fee / 1e18,
                    "currency": "ETH",
                    "rate": None,
                },
                "trades": [],
                "info": {
                    "tx_hash": tx_hash,
                    "creation_receipt": receipt,
                    "execution_tx_hash": execution_tx_hash,
                    "order_key": order_key.hex(),
                    "execution_price": execution_price,
                    "is_long": is_long,
                    "event_name": event_name,
                    "pnl_usd": float(trade_action.get("pnlUsd", 0)) / 1e30 if trade_action.get("pnlUsd") else None,
                    "size_delta_usd": float(trade_action.get("sizeDeltaUsd", 0)) / 1e30 if trade_action.get("sizeDeltaUsd") else None,
                    "price_impact_usd": float(trade_action.get("priceImpactUsd", 0)) / 1e30 if trade_action.get("priceImpactUsd") else None,
                },
            }

            # Store in cache
            self._orders[tx_hash] = order

            logger.info(
                "ORDER_TRACE: create_order() RETURNING - order_id=%s, status=%s, filled=%.8f, remaining=%.8f",
                order.get("id")[:16] if order.get("id") else "None",
                order.get("status"),
                order.get("filled", 0),
                order.get("remaining", 0),
            )
            logger.info("=" * 80)

            return order

        # No order_key - fall back to legacy behaviour (return "open" status)
        order = self._parse_order_result_to_ccxt(
            order_result,
            symbol,
            side,
            type,
            amount,
            tx_hash,
            receipt,
            order_key=order_key,
        )

        logger.info(
            "ORDER_TRACE: create_order() RETURNING - order_id=%s, status=%s, filled=%.8f, remaining=%.8f, order_key=%s",
            order.get("id")[:16] if order.get("id") else "None",
            order.get("status"),
            order.get("filled", 0),
            order.get("remaining", 0),
            order.get("info", {}).get("order_key", "unknown")[:16] if order.get("info", {}).get("order_key") else "unknown",
        )
        logger.info("=" * 80)

        return order

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
        :param amount: Order size in base currency contracts (e.g., ETH for ETH/USD). Use params['size_usd'] for USD-based sizing.
        :type amount: float
        :param params: Additional parameters (see create_order). Use 'size_usd' for direct USD sizing.
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
        """Create a market sell order (close long position).

        Convenience wrapper around create_order() for market sell orders.

        :param symbol: Market symbol (e.g., 'ETH/USD')
        :type symbol: str
        :param amount: Order size in base currency contracts (e.g., ETH for ETH/USD). Use params['size_usd'] for USD-based sizing.
        :type amount: float
        :param params: Additional parameters (see create_order). Use 'size_usd' for direct USD sizing.
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
        """Create a limit order that triggers at specified price.

        Creates a GMX LIMIT_INCREASE order that remains pending until the market
        price reaches the trigger price. Unlike market orders which execute
        immediately, limit orders allow you to enter positions at specific price levels.

        **Example:**

        .. code-block:: python

            # Limit long order - buy ETH if price drops to $3000
            order = gmx.create_limit_order(
                "ETH/USD",
                "buy",
                0,  # Ignored when size_usd is provided
                3000.0,  # Trigger price
                params={
                    "size_usd": 1000,
                    "leverage": 2.5,
                    "collateral_symbol": "ETH",
                },
            )

            # Limit short order - short ETH if price rises to $4000
            order = gmx.create_limit_order(
                "ETH/USD",
                "sell",
                0,
                4000.0,
                params={
                    "size_usd": 1000,
                    "leverage": 2.0,
                    "collateral_symbol": "USDC",
                },
            )

        :param symbol: Market symbol (e.g., 'ETH/USD')
        :type symbol: str
        :param side: Order side ('buy' for long, 'sell' for short)
        :type side: str
        :param amount: Order size in base currency (or 0 if using size_usd in params)
        :type amount: float
        :param price: Trigger price at which the order executes (USD)
        :type price: float
        :param params: Additional parameters (see create_order)
        :type params: dict | None
        :return: CCXT-compatible order structure with transaction hash
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

    def reset_failure_counter(self):
        """Reset consecutive failure counter and resume trading.

        Call this method manually after investigating and resolving the cause
        of consecutive transaction failures. This will:

        - Reset the consecutive failure counter to 0
        - Resume trading if it was paused
        - Clear the pause reason

        :Example:

            .. code-block:: python

                # After fixing gas issues or other problems
                gmx.reset_failure_counter()

                # Trading will resume on next create_order() call

        .. warning::
            Only call this after investigating and resolving the root cause
            of the failures. Resetting without fixing the underlying issue
            may lead to more wasted gas.
        """
        was_paused = self._trading_paused
        failure_count = self._consecutive_failures

        self._consecutive_failures = 0
        self._trading_paused = False
        self._trading_paused_reason = None

        if was_paused:
            logger.info(
                "Trading RESUMED - failure counter reset (was %d failures, trading was paused)",
                failure_count,
            )
        else:
            logger.info(
                "Failure counter reset (was %d failures, trading was not paused)",
                failure_count,
            )

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
        For orders with status "open", this method checks the GMX DataStore
        and EventEmitter to determine if the keeper has executed the order.

        GMX uses a two-phase execution model:
        1. Order Creation - User submits, receives status "open"
        2. Keeper Execution - Keeper executes, status changes to "closed" or "cancelled"

        This method is called by Freqtrade to poll for order status updates.

        :param id: Order ID (transaction hash of order creation)
        :type id: str
        :param symbol: Symbol (not used, for CCXT compatibility)
        :type symbol: str | None
        :param params: Additional parameters (not used)
        :type params: dict | None
        :return: CCXT-compatible order structure
        :rtype: dict
        :raises OrderNotFound: If order with given ID doesn't exist
        """
        logger.debug(
            "ORDER_TRACE: fetch_order() CALLED - order_id=%s, symbol=%s",
            id if id else "None",
            symbol,
        )

        # Check if order exists in stored orders
        if id in self._orders:
            order = self._orders[id].copy()
            logger.info(
                "ORDER_TRACE: fetch_order(%s) - FOUND IN CACHE - status=%s, filled=%.8f, remaining=%.8f",
                id,
                order.get("status"),
                order.get("filled", 0),
                order.get("remaining", 0),
            )

            # Always check fresh status - do not cache order status
            # This ensures we detect order execution/cancellation promptly
            order_key_hex = order.get("info", {}).get("order_key")
            if not order_key_hex:
                logger.warning("fetch_order(%s): no order_key stored, cannot check execution status", id)
                return order

            order_key = bytes.fromhex(order_key_hex)

            # Get creation block for accurate log scanning (important after bot restart)
            creation_block = order.get("info", {}).get("block_number")

            # Check if order still pending in DataStore
            try:
                status_result = check_order_status(
                    self.web3,
                    order_key,
                    self.config.get_chain(),
                    creation_block=creation_block,
                )
            except Exception as e:
                logger.warning("fetch_order(%s): error checking order status: %s", id, e)
                return order

            if status_result.is_pending:
                # Still waiting for keeper execution
                logger.debug("fetch_order(%s): order still pending (waiting for keeper)", id)
                return order

            # Order no longer pending - verify execution result
            if status_result.execution_receipt:
                # Import here to avoid circular dependency
                from eth_defi.gmx.verification import verify_gmx_order_execution

                verification = verify_gmx_order_execution(
                    self.web3,
                    status_result.execution_receipt,
                    order_key,
                )

                if verification.success:
                    # Order executed successfully
                    order["status"] = "closed"
                    order["filled"] = order["amount"]
                    order["remaining"] = 0.0

                    # Convert raw execution_price using token-specific decimals
                    # verification.execution_price is in raw format (30 decimals)
                    symbol = order.get("symbol")
                    if symbol and verification.execution_price:
                        market = self.markets[symbol]
                        order["average"] = self._convert_price_to_usd(verification.execution_price, market)
                    else:
                        # Fallback: keep raw value if symbol not available (shouldn't happen)
                        order["average"] = verification.execution_price

                    order["lastTradeTimestamp"] = self.milliseconds()

                    # Calculate cost based on actual execution price
                    if order.get("average") and order["amount"]:
                        order["cost"] = order["amount"] * order["average"]

                    # Update fee: replace execution fee (ETH gas) with actual trading fee (USD)
                    # Store old execution fee
                    if order.get("fee"):
                        order["info"]["execution_fee_eth"] = order["fee"].get("cost")

                    # Get market and position direction for fee calculation
                    symbol = order.get("symbol", "")
                    market = self.markets.get(symbol) if symbol and self.markets_loaded else None
                    is_long = verification.is_long if verification.is_long is not None else True

                    # Extract actual fees from verification events
                    order["fee"] = self._extract_actual_fee(verification, market, is_long, verification.size_delta_usd)

                    # Add detailed fee breakdown
                    execution_fee_eth = order["info"].get("execution_fee_eth", 0.0)
                    order["info"]["fees_breakdown"] = self._build_fee_breakdown(verification, market, is_long, execution_fee_eth)

                    # Update info with verification data
                    order["info"]["execution_tx_hash"] = status_result.execution_tx_hash
                    order["info"]["execution_receipt"] = status_result.execution_receipt
                    order["info"]["execution_block"] = status_result.execution_block
                    order["info"]["verification"] = {
                        "execution_price": verification.execution_price,
                        "size_delta_usd": verification.size_delta_usd,
                        "pnl_usd": verification.pnl_usd,
                        "price_impact_usd": verification.price_impact_usd,
                        "event_count": verification.event_count,
                        "event_names": verification.event_names,
                        "is_long": verification.is_long,
                        "fees": {
                            "position_fee": verification.fees.position_fee,
                            "borrowing_fee": verification.fees.borrowing_fee,
                            "funding_fee": verification.fees.funding_fee,
                        }
                        if verification.fees
                        else None,
                    }

                    logger.info(
                        "fetch_order(%s): order EXECUTED at price=%s, size_usd=%s, trading_fee=%s USD (rate=%s), execution_fee=%s ETH",
                        id,
                        verification.execution_price or 0,
                        verification.size_delta_usd or 0,
                        order["fee"].get("cost", 0.0) if order.get("fee") else 0.0,
                        order["fee"].get("rate", 0.0) if order.get("fee") else 0.0,
                        order["info"].get("execution_fee_eth", 0.0),
                    )
                else:
                    # Order was cancelled or frozen
                    order["status"] = "cancelled"
                    order["filled"] = 0.0
                    order["remaining"] = order["amount"]

                    # Update info with cancellation details
                    order["info"]["execution_tx_hash"] = status_result.execution_tx_hash
                    order["info"]["execution_receipt"] = status_result.execution_receipt
                    order["info"]["execution_block"] = status_result.execution_block
                    order["info"]["cancellation_reason"] = verification.decoded_error or verification.reason
                    order["info"]["event_names"] = verification.event_names

                    logger.warning(
                        "fetch_order(%s): order CANCELLED - reason=%s, events=%s",
                        id,
                        verification.decoded_error or verification.reason,
                        verification.event_names,
                    )

                # Update cache with new status
                self._orders[id] = order

            else:
                # Order removed from DataStore but no execution receipt found
                # check_order_status() already logged detailed diagnostics (Subsquid + log scan)
                logger.warning(
                    "fetch_order(%s): order removed from DataStore but no execution event found (see check_order_status logs for details)",
                    id,
                )

            return order

        # Order not in cache - try to fetch from blockchain directly
        # This handles orders from previous sessions (e.g., after bot restart)
        # Follow GMX SDK flow: extract order_key → query execution status → return correct status
        logger.info(
            "ORDER_TRACE: fetch_order(%s) - NOT IN CACHE, fetching from blockchain (e.g., after bot restart)",
            id if id else "None",
        )
        normalized_id = id if id.startswith("0x") else f"0x{id}"

        if len(normalized_id) == 66:  # Valid tx hash length (0x + 64 hex chars)
            try:
                receipt = self.web3.eth.get_transaction_receipt(normalized_id)
                tx = self.web3.eth.get_transaction(normalized_id)

                tx_success = receipt.get("status") == 1
                if not tx_success:
                    # Transaction failed - return failed order
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                        "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                        "lastTradeTimestamp": None,
                        "status": "failed",
                        "symbol": symbol if symbol else None,
                        "type": "market",
                        "side": None,
                        "price": None,
                        "amount": None,
                        "filled": 0.0,
                        "remaining": None,
                        "cost": None,
                        "trades": [],
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                            "rate": None,
                        },
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                        },
                        "average": None,
                        "fees": [],
                    }
                    logger.info("fetch_order(%s): tx failed, status=failed", id)
                    return order

                # Transaction succeeded - extract order_key to verify execution
                try:
                    order_key = extract_order_key_from_receipt(self.web3, receipt)
                except ValueError as e:
                    logger.warning("fetch_order(%s): could not extract order_key: %s", id, e)
                    order_key = None

                if not order_key:
                    # No order_key - can't verify execution, assume still pending
                    logger.warning("fetch_order(%s): no order_key, returning status=open", id)
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                        "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                        "lastTradeTimestamp": None,
                        "status": "open",
                        "symbol": symbol if symbol else None,
                        "type": "market",
                        "side": None,
                        "price": None,
                        "amount": None,
                        "filled": None,
                        "remaining": None,
                        "cost": None,
                        "trades": [],
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                            "rate": None,
                        },
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                        },
                        "average": None,
                        "fees": [],
                    }
                    return order

                # Follow GMX SDK flow: Query TradeAction via GraphQL (Subsquid)
                order_key_hex = "0x" + order_key.hex()
                trade_action = None

                try:
                    subsquid = GMXSubsquidClient(chain=self.config.get_chain())
                    # Historical query - give Subsquid a few seconds to respond
                    trade_action = subsquid.get_trade_action_by_order_key(
                        order_key_hex,
                        timeout_seconds=5,
                        poll_interval=0.5,
                    )
                except Exception as e:
                    logger.info("fetch_order(%s): Subsquid query failed: %s", id[:16], e)

                # Fallback: Query EventEmitter logs if Subsquid failed
                if trade_action is None:
                    logger.info("fetch_order(%s): Falling back to EventEmitter logs", id[:16])

                    try:
                        addresses = get_contract_addresses(self.config.get_chain())
                        event_emitter = addresses.eventemitter
                        creation_block = receipt.get("blockNumber", 0)
                        current_block = self.web3.eth.block_number

                        # Use chunked scanning to avoid RPC timeouts on large block ranges
                        trade_action = _scan_logs_chunked_for_trade_action(
                            self.web3,
                            event_emitter,
                            order_key,
                            order_key_hex,
                            creation_block,
                            current_block,
                        )

                    except Exception as e:
                        logger.debug("fetch_order(%s): EventEmitter query failed: %s", id, e)

                # Process the trade action result
                if trade_action is None:
                    # No execution found - still pending or lost
                    logger.warning(
                        "ORDER_TRACE: fetch_order(%s) - NO EXECUTION FOUND (checked Subsquid + EventEmitter) - RETURNING status=open (might be lost/pending)",
                        id,
                    )
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                        "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                        "lastTradeTimestamp": None,
                        "status": "open",
                        "symbol": symbol if symbol else None,
                        "type": "market",
                        "side": None,
                        "price": None,
                        "amount": None,
                        "filled": None,
                        "remaining": None,
                        "cost": None,
                        "trades": [],
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                            "rate": None,
                        },
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                            "order_key": order_key_hex,
                        },
                        "average": None,
                        "fees": [],
                    }
                    return order

                # Log trade_action fields for diagnostics (exclude bulky transaction data)
                trade_action_fields = {k: v for k, v in trade_action.items() if k != "transaction"}
                logger.info(
                    "fetch_order(%s): trade_action fields: %s",
                    id[:16],
                    trade_action_fields,
                )

                # Derive CCXT side from orderType + isLong
                derived_side = _derive_side_from_trade_action(trade_action)

                # Check event type
                event_name = trade_action.get("eventName", "")

                if event_name in ("OrderCancelled", "OrderFrozen"):
                    # Order cancelled/frozen
                    error_reason = trade_action.get("reason") or f"Order {event_name.lower()}"
                    logger.info(
                        "ORDER_TRACE: fetch_order(%s) - Order CANCELLED/FROZEN - reason=%s - RETURNING status=cancelled",
                        id,
                        error_reason,
                    )

                    timestamp = self.milliseconds()
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "timestamp": timestamp,
                        "datetime": self.iso8601(timestamp),
                        "lastTradeTimestamp": timestamp,
                        "symbol": symbol,
                        "type": "market",
                        "side": derived_side,  # Derived from orderType + isLong
                        "price": None,
                        "amount": None,
                        "cost": None,
                        "average": None,
                        "filled": 0.0,
                        "remaining": None,
                        "status": "cancelled",
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                            "rate": None,
                        },
                        "trades": [],
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                            "order_key": order_key_hex,
                            "event_name": event_name,
                            "cancel_reason": error_reason,
                        },
                    }
                    return order

                # Order executed successfully (OrderExecuted event)
                raw_exec_price = trade_action.get("executionPrice")
                execution_price = None
                market = self.markets.get(symbol) if symbol else None
                if raw_exec_price and market:
                    execution_price = self._convert_price_to_usd(float(raw_exec_price), market)

                execution_tx_hash = trade_action.get("transaction", {}).get("hash")
                is_long = trade_action.get("isLong")

                # Compute trading fee from trade_action data (matches Path A design)
                # Gas cost stored separately in info, trading fee in USDC in fee dict
                gas_cost_eth = float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18
                size_delta_usd = float(trade_action.get("sizeDeltaUsd", 0)) / 1e30 if trade_action.get("sizeDeltaUsd") else 0.0

                # Sum all available fee components (in collateral token decimals)
                raw_position_fee = trade_action.get("positionFeeAmount")
                raw_borrowing_fee = trade_action.get("borrowingFeeAmount")
                raw_funding_fee = trade_action.get("fundingFeeAmount")
                total_fee_tokens = 0
                if raw_position_fee:
                    total_fee_tokens += int(float(raw_position_fee))
                if raw_borrowing_fee:
                    total_fee_tokens += int(float(raw_borrowing_fee))
                if raw_funding_fee:
                    total_fee_tokens += int(float(raw_funding_fee))

                logger.info(
                    "fetch_order(%s): blockchain fee components: position=%s, borrowing=%s, funding=%s, total_tokens=%s, is_long=%s",
                    id[:16],
                    raw_position_fee,
                    raw_borrowing_fee,
                    raw_funding_fee,
                    total_fee_tokens,
                    is_long,
                )

                # Extract collateral token data from trade_action (Subsquid/EventEmitter)
                ta_collateral_token = trade_action.get("collateralToken")
                ta_collateral_price_raw = trade_action.get("collateralTokenPriceMax")
                ta_collateral_price = int(float(ta_collateral_price_raw)) if ta_collateral_price_raw else None

                # If Subsquid didn't provide collateral data, enrich from on-chain events
                if ta_collateral_token is None and execution_tx_hash:
                    try:
                        exec_receipt = self.web3.eth.get_transaction_receipt(execution_tx_hash)
                        exec_result = extract_order_execution_result(self.web3, exec_receipt, order_key)
                        if exec_result:
                            ta_collateral_token = exec_result.collateral_token
                            ta_collateral_price = exec_result.collateral_token_price
                            logger.info(
                                "fetch_order(%s): enriched collateral from on-chain events: token=%s, price=%s",
                                id[:16],
                                ta_collateral_token,
                                ta_collateral_price,
                            )
                    except Exception as e:
                        logger.warning(
                            "fetch_order(%s): failed to fetch execution receipt for collateral enrichment: %s",
                            id[:16],
                            e,
                        )

                if total_fee_tokens > 0 and market:
                    # Actual fee from Subsquid/EventEmitter trade_action
                    fee_usd = self._convert_token_fee_to_usd(
                        total_fee_tokens,
                        market,
                        is_long,
                        collateral_token=ta_collateral_token,
                        collateral_token_price=ta_collateral_price,
                    )
                    actual_rate = fee_usd / size_delta_usd if size_delta_usd > 0 else 0.0
                    currency = self.safe_string(market, "settle", "USDC")
                    fee_dict = {"cost": fee_usd, "currency": currency, "rate": actual_rate}
                    logger.info(
                        "fetch_order(%s): blockchain fee -> $%s %s (rate=%s%%)",
                        id[:16],
                        fee_usd,
                        currency,
                        actual_rate * 100,
                    )
                elif size_delta_usd > 0 and symbol:
                    # Fallback: estimate fee at 0.06% when no fee data in trade_action
                    fee_dict = self._build_trading_fee(symbol, size_delta_usd)
                    logger.info(
                        "fetch_order(%s): no fee data in trade_action, using estimated fee: %s",
                        id[:16],
                        fee_dict,
                    )
                else:
                    # Last resort: gas cost only (no trading fee data available)
                    fee_dict = {"currency": "ETH", "cost": gas_cost_eth, "rate": None}
                    logger.info(
                        "fetch_order(%s): no fee data or size, gas-only fee: %s",
                        id[:16],
                        fee_dict,
                    )

                logger.info(
                    "ORDER_TRACE: fetch_order(%s) - Order EXECUTED at price=%s, size_usd=%s, derived_side=%s, orderType=%s, isLong=%s, fee=%s - RETURNING status=closed",
                    id[:16],
                    execution_price or 0,
                    size_delta_usd,
                    derived_side,
                    trade_action.get("orderType"),
                    trade_action.get("isLong"),
                    fee_dict,
                )

                timestamp = self.milliseconds()
                order = {
                    "id": id,
                    "clientOrderId": None,
                    "timestamp": timestamp,
                    "datetime": self.iso8601(timestamp),
                    "lastTradeTimestamp": timestamp,
                    "symbol": symbol,
                    "type": "market",
                    "side": derived_side,  # Derived from orderType + isLong
                    "price": execution_price,
                    "amount": None,
                    "cost": None,
                    "average": execution_price,
                    "filled": None,  # Unknown from tx alone
                    "remaining": 0.0,
                    "status": "closed",
                    "fee": fee_dict,
                    "trades": [],
                    "info": {
                        "creation_receipt": receipt,
                        "transaction": tx,
                        "execution_tx_hash": execution_tx_hash,
                        "order_key": order_key_hex,
                        "execution_price": execution_price,
                        "is_long": is_long,
                        "event_name": event_name,
                        "execution_fee_eth": gas_cost_eth,
                        "pnl_usd": float(trade_action.get("pnlUsd", 0)) / 1e30 if trade_action.get("pnlUsd") else None,
                        "size_delta_usd": size_delta_usd if size_delta_usd else None,
                        "price_impact_usd": float(trade_action.get("priceImpactUsd", 0)) / 1e30 if trade_action.get("priceImpactUsd") else None,
                    },
                }
                return order

            except Exception as e:
                logger.warning("Could not fetch transaction %s from blockchain: %s", id, e)
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
