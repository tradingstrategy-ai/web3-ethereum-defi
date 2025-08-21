"""
Tests for GMXConfig with parametrized chain testing.

This test suite verifies the configuration functionality across different chains
and with various wallet implementations.
"""

import pytest
from eth_account import Account
from unittest.mock import MagicMock

from eth_defi.gmx.config import GMXConfig, GMXConfigManager
from eth_defi.hotwallet import HotWallet
from eth_defi.basewallet import BaseWallet
from tests.gmx.conftest import CHAIN_CONFIG


def test_init_basic(chain_name, web3_mainnet):
    """Test basic initialization without wallet or private key."""
    config = GMXConfig(web3_mainnet)

    # Check attributes
    assert config.chain == chain_name
    assert config._wallet is None
    assert config._user_wallet_address is None
    assert config._rpc_url is not None

    # Check configs
    assert config._read_config is not None
    assert config._write_config is None
    assert not config.has_write_capability()


def test_init_with_wallet_address(chain_name, web3_mainnet):
    """Test initialization with wallet address only."""
    wallet_address = "0x1234567890123456789012345678901234567890"
    config = GMXConfig(web3_mainnet, user_wallet_address=wallet_address)

    assert config._user_wallet_address == wallet_address
    assert config._wallet is None
    assert not config.has_write_capability()


def test_init_with_hot_wallet(chain_name, web3_mainnet):
    """Test initialization with HotWallet."""
    # Create a hot wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config = GMXConfig(web3_mainnet, wallet=wallet)

    assert config._wallet is wallet
    assert config._user_wallet_address == wallet.address
    assert config._write_config is not None
    assert config.has_write_capability()


def test_init_with_mock_provider_wallet(chain_name, web3_mainnet):
    """Test initialization with a mock wallet that acts like Web3ProviderWallet."""
    # Create a mock wallet that mimics Web3ProviderWallet
    mock_wallet = MagicMock(spec=BaseWallet)
    # Set up the main_address property to return a fixed address
    mock_wallet.get_main_address.return_value = "0x1234567890123456789012345678901234567890"
    mock_wallet.current_nonce = 0

    config = GMXConfig(web3_mainnet, wallet=mock_wallet)

    assert config._wallet is mock_wallet
    assert config._user_wallet_address == "0x1234567890123456789012345678901234567890"
    assert config._write_config is not None
    assert config.has_write_capability()


def test_init_with_wallet_and_address(chain_name, web3_mainnet):
    """Test initialization with both wallet and explicit address."""
    # Create a hot wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    # Use a different address than the wallet's address
    other_address = "0x9876543210987654321098765432109876543210"

    config = GMXConfig(web3_mainnet, wallet=wallet, user_wallet_address=other_address)

    # The explicitly provided address should be respected, but wallet should still be used for signing
    assert config._wallet is wallet
    assert config._user_wallet_address == other_address  # User-provided address is respected
    assert config._write_config is not None
    assert config.has_write_capability()


def test_from_private_key_legacy_support(chain_name, web3_mainnet):
    """Test the from_private_key class method for backward compatibility."""
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"

    config = GMXConfig.from_private_key(web3_mainnet, private_key)

    assert config._wallet is not None
    assert isinstance(config._wallet, HotWallet)
    assert config._user_wallet_address == config._wallet.address
    assert config.has_write_capability()

    # Check that write config was properly created
    write_config = config.get_write_config()
    assert write_config.chain == chain_name


def test_get_read_config(chain_name, web3_mainnet):
    """Test get_read_config method returns a GMXConfigManager."""
    config = GMXConfig(web3_mainnet)
    read_config = config.get_read_config()

    assert read_config.chain == chain_name
    assert read_config.private_key is None
    assert isinstance(read_config, GMXConfigManager)


def test_get_write_config_with_wallet(chain_name, web3_mainnet):
    """Test get_write_config method with wallet."""
    # Create a hot wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config = GMXConfig(web3_mainnet, wallet=wallet)

    write_config = config.get_write_config()
    assert write_config.chain == chain_name

    # The wallet-adapter signer should be set on the ConfigManager
    assert write_config._signer is not None


