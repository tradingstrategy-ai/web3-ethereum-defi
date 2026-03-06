"""GMX exchange subclass for Freqtrade.

This module provides a Freqtrade-compatible exchange class for GMX protocol,
enabling GMX to be used as a trading backend in Freqtrade strategies.

GMX is a decentralized perpetual futures exchange running on Arbitrum and Avalanche.
It uses a unique liquidity pool model instead of traditional order books.

Key Features:
- Perpetual futures trading with up to 100x leverage
- Direct execution against liquidity pools (no order books)
- Immediate market order execution
- Pending limit orders: stop-loss and take-profit placed on exchange and cancellable
- Cross and isolated margin modes
- Funding fee mechanics for long/short positions
- Zero-price-impact trades within liquidity limits

Limitations:
- No spot trading (futures only)
- No traditional order book
- Trading requires Web3 wallet (not API keys)
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exceptions import InsufficientFundsError, OperationalException, TemporaryError
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import retrier
from freqtrade.exchange.exchange_types import CcxtOrder, FtHas

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import (
    _GAS_CRITICAL_MAX_RETRIES,
    _GAS_CRITICAL_PAUSE_SECS,
    _GAS_CRITICAL_WINDOW_SECS,
    WEI_PER_ETH,
)
from eth_defi.gmx.contracts import NETWORK_TOKENS, get_contract_addresses
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.freqtrade.telegram_utils import send_freqtrade_telegram_message
from eth_defi.gmx.lagoon.approvals import UNLIMITED, approve_gmx_collateral_via_vault
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.token import fetch_erc20_details
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)

#: Maximum age for an open GMX order before force-cancelling (milliseconds).
#: GMX market orders execute within seconds via keepers.
#: 10 minutes is generous — if no keeper event after this, something is wrong.
GMX_ORDER_MAX_AGE_MS = 10 * 60 * 1000


class Gmx(Exchange):
    """Freqtrade exchange class for GMX protocol.

    This class provides Freqtrade integration for GMX, a decentralized perpetual
    futures exchange. Since GMX is a DEX with unique characteristics, some
    Freqtrade features are not supported.

    Stop-Loss and Take-Profit Support
    ----------------------------------

    GMX supports both standard Freqtrade SL/TP patterns and advanced bundled orders:

    **Standard Pattern (Default)**

        Freqtrade creates orders separately:

        1. Entry order via ``create_order()`` - opens position
        2. Stop-loss via ``create_stoploss()`` - separate transaction after entry fills
        3. Take-profit via exit signals - bot-managed exits

        This works out of the box with standard Freqtrade strategies when
        ``stoploss_on_exchange=True`` is configured.

    **Advanced Pattern (Bundled Orders)**

        Custom strategies can pass ``stopLoss`` and ``takeProfit`` parameters to
        ``create_order()`` to create all 3 orders atomically in one transaction:

        - Main order (position entry)
        - Stop-loss order (if stopLoss provided)
        - Take-profit order (if takeProfit provided)

        Benefits: Lower gas costs, atomic execution, guaranteed SL/TP placement.

        Example custom strategy::

            def enter_long(self, pair, amount, leverage):
                return self.exchange.create_order(
                    pair=pair,
                    ordertype="market",
                    side="buy",
                    amount=amount,
                    leverage=leverage,
                    stopLoss={"triggerPercent": 0.05},  # 5% SL
                    takeProfit={"triggerPercent": 0.10},  # 10% TP
                )

    Configuration example
    ---------------------

    Basic configuration::

        {
            "exchange": {
                "name": "gmx",
                "rpc_url": "https://arb1.arbitrum.io/rpc",
                "private_key": "0x...",  # Web3 private key
                "ccxt_config": {},
                "ccxt_async_config": {},
                "pair_whitelist": ["ETH/USD", "BTC/USD"],
            },
            "stake_currency": "USD",
            "trading_mode": "futures",
            "margin_mode": "isolated",
            "order_types": {
                "entry": "market",
                "exit": "market",
                "stoploss": "market",
                "stoploss_on_exchange": True,  # Enable SL on exchange
            },
        }

    Lagoon vault configuration
    --------------------------

    To trade through a Lagoon vault, set ``ccxt_config.options.vaultAddress`` to the
    Lagoon ERC-4626 vault contract address.  Presence of this key enables Lagoon mode —
    all other config is inferred automatically.

    The ``privateKey`` is the asset manager's signing key.  All GMX positions are held
    by the vault's Gnosis Safe multisig.  The ``TradingStrategyModuleV0`` address is
    auto-discovered from the Safe's enabled Zodiac modules — no manual config needed::

        {
            "exchange": {
                "name": "gmx",
                "ccxt_config": {
                    "rpcUrl": "https://arb1.arbitrum.io/rpc",
                    "privateKey": "0x...",  # Asset manager private key
                    "options": {
                        "vaultAddress": "0x...",  # Lagoon vault contract (enables Lagoon mode)
                    },
                },
                "lagoon_forward_eth": true,  # Forward ETH for keeper fees (default: true)
                "lagoon_gas_buffer": 500000,  # Extra gas for performCall (default: 500000)
                "lagoon_auto_approve": true,  # Auto-approve collateral on startup (default: true)
            }
        }

    ``ccxt_config.options.vaultAddress`` (required for Lagoon mode)
        Address of the Lagoon ERC-4626 vault contract (not the Safe address).
        Presence of this key enables Lagoon vault mode.  The ``TradingStrategyModuleV0``
        address is discovered automatically from the Safe's enabled Zodiac modules.

    ``lagoon_forward_eth`` (default: ``true``)
        When ``true``, the asset manager forwards ETH with each ``performCall``
        transaction so the Safe receives keeper execution fees automatically.

    ``lagoon_gas_buffer`` (default: ``500000``)
        Additional gas added to each order transaction to cover ``performCall`` overhead.

    ``lagoon_auto_approve`` (default: ``true``)
        When ``true``, common collateral tokens (USDC, WETH) are automatically
        approved for the GMX SyntheticsRouter on startup.
    """

    # Feature flags for GMX futures
    _ft_has: FtHas = {
        # GMX is futures-only, no spot support
        "stoploss_on_exchange": True,  # GMX supports bundled SL/TP orders
        "order_time_in_force": ["GTC"],  # Only GTC (Good-Till-Cancel) - immediate execution
        "trades_pagination": None,  # No pagination support
        "trades_has_history": True,  # Can fetch historical trades
        "l2_limit_range": None,  # No order book
        "ohlcv_candle_limit": 10000,  # Max candles per request
        "ohlcv_has_history": True,  # Historical OHLCV available
        "mark_ohlcv_price": "index",  # Use index price for mark price
        "mark_ohlcv_timeframe": "1h",  # Default mark price timeframe
        "funding_fee_timeframe": "8h",  # Funding fees every 8 hours
        "ccxt_futures_name": "swap",  # CCXT market type
        "needs_trading_fees": True,  # Trading fees apply
        "order_props_in_contracts": ["amount", "cost", "filled", "remaining"],
        "ws_enabled": False,  # WebSocket not supported yet
    }

    _ft_has_futures: FtHas = {
        "funding_fee_candle_limit": 10000,  # Max funding fee candles
        "stoploss_order_types": {"market": "market"},  # GMX supports market stop-loss
        "order_time_in_force": ["GTC"],  # Only immediate execution
        "tickers_have_price": True,  # Tickers include bid/ask
        "floor_leverage": False,  # Leverage is not floored
        "stop_price_type_field": None,  # No stop price configuration
        "order_props_in_contracts": ["amount", "cost", "filled", "remaining"],
        "stop_price_type_value_mapping": {},  # No stop price types
    }

    # GMX only supports futures with cross/isolated margin
    _supported_trading_mode_margin_pairs: list[tuple[TradingMode, MarginMode]] = [
        (TradingMode.FUTURES, MarginMode.CROSS),
        (TradingMode.FUTURES, MarginMode.ISOLATED),
    ]

    def __init__(self, *args, **kwargs):
        """Initialise GMX exchange.

        When ``ccxt_config.options.vaultAddress`` is present the exchange is
        initialised in Lagoon vault mode.  All GMX transactions are routed
        through the vault's ``TradingStrategyModuleV0.performCall()`` with the
        ``privateKey`` used as the asset manager's signing key.

        :param args: Positional arguments passed to parent Exchange.
        :param kwargs: Keyword arguments passed to parent Exchange.
        """
        super().__init__(*args, **kwargs)

        # Tracks consecutive gas-critical order failures per pair.
        # Value: (attempt_count, window_start_timestamp, paused_until_timestamp)
        # paused_until=0.0 means not yet paused.  When paused_until > now, create_order
        # raises InsufficientFundsError immediately (no GMX call, no extra Telegram).
        self._gas_critical_attempts: dict[str, tuple[int, float, float]] = {}

        #: When in Lagoon mode, stores the asset manager's HotWallet for gas checks.
        #: The Safe holds positions but the asset manager pays gas.
        self._asset_manager_wallet: Optional[HotWallet] = None

        # Initialise Lagoon vault wallet when ccxt_config.options.vaultAddress is set.
        self._init_lagoon_wallet()

    # ------------------------------------------------------------------
    # Lagoon vault support
    # ------------------------------------------------------------------

    def _init_lagoon_wallet(self) -> None:
        """Initialise Lagoon vault wallet when ``ccxt_config.options.vaultAddress`` is configured.

        Called from ``__init__()`` after the parent Exchange class has constructed
        the GMX CCXT adapter (``self._api``) with a :class:`~eth_defi.hotwallet.HotWallet`.

        Only ``ccxt_config.options.vaultAddress`` and ``ccxt_config.privateKey`` are
        required.  The ``TradingStrategyModuleV0`` address is discovered automatically
        from the Safe's enabled Zodiac modules via :meth:`LagoonVault.fetch_info`.

        When Lagoon config is present, this method:

        1. Wraps the existing HotWallet in a :class:`~eth_defi.gmx.lagoon.wallet.LagoonGMXTradingWallet`.
        2. Replaces ``self._api.wallet`` and ``self._api.wallet_address`` so the Safe is the trading account.
        3. Rebuilds ``self._api.config`` and ``self._api.trader`` to target the Safe address.
        4. Optionally approves collateral tokens for the GMX SyntheticsRouter.

        No-op when ``ccxt_config.options.vaultAddress`` is absent.
        """
        exchange_config = self._config.get("exchange", {})
        ccxt_options = exchange_config.get("ccxt_config", {}).get("options", {})
        vault_address = ccxt_options.get("vaultAddress")

        if not vault_address:
            return

        forward_eth: bool = exchange_config.get("lagoon_forward_eth", True)
        gas_buffer: int = exchange_config.get("lagoon_gas_buffer", 500_000)
        auto_approve: bool = exchange_config.get("lagoon_auto_approve", True)

        gmx_api = self._api
        web3 = gmx_api.web3
        hot_wallet: HotWallet = gmx_api.wallet

        if hot_wallet is None:
            msg = "privateKey must be provided in ccxt_config when using a Lagoon vault. The private key is the asset manager's signing key."
            raise OperationalException(msg)

        # Preserve the original HotWallet for gas balance checks.
        # The Safe holds GMX positions; the asset manager pays gas.
        self._asset_manager_wallet = hot_wallet

        # Build LagoonVault — module address is discovered from the Safe below.
        chain_id = web3.eth.chain_id
        vault_spec = VaultSpec(chain_id, vault_address)
        vault = LagoonVault(web3, vault_spec)

        # Auto-discover TradingStrategyModuleV0 from the Safe's enabled Zodiac modules.
        vault_info = vault.fetch_info()
        modules = vault_info.get("modules", [])
        if not modules:
            msg = f"Lagoon vault {vault_address}: Safe has no Zodiac modules enabled. Deploy and enable TradingStrategyModuleV0 first."
            raise OperationalException(msg)
        if len(modules) > 1:
            logger.warning(
                "Lagoon vault Safe has %d enabled modules; using first (%s). Set tradingModuleAddress explicitly if this is wrong.",
                len(modules),
                modules[0],
            )
        trading_module_address = modules[0]
        vault.trading_strategy_module_address = trading_module_address

        # Wrap all GMX transactions through TradingStrategyModuleV0.performCall().
        lagoon_wallet = LagoonGMXTradingWallet(
            vault=vault,
            asset_manager=hot_wallet,
            gas_buffer=gas_buffer,
            forward_eth=forward_eth,
        )

        # The Safe address is the GMX trading account (holds collateral and positions).
        safe_address = vault.safe_address

        # Replace wallet on the CCXT adapter.
        gmx_api.wallet = lagoon_wallet
        gmx_api._wallet = lagoon_wallet
        gmx_api.wallet_address = safe_address

        # Rebuild GMXConfig and GMXTrading to target the Safe address.
        gmx_api.config = GMXConfig(web3, user_wallet_address=safe_address, wallet=lagoon_wallet)
        gmx_api.trader = GMXTrading(gmx_api.config, gas_monitor_config=gmx_api._gas_monitor_config)

        logger.info(
            "Lagoon vault mode enabled: vault=%s safe=%s module=%s",
            vault_address,
            safe_address,
            trading_module_address,
        )

        if auto_approve:
            self._approve_lagoon_collateral(vault, hot_wallet)

    def _approve_lagoon_collateral(self, vault: "LagoonVault", asset_manager: HotWallet) -> None:
        """Approve common GMX collateral tokens for the Safe via the vault module.

        Checks existing allowances and calls
        :func:`~eth_defi.gmx.lagoon.approvals.approve_gmx_collateral_via_vault`
        for any token that does not yet have an unlimited approval.

        :param vault:
            Lagoon vault whose Safe needs token approvals.

        :param asset_manager:
            HotWallet that signs the approval transactions via ``performCall``.
        """
        web3 = vault.web3
        chain_id = web3.eth.chain_id
        chain = get_chain_name(chain_id).lower()

        gmx_supported_chains = ("arbitrum", "arbitrum_sepolia", "avalanche")
        if chain not in gmx_supported_chains:
            logger.warning(
                "Cannot auto-approve collateral: unsupported chain %s (id %d). Approve tokens manually via approve_gmx_collateral_via_vault().",
                chain,
                chain_id,
            )
            return

        safe_address = vault.safe_address
        spender = get_contract_addresses(chain).syntheticsrouter

        exchange_config = self._config.get("exchange", {})
        collateral_addresses = exchange_config.get("lagoon_collateral_tokens")

        if collateral_addresses is None:
            chain_tokens = NETWORK_TOKENS.get(chain, {})
            collateral_addresses = [v for k, v in chain_tokens.items() if k in ("USDC", "WETH", "USDC.e")]

        for token_address in collateral_addresses:
            try:
                token = fetch_erc20_details(web3, token_address)
                allowance = token.contract.functions.allowance(safe_address, spender).call()
                if allowance > 0:
                    logger.debug(
                        "Collateral %s already approved for GMX router (allowance=%d). Skipping.",
                        token.symbol,
                        allowance,
                    )
                    continue
                logger.info("Auto-approving %s for GMX SyntheticsRouter via vault...", token.symbol)
                approve_gmx_collateral_via_vault(
                    vault=vault,
                    asset_manager=asset_manager,
                    collateral_token=token,
                    amount=UNLIMITED,
                )
                logger.info("Approved %s for GMX SyntheticsRouter.", token.symbol)
            except Exception as e:
                logger.warning("Failed to auto-approve collateral %s: %s", token_address, e)

    @property
    def is_lagoon_mode(self) -> bool:
        """Return ``True`` when the exchange is operating through a Lagoon vault.

        :return:
            ``True`` when ``ccxt_config.options.vaultAddress`` was configured and
            the Lagoon wallet has been successfully initialised.
        """
        return self._asset_manager_wallet is not None

    @property
    def lagoon_vault(self) -> Optional["LagoonVault"]:
        """Return the :class:`~eth_defi.erc_4626.vault_protocol.lagoon.vault.LagoonVault` instance.

        :return:
            ``LagoonVault`` when in Lagoon mode, ``None`` otherwise.
        """
        if isinstance(getattr(self._api, "wallet", None), LagoonGMXTradingWallet):
            return self._api.wallet.vault
        return None

    def fetch_order(self, order_id: str, pair: str, params: dict | None = None) -> CcxtOrder:
        """Fetch order with GMX-specific zombie detection and cancel reason logging.

        Extends the parent ``fetch_order()`` with two GMX-specific behaviours:

        **Zombie order detection:** GMX market orders execute within seconds via
        keepers. If an order is still "open" after :data:`GMX_ORDER_MAX_AGE_MS`
        (default 10 min) with no keeper event, the indexer missed it or something
        went wrong. Force-resolve as cancelled so freqtrade can retry.

        **Cancel reason logging:** When a keeper rejects an order (e.g.
        ``OrderNotFulfillableAtAcceptablePrice``), log the GMX-specific reason
        for easier debugging in freqtrade logs.
        """
        order = super().fetch_order(order_id, pair, params)
        info = order.get("info", {})

        # Zombie order detection: force-cancel orders stuck as "open" beyond max age
        if order.get("status") == "open" and order.get("timestamp") is not None:
            age_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - order["timestamp"]
            if age_ms > GMX_ORDER_MAX_AGE_MS:
                age_seconds = age_ms // 1000
                logger.warning(
                    "GMX zombie order detected: %s for %s has been open for %d seconds. Force-resolving as cancelled.",
                    order_id[:18],
                    pair,
                    age_seconds,
                )
                order["status"] = "cancelled"
                order["filled"] = 0.0
                order["remaining"] = order.get("amount")
                order.setdefault("info", {})
                info = order["info"]
                info["gmx_status"] = "zombie_cancelled"
                info["cancel_reason"] = f"Order open for {age_seconds}s without keeper execution"
                return order

        # Log GMX-specific cancel reasons for debugging
        if order.get("status") in ("cancelled", "canceled", "expired"):
            cancel_reason = info.get("cancellation_reason") or info.get("cancel_reason")
            if cancel_reason:
                logger.info(
                    "GMX order %s for %s was %s: %s",
                    order_id[:18],
                    pair,
                    info.get("gmx_status", order["status"]),
                    cancel_reason,
                )

        return order

    @property
    def _ccxt_config(self) -> dict:
        """Get CCXT configuration for GMX.

        :return: Configuration dict for CCXT initialization
        """
        config = {}
        if self.trading_mode == TradingMode.FUTURES:
            config.update(
                {
                    "options": {
                        "defaultType": "swap",  # Use perpetual swaps
                    }
                }
            )
        return config

    def validate_config(self, config):
        """Validate exchange configuration.

        GMX requires Web3 RPC URL and private key instead of API keys.

        :param config: Freqtrade configuration dict
        :raises OperationalException: If required config is missing or invalid
        """
        super().validate_config(config)

        exchange_config = config.get("exchange", {})

        # GMX requires RPC URL
        if "rpc_url" not in exchange_config and "rpcUrl" not in exchange_config.get("ccxt_config", {}):
            msg = "GMX exchange requires 'rpc_url' in exchange config or 'rpcUrl' in ccxt_config"
            raise OperationalException(msg)

        # Trading mode must be futures
        if self.trading_mode != TradingMode.FUTURES:
            raise OperationalException(f"GMX only supports futures trading mode, got: {self.trading_mode}")

        # Margin mode must be set
        if not self.margin_mode:
            msg = "GMX requires margin_mode to be set (isolated or cross)"
            raise OperationalException(msg)

        # Validate Lagoon vault config when present in ccxt_config.options.
        ccxt_options = exchange_config.get("ccxt_config", {}).get("options", {})
        vault_address = ccxt_options.get("vaultAddress")
        if vault_address:
            if not isinstance(vault_address, str) or not vault_address.startswith("0x"):
                msg = "ccxt_config.options.vaultAddress must be a valid Ethereum address starting with '0x'."
                raise OperationalException(msg)
            has_private_key = exchange_config.get("ccxt_config", {}).get("privateKey") or exchange_config.get("private_key")
            if not has_private_key:
                msg = "privateKey must be provided in ccxt_config when using a Lagoon vault. The private key is the asset manager's signing key."
                raise OperationalException(msg)

        # Validate timerange for backtesting
        if config.get("runmode") in ["backtest", "hyperopt"]:
            self._validate_backtest_timerange(config)

    def _validate_backtest_timerange(self, config: dict) -> None:
        """Validate that backtest timerange is within available historical data.

        This method checks if the requested timerange in backtesting falls within
        the available data range in cached feather files. Raises an error if data
        is insufficient, preventing wasted computation on invalid backtests.

        :param config: Freqtrade configuration dict containing timerange and pair_whitelist
        :raises InsufficientHistoricalDataError: If timerange exceeds available data
        :raises OperationalException: If data files cannot be read
        """
        # Extract timerange parameter
        timerange_str = config.get("timerange")
        if not timerange_str:
            # No timerange specified, use all available data
            return

        # Parse timerange string (format: "20250101-20251130" or "20250101-")
        timerange_parts = timerange_str.split("-")
        if len(timerange_parts) < 2:
            # Invalid format, let freqtrade handle it
            return

        # Convert start date to timestamp (ms)
        start_str = timerange_parts[0]
        try:
            requested_start = self._parse_timerange_date(start_str)
        except ValueError:
            # Invalid date format, let freqtrade handle it
            return

        # Get pairs and timeframe
        pairs = config.get("exchange", {}).get("pair_whitelist", [])
        timeframe = config.get("timeframe", "5m")

        # Get data directory
        user_data_dir = Path(config.get("user_data_dir", "user_data"))
        datadir_config = config.get("datadir")
        if datadir_config:
            datadir = Path(datadir_config)
        else:
            # Default: user_data/data/<exchange_name>
            datadir = user_data_dir / "data" / self.name

        # Validate each pair
        for pair in pairs:
            self._validate_pair_data(
                pair=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                datadir=datadir,
            )

    def _parse_timerange_date(self, date_str: str) -> int:
        """Parse freqtrade timerange date string to millisecond timestamp.

        :param date_str: Date string in format YYYYMMDD or YYYYMMDDHHMMSS
        :return: Unix timestamp in milliseconds
        :raises ValueError: If date_str format is invalid
        """
        # Parse different formats
        if len(date_str) == 8:  # YYYYMMDD
            dt = datetime.strptime(date_str, "%Y%m%d")
        elif len(date_str) == 14:  # YYYYMMDDHHMMSS
            dt = datetime.strptime(date_str, "%Y%m%d%H%M%S")
        else:
            raise ValueError(f"Invalid timerange date format: {date_str}")

        # Convert to UTC timestamp (ms)
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _validate_pair_data(
        self,
        pair: str,
        timeframe: str,
        requested_start: int,
        datadir: Path,
    ) -> None:
        """Validate single pair's data availability against requested timerange.

        Reads feather file metadata (date column only) and checks if available
        data range covers the requested start date. Validation is date-based,
        meaning any time on the requested date is acceptable.

        :param pair: Trading pair (e.g., "ETH/USDC:USDC")
        :param timeframe: Candle timeframe (e.g., "5m", "1h")
        :param requested_start: Requested start timestamp (ms)
        :param datadir: Path to data directory containing feather files
        :raises InsufficientHistoricalDataError: If data is insufficient
        :raises OperationalException: If feather file cannot be read
        """
        # Convert pair format: "ETH/USDC:USDC" -> "ETH_USDC_USDC"
        pair_filename = pair.replace("/", "_").replace(":", "_")

        # Construct feather file path
        candle_type = "futures"  # GMX only supports futures
        feather_file = datadir / candle_type / f"{pair_filename}-{timeframe}-{candle_type}.feather"

        # Check if file exists
        if not feather_file.exists():
            raise InsufficientHistoricalDataError(
                symbol=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                available_start=None,
                available_end=None,
                candles_received=0,
            )

        # Load feather file metadata (only date column)
        try:
            df = pd.read_feather(feather_file, columns=["date"])
        except Exception as e:
            raise OperationalException(f"Failed to read data file {feather_file}: {e}")

        if len(df) == 0:
            raise InsufficientHistoricalDataError(
                symbol=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                available_start=None,
                available_end=None,
                candles_received=0,
            )

        # Extract available date range
        available_start = int(df["date"].min().timestamp() * 1000)
        available_end = int(df["date"].max().timestamp() * 1000)

        # Compare dates (ignore time) for validation
        # This allows any time on the same date to be acceptable
        requested_date = datetime.fromtimestamp(requested_start / 1000, tz=timezone.utc).date()
        available_start_date = datetime.fromtimestamp(available_start / 1000, tz=timezone.utc).date()

        # Check if data starts on a later date
        if available_start_date > requested_date:
            raise InsufficientHistoricalDataError(
                symbol=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                available_start=available_start,
                available_end=available_end,
                candles_received=len(df),
            )

    def _get_params(
        self,
        side: str,
        ordertype: str,
        leverage: float,
        reduceOnly: bool,
        time_in_force: str = "GTC",
    ) -> dict:
        """Get parameters for order creation.

        :param side: Order side ('buy' or 'sell')
        :param ordertype: Order type ('market', 'limit', etc.)
        :param leverage: Leverage multiplier
        :param reduceOnly: Whether this is a reduce-only order
        :param time_in_force: Time in force (only 'GTC' supported)
        :return: Parameters dict for CCXT order creation
        """
        params = super()._get_params(
            side=side,
            ordertype=ordertype,
            leverage=leverage,
            reduceOnly=reduceOnly,
            time_in_force=time_in_force,
        )

        # GMX-specific parameters
        params["leverage"] = leverage

        return params

    def get_max_leverage(self, pair: str, stake_amount: float | None) -> float:
        """Get maximum leverage for a trading pair on GMX.

        GMX supports different leverage limits per market based on the
        minCollateralFactor. This is already loaded in the market info.

        :param pair: Trading pair symbol (e.g., "ETH/USD")
        :param stake_amount: Stake amount (not used for GMX as leverage is market-specific)
        :return: Maximum leverage as float (e.g., 50.0 for 50x)
        :raises OperationalException: If pair not found or leverage info unavailable
        """
        try:
            # Get market info from CCXT
            market = self.markets.get(pair)

            if not market:
                # If markets not loaded, return default
                logger.warning("Market %s not found, returning default leverage of 50x", pair)
                return 50.0

            # Get max leverage from market limits
            max_leverage = market.get("limits", {}).get("leverage", {}).get("max")

            if max_leverage and max_leverage > 0:
                return float(max_leverage)

            # Fallback to default GMX leverage
            logger.debug("No leverage limit found for %s, using default 50x", pair)
            return 50.0

        except Exception as e:
            logger.warning("Error getting max leverage for %s: %s, returning default 50x", pair, e)
            return 50.0

    def fetch_onchain_positions(self, use_graphql: bool = False) -> dict:
        """Fetch live GMX positions directly from the contracts (or Subsquid when enabled).

        This gives Freqtrade a second, on-chain source of truth to reconcile
        dashboard state after opens/closes. It mirrors the logic used by the
        CCXT adapter so you can verify that positions are really open/closed
        when the UI or logs look suspicious.
        """
        gmx = getattr(self, "_api", None)
        wallet = getattr(gmx, "wallet_address", None)

        if not gmx or not getattr(gmx, "config", None):
            msg = "GMX CCXT client is not initialized"
            raise OperationalException(msg)
        if not wallet:
            msg = "GMX wallet_address is missing; cannot fetch on-chain positions"
            raise OperationalException(msg)

        positions = GetOpenPositions(gmx.config, use_graphql=use_graphql).get_data(wallet)
        logger.info("Fetched %s on-chain GMX positions for wallet %s", len(positions), wallet)
        return positions

    @retrier(retries=0)
    def create_stoploss(
        self,
        pair: str,
        amount: float,
        stop_price: float,
        order_types: dict,
        side: str,
        leverage: float,
    ) -> dict:
        """Create a stop-loss order on GMX.

        GMX supports bundled stop-loss orders that are created atomically with positions.
        This method creates a standalone stop-loss order for existing positions.

        :param pair: Trading pair (e.g., "ETH/USDC:USDC")
        :param amount: Position size in base currency (e.g., BTC for BTC/USD, ETH for ETH/USD)
        :param stop_price: Stop-loss trigger price
        :param order_types: Freqtrade order type configuration
        :param side: Order side ("buy" for closing short, "sell" for closing long)
        :param leverage: Leverage multiplier
        :return: CCXT-compatible order structure
        :raises TemporaryError: If order creation fails temporarily
        :raises DDosProtection: If rate limit exceeded
        """
        logger.debug("*" * 80)
        logger.debug("*** GMX create_stoploss CALLED ***")
        logger.debug(
            "  pair=%s, amount=%.8f, stop_price=%.2f, side=%s, leverage=%.2f",
            pair,
            amount,
            stop_price,
            side,
            leverage,
        )
        logger.debug("  order_types=%s", order_types)
        logger.debug("*" * 80)

        try:
            # Guard: check for an existing pending SL order on the exchange before
            # creating a new one.  GMX allows only one SL per position; placing a
            # duplicate wastes ETH on the execution fee and the second tx is silently
            # ignored by the keeper.  This can happen when freqtrade previously
            # received status="closed" for the SL creation tx and mistakenly thought
            # the order was no longer live.
            try:
                pending = self._api.fetch_open_orders(
                    symbol=pair,
                    params={"pending_orders_only": True},
                )
                existing_sl = [o for o in pending if o.get("type") in ("stopLoss", "stop_loss") and o.get("side") == side]
                if existing_sl:
                    logger.warning(
                        "Stop-loss already exists on GMX for %s (side=%s, trigger=%.4f, id=%s) — skipping duplicate creation to avoid wasting ETH",
                        pair,
                        side,
                        existing_sl[0].get("price", 0),
                        existing_sl[0].get("id", "?"),
                    )
                    return existing_sl[0]
            except Exception as check_err:
                # Non-fatal: if the duplicate check fails, proceed with creation
                logger.warning("Could not check for existing SL orders on %s: %s — proceeding", pair, check_err)

            # Convert amount from base currency to USD
            # Freqtrade passes amount in base currency (BTC/ETH), but GMX expects USD
            ticker = self._api.fetch_ticker(pair)
            current_price = ticker["last"]
            amount_usd = amount * current_price

            logger.debug(
                ">>> Converting stop-loss amount for %s: %.8f (base currency) * %.2f (price) = %.2f USD",
                pair,
                amount,
                current_price,
                amount_usd,
            )

            # GMX uses standalone SL/TP order type
            params = {
                "leverage": leverage,
                "stopLossPrice": stop_price,
            }

            logger.debug("Creating standalone stop-loss order with params: %s", params)

            # Create standalone stop-loss order via CCXT
            order = self._api.create_order(
                symbol=pair,
                type="stop_loss",  # GMX-specific order type
                side=side,
                amount=amount_usd,
                params=params,
            )

            logger.debug("*" * 80)
            logger.debug(
                "✓ Created stop-loss order for %s: price=%.2f, amount=%.2f USD",
                pair,
                stop_price,
                amount_usd,
            )
            logger.debug("*" * 80)
            return order

        except Exception as e:
            logger.error("Failed to create stop-loss for %s: %s", pair, e)
            raise TemporaryError(f"GMX stop-loss creation failed: {e}")

    def stoploss_adjust(self, stop_loss: float, order: dict, side: str) -> bool:
        """Check if the exchange stop-loss order needs to be cancelled and recreated.

        Freqtrade calls this inside ``handle_trailing_stoploss_on_exchange`` to decide
        whether to cancel the current SL order and place a new one at ``stop_loss``.
        Returning ``True`` means "yes, please cancel and recreate".  Returning ``False``
        means "no change needed, keep the existing order as-is".

        GMX SL orders are immutable once created — to move the SL we must cancel the
        current order and submit a fresh one.  That is exactly what freqtrade does when
        this method returns ``True``.

        :param stop_loss:
            New (absolute) stoploss price, already rounded via ``price_to_precision``.
        :param order:
            Existing on-exchange stoploss order dict returned by ``fetch_stoploss_order``.
        :param side:
            ``"sell"`` for long positions, ``"buy"`` for short positions.
        :return:
            ``True`` if the current order's trigger price differs from ``stop_loss``
            in the direction that tightens the stop (up for longs, down for shorts).
        """
        existing_price = float(order.get("stopPrice") or order.get("price") or 0)
        if not existing_price:
            # No price information — assume update is needed to be safe.
            return True

        if side == "sell":
            # Long position: only move SL up (closer to current price).
            result = stop_loss > existing_price
        else:
            # Short position: only move SL down.
            result = stop_loss < existing_price

        logger.info(
            "stoploss_adjust: side=%s, new_sl=%.6f, existing_sl=%.6f → adjust=%s",
            side,
            stop_loss,
            existing_price,
            result,
        )
        return result

    def _send_gas_critical_telegram_alert(self, pair: str, attempt_count: int) -> None:
        """Send a Telegram alert when gas is critically low and exit orders are paused.

        Called just before raising ``InsufficientFundsError`` so the operator knows
        the bot has paused exit attempts.  Delegates delivery to
        :func:`~eth_defi.gmx.freqtrade.telegram_utils.send_freqtrade_telegram_message`;
        failure is non-fatal — the exception is raised regardless.

        :param pair: Trading pair that failed to exit.
        :param attempt_count: Number of consecutive failed attempts.
        """
        wallet_address = ""
        try:
            # In Lagoon mode the asset manager pays gas — use their address.
            if self._asset_manager_wallet is not None:
                wallet_address = self._asset_manager_wallet.address
            elif hasattr(self._api, "wallet") and self._api.wallet:
                wallet_address = self._api.wallet.address
        except Exception:
            pass

        bot_name = self._config.get("bot_name", "freqtrade")
        pause_mins = _GAS_CRITICAL_PAUSE_SECS // 60
        msg = f"⛽ *{bot_name} — Gas Critical*\n\nExit orders for `{pair}` are *paused for {pause_mins} minutes* after {attempt_count} consecutive gas failures.\n\nTop up wallet `{wallet_address}` with ETH on Arbitrum to resume trading.\nThe bot will automatically retry after the pause expires."

        sent = send_freqtrade_telegram_message(self._config, msg)
        if sent:
            logger.info("Gas critical Telegram alert sent for %s", pair)

    @retrier
    def create_order(
        self,
        *,
        pair: str,
        ordertype: str,
        side: str,
        amount: float,
        rate: float | None = None,
        leverage: float = 1.0,
        reduceOnly: bool = False,
        time_in_force: str = "GTC",
        initial_order: bool = True,
        **kwargs,
    ) -> dict:
        """Create order with optional bundled stop-loss and take-profit support.

        GMX supports two order creation patterns:

        **Standard Freqtrade Pattern (separate orders):**
            Used by default when no SL/TP parameters are provided. Freqtrade will:

            1. Call ``create_order()`` to open position (single order)
            2. Call ``create_stoploss()`` after entry fills (separate transaction)
            3. Call ``create_order()`` for exits/take-profit (separate transaction)

            This is the standard Freqtrade flow and works out of the box.

        **Advanced Pattern (bundled orders):**
            When ``stopLoss`` or ``takeProfit`` parameters are provided, GMX creates
            all orders atomically in a single transaction:

            - Main order (entry position)
            - Stop-loss order (if stopLoss provided)
            - Take-profit order (if takeProfit provided)

            This reduces gas costs and ensures atomic execution. Requires custom
            Freqtrade strategies to pass SL/TP parameters.

        Example (standard Freqtrade)::

            # Freqtrade calls this automatically - single entry order
            order = exchange.create_order(
                pair="ETH/USDC:USDC",
                ordertype="market",
                side="buy",
                amount=1000,  # USD
                leverage=3.0,
            )
            # Later, Freqtrade calls create_stoploss() separately

        Example (bundled orders - custom strategy)::

            # Custom strategy can pass SL/TP for bundled order
            order = exchange.create_order(
                pair="ETH/USDC:USDC",
                ordertype="market",
                side="buy",
                amount=1000,
                leverage=3.0,
                stopLoss={"triggerPrice": 1850.0},  # CCXT unified
                takeProfit={"triggerPrice": 2200.0},
            )
            # Creates 3 orders in 1 transaction

        Example (GMX percentage-based triggers)::

            order = exchange.create_order(
                pair="ETH/USDC:USDC",
                ordertype="market",
                side="buy",
                amount=1000,
                leverage=3.0,
                stopLoss={"triggerPercent": 0.05},  # 5% below entry
                takeProfit={"triggerPercent": 0.10},  # 10% above entry
            )

        :param pair: Trading pair (e.g., "ETH/USDC:USDC")
        :param ordertype: Order type ("market", "limit")
        :param side: Order side ("buy" for long, "sell" for short)
        :param amount: Order size in USD
        :param rate: Limit price (not used for GMX market orders)
        :param leverage: Leverage multiplier (1.0 to 100.0)
        :param reduceOnly: Whether this is a reduce-only order
        :param time_in_force: Time in force (only "GTC" supported by GMX)
        :param initial_order: Whether this is an initial order (True) or adjustment (False)
        :param **kwargs: Additional parameters. For bundled orders:

            - ``stopLoss``: Stop-loss configuration (dict or float)

              - Dict: ``{"triggerPrice": 1850.0}`` (CCXT unified)
              - Dict: ``{"triggerPercent": 0.05}`` (GMX extension, 5% below entry)
              - Float: ``1850.0`` (interpreted as triggerPrice)

            - ``takeProfit``: Take-profit configuration (dict or float)

              - Dict: ``{"triggerPrice": 2200.0}`` (CCXT unified)
              - Dict: ``{"triggerPercent": 0.10}`` (GMX extension, 10% above entry)
              - Float: ``2200.0`` (interpreted as triggerPrice)

            - ``stopLossPrice``: Alternative CCXT unified parameter (float)
            - ``takeProfitPrice``: Alternative CCXT unified parameter (float)
            - ``collateral_symbol``: Collateral token (e.g., "USDC")
            - ``slippage_percent``: Slippage tolerance (default: 0.003)

        :return: CCXT-compatible order structure with GMX-specific info
        :raises TemporaryError: If order creation fails temporarily
        :raises OperationalException: If parameters are invalid
        """
        # Enhanced logging with visual separators for workflow visibility
        logger.debug("=" * 80)
        logger.debug("*** GMX FREQTRADE create_order CALLED ***")
        logger.debug(
            "  pair=%s, ordertype=%s, side=%s, amount=%.8f, rate=%s, leverage=%.2f, reduceOnly=%s, time_in_force=%s, initial_order=%s",
            pair,
            ordertype,
            side,
            amount,
            rate,
            leverage,
            reduceOnly,
            time_in_force,
            initial_order,
        )
        if kwargs:
            logger.debug("  kwargs=%s", kwargs)
        logger.debug("=" * 80)

        # Pre-flight: skip immediately if this pair is in a gas-critical pause.
        # This prevents unnecessary GMX API calls (and keeper fee waste) while gas is low.
        _now = time.time()
        _gas_state = self._gas_critical_attempts.get(pair)
        if _gas_state is not None:
            _count, _win, _paused_until = _gas_state
            if _paused_until > _now:
                _remaining = int(_paused_until - _now)
                logger.debug(
                    "⛽ Gas pause active for %s — skipping exit order (%ds remaining)",
                    pair,
                    _remaining,
                )
                raise InsufficientFundsError(f"GMX gas critical: exit orders paused for {pair} ({_remaining}s remaining). Top up wallet ETH to resume.")
            elif _paused_until > 0.0:
                # Pause expired — clear state and log recovery
                logger.info(
                    "⛽ Gas pause expired for %s after %ds — resuming exit orders",
                    pair,
                    int(_GAS_CRITICAL_PAUSE_SECS),
                )
                self._gas_critical_attempts.pop(pair)

        # Check wallet ETH balance and warn if low (before creating order).
        # In Lagoon mode the asset manager pays gas, not the Safe.
        try:
            if hasattr(self._api, "web3") and hasattr(self._api, "wallet"):
                gas_payer = self._asset_manager_wallet if self._asset_manager_wallet is not None else self._api.wallet
                balance_wei = self._api.web3.eth.get_balance(gas_payer.address)
                balance_eth = balance_wei / WEI_PER_ETH

                # Warn if balance is low (< 0.01 ETH)
                if balance_eth < 0.01:
                    _gas_warn_msg = f"💰 GMX GAS WARNING: Low ETH balance {balance_eth:.6f} ETH. Minimum recommended: 0.01 ETH. Top up wallet {gas_payer.address} to avoid order failures."
                    logger.warning(_gas_warn_msg)
                    send_freqtrade_telegram_message(self._config, _gas_warn_msg)
        except Exception:
            # Silently ignore balance check failures (don't block order creation)
            pass

        # Call parent create_order which uses CCXT underneath
        # Note: initial_order is GMX-specific, don't pass to parent Exchange
        logger.debug(">>> Delegating to parent Exchange.create_order() -> GMX CCXT adapter")
        order = super().create_order(
            pair=pair,
            ordertype=ordertype,
            side=side,
            amount=amount,
            rate=rate,
            leverage=leverage,
            reduceOnly=reduceOnly,
            time_in_force=time_in_force,
            **kwargs,
        )

        # Detect "position already closed" synthetic orders from the CCXT adapter.
        # This happens when the bot tries to exit a position that no longer exists
        # on-chain. The CCXT adapter returns a synthetic closed order with
        # info["reason"] == "position_already_closed". The actual close reason
        # (stop-loss, liquidation, manual close) is not available at this layer.
        if order.get("info", {}).get("reason") == "position_already_closed":
            logger.warning(
                "GMX position for %s no longer exists on-chain. Returning synthetic closed order with exit_reason=%s",
                pair,
                order.get("info", {}).get("exit_reason", "sold_on_exchange"),
            )

        logger.debug("=" * 80)
        logger.debug("*** GMX CCXT adapter RETURNED order ***")
        logger.debug(
            "  id=%s, status=%s, filled=%.8f, remaining=%.8f",
            order.get("id"),
            order.get("status"),
            order.get("filled", 0),
            order.get("remaining", 0),
        )
        logger.debug("  cost=%.2f, average=%.4f", order.get("cost", 0), order.get("average", 0))
        # Log order info for debugging balance/profit issues
        order_info = order.get("info", {})
        if order_info:
            logger.debug("  FREQTRADE_ORDER_TRACE: info=%s", order_info)
        logger.debug("=" * 80)

        # --- Gas-critical retry throttle ---
        # When the CCXT layer returns a rejected order due to insufficient ETH gas
        # (status="rejected", info.error="insufficient_gas"), freqtrade would
        # normally continue past create_order, hit _notify_exit, and spam Telegram
        # with "Exiting …" on every bot loop.  After _GAS_CRITICAL_MAX_RETRIES
        # attempts we raise InsufficientFundsError instead, which freqtrade catches
        # at execute_trade_exit:2151 and returns False — no Telegram notification,
        # no DB entry, but the bot keeps running and will retry on the next loop
        # once gas is topped up.

        if order.get("status") == "rejected" and order.get("info", {}).get("error") == "insufficient_gas":
            # Re-sample time here; _now (pre-flight) was taken before super().create_order()
            # and may be several seconds stale after the GMX RPC round-trip.
            now = time.time()
            count, window_start, paused_until = self._gas_critical_attempts.get(pair, (0, now, 0.0))

            # Reset window if it expired
            if now - window_start > _GAS_CRITICAL_WINDOW_SECS:
                count = 0
                window_start = now

            count += 1
            self._gas_critical_attempts[pair] = (count, window_start, paused_until)

            if count == _GAS_CRITICAL_MAX_RETRIES:
                # First time hitting the threshold: send Telegram ONCE and start the pause.
                paused_until = now + _GAS_CRITICAL_PAUSE_SECS
                self._gas_critical_attempts[pair] = (count, window_start, paused_until)
                logger.error(
                    "🚫 Gas critical: %d consecutive failed attempts for %s. Pausing exit orders for %ds. Top up wallet ETH to resume trading.",
                    count,
                    pair,
                    _GAS_CRITICAL_PAUSE_SECS,
                )
                self._send_gas_critical_telegram_alert(pair, count)
                raise InsufficientFundsError(f"GMX gas critical after {count} attempts for {pair}. Exit orders paused for {_GAS_CRITICAL_PAUSE_SECS}s. Top up wallet ETH.")
            elif count > _GAS_CRITICAL_MAX_RETRIES:
                # Unreachable in steady-state: once count hits the threshold the pre-flight
                # guard intercepts all subsequent calls before reaching this post-order block.
                # Note: _gas_critical_attempts is in-memory and reset on bot restart, so a
                # freshly-restarted bot will re-accumulate from count=0 (up to 3 retries)
                # before pausing again — this branch is not reached in that case either.
                logger.debug(
                    "Gas still critical for %s (attempt %d, pause should be active)",
                    pair,
                    count,
                )
                raise InsufficientFundsError(f"GMX gas critical (attempt {count}) for {pair}. Pause is active.")
            else:
                logger.warning(
                    "⛽ Gas critical attempt %d/%d for %s — will pause exit orders after %d failures.",
                    count,
                    _GAS_CRITICAL_MAX_RETRIES,
                    pair,
                    _GAS_CRITICAL_MAX_RETRIES,
                )
        # Successful order — reset the gas failure counter for this pair
        elif pair in self._gas_critical_attempts:
            self._gas_critical_attempts.pop(pair)

        return order
