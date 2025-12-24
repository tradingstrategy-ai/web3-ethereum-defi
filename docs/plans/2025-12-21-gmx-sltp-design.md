# GMX Stop Loss / Take Profit Implementation Design

## Overview

Add stop loss (SL) and take profit (TP) order support to the GMX Python SDK, aligned with the official TypeScript SDK patterns.

## Reference

- TypeScript SDK: https://github.com/gmx-io/gmx-interface/tree/release/sdk
- Key files analyzed:
  - `src/modules/orders/orders.ts` - createIncreaseOrder, createDecreaseOrder
  - `src/modules/orders/transactions/createDecreaseOrderTxn.ts` - decrease order encoding
  - `src/types/orders.ts` - OrderType enum
  - `src/types/trade.ts` - DecreasePositionAmounts
  - `src/types/sidecarOrders.ts` - SidecarSlTpOrderEntry

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Approach | Both bundled and standalone | Bundled for atomic open+SL+TP, standalone for existing positions |
| Trigger prices | Absolute and percentage | Traders think differently - some want $1900, others want 5% |
| Partial closes | Percentage and USD | Maximum flexibility for scaling out |
| Auto-cancel | Configurable, default True | Prevents orphaned orders |
| Code location | New `order/sltp_order.py` | Isolated logic, composes existing classes |
| CCXT integration | Later phase | Native SDK first |
| SL slippage | None (0 or MaxUint256) | Prioritize execution in fast moves |
| TP slippage | Applied | Protect profits |
| Fee handling | Auto-calculate with buffer | Sensible default, override for volatile gas |

## Order Types

```python
class OrderType(IntEnum):
    MARKET_SWAP = 0
    LIMIT_SWAP = 1
    MARKET_INCREASE = 2
    LIMIT_INCREASE = 3
    MARKET_DECREASE = 4
    LIMIT_DECREASE = 5        # Take Profit
    STOP_LOSS_DECREASE = 6    # Stop Loss
    LIQUIDATION = 7
```

**Trigger Threshold Logic:**
- Take Profit (LimitDecrease): Long triggers ABOVE, Short triggers BELOW
- Stop Loss (StopLossDecrease): Long triggers BELOW, Short triggers ABOVE

## Data Structures

```python
@dataclass
class DecreaseAmounts:
    """Core decrease order amounts - mirrors TypeScript DecreasePositionAmounts."""
    size_delta_usd: int                    # Position size to close (30 decimals)
    collateral_delta_amount: int = 0       # Collateral to withdraw
    trigger_price: int | None = None       # Price that activates order (30 decimals)
    acceptable_price: int = 0              # Worst acceptable execution price
    trigger_order_type: OrderType | None = None
    is_full_close: bool = False
    min_output_usd: int = 0
    decrease_swap_type: int = 0            # 0=NoSwap, 1=SwapPnlToCollateral, 2=SwapCollateralToPnl


@dataclass
class SLTPEntry:
    """User-friendly SL/TP configuration."""
    # Trigger - specify ONE
    trigger_price: float | None = None     # Absolute price
    trigger_percent: float | None = None   # Percent from entry (0.05 = 5%)

    # Close size - specify ONE (default: 100%)
    close_percent: float = 1.0             # Fraction of position
    close_size_usd: float | None = None    # Absolute USD amount

    auto_cancel: bool = True
    decrease_amounts: DecreaseAmounts | None = None  # Computed internally


@dataclass
class SLTPParams:
    """Combined SL/TP parameters for bundled creation."""
    stop_loss: SLTPEntry | None = None
    take_profit: SLTPEntry | None = None
    execution_fee_buffer: float = 1.0


@dataclass
class SLTPOrderResult:
    """Result from creating orders with SL/TP."""
    transaction: TxParams | None = None
    total_execution_fee: int = 0
    main_order_fee: int = 0
    stop_loss_fee: int = 0
    take_profit_fee: int = 0
    entry_price: float = 0.0
    stop_loss_trigger_price: float | None = None
    take_profit_trigger_price: float | None = None
    stop_loss_transaction: TxParams | None = None   # For standalone
    take_profit_transaction: TxParams | None = None # For standalone
```

## Class Structure

```python
class SLTPOrder(BaseOrder):
    """Stop Loss and Take Profit order management."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self._increase_order = IncreaseOrder(config, *args, **kwargs)
        self._decrease_order = DecreaseOrder(config, *args, **kwargs)

    # === Bundled Approach ===
    def create_increase_order_with_sltp(
        self,
        market_symbol: str,
        is_long: bool,
        size_delta_usd: float,
        collateral_amount: float,
        sltp_params: SLTPParams | None = None,
        collateral_token_symbol: str | None = None,
        slippage_percent: float = 0.003,
    ) -> SLTPOrderResult:
        """Open position + SL + TP in single atomic transaction."""
        ...

    # === Standalone Approach ===
    def create_stop_loss_order(
        self,
        market_symbol: str,
        is_long: bool,
        position_size_usd: float,
        entry: SLTPEntry,
        entry_price: float | None = None,
        slippage_percent: float = 0.003,
    ) -> OrderResult:
        """Create standalone stop loss for existing position."""
        ...

    def create_take_profit_order(
        self,
        market_symbol: str,
        is_long: bool,
        position_size_usd: float,
        entry: SLTPEntry,
        entry_price: float | None = None,
        slippage_percent: float = 0.003,
    ) -> OrderResult:
        """Create standalone take profit for existing position."""
        ...
```

