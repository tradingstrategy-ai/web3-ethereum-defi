"""Gains Network (gTrade) trading integration.

Minimal SDK for Gains Network leveraged trading. Returns unsigned transactions.

Example:
    ```python
    from eth_defi.gains.trading import GainsTrading, TradeParams

    # Initialize with web3
    trading = GainsTrading(web3)

    # Open a long position on BTC/USD
    params = TradeParams(
        pair_index=0,
        collateral_token="USDC",
        collateral_amount=Decimal("100"),
        is_long=True,
        leverage=10,
    )

    # Returns unsigned transaction
    result = trading.open_market_order(params, user_address="0x...")
    # Sign and send separately
    ```

References:
- https://gains-network.gitbook.io/docs-home/what-is-gains-network/contract-addresses
- https://gains-network.gitbook.io/docs-home/developer/integrators/trading-contracts
"""
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from web3 import Web3
from web3.contract import Contract
from eth_typing import ChecksumAddress
from web3.types import TxParams

from eth_defi.abi import get_deployed_contract
from eth_defi.chain import get_chain_name
from eth_defi.gains.constants import (
    COLLATERAL_TOKENS,
    GAINS_DIAMOND_ADDRESSES,
    to_contract_price,
    to_collateral_amount,
)

logger = logging.getLogger(__name__)





@dataclass
class TradeParams:
    """Parameters for opening a trade on Gains Network.

    :param pair_index: Index of the trading pair (0 for BTC/USD, 1 for ETH/USD, etc.)
    :param collateral_token: Collateral token symbol ('USDC', 'DAI', 'WETH')
    :param collateral_amount: Amount of collateral to use (in token decimals)
    :param is_long: True for long position, False for short
    :param leverage: Leverage multiplier (e.g., 10 for 10x)
    :param slippage_percent: Maximum acceptable slippage percentage (e.g., 0.5 for 0.5%)
    :param stop_loss_price: Optional stop loss price
    :param take_profit_price: Optional take profit price
    :param limit_price: For limit orders, the target execution price
    """
    pair_index: int
    collateral_token: str
    collateral_amount: Decimal
    is_long: bool
    leverage: int
    slippage_percent: float = 0.5
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    limit_price: Optional[Decimal] = None


@dataclass
class TradeResult:
    """Result of trade creation containing unsigned transaction.

    :param transaction: Unsigned transaction ready for signing
    :param gas_limit: Estimated gas limit
    :param max_slippage_p: Maximum slippage in basis points (e.g., 5 = 0.5%)
    """
    transaction: TxParams
    gas_limit: int
    max_slippage_p: int


@dataclass
class TradeInfo:
    """Information about an open trade.

    This corresponds to the Trade struct in GNSTradingStorage.sol
    """
    trader: ChecksumAddress
    pair_index: int
    index: int  # Trade index for this trader
    initial_pos_token: int  # Position size in USD (with 1e10 precision)
    pos_token: int  # Current position size
    open_price: int  # Opening price (with 1e10 precision)
    is_long: bool
    leverage: int
    tp: int  # Take profit price (with 1e10 precision)
    sl: int  # Stop loss price (with 1e10 precision)
    collateral_index: int  # Index of collateral token being used


