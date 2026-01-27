"""
Tests for GMX gas monitoring functionality.

This module tests the gas monitoring system including:
- GasMonitorConfig configuration
- GasCheckResult status levels
- GasEstimate calculations
- TradeExecutionResult data structures
- GMXGasMonitor class methods
- Environment variable configuration
"""

import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from eth_defi.gmx.gas_monitor import (
    GasCheckResult,
    GasEstimate,
    GasMonitorConfig,
    GMXGasMonitor,
    InsufficientGasError,
    TradeExecutionResult,
    create_gas_monitor_config_from_env,
)
from eth_defi.gmx.constants import (
    DEFAULT_GAS_CRITICAL_THRESHOLD_USD,
    DEFAULT_GAS_ESTIMATE_BUFFER,
    DEFAULT_GAS_MONITOR_ENABLED,
    DEFAULT_GAS_RAISE_ON_CRITICAL,
    DEFAULT_GAS_WARNING_THRESHOLD_USD,
)


# =============================================================================
# Unit tests for data structures (no RPC required)
# =============================================================================


def test_gas_monitor_config_defaults():
    """Test GasMonitorConfig uses correct defaults."""
    config = GasMonitorConfig()

    assert config.warning_threshold_usd == DEFAULT_GAS_WARNING_THRESHOLD_USD
    assert config.critical_threshold_usd == DEFAULT_GAS_CRITICAL_THRESHOLD_USD
    assert config.enabled == DEFAULT_GAS_MONITOR_ENABLED
    assert config.raise_on_critical == DEFAULT_GAS_RAISE_ON_CRITICAL
    assert config.gas_estimate_buffer == DEFAULT_GAS_ESTIMATE_BUFFER


def test_gas_monitor_config_custom_values():
    """Test GasMonitorConfig with custom values."""
    config = GasMonitorConfig(
        warning_threshold_usd=15.0,
        critical_threshold_usd=3.0,
        enabled=False,
        raise_on_critical=False,
        gas_estimate_buffer=1.5,
    )

    assert config.warning_threshold_usd == 15.0
    assert config.critical_threshold_usd == 3.0
    assert config.enabled is False
    assert config.raise_on_critical is False
    assert config.gas_estimate_buffer == 1.5


def test_gas_check_result_ok():
    """Test GasCheckResult with ok status."""
    result = GasCheckResult(
        passed=True,
        native_balance_wei=1000000000000000000,  # 1 ETH
        native_balance=Decimal("1.0"),
        native_price_usd=3500.0,
        balance_usd=3500.0,
        status="ok",
        message="Gas balance OK",
    )

    assert result.passed is True
    assert result.status == "ok"
    assert result.balance_usd == 3500.0


def test_gas_check_result_warning():
    """Test GasCheckResult with warning status."""
    result = GasCheckResult(
        passed=True,
        native_balance_wei=2500000000000000,  # 0.0025 ETH
        native_balance=Decimal("0.0025"),
        native_price_usd=3500.0,
        balance_usd=8.75,
        status="warning",
        message="Low gas balance: 0.0025 ETH (~$8.75) is below warning threshold ($10.00)",
    )

    assert result.passed is True  # Warning still passes
    assert result.status == "warning"
    assert result.balance_usd == 8.75


def test_gas_check_result_critical():
    """Test GasCheckResult with critical status."""
    result = GasCheckResult(
        passed=False,
        native_balance_wei=500000000000000,  # 0.0005 ETH
        native_balance=Decimal("0.0005"),
        native_price_usd=3500.0,
        balance_usd=1.75,
        status="critical",
        message="Critical: balance below critical threshold",
    )

    assert result.passed is False  # Critical does not pass
    assert result.status == "critical"
    assert result.balance_usd == 1.75


def test_gas_estimate_structure():
    """Test GasEstimate data structure."""
    estimate = GasEstimate(
        raw_gas_limit=375000,
        gas_limit=450000,  # After 1.2x buffer
        safety_buffer=1.2,
        gas_price_wei=80000000,  # 0.08 gwei
        estimated_cost_wei=36000000000000,  # 0.000036 ETH
        estimated_cost_native=Decimal("0.000036"),
        native_price_usd=3500.0,
        estimated_cost_usd=0.126,
    )

    assert estimate.raw_gas_limit == 375000
    assert estimate.gas_limit == 450000
    assert estimate.safety_buffer == 1.2
    assert estimate.gas_limit == int(estimate.raw_gas_limit * estimate.safety_buffer)


