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


def test_get_config(chain_name, web3_mainnet):
    """Test get_config method returns a GMXConfigManager."""
    config = GMXConfig(web3_mainnet)
    config_manager = config.get_config()

    assert config_manager.chain == chain_name
    assert config_manager.private_key is None
    assert isinstance(config_manager, GMXConfigManager)


def test_get_config_with_address(chain_name, web3_mainnet):
    """Test get_config method with address."""
    wallet_address = "0x1234567890123456789012345678901234567890"
    config = GMXConfig(web3_mainnet, user_wallet_address=wallet_address)

    config_manager = config.get_config()
    assert config_manager.chain == chain_name
    assert config_manager.user_wallet_address == wallet_address
    # No signer should be set as signing is handled separately
    assert config_manager._signer is None


def test_get_config_without_address(chain_name, web3_mainnet):
    """Test get_config method without address."""
    config = GMXConfig(web3_mainnet)

    # Should return config without error since no signing is involved
    config_manager = config.get_config()
    assert config_manager.chain == chain_name
    assert config_manager.user_wallet_address is None
    assert config_manager._signer is None


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