## Price Calculations

```python
def get_trigger_threshold_type(order_type: OrderType, is_long: bool) -> str:
    """Determine if order triggers above or below price."""
    if order_type == OrderType.LIMIT_DECREASE:
        return ">" if is_long else "<"
    elif order_type == OrderType.STOP_LOSS_DECREASE:
        return "<" if is_long else ">"


def calculate_trigger_price(
    entry_price: float,
    trigger_percent: float,
    is_long: bool,
    order_type: OrderType,
) -> float:
    """Calculate absolute trigger price from percentage."""
    if order_type == OrderType.STOP_LOSS_DECREASE:
        return entry_price * (1 - trigger_percent) if is_long else entry_price * (1 + trigger_percent)
    else:  # Take profit
        return entry_price * (1 + trigger_percent) if is_long else entry_price * (1 - trigger_percent)


def calculate_acceptable_price(
    trigger_price: float,
    is_long: bool,
    order_type: OrderType,
    slippage_percent: float,
    index_token_decimals: int,
) -> int:
    """Calculate acceptable price for contract."""
    if order_type == OrderType.STOP_LOSS_DECREASE:
        # Prioritize execution
        return 0 if is_long else 2**256 - 1

    # Take profit: protect price
    if is_long:
        price = trigger_price * (1 - slippage_percent)
    else:
        price = trigger_price * (1 + slippage_percent)

    return int(price * PRECISION // (10 ** index_token_decimals))
```

## Multicall Bundling

Reuses existing BaseOrder pattern:
- `_send_wnt(amount)` - encoded sendWnt call
- `_send_tokens(token, amount)` - encoded sendTokens call
- `_create_order(arguments)` - encoded createOrder call
- `_build_transaction(multicall_args, value, gas)` - wraps with multicall

**Bundled structure:**
1. sendWnt (total: main_fee + sl_fee + tp_fee + collateral if native)
2. sendTokens (if ERC20 collateral)
3. createOrder (main increase)
4. createOrder (SL decrease) - if present
5. createOrder (TP decrease) - if present

## Files to Create/Modify

| File | Action |
|------|--------|
| `eth_defi/gmx/constants.py` | Add OrderType IntEnum |
| `eth_defi/gmx/order/sltp_order.py` | Create - main implementation |
| `eth_defi/gmx/order/__init__.py` | Export new classes |
| `scripts/gmx/gmx_sltp_order.py` | Create - usage examples |
| `tests/gmx/test_sltp_order.py` | Create - unit tests |

## Usage Examples

```python
from eth_defi.gmx.order.sltp_order import SLTPOrder, SLTPParams, SLTPEntry

sltp = SLTPOrder(config)

# Open long with 5% SL and 15% TP
result = sltp.create_increase_order_with_sltp(
    market_symbol="ETH",
    is_long=True,
    size_delta_usd=10000,
    collateral_amount=1.5,
    sltp_params=SLTPParams(
        stop_loss=SLTPEntry(trigger_percent=0.05),
        take_profit=SLTPEntry(trigger_percent=0.15),
    ),
)

# Open with absolute prices
result = sltp.create_increase_order_with_sltp(
    market_symbol="BTC",
    is_long=False,
    size_delta_usd=5000,
    collateral_amount=0.1,
    sltp_params=SLTPParams(
        stop_loss=SLTPEntry(trigger_price=45000),
        take_profit=SLTPEntry(trigger_price=38000),
    ),
)

# Partial close - scale out
result = sltp.create_increase_order_with_sltp(
    market_symbol="ETH",
    is_long=True,
    size_delta_usd=10000,
    collateral_amount=2.0,
    sltp_params=SLTPParams(
        stop_loss=SLTPEntry(trigger_percent=0.05, close_percent=1.0),
        take_profit=SLTPEntry(trigger_percent=0.10, close_percent=0.5),
    ),
)

# Add SL to existing position
sl_result = sltp.create_stop_loss_order(
    market_symbol="ETH",
    is_long=True,
    position_size_usd=10000,
    entry=SLTPEntry(trigger_price=1850),
    entry_price=2000,
)

# Add TP with USD-based close size
tp_result = sltp.create_take_profit_order(
    market_symbol="BTC",
    is_long=True,
    position_size_usd=50000,
    entry=SLTPEntry(trigger_price=75000, close_size_usd=25000),
)
```

## Implementation Phases

### Phase 1: Core Infrastructure
- Add OrderType IntEnum to constants.py
- Create sltp_order.py with data structures
- Implement price calculation functions

### Phase 2: Standalone Orders
- Implement create_stop_loss_order
- Implement create_take_profit_order
- Unit tests for standalone orders

### Phase 3: Bundled Orders
- Implement create_increase_order_with_sltp
- Multicall bundling logic
- Integration tests

### Phase 4: Examples and Documentation
- Create scripts/gmx/gmx_sltp_order.py
- Update module exports

### Future: CCXT Integration
- Add SL/TP to CCXT exchange interface
- Standard CCXT method signatures
