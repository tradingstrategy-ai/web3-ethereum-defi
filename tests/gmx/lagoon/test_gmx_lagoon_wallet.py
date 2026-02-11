"""Tests for LagoonWallet - GMX trading through Lagoon vaults.

These tests use mocks to verify the wallet logic without requiring
real blockchain access or GMX API calls.
"""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from hexbytes import HexBytes


@pytest.fixture
def mock_web3():
    """Mock Web3 instance."""
    web3 = Mock()
    web3.eth.chain_id = 42161  # Arbitrum
    web3.eth.get_balance.return_value = 10**18  # 1 ETH
    return web3


@pytest.fixture
def mock_vault(mock_web3):
    """Mock Lagoon vault."""
    vault = Mock()
    vault.web3 = mock_web3
    vault.vault_address = "0x1234567890123456789012345678901234567890"
    vault.safe_address = "0xSafe1234567890123456789012345678901234"
    vault.trading_strategy_module_address = "0xModule12345678901234567890123456789012"

    # Mock trading strategy module
    module = Mock()
    perform_call_result = Mock()
    perform_call_result._encode_transaction_data.return_value = b"encoded_data"
    perform_call_result.address = vault.trading_strategy_module_address
    module.functions.performCall.return_value = perform_call_result
    vault.trading_strategy_module = module

    return vault


@pytest.fixture
def mock_asset_manager(mock_web3):
    """Mock asset manager hot wallet."""
    wallet = Mock()
    wallet.address = "0xAssetManager123456789012345678901234567"
    wallet.sync_nonce = Mock()
    wallet.allocate_nonce = Mock(return_value=42)

    # Mock signing
    signed_tx = Mock()
    signed_tx.rawTransaction = HexBytes(b"\x00" * 32)
    signed_tx.raw_transaction = HexBytes(b"\x00" * 32)
    signed_tx.hash = HexBytes(b"\x01" * 32)
    signed_tx.nonce = 42
    signed_tx.r = 1
    signed_tx.s = 2
    signed_tx.v = 27
    wallet.sign_bound_call_with_new_nonce.return_value = signed_tx

    return wallet


def test_lagoon_wallet_is_base_wallet(mock_vault, mock_asset_manager):
    """Test that LagoonWallet implements BaseWallet interface."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet
    from eth_defi.basewallet import BaseWallet

    wallet = LagoonWallet(mock_vault, mock_asset_manager)

    # Should be a BaseWallet subclass
    assert isinstance(wallet, BaseWallet)


def test_lagoon_wallet_init(mock_vault, mock_asset_manager):
    """Test LagoonWallet initialisation."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    wallet = LagoonWallet(mock_vault, mock_asset_manager)

    assert wallet.vault == mock_vault
    assert wallet.asset_manager == mock_asset_manager
    assert wallet.web3 == mock_vault.web3


def test_lagoon_wallet_address_returns_safe(mock_vault, mock_asset_manager):
    """Test that wallet.address returns the Safe address, not asset manager."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    wallet = LagoonWallet(mock_vault, mock_asset_manager)

    # Address should be the Safe, not the asset manager
    assert wallet.address == mock_vault.safe_address
    assert wallet.get_main_address() == mock_vault.safe_address


def test_lagoon_wallet_sync_nonce_delegates(mock_vault, mock_asset_manager, mock_web3):
    """Test that sync_nonce delegates to asset manager."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    wallet = LagoonWallet(mock_vault, mock_asset_manager)
    wallet.sync_nonce(mock_web3)

    mock_asset_manager.sync_nonce.assert_called_once_with(mock_web3)


def test_lagoon_wallet_allocate_nonce_delegates(mock_vault, mock_asset_manager):
    """Test that allocate_nonce delegates to asset manager."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    wallet = LagoonWallet(mock_vault, mock_asset_manager)
    nonce = wallet.allocate_nonce()

    assert nonce == 42
    mock_asset_manager.allocate_nonce.assert_called_once()


def test_lagoon_wallet_sign_wraps_in_perform_call(mock_vault, mock_asset_manager):
    """Test that sign_transaction_with_new_nonce wraps tx in performCall."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    wallet = LagoonWallet(mock_vault, mock_asset_manager)

    # Create a mock GMX transaction
    tx = {
        "to": "0xExchangeRouter123456789012345678901234",
        "data": HexBytes(b"\x00" * 100),
        "value": 10**16,  # 0.01 ETH
        "gas": 1_500_000,
    }

    # Sign it
    signed = wallet.sign_transaction_with_new_nonce(tx)

    # Verify performCall was called with correct args
    mock_vault.trading_strategy_module.functions.performCall.assert_called_once_with(
        tx["to"],
        tx["data"],
        tx["value"],
    )

    # Verify asset_manager.sign_bound_call_with_new_nonce was called
    mock_asset_manager.sign_bound_call_with_new_nonce.assert_called_once()

    # Check gas was increased by buffer
    call_args = mock_asset_manager.sign_bound_call_with_new_nonce.call_args
    tx_params = call_args.kwargs.get("tx_params", {})
    assert tx_params["gas"] == 1_500_000 + 200_000  # Original + buffer