class GainsTrading:
    """Gains Network trading interface.

    Returns unsigned transactions for all trading operations.
    """

    def __init__(self, web3: Web3, chain: Optional[str] = None):
        """Initialize Gains trading interface.

        :param web3: Web3 instance
        :param chain: Network name (auto-detected if not provided)
        """
        self.web3 = web3
        
        # Auto-detect chain from chain_id
        if chain is None:
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id)
        
        self.chain = chain
        
        # Get diamond contract address
        self.diamond_address = GAINS_DIAMOND_ADDRESSES.get(chain)
        if not self.diamond_address:
            raise ValueError(f"Gains Network not supported on chain: {chain}")
        
        # Load the diamond contract
        self.diamond_contract = self._load_diamond_contract()
        
        # Supported collateral tokens
        self.collateral_tokens = COLLATERAL_TOKENS.get(chain, {})

    def _load_diamond_contract(self) -> Contract:
        """Load the GNSMultiCollatDiamond contract."""
        return get_deployed_contract(
            self.web3,
            "gains/GNSMultiCollatDiamond.json",
            self.diamond_address,
        )

    def _get_collateral_index(self, collateral_symbol: str) -> int:
        """Get collateral index for token symbol by querying the contract."""
        if collateral_symbol not in self.collateral_tokens:
            raise ValueError(f"Unsupported collateral: {collateral_symbol}")
        
        collateral_address = self.collateral_tokens[collateral_symbol]
        
        # Query all collaterals from contract to find the index
        collaterals = self.diamond_contract.functions.getCollaterals().call()
        for index, collateral_info in enumerate(collaterals):
            if collateral_info[0].lower() == collateral_address.lower():
                return index
        
        raise ValueError(f"Collateral {collateral_symbol} ({collateral_address}) not found in contract")

    def _prepare_trade_input(
            self,
            params: TradeParams,
            user_address: ChecksumAddress,
            trade_type: int = 0,
    ) -> tuple:
        """Prepare trade input for contract.

        :param params: Trade parameters
        :param user_address: User's wallet address
        :param trade_type: 0=MARKET, 1=LIMIT
        :return: trade_struct tuple
        """
        collateral_index = self._get_collateral_index(params.collateral_token)

        # Convert collateral amount using helper function
        collateral_amount = to_collateral_amount(
            float(params.collateral_amount), params.collateral_token
        )

        # Convert prices using helper function
        tp_price = to_contract_price(float(params.take_profit_price)) if params.take_profit_price else 0
        sl_price = to_contract_price(float(params.stop_loss_price)) if params.stop_loss_price else 0

        # Trade struct (v10 format)
        # See: https://docs.gains.trade/developer/integrators/guides/v10-migration
        trade = (
            user_address,          # user: address
            0,                     # index: uint32 (assigned by contract)
            params.pair_index,     # pairIndex: uint16
            params.leverage,       # leverage: uint24
            params.is_long,        # long: bool
            True,                  # isOpen: bool (true for opening trade)
            collateral_index,      # collateralIndex: uint8
            trade_type,            # tradeType: uint8 (0=MARKET, 1=LIMIT)
            collateral_amount,     # collateralAmount: uint120
            0,                     # openPrice: uint64 (filled by oracle)
            tp_price,              # tp: uint64
            sl_price,              # sl: uint64
            False,                 # isCounterTrade: bool
            0,                     # positionSizeToken: uint160 (filled by contract)
            0,                     # __placeholder: uint24
        )

        return trade

    def open_market_order(
            self,
            params: TradeParams,
            user_address: ChecksumAddress,
            max_slippage_p: Optional[int] = None,
    ) -> TradeResult:
        """Open a market order.

        Returns unsigned transaction. Ensure collateral is approved first.

        :param params: Trade parameters
        :param user_address: User's wallet address
        :param max_slippage_p: Max slippage in basis points (e.g., 5 = 0.5%)
        :return: TradeResult with unsigned transaction
        """
        trade = self._prepare_trade_input(params, user_address, trade_type=0)
        
        if max_slippage_p is None:
            max_slippage_p = int(params.slippage_percent * 10)
        
        function_call = self.diamond_contract.functions.openTrade(
            trade,
            max_slippage_p,
            Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),  # referrer
        )
        
        gas_limit = 500_000
        tx = function_call.build_transaction({'from': user_address, 'gas': gas_limit})
        
        return TradeResult(
            transaction=tx,
            gas_limit=gas_limit,
            max_slippage_p=max_slippage_p,
        )

    def open_limit_order(
            self,
            params: TradeParams,
            user_address: ChecksumAddress,
    ) -> TradeResult:
        """Open a limit order.

        :param params: Trade parameters (must include limit_price)
        :param user_address: User's wallet address
        :return: TradeResult with unsigned transaction
        """
        if params.limit_price is None:
            raise ValueError("Limit price required for limit orders")
        
        trade = self._prepare_trade_input(params, user_address, trade_type=1)
        
        function_call = self.diamond_contract.functions.openTrade(
            trade,
            0,  # max_slippage_p (0 for limit orders)
            Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),  # referrer
        )
        
        gas_limit = 500_000
        tx = function_call.build_transaction({'from': user_address, 'gas': gas_limit})
        
        return TradeResult(
            transaction=tx,
            gas_limit=gas_limit,
            max_slippage_p=0,
        )

    def close_trade_market(
            self,
            pair_index: int,
            trade_index: int,
            user_address: ChecksumAddress,
    ) -> TxParams:
        """Close an open trade at market price.

        :param pair_index: Trading pair index
        :param trade_index: Trade index
        :param user_address: User's wallet address
        :return: Unsigned transaction
        """
        function_call = self.diamond_contract.functions.closeTradeMarket(
            pair_index,
            trade_index,
        )
        
        return function_call.build_transaction({
            'from': user_address,
            'gas': 400_000,
        })

    def get_open_trades(self, trader: ChecksumAddress) -> list[TradeInfo]:
        """Get all open trades for a trader.

        :param trader: Trader address
        :return: List of open trades
        """
        trades = self.diamond_contract.functions.getOpenTrades(trader).call()
        
        return [
            TradeInfo(
                trader=Web3.to_checksum_address(trade[0]),
                pair_index=trade[1],
                index=trade[2],
                initial_pos_token=trade[3],
                pos_token=trade[4],
                open_price=trade[5],
                is_long=trade[6],
                leverage=trade[7],
                tp=trade[8],
                sl=trade[9],
                collateral_index=trade[10],
            )
            for trade in trades
        ]

    def update_sl(
            self,
            pair_index: int,
            trade_index: int,
            new_sl_price: Decimal,
            user_address: ChecksumAddress,
    ) -> TxParams:
        """Update stop loss for an open trade.

        :param pair_index: Trading pair index
        :param trade_index: Trade index
        :param new_sl_price: New stop loss price
        :param user_address: User's wallet address
        :return: Unsigned transaction
        """
        new_sl = to_contract_price(float(new_sl_price))
        
        function_call = self.diamond_contract.functions.updateSl(
            pair_index,
            trade_index,
            new_sl,
        )
        
        return function_call.build_transaction({
            'from': user_address,
            'gas': 200_000,
        })

    def update_tp(
            self,
            pair_index: int,
            trade_index: int,
            new_tp_price: Decimal,
            user_address: ChecksumAddress,
    ) -> TxParams:
        """Update take profit for an open trade.

        :param pair_index: Trading pair index
        :param trade_index: Trade index
        :param new_tp_price: New take profit price
        :param user_address: User's wallet address
        :return: Unsigned transaction
        """
        new_tp = to_contract_price(float(new_tp_price))
        
        function_call = self.diamond_contract.functions.updateTp(
            pair_index,
            trade_index,
            new_tp,
        )
        
        return function_call.build_transaction({
            'from': user_address,
            'gas': 200_000,
        })