def test_get_write_config_without_wallet(chain_name, web3_mainnet):
    """Test get_write_config method without wallet should raise ValueError."""
    config = GMXConfig(web3_mainnet)

    with pytest.raises(ValueError) as excinfo:
        config.get_write_config()

    assert "No wallet provided" in str(excinfo.value)


def test_has_write_capability(chain_name, web3_mainnet):
    """Test has_write_capability method."""
    # Without wallet
    config = GMXConfig(web3_mainnet)
    assert not config.has_write_capability()

    # With wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config_with_wallet = GMXConfig(web3_mainnet, wallet=wallet)
    assert config_with_wallet.has_write_capability()


def test_get_chain(chain_name, web3_mainnet):
    """Test get_chain method."""
    config = GMXConfig(web3_mainnet)
    assert config.get_chain() == chain_name


def test_get_wallet_address(chain_name, web3_mainnet):
    """Test get_wallet_address method."""
    # Without wallet address
    config = GMXConfig(web3_mainnet)
    assert config.get_wallet_address() is None

    # With explicit wallet address
    wallet_address = "0x1234567890123456789012345678901234567890"
    config_with_address = GMXConfig(web3_mainnet, user_wallet_address=wallet_address)
    assert config_with_address.get_wallet_address() == wallet_address

    # With wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    config_with_wallet = GMXConfig(web3_mainnet, wallet=wallet)
    assert config_with_wallet.get_wallet_address() == wallet.address


def test_get_network_info(chain_name, web3_mainnet):
    """Test get_network_info method."""
    config = GMXConfig(web3_mainnet)

    network_info = config.get_network_info()
    assert network_info["chain"] == chain_name
    assert isinstance(network_info["rpc_url"], str)
    assert "chain_id" in network_info

    assert network_info["chain_id"] == CHAIN_CONFIG[chain_name]["chain_id"]


def test_wallet_adapter_signer(chain_name, web3_mainnet):
    """Test the WalletAdapterSigner works correctly."""
    # Create a hot wallet with a known private key
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_mainnet)

    # Create config with the wallet
    config = GMXConfig(web3_mainnet, wallet=wallet)

    # Get the write config with the adapter signer
    write_config = config.get_write_config()

    # Check that the adapter signer has the correct wallet address
    assert write_config._signer.get_address() == wallet.address


def test_private_key_auto_address_derivation(web3_fork, chain_name, anvil_private_key):
    """Test that from_private_key method sets up the correct wallet address."""
    # Determine expected address from this private key
    account = Account.from_key(anvil_private_key)
    expected_address = account.address

    # Create GMXConfig using from_private_key method
    config = GMXConfig.from_private_key(web3_fork, anvil_private_key)

    # Check that the wallet was created
    assert config._wallet is not None
    assert isinstance(config._wallet, HotWallet)

    # Verify that the address was derived correctly
    assert config._user_wallet_address is not None
    assert config._user_wallet_address == expected_address
    assert config.get_wallet_address() == expected_address

    # Verify write capability
    assert config.has_write_capability()

    # Check that read and write configs have the correct address
    assert config.get_read_config().user_wallet_address == expected_address
    assert config.get_write_config().user_wallet_address == expected_address


def test_private_key_with_explicit_address(web3_fork, chain_name, anvil_private_key):
    """Test that when wallet and explicit address are provided, the explicit address is used."""
    # Use an explicit address different from the private key's address
    explicit_address = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # Second anvil address

    # Create wallet from private key
    from eth_account import Account

    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_fork)

    # Create GMXConfig with both wallet and explicit address
    config = GMXConfig(
        web3_fork,
        wallet=wallet,
        user_wallet_address=explicit_address,
    )

    # Check that the wallet was created
    assert config._wallet is not None
    assert isinstance(config._wallet, HotWallet)

    # Verify that the explicit address is used (not the derived one)
    assert config._user_wallet_address == explicit_address
    assert config.get_wallet_address() == explicit_address

    # Verify write capability
    assert config.has_write_capability()