def test_trade_execution_result_success():
    """Test TradeExecutionResult for successful trade."""
    result = TradeExecutionResult(
        success=True,
        status="executed",
        reason=None,
        tx_hash="0x123abc",
        receipt={"status": 1, "gasUsed": 387234},
        order_result=None,
        gas_check=None,
        gas_used=387234,
        gas_cost_native=Decimal("0.000031"),
        gas_cost_usd=0.10,
        error_message=None,
    )

    assert result.success is True
    assert result.status == "executed"
    assert result.reason is None
    assert result.tx_hash == "0x123abc"


def test_trade_execution_result_failed_out_of_gas():
    """Test TradeExecutionResult for out-of-gas failure."""
    result = TradeExecutionResult(
        success=False,
        status="failed",
        reason="out_of_gas",
        tx_hash=None,
        receipt=None,
        order_result=None,
        gas_check=None,
        gas_used=None,
        gas_cost_native=None,
        gas_cost_usd=None,
        error_message="Insufficient funds for gas",
    )

    assert result.success is False
    assert result.status == "failed"
    assert result.reason == "out_of_gas"
    assert result.error_message == "Insufficient funds for gas"


def test_trade_execution_result_rejected_critical():
    """Test TradeExecutionResult for critical balance rejection."""
    gas_check = GasCheckResult(
        passed=False,
        native_balance_wei=100000000000000,
        native_balance=Decimal("0.0001"),
        native_price_usd=3500.0,
        balance_usd=0.35,
        status="critical",
        message="Critical balance",
    )

    result = TradeExecutionResult(
        success=False,
        status="rejected",
        reason="critical_balance",
        tx_hash=None,
        receipt=None,
        order_result=None,
        gas_check=gas_check,
        gas_used=None,
        gas_cost_native=None,
        gas_cost_usd=None,
        error_message="Critical balance",
    )

    assert result.success is False
    assert result.status == "rejected"
    assert result.reason == "critical_balance"
    assert result.gas_check is not None
    assert result.gas_check.status == "critical"


def test_insufficient_gas_error():
    """Test InsufficientGasError exception."""
    gas_check = GasCheckResult(
        passed=False,
        native_balance_wei=100000000000000,
        native_balance=Decimal("0.0001"),
        native_price_usd=3500.0,
        balance_usd=0.35,
        status="critical",
        message="Critical balance",
    )

    error = InsufficientGasError("Insufficient gas for trade", gas_check)

    assert str(error) == "Insufficient gas for trade"
    assert error.gas_check is gas_check
    assert error.gas_check.status == "critical"


def test_create_gas_monitor_config_from_env_defaults():
    """Test create_gas_monitor_config_from_env with no env vars set."""
    # Clear any existing env vars
    env_vars = [
        "GMX_GAS_WARNING_THRESHOLD_USD",
        "GMX_GAS_CRITICAL_THRESHOLD_USD",
        "GMX_GAS_MONITOR_ENABLED",
        "GMX_GAS_ESTIMATE_BUFFER",
        "GMX_GAS_RAISE_ON_CRITICAL",
    ]
    original_values = {}
    for var in env_vars:
        original_values[var] = os.environ.pop(var, None)

    try:
        config = create_gas_monitor_config_from_env()

        assert config.warning_threshold_usd == DEFAULT_GAS_WARNING_THRESHOLD_USD
        assert config.critical_threshold_usd == DEFAULT_GAS_CRITICAL_THRESHOLD_USD
        assert config.enabled == DEFAULT_GAS_MONITOR_ENABLED
        assert config.raise_on_critical == DEFAULT_GAS_RAISE_ON_CRITICAL
        assert config.gas_estimate_buffer == DEFAULT_GAS_ESTIMATE_BUFFER

    finally:
        # Restore original env vars
        for var, value in original_values.items():
            if value is not None:
                os.environ[var] = value


def test_create_gas_monitor_config_from_env_custom():
    """Test create_gas_monitor_config_from_env with custom env vars."""
    env_vars = {
        "GMX_GAS_WARNING_THRESHOLD_USD": "15.0",
        "GMX_GAS_CRITICAL_THRESHOLD_USD": "3.0",
        "GMX_GAS_MONITOR_ENABLED": "false",
        "GMX_GAS_ESTIMATE_BUFFER": "1.5",
        "GMX_GAS_RAISE_ON_CRITICAL": "false",
    }

    original_values = {}
    for var in env_vars:
        original_values[var] = os.environ.get(var)

    try:
        for var, value in env_vars.items():
            os.environ[var] = value

        config = create_gas_monitor_config_from_env()

        assert config.warning_threshold_usd == 15.0
        assert config.critical_threshold_usd == 3.0
        assert config.enabled is False
        assert config.gas_estimate_buffer == 1.5
        assert config.raise_on_critical is False

    finally:
        # Restore original env vars
        for var, value in original_values.items():
            if value is not None:
                os.environ[var] = value
            else:
                os.environ.pop(var, None)


