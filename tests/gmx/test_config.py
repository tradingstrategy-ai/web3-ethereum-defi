"""
Tests for GMXConfig with parametrized chain testing.

This test suite verifies the configuration functionality across different chains
and with various wallet implementations.
"""

from eth_defi.gmx.config import GMXConfig, GMXConfigManager
from tests.gmx.conftest import CHAIN_CONFIG


def test_init_basic(chain_name, web3_mainnet):
    """Test basic initialization without wallet or private key."""
    config = GMXConfig(web3_mainnet)

    # Check attributes
    assert config.chain == chain_name
    assert config._user_wallet_address is None
    assert config._rpc_url is not None

    # Check configuration
    assert not config.has_write_capability()
    assert config.get_config() is not None
    assert config.get_config().chain == chain_name


def test_init_with_wallet_address_only(chain_name, web3_mainnet):
    """Test initialization with wallet address only."""
    wallet_address = "0x1234567890123456789012345678901234567890"
    config = GMXConfig(web3_mainnet, user_wallet_address=wallet_address)

    assert config._user_wallet_address == wallet_address
    assert config.has_write_capability()  # Should have write capability with address


def test_init_with_wallet_address(chain_name, web3_mainnet):
    """Test initialization with wallet address."""
    wallet_address = "0x1234567890123456789012345678901234567890"

    config = GMXConfig(web3_mainnet, user_wallet_address=wallet_address)

    assert config._user_wallet_address == wallet_address
    assert config.has_write_capability()
    assert config.get_config().user_wallet_address == wallet_address


# def test_init_with_mock_provider_wallet(chain_name, web3_mainnet):
#     """Test initialization with a mock wallet that acts like Web3ProviderWallet."""
#     # Create a mock wallet that mimics Web3ProviderWallet
#     mock_wallet = MagicMock(spec=BaseWallet)
#     # Set up the main_address property to return a fixed address
#     mock_wallet.get_main_address.return_value = "0x1234567890123456789012345678901234567890"
#     mock_wallet.current_nonce = 0

#     config = GMXConfig(web3_mainnet, wallet=mock_wallet)

#     assert config._wallet is mock_wallet
#     assert config._user_wallet_address == "0x1234567890123456789012345678901234567890"
#     assert config._write_config is not None
#     assert config.has_write_capability()


def test_get_read_config(chain_name, web3_mainnet):
    """Test get_read_config method returns a GMXConfigManager."""
    config = GMXConfig(web3_mainnet)
    read_config = config.get_read_config()

    assert read_config.chain == chain_name
    assert read_config.private_key is None
    assert isinstance(read_config, GMXConfigManager)


def test_get_write_config_with_address(chain_name, web3_mainnet):
    """Test get_write_config method with address."""
    wallet_address = "0x1234567890123456789012345678901234567890"
    config = GMXConfig(web3_mainnet, user_wallet_address=wallet_address)

    write_config = config.get_write_config()
    assert write_config.chain == chain_name
    assert write_config.user_wallet_address == wallet_address
    # No signer should be set as signing is handled separately
    assert write_config._signer is None


def test_get_write_config_without_address(chain_name, web3_mainnet):
    """Test get_write_config method without address."""
    config = GMXConfig(web3_mainnet)

    # Should return config without error since no signing is involved
    write_config = config.get_write_config()
    assert write_config.chain == chain_name
    assert write_config.user_wallet_address is None
    assert write_config._signer is None


def test_has_write_capability(chain_name, web3_mainnet):
    """Test has_write_capability method."""
    # Without address
    config = GMXConfig(web3_mainnet)
    assert not config.has_write_capability()

    # With address
    wallet_address = "0x1234567890123456789012345678901234567890"
    config_with_address = GMXConfig(web3_mainnet, user_wallet_address=wallet_address)
    assert config_with_address.has_write_capability()


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


def test_get_network_info(chain_name, web3_mainnet):
    """Test get_network_info method."""
    config = GMXConfig(web3_mainnet)

    network_info = config.get_network_info()
    assert network_info["chain"] == chain_name
    assert isinstance(network_info["rpc_url"], str)
    assert "chain_id" in network_info

    assert network_info["chain_id"] == CHAIN_CONFIG[chain_name]["chain_id"]


# def test_wallet_adapter_signer(chain_name, web3_mainnet):
#     """Test removed - no signer functionality in configuration anymore."""
#     # Signer functionality removed - signing handled separately during tx execution


# def test_private_key_auto_address_derivation(web3_fork, chain_name, anvil_private_key):
#     """Test removed - from_private_key method no longer exists."""
#     # Use direct address initialization: GMXConfig(web3, user_wallet_address="0x...")


# def test_private_key_with_explicit_address(web3_fork, chain_name, anvil_private_key):
#     """Test removed - wallet functionality no longer exists."""
#     # Only address-based initialization supported now


# def test_comparison_with_different_initialization_methods(web3_fork, chain_name, anvil_private_key):
#     """Test removed - only one initialization method now exists."""
#     # Only address-based initialization: GMXConfig(web3, user_wallet_address="0x...")

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
    config_manager = GMXConfigManager(chain="arbitrum", chain_id=42161, user_wallet_address=None)

    assert config_manager.chain == "arbitrum"
    assert config_manager.chain_id == 42161
    assert config_manager.user_wallet_address is None
    assert config_manager.private_key is None
    assert config_manager._signer is None


def test_gmx_config_manager_with_address():
    """Test GMXConfigManager with user address."""
    wallet_address = "0x1234567890123456789012345678901234567890"

    config_manager = GMXConfigManager(chain="arbitrum", chain_id=42161, user_wallet_address=wallet_address)

    assert config_manager.chain == "arbitrum"
    assert config_manager.chain_id == 42161
    assert config_manager.user_wallet_address == wallet_address
    assert config_manager.private_key is None
    assert config_manager._signer is None


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