def test_comparison_with_different_initialization_methods(web3_fork, chain_name, anvil_private_key):
    """Test that different initialization methods result in consistent addresses."""
    # Create account and wallet
    account = Account.from_key(anvil_private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3_fork)
    expected_address = wallet.address

    # Create GMXConfig in three different ways
    config1 = GMXConfig.from_private_key(web3_fork, anvil_private_key)  # Factory method
    config2 = GMXConfig(web3_fork, wallet=wallet)  # Wallet only
    config3 = GMXConfig.from_private_key(web3_fork, anvil_private_key)  # Factory method (again)

    # All three should result in the same address
    assert config1.get_wallet_address() == expected_address
    assert config2.get_wallet_address() == expected_address
    assert config3.get_wallet_address() == expected_address

    # All three should have write capability
    assert config1.has_write_capability()
    assert config2.has_write_capability()
    assert config3.has_write_capability()

# TODO: Keep the other class tests commented out for now
# def test_integration_with_trading(web3_fork, chain_name, wallet_with_usdc, anvil_private_key):
#     """Test that a config created with just a private key works with trading operations."""
#     from eth_defi.gmx.trading import GMXTrading

#     # Create GMXConfig using from_private_key method
#     config = GMXConfig.from_private_key(web3_fork, anvil_private_key)

#     # Create a trading instance
#     trading = GMXTrading(config)

#     # Test parameters
#     if chain_name == "arbitrum":
#         market_symbol = "ETH"
#         collateral_symbol = "USDC"
#     else:  # avalanche
#         market_symbol = "AVAX"
#         collateral_symbol = "USDC"

#     # Try to create a position order in debug mode
#     order = trading.open_position(
#         market_symbol=market_symbol,
#         collateral_symbol=collateral_symbol,
#         start_token_symbol=collateral_symbol,
#         is_long=True,
#         size_delta_usd=100,
#         leverage=2,
#         debug_mode=True,
#     )

#     # Verify the order was created
#     assert order is not None
#     assert order.is_long is True
#     assert order.debug_mode is True

#     # Verify the order has the correct config with the derived address
#     assert hasattr(order, "config")
#     assert order.config.user_wallet_address == config.get_wallet_address()


def test_gmx_config_manager_basic():
    """Test GMXConfigManager basic functionality."""
    config_manager = GMXConfigManager(chain="arbitrum", chain_id=42161, wallet=None, web3=None)

    assert config_manager.chain == "arbitrum"
    assert config_manager.chain_id == 42161
    assert config_manager.user_wallet_address is None
    assert config_manager.wallet is None
    assert config_manager.private_key is None
    assert config_manager._signer is None


def test_gmx_config_manager_with_wallet(web3_mainnet):
    """Test GMXConfigManager with wallet."""
    # Create a hot wallet
    private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"
    account = Account.from_key(private_key)
    wallet = HotWallet(account)

    config_manager = GMXConfigManager(chain="arbitrum", chain_id=42161, wallet=wallet, web3=web3_mainnet)

    assert config_manager.chain == "arbitrum"
    assert config_manager.chain_id == 42161
    assert config_manager.user_wallet_address == wallet.address
    assert config_manager.wallet is wallet
    assert config_manager.private_key is None
    assert config_manager._signer is not None


# def test_config_compatibility_with_gmx_classes(chain_name, web3_mainnet):
#     """Test that our config works with GMX data classes."""
#     from eth_defi.gmx.data import GMXMarketData
#
#     # Create config
#     config = GMXConfig(web3_mainnet)
#
#     # Create market data instance
#     market_data = GMXMarketData(config)
#
#     # Verify it uses our config manager
#     assert isinstance(market_data.config, GMXConfigManager)
#     assert market_data.config.chain == chain_name
