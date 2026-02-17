"""
GMX Gas Monitoring Module.

This module provides comprehensive gas monitoring for GMX trading operations, including:
- Balance monitoring with configurable warning and critical thresholds
- Gas estimation with safety buffers
- USD cost calculation using GMX oracle prices
- Graceful failure handling for out-of-gas scenarios

Example usage:

.. code-block:: python

    from eth_defi.gmx.gas_monitor import GasMonitorConfig, GMXGasMonitor

    # Configure thresholds
    config = GasMonitorConfig(
        warning_threshold_usd=10.0,
        critical_threshold_usd=2.0,
        raise_on_critical=False,
    )

    # Create monitor
    monitor = GMXGasMonitor(web3, chain="arbitrum", config=config)

    # Check balance
    result = monitor.check_gas_balance(wallet_address)
    if result.status == "critical":
        print(f"Critical: {result.message}")
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ccxt.base.errors import InsufficientFunds as InsufficientFundsError
from eth_typing import HexAddress
from web3 import Web3
from web3.types import TxParams

from eth_defi.gmx.constants import (
    DEFAULT_GAS_CRITICAL_THRESHOLD_USD,
    DEFAULT_GAS_ESTIMATE_BUFFER,
    DEFAULT_GAS_MONITOR_ENABLED,
    DEFAULT_GAS_RAISE_ON_CRITICAL,
    DEFAULT_GAS_WARNING_THRESHOLD_USD,
    PRECISION,
)
from eth_defi.gmx.contracts import NETWORK_TOKENS

if TYPE_CHECKING:
    from eth_defi.gmx.order.base_order import OrderResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GasMonitorConfig:
    """Configuration for gas monitoring.

    :param warning_threshold_usd: Balance threshold (USD) below which a warning is logged
    :param critical_threshold_usd: Balance threshold (USD) below which trades may be rejected
    :param enabled: Whether gas monitoring is active
    :param raise_on_critical: If True, raise exception on critical; if False, return failed result
    :param gas_estimate_buffer: Multiplier applied to gas estimates (e.g., 1.2 = 20% buffer)
    """

    warning_threshold_usd: float = DEFAULT_GAS_WARNING_THRESHOLD_USD
    critical_threshold_usd: float = DEFAULT_GAS_CRITICAL_THRESHOLD_USD
    enabled: bool = DEFAULT_GAS_MONITOR_ENABLED
    raise_on_critical: bool = DEFAULT_GAS_RAISE_ON_CRITICAL
    gas_estimate_buffer: float = DEFAULT_GAS_ESTIMATE_BUFFER


@dataclass(slots=True)
class GasCheckResult:
    """Result of a gas balance check.

    :param passed: Whether the balance is sufficient for trading
    :param native_balance_wei: Raw balance in wei
    :param native_balance: Balance in native token units (ETH/AVAX)
    :param native_price_usd: Current price of native token in USD (None if unavailable)
    :param balance_usd: Balance value in USD (None if price unavailable)
    :param status: Status level - 'ok', 'warning', or 'critical'
    :param message: Human-readable status message
    """

    passed: bool
    native_balance_wei: int
    native_balance: Decimal
    native_price_usd: float | None
    balance_usd: float | None
    status: str  # 'ok', 'warning', 'critical'
    message: str


@dataclass(slots=True)
class GasEstimate:
    """Gas estimation result with cost breakdown.

    :param raw_gas_limit: Raw estimate from web3.eth.estimate_gas()
    :param gas_limit: Final estimate after buffer applied
    :param safety_buffer: Buffer multiplier that was applied (e.g., 1.2)
    :param gas_price_wei: Current gas price in wei
    :param estimated_cost_wei: Estimated total cost in wei
    :param estimated_cost_native: Estimated cost in native token units
    :param native_price_usd: Current price of native token in USD (None if unavailable)
    :param estimated_cost_usd: Estimated cost in USD (None if price unavailable)
    """

    raw_gas_limit: int
    gas_limit: int
    safety_buffer: float
    gas_price_wei: int
    estimated_cost_wei: int
    estimated_cost_native: Decimal
    native_price_usd: float | None
    estimated_cost_usd: float | None


@dataclass(slots=True)
class TradeExecutionResult:
    """Result of a trade execution attempt.

    Provides comprehensive information about trade outcome, including success/failure
    status, gas costs, and any error information. This allows graceful handling of
    failures without crashing.

    :param success: Whether the trade was executed successfully
    :param status: Status code - 'executed', 'failed', or 'rejected'
    :param reason: Failure reason if not successful - 'out_of_gas', 'reverted', 'critical_balance', etc.
    :param tx_hash: Transaction hash if submitted (None if rejected before submission)
    :param receipt: Transaction receipt if confirmed (None otherwise)
    :param order_result: The original OrderResult that was attempted
    :param gas_check: Pre-trade gas balance check result
    :param gas_used: Actual gas used (from receipt, None if not executed)
    :param gas_cost_native: Actual gas cost in native token (None if not executed)
    :param gas_cost_usd: Actual gas cost in USD (None if not executed or price unavailable)
    :param error_message: Detailed error message if failed
    """

    success: bool
    status: str  # 'executed', 'failed', 'rejected'
    reason: str | None
    tx_hash: str | None
    receipt: dict | None
    order_result: "OrderResult | None"
    gas_check: GasCheckResult | None
    gas_used: int | None
    gas_cost_native: Decimal | None
    gas_cost_usd: float | None
    error_message: str | None


class InsufficientGasError(InsufficientFundsError):
    """Raised when gas balance is critically low.

    Inherits from CCXT's ``InsufficientFunds`` so Freqtrade's built-in
    handler (``handle_insufficient_funds``) catches it automatically.
    """

    def __init__(self, message: str, gas_check: GasCheckResult):
        """Initialise with message and gas check result.

        :param message: Error message
        :param gas_check: The gas check result that triggered the error
        """
        super().__init__(message)
        self.gas_check = gas_check


class GMXGasMonitor:
    """Gas monitoring for GMX trading operations.

    Provides balance checking, gas estimation, and logging for GMX trades.
    Integrates with GMX oracle for USD price calculations.

    :param web3: Web3 instance connected to the blockchain
    :param chain: Chain name ('arbitrum', 'avalanche', 'arbitrum_sepolia')
    :param config: Optional gas monitor configuration
    """

    def __init__(
        self,
        web3: Web3,
        chain: str,
        config: GasMonitorConfig | None = None,
    ):
        """Initialise the gas monitor.

        :param web3: Web3 instance connected to the blockchain
        :param chain: Chain name (e.g., 'arbitrum', 'avalanche')
        :param config: Gas monitoring configuration (uses defaults if None)
        """
        self.web3 = web3
        self.chain = chain
        self.config = config or GasMonitorConfig()
        self._oracle_prices = None

        logger.debug(
            "Initialised GMXGasMonitor for %s (warning=$%.2f, critical=$%.2f)",
            chain,
            self.config.warning_threshold_usd,
            self.config.critical_threshold_usd,
        )

    @property
    def oracle_prices(self):
        """Lazy-load oracle prices client."""
        if self._oracle_prices is None:
            from eth_defi.gmx.core.oracle import OraclePrices

            self._oracle_prices = OraclePrices(self.chain)
        return self._oracle_prices

    def get_native_token_price_usd(self) -> float | None:
        """Fetch the native token (ETH/AVAX) price from GMX oracle.

        :return: Price in USD, or None if unavailable
        """
        try:
            # Get native token address from NETWORK_TOKENS
            # Use WETH for Arbitrum chains, WAVAX for Avalanche
            chain_tokens = NETWORK_TOKENS.get(self.chain, {})
            if self.chain in ("arbitrum", "arbitrum_sepolia"):
                native_address = chain_tokens.get("WETH") or chain_tokens.get("ETH")
            elif self.chain in ("avalanche", "avalanche_fuji"):
                native_address = chain_tokens.get("WAVAX") or chain_tokens.get("AVAX")
            else:
                native_address = chain_tokens.get("WETH") or chain_tokens.get("ETH")

            if not native_address:
                logger.warning("No native token address configured for chain: %s", self.chain)
                return None

            price_data = self.oracle_prices.get_price_for_token(native_address)
            if price_data is None:
                logger.warning("No oracle price found for native token on %s", self.chain)
                return None

            # GMX oracle returns prices with PRECISION decimal places adjusted for token decimals
            # Use minPriceFull and maxPriceFull (minPrice/maxPrice are often null in API responses)
            # Price format: price_usd * 10^(PRECISION - token_decimals)
            # Native tokens (WETH, WAVAX) have 18 decimals, so effective precision is 10^12
            min_price_raw = price_data.get("minPriceFull")
            max_price_raw = price_data.get("maxPriceFull")

            # Handle None values explicitly (dict.get default only applies if key missing)
            min_price = int(min_price_raw) if min_price_raw is not None else 0
            max_price = int(max_price_raw) if max_price_raw is not None else 0

            if min_price == 0 and max_price == 0:
                logger.warning("Oracle returned zero prices for native token (minPriceFull/maxPriceFull both zero or null)")
                return None

            mid_price = (min_price + max_price) / 2
            # Native tokens (WETH, WAVAX) have 18 decimals
            # Convert from GMX precision: price_usd = raw_price / 10^(PRECISION - 18)
            native_token_decimals = 18
            price_usd = float(mid_price) / (10 ** (PRECISION - native_token_decimals))

            logger.debug(
                "Native token price: $%.2f (min=$%.2f, max=$%.2f)",
                price_usd,
                min_price / (10 ** (PRECISION - native_token_decimals)),
                max_price / (10 ** (PRECISION - native_token_decimals)),
            )
            return price_usd

        except Exception as e:
            logger.warning("Failed to fetch native token price: %s", e)
            return None

    def check_gas_balance(self, wallet_address: HexAddress | str) -> GasCheckResult:
        """Check wallet gas balance against configured thresholds.

        :param wallet_address: Address to check balance for
        :return: GasCheckResult with status and balance information
        """
        # Get native balance
        balance_wei = self.web3.eth.get_balance(wallet_address)
        balance_native = Decimal(balance_wei) / Decimal(10**18)

        # Get USD price
        price_usd = self.get_native_token_price_usd()
        balance_usd = float(balance_native) * price_usd if price_usd else None

        # Determine status
        if balance_usd is not None:
            if balance_usd < self.config.critical_threshold_usd:
                status = "critical"
                passed = False
                message = f"Critical: {balance_native:.6f} native (~${balance_usd:.2f}) is below critical threshold (${self.config.critical_threshold_usd:.2f})"
            elif balance_usd < self.config.warning_threshold_usd:
                status = "warning"
                passed = True
                message = f"Warning: {balance_native:.6f} native (~${balance_usd:.2f}) is below warning threshold (${self.config.warning_threshold_usd:.2f})"
            else:
                status = "ok"
                passed = True
                message = f"Gas balance OK: {balance_native:.6f} native (~${balance_usd:.2f})"
        else:
            # Cannot determine USD value, use native balance heuristic
            # Assume 0.001 native token is roughly minimum for a transaction
            if balance_native < Decimal("0.0001"):
                status = "critical"
                passed = False
                message = f"Critical: {balance_native:.6f} native (USD price unavailable)"
            elif balance_native < Decimal("0.001"):
                status = "warning"
                passed = True
                message = f"Warning: {balance_native:.6f} native (USD price unavailable)"
            else:
                status = "ok"
                passed = True
                message = f"Gas balance: {balance_native:.6f} native (USD price unavailable)"

        return GasCheckResult(
            passed=passed,
            native_balance_wei=balance_wei,
            native_balance=balance_native,
            native_price_usd=price_usd,
            balance_usd=balance_usd,
            status=status,
            message=message,
        )

    def estimate_transaction_gas(
        self,
        tx: TxParams,
        from_addr: HexAddress | str,
    ) -> GasEstimate:
        """Estimate gas for a transaction with safety buffer.

        :param tx: Transaction parameters
        :param from_addr: Address that will send the transaction
        :return: GasEstimate with raw and buffered values
        """
        # Build transaction dict for estimation
        estimate_tx = dict(tx)
        estimate_tx["from"] = from_addr

        # Remove fields that might interfere with estimation
        for field in ["gas", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas", "nonce"]:
            estimate_tx.pop(field, None)

        # Get raw gas estimate
        raw_gas = self.web3.eth.estimate_gas(estimate_tx)

        # Apply safety buffer
        gas_limit = int(raw_gas * self.config.gas_estimate_buffer)

        # Get current gas price
        try:
            gas_price = self.web3.eth.gas_price
        except Exception:
            # Fallback to a reasonable default if gas price unavailable
            gas_price = self.web3.to_wei(0.1, "gwei")

        # Calculate costs
        estimated_cost_wei = gas_limit * gas_price
        estimated_cost_native = Decimal(estimated_cost_wei) / Decimal(10**18)

        # Get USD cost
        price_usd = self.get_native_token_price_usd()
        estimated_cost_usd = float(estimated_cost_native) * price_usd if price_usd else None

        return GasEstimate(
            raw_gas_limit=raw_gas,
            gas_limit=gas_limit,
            safety_buffer=self.config.gas_estimate_buffer,
            gas_price_wei=gas_price,
            estimated_cost_wei=estimated_cost_wei,
            estimated_cost_native=estimated_cost_native,
            native_price_usd=price_usd,
            estimated_cost_usd=estimated_cost_usd,
        )

    def log_gas_estimate(self, estimate: GasEstimate, operation: str) -> None:
        """Log gas estimate details for an operation.

        :param estimate: Gas estimate to log
        :param operation: Description of the operation (e.g., 'GMX order')
        """
        # Log estimate breakdown
        logger.info(
            "%s gas estimate: raw=%d, buffer=%.1fx, final=%d gas",
            operation,
            estimate.raw_gas_limit,
            estimate.safety_buffer,
            estimate.gas_limit,
        )

        # Log cost in native and USD
        gas_price_gwei = estimate.gas_price_wei / 10**9
        if estimate.estimated_cost_usd is not None:
            # Check if gas cost exceeds $1 and log appropriate warning
            if estimate.estimated_cost_usd > 1.0:
                logger.warning(
                    "%s gas cost HIGH: %d gas @ %.2f gwei = %.6f native (~$%.2f) - EXCEEDS $1 THRESHOLD",
                    operation,
                    estimate.gas_limit,
                    gas_price_gwei,
                    estimate.estimated_cost_native,
                    estimate.estimated_cost_usd,
                )
            else:
                logger.info(
                    "%s gas cost: %d gas @ %.2f gwei = %.6f native (~$%.2f)",
                    operation,
                    estimate.gas_limit,
                    gas_price_gwei,
                    estimate.estimated_cost_native,
                    estimate.estimated_cost_usd,
                )
        else:
            logger.info(
                "%s gas cost: %d gas @ %.2f gwei = %.6f native",
                operation,
                estimate.gas_limit,
                gas_price_gwei,
                estimate.estimated_cost_native,
            )

    def log_gas_usage(
        self,
        receipt: dict,
        native_price_usd: float | None,
        operation: str,
        estimated_gas: int | None = None,
    ) -> tuple[Decimal, float | None]:
        """Log actual gas used after transaction confirmation.

        :param receipt: Transaction receipt
        :param native_price_usd: Current native token price in USD
        :param operation: Description of the operation
        :param estimated_gas: Original gas estimate for efficiency calculation
        :return: Tuple of (gas_cost_native, gas_cost_usd)
        """
        gas_used = receipt.get("gasUsed", 0)
        effective_gas_price = receipt.get("effectiveGasPrice", 0)

        # Calculate actual cost
        gas_cost_wei = gas_used * effective_gas_price
        gas_cost_native = Decimal(gas_cost_wei) / Decimal(10**18)
        gas_cost_usd = float(gas_cost_native) * native_price_usd if native_price_usd else None

        # Calculate efficiency if estimate provided
        efficiency_str = ""
        if estimated_gas and estimated_gas > 0:
            efficiency = (gas_used / estimated_gas) * 100
            efficiency_str = f" ({efficiency:.0f}% of estimate)"

        # Log usage with warning if cost exceeds $1
        gas_price_gwei = effective_gas_price / 10**9
        if gas_cost_usd is not None:
            # Check if actual gas cost exceeds $1
            if gas_cost_usd > 1.0:
                logger.warning(
                    "%s gas used HIGH: %d gas%s @ %.2f gwei = %.6f native (~$%.2f) - EXCEEDS $1 THRESHOLD",
                    operation,
                    gas_used,
                    efficiency_str,
                    gas_price_gwei,
                    gas_cost_native,
                    gas_cost_usd,
                )
            else:
                logger.info(
                    "%s gas used: %d gas%s @ %.2f gwei = %.6f native (~$%.2f)",
                    operation,
                    gas_used,
                    efficiency_str,
                    gas_price_gwei,
                    gas_cost_native,
                    gas_cost_usd,
                )
        else:
            logger.info(
                "%s gas used: %d gas%s @ %.2f gwei = %.6f native",
                operation,
                gas_used,
                efficiency_str,
                gas_price_gwei,
                gas_cost_native,
            )

        return gas_cost_native, gas_cost_usd

    def log_gas_check_warning(self, gas_check: GasCheckResult) -> None:
        """Log a warning for low gas balance.

        :param gas_check: The gas check result to log
        """
        if gas_check.status == "warning":
            logger.warning(
                "Low gas balance: %.6f native (~$%.2f) is below warning threshold ($%.2f)",
                gas_check.native_balance,
                gas_check.balance_usd or 0,
                self.config.warning_threshold_usd,
            )
        elif gas_check.status == "critical":
            logger.error(
                "Critical gas balance: %.6f native (~$%.2f) is below critical threshold ($%.2f)",
                gas_check.native_balance,
                gas_check.balance_usd or 0,
                self.config.critical_threshold_usd,
            )


def create_gas_monitor_config_from_env() -> GasMonitorConfig:
    """Create GasMonitorConfig from environment variables.

    Environment variables:
    - GMX_GAS_WARNING_THRESHOLD_USD: Warning threshold in USD (default: 10.0)
    - GMX_GAS_CRITICAL_THRESHOLD_USD: Critical threshold in USD (default: 2.0)
    - GMX_GAS_MONITOR_ENABLED: Whether monitoring is enabled (default: true)
    - GMX_GAS_ESTIMATE_BUFFER: Gas estimate buffer multiplier (default: 1.2)
    - GMX_GAS_RAISE_ON_CRITICAL: Whether to raise on critical (default: true)

    :return: Configured GasMonitorConfig instance
    """
    import os

    def get_float(key: str, default: float) -> float:
        value = os.environ.get(key)
        return float(value) if value else default

    def get_bool(key: str, default: bool) -> bool:
        value = os.environ.get(key, "").lower()
        if value in ("true", "1", "yes"):
            return True
        elif value in ("false", "0", "no"):
            return False
        return default

    return GasMonitorConfig(
        warning_threshold_usd=get_float("GMX_GAS_WARNING_THRESHOLD_USD", DEFAULT_GAS_WARNING_THRESHOLD_USD),
        critical_threshold_usd=get_float("GMX_GAS_CRITICAL_THRESHOLD_USD", DEFAULT_GAS_CRITICAL_THRESHOLD_USD),
        enabled=get_bool("GMX_GAS_MONITOR_ENABLED", DEFAULT_GAS_MONITOR_ENABLED),
        gas_estimate_buffer=get_float("GMX_GAS_ESTIMATE_BUFFER", DEFAULT_GAS_ESTIMATE_BUFFER),
        raise_on_critical=get_bool("GMX_GAS_RAISE_ON_CRITICAL", DEFAULT_GAS_RAISE_ON_CRITICAL),
    )