# =============================================================================
# Integration tests with mock Web3 (no actual RPC required)
# =============================================================================


def test_gmx_gas_monitor_init():
    """Test GMXGasMonitor initialisation."""
    mock_web3 = MagicMock()

    monitor = GMXGasMonitor(
        web3=mock_web3,
        chain="arbitrum",
        config=GasMonitorConfig(
            warning_threshold_usd=15.0,
            critical_threshold_usd=3.0,
        ),
    )

    assert monitor.web3 is mock_web3
    assert monitor.chain == "arbitrum"
    assert monitor.config.warning_threshold_usd == 15.0
    assert monitor.config.critical_threshold_usd == 3.0


def test_gmx_gas_monitor_init_default_config():
    """Test GMXGasMonitor with default config."""
    mock_web3 = MagicMock()

    monitor = GMXGasMonitor(web3=mock_web3, chain="arbitrum")

    assert monitor.config.warning_threshold_usd == DEFAULT_GAS_WARNING_THRESHOLD_USD
    assert monitor.config.critical_threshold_usd == DEFAULT_GAS_CRITICAL_THRESHOLD_USD


def test_check_gas_balance_ok():
    """Test check_gas_balance returns ok status for sufficient balance."""
    mock_web3 = MagicMock()
    mock_web3.eth.get_balance.return_value = 10000000000000000000  # 10 ETH

    monitor = GMXGasMonitor(
        web3=mock_web3,
        chain="arbitrum",
        config=GasMonitorConfig(
            warning_threshold_usd=10.0,
            critical_threshold_usd=2.0,
        ),
    )

    # Mock the oracle prices to return ETH at $3500
    with patch.object(monitor, "get_native_token_price_usd", return_value=3500.0):
        result = monitor.check_gas_balance("0x1234567890123456789012345678901234567890")

    assert result.passed is True
    assert result.status == "ok"
    assert result.balance_usd == 35000.0  # 10 ETH * $3500


def test_check_gas_balance_warning():
    """Test check_gas_balance returns warning status for low balance."""
    mock_web3 = MagicMock()
    mock_web3.eth.get_balance.return_value = 2500000000000000  # 0.0025 ETH

    monitor = GMXGasMonitor(
        web3=mock_web3,
        chain="arbitrum",
        config=GasMonitorConfig(
            warning_threshold_usd=10.0,
            critical_threshold_usd=2.0,
        ),
    )

    # Mock the oracle prices to return ETH at $3500 -> 0.0025 ETH = $8.75
    with patch.object(monitor, "get_native_token_price_usd", return_value=3500.0):
        result = monitor.check_gas_balance("0x1234567890123456789012345678901234567890")

    assert result.passed is True  # Warning still passes
    assert result.status == "warning"
    assert result.balance_usd == pytest.approx(8.75, rel=0.01)


def test_check_gas_balance_critical():
    """Test check_gas_balance returns critical status for very low balance."""
    mock_web3 = MagicMock()
    mock_web3.eth.get_balance.return_value = 500000000000000  # 0.0005 ETH

    monitor = GMXGasMonitor(
        web3=mock_web3,
        chain="arbitrum",
        config=GasMonitorConfig(
            warning_threshold_usd=10.0,
            critical_threshold_usd=2.0,
        ),
    )

    # Mock the oracle prices to return ETH at $3500 -> 0.0005 ETH = $1.75
    with patch.object(monitor, "get_native_token_price_usd", return_value=3500.0):
        result = monitor.check_gas_balance("0x1234567890123456789012345678901234567890")

    assert result.passed is False
    assert result.status == "critical"
    assert result.balance_usd == pytest.approx(1.75, rel=0.01)


def test_check_gas_balance_no_price():
    """Test check_gas_balance when price is unavailable falls back to native balance."""
    mock_web3 = MagicMock()
    mock_web3.eth.get_balance.return_value = 10000000000000000  # 0.01 ETH

    monitor = GMXGasMonitor(
        web3=mock_web3,
        chain="arbitrum",
        config=GasMonitorConfig(),
    )

    # Mock the oracle prices to return None (unavailable)
    with patch.object(monitor, "get_native_token_price_usd", return_value=None):
        result = monitor.check_gas_balance("0x1234567890123456789012345678901234567890")

    # With 0.01 ETH, should be ok (above 0.001 heuristic threshold)
    assert result.passed is True
    assert result.status == "ok"
    assert result.balance_usd is None


