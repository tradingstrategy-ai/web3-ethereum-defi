from ccxt.base.errors import ExchangeError
from ccxt.base.errors import NetworkError
from ccxt.base.errors import NotSupported
from ccxt.base.errors import AuthenticationError
from ccxt.base.errors import DDoSProtection
from ccxt.base.errors import RequestTimeout
from ccxt.base.errors import ExchangeNotAvailable
from ccxt.base.errors import InvalidAddress
from ccxt.base.errors import InvalidOrder
from ccxt.base.errors import ArgumentsRequired
from ccxt.base.errors import BadSymbol
from ccxt.base.errors import NullResponse
from ccxt.base.errors import RateLimitExceeded
from ccxt.base.errors import OperationFailed
from ccxt.base.errors import BadRequest
from ccxt.base.errors import BadResponse
from ccxt.base.errors import InvalidProxySettings
from ccxt.base.errors import UnsubscribeError

from ccxt.base.decimal_to_precision import DECIMAL_PLACES, TICK_SIZE, NO_PADDING, TRUNCATE, ROUND, ROUND_UP, ROUND_DOWN, SIGNIFICANT_DIGITS


def describe_gmx() -> dict:
    """Return the CCXT exchange description for GMX DEX.

    See CCXT documentation for `Exchange.describe() <https://docs.ccxt.com/#/?id=exchange-class>`__
    """

    timeout = 30

    return {
        "id": "gmx",
        "name": "GMX",
        "countries": None,
        "enableRateLimit": True,
        "rateLimit": 2000,  # milliseconds = seconds * 1000
        "timeout": timeout * 1000,  # milliseconds = seconds * 1000
        "certified": False,  # if certified by the CCXT dev team
        "pro": False,  # if it is integrated with CCXT Pro for WebSocket support
        "alias": False,  # whether self exchange is an alias to another exchange
        "dex": False,
        "has": {
            "publicAPI": True,
            "privateAPI": True,
            "CORS": None,
            "sandbox": None,
            "spot": False,
            "margin": True,  # GMX uses cross margin
            "swap": True,  # GMX provides perpetual swaps
            "future": True,
            "option": False,
            "addMargin": False,  # Not yet
            "borrowCrossMargin": None,
            "borrowIsolatedMargin": None,
            "borrowMargin": None,
            "cancelAllOrders": False,
            "cancelAllOrdersWs": None,
            "cancelOrder": False,  # Requires contract integration
            "cancelOrderWithClientOrderId": None,
            "cancelOrderWs": None,
            "cancelOrders": None,
            "cancelOrdersWithClientOrderId": None,
            "cancelOrdersWs": None,
            "closeAllPositions": None,
            "closePosition": None,
            "createDepositAddress": None,
            "createLimitBuyOrder": False,
            "createLimitBuyOrderWs": None,
            "createLimitOrder": True,  #  (returns unsigned tx or auto-signs)
            "createLimitOrderWs": None,
            "createLimitSellOrder": False,
            "createLimitSellOrderWs": None,
            "createMarketBuyOrder": True,  #  (returns unsigned tx or auto-signs)
            "createMarketBuyOrderWs": None,
            "createMarketBuyOrderWithCost": None,
            "createMarketBuyOrderWithCostWs": None,
            "createMarketOrder": True,  #  (returns unsigned tx or auto-signs)
            "createMarketOrderWs": False,
            "createMarketOrderWithCost": None,
            "createMarketOrderWithCostWs": None,
            "createMarketSellOrder": True,  #  (returns unsigned tx or auto-signs)
            "createMarketSellOrderWs": None,
            "createMarketSellOrderWithCost": None,
            "createMarketSellOrderWithCostWs": None,
            "createOrder": True,  #  (returns unsigned tx or auto-signs)
            "createOrderWs": None,
            "createOrders": None,
            "createOrderWithTakeProfitAndStopLoss": None,
            "createOrderWithTakeProfitAndStopLossWs": None,
            "createPostOnlyOrder": None,
            "createPostOnlyOrderWs": None,
            "createReduceOnlyOrder": None,
            "createReduceOnlyOrderWs": None,
            "createStopLimitOrder": None,
            "createStopLimitOrderWs": None,
            "createStopLossOrder": None,
            "createStopLossOrderWs": None,
            "createStopMarketOrder": None,
            "createStopMarketOrderWs": None,
            "createStopOrder": None,
            "createStopOrderWs": None,
            "createTakeProfitOrder": None,
            "createTakeProfitOrderWs": None,
            "createTrailingAmountOrder": None,
            "createTrailingAmountOrderWs": None,
            "createTrailingPercentOrder": None,
            "createTrailingPercentOrderWs": None,
            "createTriggerOrder": None,
            "createTriggerOrderWs": None,
            "deposit": None,
            "editOrder": False,
            "editOrderWithClientOrderId": None,
            "editOrders": None,
            "editOrderWs": None,
            "fetchAccounts": None,
            "fetchBalance": True,
            "fetchBalanceWs": None,
            "fetchBidsAsks": None,
            "fetchBorrowInterest": None,
            "fetchBorrowRate": None,
            "fetchBorrowRateHistories": None,
            "fetchBorrowRateHistory": None,
            "fetchBorrowRates": None,
            "fetchBorrowRatesPerSymbol": None,
            "fetchCanceledAndClosedOrders": None,
            "fetchCanceledOrders": None,
            "fetchClosedOrder": None,
            "fetchClosedOrders": None,
            "fetchClosedOrdersWs": None,
            "fetchConvertCurrencies": None,
            "fetchConvertQuote": None,
            "fetchConvertTrade": None,
            "fetchConvertTradeHistory": None,
            "fetchCrossBorrowRate": None,
            "fetchCrossBorrowRates": None,
            "fetchCurrencies": True,
            "fetchCurrenciesWs": None,
            "fetchDeposit": None,
            "fetchDepositAddress": None,
            "fetchDepositAddresses": None,
            "fetchDepositAddressesByNetwork": None,
            "fetchDeposits": None,
            "fetchDepositsWithdrawals": None,
            "fetchDepositsWs": None,
            "fetchDepositWithdrawFee": None,
            "fetchDepositWithdrawFees": None,
            "fetchFundingHistory": True,  # Returns empty list (GMX doesn't track historical funding)
            "fetchFundingRate": True,  #
            "fetchFundingRateHistory": True,  #
            "fetchFundingInterval": None,
            "fetchFundingIntervals": None,
            "fetchFundingRates": None,
            "fetchGreeks": None,
            "fetchIndexOHLCV": None,
            "fetchIsolatedBorrowRate": None,
            "fetchIsolatedBorrowRates": None,
            "fetchMarginAdjustmentHistory": None,
            "fetchIsolatedPositions": None,
            "fetchL2OrderBook": False,  # GMX uses liquidity pools, not order books
            "fetchL3OrderBook": None,
            "fetchLastPrices": None,
            "fetchLedger": None,
            "fetchLedgerEntry": None,
            "fetchLeverage": True,
            "fetchLeverages": None,
            "fetchLeverageTiers": True,
            "fetchMarketLeverageTiers": True,
            "fetchLiquidations": None,
            "fetchLongShortRatio": None,
            "fetchLongShortRatioHistory": None,
            "fetchMarginMode": None,
            "fetchMarginModes": None,
            "fetchMarkets": True,
            "fetchMarketsWs": None,
            "fetchMarkOHLCV": None,
            "fetchMyLiquidations": None,
            "fetchMySettlementHistory": None,
            "fetchMyTrades": True,
            "fetchMyTradesWs": None,
            "fetchOHLCV": True,
            "fetchOHLCVWs": None,
            "fetchOpenInterest": True,
            "fetchOpenInterests": True,
            "fetchOpenInterestHistory": True,
            "fetchOpenOrder": None,
            "fetchOpenOrders": True,  #  (returns positions)
            "fetchOpenOrdersWs": None,
            "fetchOption": None,
            "fetchOptionChain": None,
            "fetchOrder": True,  # Enabled for backtesting (returns stub data)
            "fetchOrderWithClientOrderId": None,
            "fetchOrderBook": False,  # GMX uses liquidity pools, not order books
            "fetchOrderBooks": None,
            "fetchOrderBookWs": None,
            "fetchOrders": None,
            "fetchOrdersByStatus": None,
            "fetchOrdersWs": None,
            "fetchOrderTrades": None,
            "fetchOrderWs": None,
            "fetchPosition": None,
            "fetchPositionHistory": None,
            "fetchPositionsHistory": None,
            "fetchPositionWs": None,
            "fetchPositionMode": None,
            "fetchPositions": True,
            "fetchPositionsWs": None,
            "fetchPositionsForSymbol": None,
            "fetchPositionsForSymbolWs": None,
            "fetchPositionsRisk": None,
            "fetchPremiumIndexOHLCV": None,
            "fetchSettlementHistory": None,
            "fetchStatus": True,
            "fetchTicker": True,
            "fetchTickerWs": None,
            "fetchTickers": True,
            "fetchMarkPrices": None,
            "fetchTickersWs": None,
            "fetchTime": True,
            "fetchTrades": True,
            "fetchTradesWs": None,
            "fetchTradingFee": None,
            "fetchTradingFees": None,
            "fetchTradingFeesWs": None,
            "fetchTradingLimits": None,
            "fetchTransactionFee": None,
            "fetchTransactionFees": None,
            "fetchTransactions": None,
            "fetchTransfer": None,
            "fetchTransfers": None,
            "fetchUnderlyingAssets": None,
            "fetchVolatilityHistory": None,
            "fetchWithdrawAddresses": None,
            "fetchWithdrawal": None,
            "fetchWithdrawals": None,
            "fetchWithdrawalsWs": None,
            "fetchWithdrawalWhitelist": None,
            "reduceMargin": False,  # Not yet
            "repayCrossMargin": None,
            "repayIsolatedMargin": None,
            "setLeverage": True,
            "setMargin": None,
            "setMarginMode": False,  # GMX uses cross margin only
            "setPositionMode": None,
            "signIn": None,
            "transfer": None,
            "watchBalance": None,
            "watchMyTrades": None,
            "watchOHLCV": None,
            "watchOHLCVForSymbols": None,
            "watchOrderBook": None,
            "watchBidsAsks": None,
            "watchOrderBookForSymbols": None,
            "watchOrders": None,
            "watchOrdersForSymbols": None,
            "watchPosition": None,
            "watchPositions": None,
            "watchStatus": None,
            "watchTicker": None,
            "watchTickers": None,
            "watchTrades": None,
            "watchTradesForSymbols": None,
            "watchLiquidations": None,
            "watchLiquidationsForSymbols": None,
            "watchMyLiquidations": None,
            "unWatchOrders": None,
            "unWatchTrades": None,
            "unWatchTradesForSymbols": None,
            "unWatchOHLCVForSymbols": None,
            "unWatchOrderBookForSymbols": None,
            "unWatchPositions": None,
            "unWatchOrderBook": None,
            "unWatchTickers": None,
            "unWatchMyTrades": None,
            "unWatchTicker": None,
            "unWatchOHLCV": None,
            "watchMyLiquidationsForSymbols": None,
            "withdraw": None,
            "ws": None,
        },
        "urls": {
            "logo": None,
            "api": None,
            "www": None,
            "doc": None,
            "fees": None,
        },
        "api": None,
        "requiredCredentials": {
            "apiKey": True,
            "secret": True,
            "uid": False,
            "accountId": False,
            "login": False,
            "password": False,
            "twofa": False,  # 2-factor authentication(one-time password key)
            "privateKey": False,  # a "0x"-prefixed hexstring private key for a wallet
            "walletAddress": False,  # the wallet address "0x"-prefixed hexstring
            "token": False,  # reserved for HTTP auth in some cases
        },
        "markets": None,  # to be filled manually or by fetchMarkets
        "currencies": {},  # to be filled manually or by fetchMarkets
        "timeframes": None,  # redefine if the exchange has.fetchOHLCV
        "fees": {
            "trading": {
                "tierBased": None,
                "percentage": None,
                "taker": None,
                "maker": None,
            },
            "funding": {
                "tierBased": None,
                "percentage": None,
                "withdraw": {},
                "deposit": {},
            },
        },
        "status": {
            "status": "ok",
            "updated": None,
            "eta": None,
            "url": None,
        },
        "exceptions": None,
        "httpExceptions": {
            "422": ExchangeError,
            "418": DDoSProtection,
            "429": RateLimitExceeded,
            "404": ExchangeNotAvailable,
            "409": ExchangeNotAvailable,
            "410": ExchangeNotAvailable,
            "451": ExchangeNotAvailable,
            "500": ExchangeNotAvailable,
            "501": ExchangeNotAvailable,
            "502": ExchangeNotAvailable,
            "520": ExchangeNotAvailable,
            "521": ExchangeNotAvailable,
            "522": ExchangeNotAvailable,
            "525": ExchangeNotAvailable,
            "526": ExchangeNotAvailable,
            "400": ExchangeNotAvailable,
            "403": ExchangeNotAvailable,
            "405": ExchangeNotAvailable,
            "503": ExchangeNotAvailable,
            "530": ExchangeNotAvailable,
            "408": RequestTimeout,
            "504": RequestTimeout,
            "401": AuthenticationError,
            "407": AuthenticationError,
            "511": AuthenticationError,
        },
        "commonCurrencies": {
            "XBT": "BTC",
            "BCHSV": "BSV",
            "USD": "USDC",  # GMX uses USDC for settlement but commonly referred to as USD
        },
        "precisionMode": TICK_SIZE,
        "paddingMode": NO_PADDING,
        "limits": {
            "leverage": {"min": None, "max": None},
            "amount": {"min": None, "max": None},
            "price": {"min": None, "max": None},
            "cost": {"min": None, "max": None},
        },
        "features": {
            "spot": {
                "fetchOHLCV": {
                    "limit": 5000,
                },
            },
            "swap": {
                "linear": {
                    "fetchOHLCV": {
                        "limit": 5000,
                    },
                },
                "inverse": {},  # GMX doesn't have inverse contracts, but CCXT requires this key
            },
            "futures": {  # Freqtrade might look for "futures" instead of "swap"
                "linear": {
                    "fetchOHLCV": {
                        "limit": 5000,
                    },
                },
            },
        },
    }