def test_lagoon_wallet_get_native_balance(mock_vault, mock_asset_manager, mock_web3):
    """Test that get_native_currency_balance returns Safe balance."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    wallet = LagoonWallet(mock_vault, mock_asset_manager)
    balance = wallet.get_native_currency_balance(mock_web3)

    # Should query Safe address, not asset manager
    mock_web3.eth.get_balance.assert_called_once_with(mock_vault.safe_address)
    assert balance == Decimal("1")  # 1 ETH


def test_lagoon_wallet_rejects_invalid_vault(mock_asset_manager):
    """Test that LagoonWallet rejects objects without trading_strategy_module."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    invalid_vault = Mock(spec=[])  # No trading_strategy_module attribute

    with pytest.raises(TypeError, match="trading_strategy_module"):
        LagoonWallet(invalid_vault, mock_asset_manager)


def test_lagoon_wallet_rejects_invalid_asset_manager(mock_vault):
    """Test that LagoonWallet rejects objects without sign_bound_call_with_new_nonce."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    invalid_manager = Mock(spec=[])  # No sign_bound_call_with_new_nonce attribute

    with pytest.raises(TypeError, match="HotWallet-like"):
        LagoonWallet(mock_vault, invalid_manager)


def test_lagoon_wallet_rejects_vault_without_module_address(mock_asset_manager, mock_web3):
    """Test that LagoonWallet rejects vault without TradingStrategyModuleV0."""
    from eth_defi.gmx.lagoon.wallet import LagoonWallet

    vault = Mock()
    vault.web3 = mock_web3
    vault.trading_strategy_module = Mock()
    vault.trading_strategy_module_address = None  # Not configured
    vault.vault_address = "0x1234"

    with pytest.raises(ValueError, match="no TradingStrategyModuleV0"):
        LagoonWallet(vault, mock_asset_manager)


def test_approve_gmx_collateral_via_vault():
    """Test approving GMX collateral through vault."""
    with patch("eth_defi.gmx.lagoon.approvals.get_contract_addresses") as mock_addresses:
        addresses = Mock()
        addresses.syntheticsrouter = "0xSyntheticsRouter1234567890123456789012"
        mock_addresses.return_value = addresses

        # Create mocks
        mock_web3 = Mock()
        mock_web3.eth.chain_id = 42161
        mock_web3.to_hex.return_value = "0xabcd1234"

        mock_vault = Mock()
        mock_vault.web3 = mock_web3
        mock_vault.transact_via_trading_strategy_module.return_value = Mock()

        mock_wallet = Mock()
        mock_wallet.sync_nonce = Mock()
        signed_tx = Mock()
        signed_tx.raw_transaction = HexBytes(b"\x00" * 32)
        mock_wallet.sign_bound_call_with_new_nonce.return_value = signed_tx

        mock_token = Mock()
        mock_token.symbol = "USDC"
        mock_token.address = "0xUSDC123456789012345678901234567890123"
        mock_token.convert_to_raw.return_value = 10**9  # 1000 USDC
        mock_token.contract.functions.approve.return_value = Mock()

        mock_web3.eth.send_raw_transaction.return_value = HexBytes(b"\x00" * 32)

        # Mock assert_transaction_success
        with patch("eth_defi.gmx.lagoon.approvals.assert_transaction_success_with_explanation"):
            from eth_defi.gmx.lagoon.approvals import approve_gmx_collateral_via_vault

            tx_hash = approve_gmx_collateral_via_vault(
                vault=mock_vault,
                asset_manager=mock_wallet,
                collateral_token=mock_token,
                amount=Decimal("1000"),
            )

            assert tx_hash is not None
            mock_vault.transact_via_trading_strategy_module.assert_called_once()
            mock_wallet.sign_bound_call_with_new_nonce.assert_called_once()