def test_estimate_transaction_gas():
    """Test estimate_transaction_gas applies buffer correctly."""
    mock_web3 = MagicMock()
    mock_web3.eth.estimate_gas.return_value = 375000
    mock_web3.eth.gas_price = 80000000  # 0.08 gwei
    mock_web3.to_wei = lambda x, unit: int(x * 10**9) if unit == "gwei" else x

    monitor = GMXGasMonitor(
        web3=mock_web3,
        chain="arbitrum",
        config=GasMonitorConfig(gas_estimate_buffer=1.2),
    )

    tx = {"to": "0x1234", "data": "0x"}

    with patch.object(monitor, "get_native_token_price_usd", return_value=3500.0):
        estimate = monitor.estimate_transaction_gas(
            tx=tx,
            from_addr="0x1234567890123456789012345678901234567890",
        )

    assert estimate.raw_gas_limit == 375000
    assert estimate.gas_limit == 450000  # 375000 * 1.2
    assert estimate.safety_buffer == 1.2


def test_log_gas_estimate(caplog):
    """Test log_gas_estimate produces correct output."""
    mock_web3 = MagicMock()

    monitor = GMXGasMonitor(web3=mock_web3, chain="arbitrum")

    estimate = GasEstimate(
        raw_gas_limit=375000,
        gas_limit=450000,
        safety_buffer=1.2,
        gas_price_wei=80000000000,  # 80 gwei
        estimated_cost_wei=36000000000000000,  # 0.036 ETH
        estimated_cost_native=Decimal("0.036"),
        native_price_usd=3500.0,
        estimated_cost_usd=126.0,
    )

    import logging

    with caplog.at_level(logging.INFO):
        monitor.log_gas_estimate(estimate, "GMX order")

    # Check that logs contain expected information
    log_text = caplog.text
    assert "375000" in log_text  # raw estimate
    assert "1.2" in log_text  # buffer
    assert "450000" in log_text  # final estimate


def test_log_gas_usage(caplog):
    """Test log_gas_usage produces correct output."""
    mock_web3 = MagicMock()

    monitor = GMXGasMonitor(web3=mock_web3, chain="arbitrum")

    receipt = {
        "gasUsed": 387234,
        "effectiveGasPrice": 80000000000,  # 80 gwei
    }

    import logging

    with caplog.at_level(logging.INFO):
        gas_cost_native, gas_cost_usd = monitor.log_gas_usage(
            receipt=receipt,
            native_price_usd=3500.0,
            operation="GMX order",
            estimated_gas=450000,
        )

    # Check return values
    assert gas_cost_native > 0
    assert gas_cost_usd > 0

    # Check that logs contain expected information
    log_text = caplog.text
    assert "387234" in log_text  # actual gas used


# =============================================================================
# Integration tests with real RPC (requires JSON_RPC_ARBITRUM env var)
# =============================================================================


def test_get_native_token_price_usd_arbitrum(chain_name, web3_mainnet):
    """Test fetching native token price from GMX oracle on mainnet."""
    monitor = GMXGasMonitor(
        web3=web3_mainnet,
        chain=chain_name,
    )

    price = monitor.get_native_token_price_usd()

    # ETH price should be reasonable (between $100 and $100000)
    assert price is not None
    assert 100 < price < 100000


def test_check_gas_balance_mainnet(chain_name, web3_mainnet):
    """Test check_gas_balance on mainnet with a known address."""
    monitor = GMXGasMonitor(
        web3=web3_mainnet,
        chain=chain_name,
    )

    # Use a known Binance hot wallet that should have ETH
    # Note: This test just verifies the integration works
    result = monitor.check_gas_balance("0xF977814e90dA44bFA03b6295A0616a897441aceC")

    # Should return a valid result (we don't check specific values)
    assert result.native_balance_wei >= 0
    assert result.status in ("ok", "warning", "critical")


def test_gas_monitor_oracle_lazy_load(chain_name, web3_mainnet):
    """Test that oracle prices instance is lazily loaded."""
    monitor = GMXGasMonitor(
        web3=web3_mainnet,
        chain=chain_name,
    )

    # Oracle should not be loaded yet
    assert monitor._oracle_prices is None

    # Access the property to trigger lazy loading
    oracle = monitor.oracle_prices

    # Now it should be loaded
    assert monitor._oracle_prices is not None
    assert oracle is monitor._oracle_prices
